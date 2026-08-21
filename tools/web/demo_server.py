# -*- coding: utf-8 -*-
"""极快演示端点 —— 可以放心给人点的只读 Chat BI 服务（v0.28.0 W164，ADR-42）。

与 `tools/web/server.py`（本机开发服务）**完全独立**：那套无鉴权、`/api/跑`
在服务进程内执行提交的极快源码，等价本地 RCE，AGENTS.md §三 明令不许挂网。
本模块是新开的第二套，四道闸全部**默认拒绝**：

    POST /演示/问     {需求}          → 规划上下文包（离线，一个模型都不碰）
    POST /演示/跑     {方案}          → 组 → 子进程执行 → 跑响应（**只收方案，出现「源码」键即 400**）
    GET  /演示/白名单                 → {允许块:[...], 数据集根:"..."}

四道闸（详见 `docs/ADR-42-演示端点安全模型.md`）：

  1. 鉴权：`Authorization: Bearer <token>`，Token 只从环境变量 `JIKUAI_DEMO_TOKEN`
     取；未设则**拒绝启动**（`build_server` 抛，`main` 退 1）。比较用
     `hmac.compare_digest`。命令行不收 key。
  2. 块白名单：只放行 `允许块`（制造域引擎/口径块）。方案任何一步不在白名单 → 400。
  3. 执行隔离：`组` 出源码后用 `subprocess` 起**独立解释器**跑，带 `timeout`，超时
     504。**绝不在服务进程内 `run_source`**——那正是旧服务的病根。
  4. 数据集只读：方案里出现的文件路径必须 `realpath` 后落在 `赛题/chatbi/数据集/`
     之内（`commonpath` 前缀校验，吃 `..` 与符号链接逃逸），越界 → 400。

实现约束：只用标准库。协议字段名一律取自 `jikuai.service.schema`（唯一真源），
本模块**不自造协议字段**。
"""

import argparse
import hmac
import json
import logging
import os
import re
import socketserver
import subprocess
import sys
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.service import schema                                  # noqa: E402

#: 协议字段名一律从 schema 常量取，整元组解包——协议真加字段这里会当场 ValueError
#: （与 `server.py` 同一套 W20 硬门槛做法）。
_F需求, _F共享, _F打印 = schema.PLAN_OPTIONAL
_F步骤 = schema.PLAN_REQUIRED[0]
_F块, _F领域, _F导出名 = schema.STEP_REQUIRED
_F参数, _F说明, _F命名空间 = schema.STEP_OPTIONAL
_F源码, _F执行结果 = schema.RUN_ENVELOPE_REQUIRED
#: 共享常量的字段名。`值` 不是协议 schema 的一部分（它是方案的 `共享[].值`），
#: 但键名同样不写裸字面量到判断逻辑里——集中在此。
_F名 = '名'
_F值 = '值'

__all__ = [
    'DEFAULT_HOST', 'DEFAULT_PORT', 'TOKEN_ENV', 'MAX_BODY', 'MAX_PLAN_BYTES',
    'EXEC_TIMEOUT', '允许块', '数据集根', 'DemoHandler', 'build_server', 'main',
]

_LOG = logging.getLogger('jikuai.demo')

#: 演示端点默认也只绑回环。本轮不做公网部署（ADR-42），要挂网另立 v0.29.0 的反代方案。
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 5001

#: 鉴权 Token 的**唯一**来源。命令行不收 key（沿用 v0.27.0 `JIKUAI_PLANNER_TOKEN`
#: 的拍板）。未设则拒绝启动，不生成随机默认 Token 打日志。
TOKEN_ENV = 'JIKUAI_DEMO_TOKEN'

#: 请求体上限 1 MiB（沿用 server.py 口径，不重造）。
MAX_BODY = 1024 * 1024

#: 单个方案序列化上限（沿用 server.py 的 MAX_PLAN_BYTES 口径）。
MAX_PLAN_BYTES = 64 * 1024

#: 子进程执行墙钟上限（秒）。演示题最长链 17 步、数据集最大单文件 203KB，
#: 正常在数秒内完成；超时按 504 处理而不是无限等。
EXEC_TIMEOUT = 30

#: 块白名单：只放行制造域引擎层 + 口径层块（目录名）。**默认拒绝**——白名单外的块
#: （尤其任何能碰 `蟒:` 桥、文件写入、网络的块）一律不组进可执行源码。
#: 这份清单是演示端点的信任边界，改它等于改可执行面，G24 会核它非空。
允许块 = frozenset({
    # 引擎层（关系算子 / 数据质量）
    '表载入', '表元信息', '质量体检', '选取', '投影', '排序', '取前N',
    '连接', '分组汇总', '窗口', '月标', '旬标', '邻期关联',
    # 口径层（指标块）
    '产量汇总', '达成率权重', '达成率均值', '缺陷汇总', '缺陷率',
    '能耗汇总', '单车电耗现成', '单车电耗重算', '停线汇总', '延期汇总',
    '延期排行', '班间对比', '窗间对比', '基线偏离',
})

#: 演示只读数据集根。方案里出现的文件路径必须落在这个目录之内。
数据集根 = os.path.join(_REPO, '赛题', 'chatbi', '数据集')

#: 极快字符串字面量的引号（半角与全角）。方案 `共享[].值` 里的路径是极快源码里的
#: 字符串字面量，形如 `“赛题/chatbi/数据集/x.csv”`；剥掉这些引号再做路径校验。
_引号 = '"\'“”‘’「」『』'

#: 盘符前缀（Windows），path.join 遇到会整段替换，必须先拒。
_盘符 = re.compile(r'^[A-Za-z]:')

_GLUE = None
_PLANNER = None


class _请求错误(Exception):
    """调用方的错 → 4xx + 中文原因；与「服务器内部错误」(500) 严格分开。"""

    def __init__(self, 原因, 状态=400):
        super().__init__(原因)
        self.原因 = 原因
        self.状态 = 状态


def _按路径加载(文件名, 模块名):
    """按文件路径加载 `tools/ai-bridge/<文件名>`（口径同 server._glue）。"""
    import importlib.util
    path = os.path.join(_REPO, 'tools', 'ai-bridge', 文件名)
    if not os.path.isfile(path):
        raise _请求错误('找不到 %s；演示端点需要仓库内的 tools/ai-bridge/' % path,
                      状态=503)
    spec = importlib.util.spec_from_file_location(模块名, path)
    if spec is None or spec.loader is None:
        raise _请求错误('%s 无法作为模块加载' % path, 状态=503)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _glue():
    global _GLUE
    if _GLUE is None:
        _GLUE = _按路径加载('glue.py', '_jikuai_demo_glue')
    return _GLUE


def _planner():
    global _PLANNER
    if _PLANNER is None:
        # planner.py 那个目录有 select.py，压在标准库前会遮蔽 select（v0.27.0 必记单点）。
        # _按路径加载 用 spec_from_file_location 不动 sys.path，天然规避。
        _PLANNER = _按路径加载('planner.py', '_jikuai_demo_planner')
    return _PLANNER


# ---- 四道闸 -------------------------------------------------------------

def _核验令牌(令牌):
    """闸 1：Bearer Token 校验。令牌只从 TOKEN_ENV 取，用 compare_digest 比。"""
    期望 = os.environ.get(TOKEN_ENV)
    if not 期望:
        # 服务本不该在没配 Token 时起来（build_server 已挡）；走到这里是防御性 500。
        raise _请求错误('服务未配置 %s，拒绝所有请求' % TOKEN_ENV, 状态=500)
    if not 令牌 or not hmac.compare_digest(令牌, 期望):
        raise _请求错误('未授权：缺少或错误的 Bearer Token', 状态=401)


def _提取Bearer(请求头):
    值 = 请求头.get('Authorization') or ''
    if 值.startswith('Bearer '):
        return 值[len('Bearer '):].strip()
    return ''


def _校验白名单(方案):
    """闸 2：方案每一步的块都必须在 `允许块` 里。"""
    for i, 步 in enumerate(方案.get(_F步骤) or [], 1):
        块 = 步.get(_F块)
        领域 = 步.get(_F领域)
        if 领域 != '制造':
            raise _请求错误('步骤 %d 的领域「%s」不在演示白名单（演示只放行制造域）'
                          % (i, 领域))
        if 块 not in 允许块:
            raise _请求错误('步骤 %d 的块「%s」不在演示白名单' % (i, 块))
    return 方案


def _剥引号(文本):
    return 文本.strip().strip(_引号).strip()


def _看似路径(文本):
    return ('/' in 文本) or ('\\' in 文本) or 文本.lower().endswith('.csv')


def _校验数据集路径(方案):
    """闸 4：方案 `共享[].值` 里任何看似路径的字符串必须落在数据集根之内。

    极快字符串字面量带引号（半角/全角），先剥引号再判。用 realpath + commonpath，
    吃掉 `..` 与符号链接逃逸；盘符/绝对路径先拒（path.join 遇到会整段替换根）。
    """
    根 = os.path.realpath(数据集根)
    for 项 in 方案.get(_F共享) or []:
        if not isinstance(项, dict):
            continue
        原值 = 项.get(_F值)
        if not isinstance(原值, str):
            continue
        候选 = _剥引号(原值)
        if not 候选 or not _看似路径(候选):
            continue
        if os.path.isabs(候选) or _盘符.match(候选):
            raise _请求错误('共享「%s」的路径「%s」是绝对路径，演示只允许数据集内相对路径'
                          % (项.get(_F名), 候选))
        目标 = os.path.realpath(os.path.join(_REPO, 候选))
        try:
            在内 = os.path.commonpath([根, 目标]) == 根
        except ValueError:
            在内 = False
        if not 在内:
            raise _请求错误('共享「%s」的路径「%s」越界，演示只允许读 赛题/chatbi/数据集/ 下的文件'
                          % (项.get(_F名), 候选))
    return 方案


def _取方案(body):
    """从请求体取方案。**出现「源码」键一律拒**（闸的一部分：演示不收源码）。"""
    if _F源码 in body:
        raise _请求错误('演示端点不接受「%s」字段——只收「方案」，源码由服务端确定性生成'
                      % _F源码)
    方案 = body.get('方案') if '方案' in body else body
    if not isinstance(方案, dict):
        raise _请求错误('「方案」必须是 JSON 对象，实际是 %s' % type(方案).__name__)
    if _F源码 in 方案:
        raise _请求错误('方案里不许带「%s」字段' % _F源码)
    序列化 = json.dumps(方案, ensure_ascii=False)
    if len(序列化.encode('utf-8')) > MAX_PLAN_BYTES:
        raise _请求错误('方案过大（超过 %d 字节）' % MAX_PLAN_BYTES, 状态=413)
    try:
        schema.ensure_plan(方案)
    except schema.SchemaError as e:
        raise _请求错误(str(e))
    _校验白名单(方案)
    _校验数据集路径(方案)
    return 方案


# ---- 子进程执行（闸 3）--------------------------------------------------

#: 子进程引导：把 src/ 挂上，读 .jk 文件用 run_source 跑，stdout 归 stdout。
#: 刻意不在服务进程内 run_source——服务进程内执行提交代码正是旧服务的病根。
_子进程引导 = (
    'import sys; sys.path.insert(0, %r); '
    'from jikuai.main import run_source; '
    'src=open(sys.argv[1], encoding="utf-8").read(); '
    'run_source(src, file=sys.argv[1])'
)


def _子进程跑(源码):
    """把源码落临时 .jk，起独立解释器执行，带超时。返回 schema 执行结果。"""
    fd, path = tempfile.mkstemp(prefix='jk_演示跑_', suffix='.jk')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(源码)
        引导 = _子进程引导 % _SRC
        # 子进程环境：强制 UTF-8 输出（Windows 默认按 GBK 写 stdout，父进程按
        # UTF-8 解就炸），并**抹掉鉴权令牌**——被执行的代码没有任何理由看到它。
        环境 = dict(os.environ)
        环境['PYTHONIOENCODING'] = 'utf-8'
        环境['PYTHONUTF8'] = '1'
        环境.pop(TOKEN_ENV, None)
        try:
            完成 = subprocess.run(
                [sys.executable, '-c', 引导, path],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=EXEC_TIMEOUT, cwd=_REPO, env=环境,
            )
        except subprocess.TimeoutExpired:
            raise _请求错误('执行超时（超过 %d 秒）' % EXEC_TIMEOUT, 状态=504)
        错误 = None
        if 完成.returncode != 0:
            # 子进程内异常的可读消息在 stderr 末尾；不回 traceback 全文。
            尾 = (完成.stderr or '').strip().splitlines()
            错误 = 尾[-1] if 尾 else ('子进程非零退出：%d' % 完成.returncode)
        return schema.make_result(
            stdout=完成.stdout or '', stderr=完成.stderr or '',
            返回值='', 耗时毫秒=0.0, 错误=错误,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---- 端点 ---------------------------------------------------------------

def 问(body):
    """`POST /演示/问`：需求 → 规划上下文包（离线，一个模型都不碰）。"""
    需求 = body.get(_F需求)
    if not isinstance(需求, str) or not 需求.strip():
        raise _请求错误('「需求」必须是非空字符串')
    planner = _planner()
    return planner.build_context(需求.strip())


def 跑(body):
    """`POST /演示/跑`：方案 → 组 → 子进程执行。只收方案，不收源码。"""
    方案 = _取方案(body)
    glue = _glue()
    try:
        源码 = glue.synthesize(方案, 自动链式=True)
    except ValueError as e:
        raise _请求错误('粘合失败：%s' % e)
    if '需人工填参' in 源码:
        执行结果 = schema.make_result(
            错误='方案有步骤的参数未指定；在「步骤」里补 `参数`')
        return schema.make_run_envelope(源码, 执行结果, 需求=方案.get(_F需求))
    return schema.make_run_envelope(源码, _子进程跑(源码), 需求=方案.get(_F需求))


def 白名单():
    """`GET /演示/白名单`：当前允许的块清单 + 数据集根，供前端与审计自查。"""
    return {'允许块': sorted(允许块), '数据集根': os.path.relpath(数据集根, _REPO)}


# ---- HTTP 层 -----------------------------------------------------------

_POST路由 = {'/演示/问': 问, '/演示/跑': 跑}
_GET路由 = ('/演示/白名单',)


class DemoHandler(BaseHTTPRequestHandler):
    """演示端点请求处理器。每个请求先过鉴权闸，再分派。"""

    server_version = 'JiKuaiDemo/0.28.0'
    protocol_version = 'HTTP/1.1'

    def _发送(self, 状态, 数据, 类型):
        self.send_response(状态)
        self.send_header('Content-Type', 类型)
        self.send_header('Content-Length', str(len(数据)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(数据)

    def _发JSON(self, 状态, 对象):
        self._发送(状态, json.dumps(对象, ensure_ascii=False).encode('utf-8'),
                 'application/json; charset=utf-8')

    def _读body(self):
        raw = self.headers.get('Content-Length')
        if raw is None:
            raise _请求错误('缺少 Content-Length 头')
        try:
            长度 = int(raw)
        except ValueError:
            raise _请求错误('Content-Length 不是整数：%r' % raw)
        if 长度 < 0:
            raise _请求错误('Content-Length 不能为负')
        if 长度 > MAX_BODY:
            raise _请求错误('请求体 %d 字节，超过上限 %d 字节' % (长度, MAX_BODY),
                          状态=413)
        数据 = self.rfile.read(长度) if 长度 else b''
        try:
            文本 = 数据.decode('utf-8')
        except UnicodeDecodeError as e:
            raise _请求错误('请求体不是合法 UTF-8：%s' % e)
        try:
            body = json.loads(文本) if 文本.strip() else None
        except ValueError as e:
            raise _请求错误('请求体不是合法 JSON：%s' % e)
        if not isinstance(body, dict):
            raise _请求错误('请求体必须是 JSON 对象')
        return body

    def do_GET(self):
        self._分发(self._GET)

    def do_HEAD(self):
        self._分发(self._GET)

    def do_POST(self):
        self._分发(self._POST)

    def _GET(self):
        路径 = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        _核验令牌(_提取Bearer(self.headers))
        if 路径 == '/演示/白名单':
            self._发JSON(200, 白名单())
        else:
            raise _请求错误('未知端点 %s（GET 只有 %s）'
                          % (路径, '、'.join(_GET路由)), 状态=404)

    def _POST(self):
        路径 = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        _核验令牌(_提取Bearer(self.headers))
        处理 = _POST路由.get(路径)
        if 处理 is None:
            raise _请求错误('未知端点 %s（POST 只有 %s）'
                          % (路径, '、'.join(sorted(_POST路由))), 状态=404)
        self._发JSON(200, 处理(self._读body()))

    def _分发(self, 处理):
        try:
            处理()
        except _请求错误 as e:
            self._发JSON(e.状态, {'错误': e.原因})
        except Exception as e:                                 # noqa: BLE001
            _LOG.exception('处理 %s %s 时内部错误', self.command, self.path)
            self._发JSON(500, {'错误': '服务器内部错误：%s：%s'
                             % (type(e).__name__, e)})

    def log_message(self, fmt, *args):
        _LOG.info('%s - %s', self.address_string(), fmt % args)


class _不反查HTTPServer(ThreadingHTTPServer):
    """绑定时不做反向 DNS（同 server.py 的同名类，避 macOS runner getfqdn 阻塞）。"""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def build_server(host=DEFAULT_HOST, port=DEFAULT_PORT):
    """建一个未启动的演示服务实例。**未配 TOKEN_ENV 时拒绝构造。**

    `port=0` 让内核分配空闲端口（测试用）。

    Token 必须是 **ASCII**：它要进 HTTP `Authorization` 头，而头值只收 latin-1
    （`http.client` 的硬约束，也是 CVE-2019-9740 的防线）。带中文的 Token 会让
    **客户端**在发请求时抛 `UnicodeEncodeError`——报错落在调用方、离病根很远。
    所以在启动这一刻就拒，给一句能照着改的中文理由。
    """
    令牌 = os.environ.get(TOKEN_ENV)
    if not 令牌:
        raise RuntimeError(
            '未设置环境变量 %s —— 演示端点带鉴权，拒绝在无 Token 情况下启动。'
            '请先 `export %s=<你的令牌>`。' % (TOKEN_ENV, TOKEN_ENV))
    if not 令牌.isascii():
        raise RuntimeError(
            '%s 含非 ASCII 字符 —— HTTP 头值只收 latin-1，带中文的 Token 会让'
            '客户端发请求时就抛 UnicodeEncodeError。请改成纯 ASCII。' % TOKEN_ENV)
    return _不反查HTTPServer((host, port), DemoHandler)


def main(argv=None):
    """启动演示端点。返回退出码（0 正常 / 1 异常）。"""
    p = argparse.ArgumentParser(
        description='极快演示端点（带鉴权、块白名单、子进程隔离、数据集只读）')
    p.add_argument('--地址', '--host', dest='host', default=DEFAULT_HOST,
                   help='监听地址，默认 %s' % DEFAULT_HOST)
    p.add_argument('--端口', '--port', dest='port', type=int,
                   default=DEFAULT_PORT, help='监听端口，默认 %d' % DEFAULT_PORT)
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    try:
        srv = build_server(args.host, args.port)
    except RuntimeError as e:
        print('启动失败：%s' % e, file=sys.stderr)
        return 1

    实际地址, 实际端口 = srv.server_address[0], srv.server_address[1]
    print('极快演示端点已启动：http://%s:%d/' % (实际地址, 实际端口), file=sys.stderr)
    print('已启用：Bearer 鉴权 / 块白名单 / 子进程隔离 / 数据集只读。本轮不做公网部署。',
          file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。', file=sys.stderr)
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
