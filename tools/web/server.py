# -*- coding: utf-8 -*-
"""极快 Web UI 通道 —— 本地开发服务（v0.15.0 W17/W18）。

三段式设计语言在 Web 上的落地：`[需求]→[候选]→[方案]→[源码]→[结果]`，与
`jk 块 选/组/跑` 完全同构，只是换了传输层。五个端点：

    GET  /api/blocks   → `stdlib/blocks/索引.json` 原文
    GET  /api/能力     → {神经可用, 索引版本, 块数}      能力探测（W19 前端用）
    POST /api/选       → {需求, top?, 神经?}      → `选响应` {需求, 候选[, 降级说明]}
    POST /api/组       → {方案} 或方案本体        → `组响应` {源码}
    POST /api/跑       → {方案} 或方案本体        → `跑响应` {源码, 执行结果[, 需求]}

`/` 挂 `tools/web/static/` 的单页。

**安全边界（务必先读）**
------------------------
本服务**没有任何鉴权**，`/api/跑` 会在本机进程内执行调用方提交的极快代码
（等价于本地 RCE）。因此：

* 默认只监听 `127.0.0.1`，不要改成 `0.0.0.0`，不要反代到公网；
* 仅供本机单人开发调试使用，不是生产服务，也不是多租户服务；
* 任何需要暴露给他人的场景，请改走 CLI（`jk 块 跑`）或自建带沙箱的通道。

**实现约束**：只用标准库（`http.server` + `json` + `logging`）。W17 DoD 明写
不许新增 pip 依赖。协议字段一律取自 `jikuai.service.schema`（唯一真源，见
`docs/协议-三通道.md`），本通道**不自造字段**——`/api/能力` 的三个字段是
能力探测而非数据协议，不进 schema，只在协议文档里记一笔。
"""

import argparse
import contextlib
import io
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    # 与 `tools/ai-bridge/glue.py` 同策略：桥接工具自己把 `src/` 挂上，
    # 免得强制用户先 `pip install -e .` 才能起 Web UI。
    sys.path.insert(0, _SRC)

from jikuai.service import schema                                  # noqa: E402

#: 协议字段名一律从 schema 常量取，本文件不写裸字面量（W20 硬门槛）。
#: 整元组解包而不是硬编码下标：协议真加了字段这里会当场 ValueError。
_F需求, _F共享, _F打印 = schema.PLAN_OPTIONAL
_F步骤 = schema.PLAN_REQUIRED[0]
_F块, _F领域, _F导出名 = schema.STEP_REQUIRED
_F源码, _F执行结果 = schema.RUN_ENVELOPE_REQUIRED

__all__ = [
    'DEFAULT_HOST', 'DEFAULT_PORT', 'MAX_BODY', 'SAFETY_NOTICE',
    'JiKuaiHandler', 'build_server', 'static_root', 'main',
]

_LOG = logging.getLogger('jikuai.web')

#: 默认只绑本机回环。改这个值等于把本地 RCE 开放给同网段，别改。
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 5000

#: 请求体上限 1 MiB。`rfile.read(Content-Length)` 会照着头部给的数字分配内存，
#: 不设上限就等于让任何请求方决定服务端的内存用量。
MAX_BODY = 1024 * 1024

#: 超限请求体的**排空**上限。超限后仍要把已经在路上的 body 读掉丢弃，否则
#: 413 响应发不到客户端手里（见 `JiKuaiHandler._排空`）。排空是分块丢弃，
#: 内存占用是常数；但也不能无限陪读，超过这个数就直接断。
MAX_DRAIN = 8 * 1024 * 1024

#: `top` 的上限。105 个块全返回也没意义，防止有人拿 `top=1e9` 找乐子。
MAX_TOP = 50

#: 启动时必打的中文风险提示（README 里同步有一份）。
SAFETY_NOTICE = (
    '⚠ 安全提示：本服务无鉴权，/api/跑 会在本机直接执行你提交的极快代码，'
    '仅供本地开发调试使用。请勿绑定 0.0.0.0、勿反向代理、勿暴露到公网。'
)

#: 粘合器给「参数没填上」留的记号（见 `glue.synthesize`）。`跑` 见到它就拒绝执行。
_占位记号 = '需人工填参'

#: 静态资源的 MIME 表。刻意只认这几种——静态目录里本来就只该有这几种文件，
#: 未知后缀一律 `application/octet-stream`，浏览器不会当脚本执行。
_MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
}

#: 盘符前缀（`C:...`）。Windows 上 `os.path.join(根, 'C:/x')` 会整段替换掉根，
#: 必须在 join 之前就拒掉。
_盘符 = re.compile(r'^[A-Za-z]:')

#: 解释器执行是进程级的（`redirect_stdout` 换的是全局 `sys.stdout`），
#: ThreadingHTTPServer 下必须串行化，否则并发两个 `跑` 会互相偷走对方的输出。
# redirect_stdout/stderr 替换全局 sys.stdout/stderr，故其它 handler 禁止 print；添加中间件时须避免 sys.stdout.write。
_执行锁 = threading.Lock()

#: 粘合器模块缓存。
_GLUE = None


# ---- 仓库内资源 --------------------------------------------------------

def static_root() -> str:
    """单页资源目录 `tools/web/static/` 的绝对路径。"""
    return os.path.join(_HERE, 'static')


def _index_path() -> str:
    """`stdlib/blocks/索引.json` 的绝对路径（走 `blocks.blocks_root()`，别自己拼）。"""
    from jikuai.pkg.blocks import blocks_root
    return os.path.join(blocks_root(), '索引.json')


def _glue():
    """按文件路径加载 `tools/ai-bridge/glue.py`。

    `tools/ai-bridge` 不是包，也不该是。与 `pkg/blocks_cli._glue()` 同一套做法：
    `importlib` 挂一个带命名空间前缀的模块名，既不污染 `sys.path`，也不会和
    别人的 `import glue` 撞车。
    """
    global _GLUE
    if _GLUE is not None:
        return _GLUE
    import importlib.util
    path = os.path.join(_REPO, 'tools', 'ai-bridge', 'glue.py')
    if not os.path.isfile(path):
        raise _请求错误('找不到粘合器 %s；组/跑 需要仓库内的 tools/ai-bridge/' % path)
    spec = importlib.util.spec_from_file_location('_jikuai_ai_bridge_glue', path)
    if spec is None or spec.loader is None:      # 理论上不可达，保底不抛裸异常
        raise _请求错误('粘合器 %s 无法作为模块加载' % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _GLUE = mod
    return mod


# ---- 错误分层 ----------------------------------------------------------

class _请求错误(Exception):
    """调用方的错（坏 JSON / 字段不合法 / 块不存在）→ 4xx + 中文原因。

    与「服务器内部错误」严格分开：前者是 400，后者才是 500。把两者混在一起
    的服务没法用——调用方分不清该改自己的请求还是该报 bug。
    """

    def __init__(self, 原因: str, 状态: int = 400):
        super().__init__(原因)
        self.原因 = 原因
        self.状态 = 状态


# ---- 业务：选 / 组 / 跑 -------------------------------------------------

def _取方案(body: dict) -> dict:
    """从请求体里取出方案。

    两种写法都收（README §端点 有说明）：

        {"方案": {"步骤": [...]}}      -- 信封式，推荐，未来好加平级选项
        {"步骤": [...]}                -- 方案本体，`jk 块 组 -` 的 JSON 直接贴

    判据是「有没有 `方案` 键」。取出后立刻过 `schema.ensure_plan`——协议校验
    在业务之前，不给粘合器喂半成品。
    """
    方案 = body.get('方案') if '方案' in body else body
    if not isinstance(方案, dict):
        raise _请求错误('「方案」必须是 JSON 对象，实际是 %s' % type(方案).__name__)
    try:
        schema.ensure_plan(方案)
    except schema.SchemaError as e:
        raise _请求错误(str(e))
    return _校验块存在(方案)


def _校验块存在(方案: dict) -> dict:
    """协议之外的语义校验：领域在白名单里、块目录真实存在。

    `schema.ensure_plan` 只管字段形状，管不到「这个块是不是真的有」。不在这里
    拦的话，glue 会照着不存在的块生成 `从 blocks.数据.不存在 导入 x`，等到
    执行期才炸——报错位置离病根太远。
    """
    from jikuai.pkg.blocks import ALLOWED_DOMAINS, blocks_root
    for i, 步 in enumerate(方案.get(_F步骤) or [], 1):
        领域, 块 = 步.get(_F领域), 步.get(_F块)
        if 领域 not in ALLOWED_DOMAINS:
            raise _请求错误('步骤 %d 的领域「%s」不在白名单（允许：%s）'
                          % (i, 领域, '/'.join(sorted(ALLOWED_DOMAINS))))
        if not os.path.isdir(os.path.join(blocks_root(), 领域, 块)):
            raise _请求错误('步骤 %d 的块「%s」不存在：blocks/%s 下没有这个目录'
                          % (i, 块, 领域))
    return 方案


def 选(body: dict) -> dict:
    """`POST /api/选`：需求文本 → 候选列表。

    `神经: true` 才起 sidecar 子进程拉查询向量；拿不到就降级到启发式，并在
    响应里附 `降级说明`。降级不是失败，所以仍然是 200。
    """
    需求 = body.get(_F需求)
    if not isinstance(需求, str) or not 需求.strip():
        raise _请求错误('「需求」必须是非空字符串')
    需求 = 需求.strip()

    top = body.get('top', 5)
    if isinstance(top, bool) or not isinstance(top, int):
        raise _请求错误('「top」必须是整数')
    if not 1 <= top <= MAX_TOP:
        raise _请求错误('「top」必须在 1..%d 之间' % MAX_TOP)

    用神经 = body.get('神经', False)
    if not isinstance(用神经, bool):
        raise _请求错误('「神经」必须是布尔值')

    from jikuai.ai import retrieval
    查询向量 = None
    降级说明 = None
    if 用神经:
        from jikuai.ai import embed_client
        vec, why = embed_client.fetch_query_vector(
            需求, expected_dim=embed_client.index_dim())
        if vec is None:
            # 文案前缀走常量，与 CLI（blocks_cli）/ REPL（repl_session）同源。
            from jikuai.ai.embed_client import DEGRADE_PREFIX
            降级说明 = DEGRADE_PREFIX + why
        else:
            查询向量 = vec

    try:
        hits = retrieval.retrieve(需求, top=top, query_vector=查询向量)
    except retrieval.RetrievalError as e:
        # 维度不符只可能出在「索引与模型不同步」上。上面已经比对过 dim，
        # 走到这里说明索引在两次读之间被换掉了——算调用方环境问题，400。
        raise _请求错误(str(e))

    return schema.make_select_envelope(
        需求, [schema.candidate_from_hit(h) for h in hits], 降级说明=降级说明)


def 组(body: dict) -> dict:
    """`POST /api/组`：方案 → 极快源码。

    形状是单字段 `{源码: str}`（协议文档里的 `组响应`）。W20 复盘裁决：
    单字段响应不值得为它单立一份 schema 常量，键名从 `RUN_ENVELOPE_REQUIRED`
    借用即可——CLI/Web 两边都靠这一个字面量取值，不重复。
    """
    方案 = _取方案(body)
    glue = _glue()
    try:
        源码 = glue.synthesize(方案, 自动链式=True)
    except ValueError as e:
        raise _请求错误('粘合失败：%s' % e)
    return {_F源码: 源码}


def 能力() -> dict:
    """`GET /api/能力`：给前端点亮「神经」开关的能力探测（v0.15.0 W20）。

    契约（W19 前端按这个写，字段名别改）：

        {"神经可用": bool, "索引版本": str, "块数": int}

    - `神经可用` 判据 = sidecar 可解析出命令 **且** 向量索引能加载。两者都
      复用运行时现成的判据（`embed_client.resolve_command` /
      `retrieval.load_vector_index`），不自己 `os.path.isfile` 拍脑袋——
      自己拍会和真实检索路径的判据漂开：`resolve_command` 还认
      `JIKUAI_AI_EMBED_CMD` 覆盖，`load_vector_index` 还校验魔数与格式版本。
    - `索引版本` / `块数` 取自 `索引.json`（与 `GET /api/blocks` 同源）；
      索引读不到时给 `''` / `0`，而不是报错——能力探测本身不该失败。
    """
    from jikuai.ai import embed_client, retrieval
    神经可用 = (embed_client.resolve_command() is not None
             and retrieval.load_vector_index() is not None)
    版本, 块数 = '', 0
    try:
        with open(_index_path(), 'r', encoding='utf-8') as f:
            索引 = json.load(f)
        版本 = str(索引.get('版本') or '')
        块数 = len(索引.get('块') or [])
    except (OSError, ValueError):
        pass
    return {'神经可用': bool(神经可用), '索引版本': 版本, '块数': 块数}


def 跑(body: dict) -> dict:
    """`POST /api/跑`：方案 → 组 → 执行 → `schema.make_run_envelope`。

    业务失败（源码含占位符、解释器抛异常）走 `错误` 字段而不是 HTTP 5xx——
    「你的程序跑挂了」和「服务坏了」是两件事，混在一个状态码里前端没法处理。
    """
    方案 = _取方案(body)
    glue = _glue()
    try:
        源码 = glue.synthesize(方案, 自动链式=True)
    except ValueError as e:
        raise _请求错误('粘合失败：%s' % e)
    if _占位记号 in 源码:
        执行结果 = schema.make_result(
            错误='方案有步骤的参数未指定（源码里出现「%s」）；'
                '在「步骤」里补 `参数`，或换成类型图能自动链上的组合' % _占位记号)
        return schema.make_run_envelope(源码, 执行结果, 需求=方案.get(_F需求))
    return schema.make_run_envelope(源码, 执行源码(源码), 需求=方案.get(_F需求))


def 执行源码(源码: str) -> dict:
    """把源码落**临时** `.jk` 再交给解释器跑，返回 `schema` 执行结果。

    落临时文件而不直接 `run_source(源码)` 的理由同 `blocks_cli._执行源码`：
    模块解析要靠 `current_file` 定位搜索路径，给个真实路径最省事。

    与 CLI 版的差别：这里**同时**拦 stdout 与 stderr。诊断（`_report_diagnostics`）
    是打到 stderr 的，CLI 让它直接落终端就行，Web 层要是不拦，这些中文诊断会
    跑到服务进程的控制台里、前端一个字都看不到。
    """
    # 与 blocks_cli._执行源码 同源实现；不抽共享是因为 src/ 零第三方依赖约束禁止运行时包依赖 tools/。
    from jikuai.main import run_source

    fd, path = tempfile.mkstemp(prefix='jk_web跑_', suffix='.jk')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(源码)
        缓出, 缓错 = io.StringIO(), io.StringIO()
        错误 = None
        诊断 = None
        结果 = None
        with _执行锁:
            起 = time.perf_counter()
            try:
                with contextlib.redirect_stdout(缓出), \
                        contextlib.redirect_stderr(缓错):
                    结果 = run_source(源码, file=path)
            except Exception as e:                          # noqa: BLE001
                # 只回异常类名 + 消息，不回 traceback：栈里带着服务端的绝对
                # 路径与内部模块结构，对调用方没用，对攻击者有用。
                错误 = '%s：%s' % (type(e).__name__, e)
                # 带位置信息（JiKuaiError.info）时顺手填 `诊断`，前端才能把
                # 出错的行/列高亮出来（W19）；拿不到就保持 None，不伪造。
                # 提取逻辑下沉到 schema（CLI 与 Web 共用一份，不再各写一遍）。
                诊断 = schema.diagnostics_from_error(e)
            耗时 = (time.perf_counter() - 起) * 1000.0
        return schema.make_result(
            stdout=缓出.getvalue(), stderr=缓错.getvalue(),
            返回值='' if 错误 else repr(结果),
            耗时毫秒=耗时, 错误=错误, 诊断=诊断,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def 块索引() -> dict:
    """`GET /api/blocks`：`索引.json` 原文。"""
    path = _index_path()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except OSError as e:
        raise _请求错误('读不到块索引 %s：%s' % (path, e), 状态=503)
    except ValueError as e:
        raise _请求错误('块索引不是合法 JSON：%s' % e, 状态=503)


#: POST 路由表。键是 URL path，值是 `(dict) -> dict`。
_POST路由 = {
    '/api/选': 选,
    '/api/组': 组,
    '/api/跑': 跑,
}


# ---- HTTP 层 -----------------------------------------------------------

class JiKuaiHandler(BaseHTTPRequestHandler):
    """极快 Web UI 的请求处理器。

    只实现 GET / POST。`BaseHTTPRequestHandler` 对未实现的方法会自动回 501，
    不用自己写。
    """

    server_version = 'JiKuaiWeb/0.15.0'
    protocol_version = 'HTTP/1.1'      # 要 Content-Length 精确才敢开长连接

    # -- 输出辅助 --

    def _发送(self, 状态: int, 数据: bytes, 类型: str,
             额外头=None, 关闭: bool = False) -> None:
        self.send_response(状态)
        self.send_header('Content-Type', 类型)
        self.send_header('Content-Length', str(len(数据)))
        # 单页不需要被嵌进别人的 iframe，也不需要浏览器猜 MIME。
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        if 关闭:
            self.send_header('Connection', 'close')
        for 名, 值 in (额外头 or {}).items():
            self.send_header(名, 值)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(数据)
        if 关闭:
            self.close_connection = True

    def _发JSON(self, 状态: int, 对象, 关闭: bool = False) -> None:
        数据 = json.dumps(对象, ensure_ascii=False).encode('utf-8')
        self._发送(状态, 数据, 'application/json; charset=utf-8', 关闭=关闭)

    def _发错误(self, 状态: int, 原因: str, 关闭: bool = False) -> None:
        """错误也走 JSON —— 前端只需要认一种响应形状。"""
        self._发JSON(状态, {'错误': 原因}, 关闭=关闭)

    # -- 读请求体 --

    def _排空(self, 长度: int) -> None:
        """把超限/不要的请求体分块读掉丢弃。

        为什么不能省：客户端还在往连接里灌 1 MiB body 的时候，我们直接回
        413 + 关连接，TCP 侧会给客户端一个 RST —— 客户端拿到的是
        `ConnectionAbortedError` 而不是我们那句中文 413，错误消息白写。
        分块丢弃的内存占用是常数（64 KiB），`MAX_BODY` 要保护的正是这个。
        超过 `MAX_DRAIN` 就不再陪读，那已经属于滥用。
        """
        余 = min(长度, MAX_DRAIN)
        while 余 > 0:
            try:
                块 = self.rfile.read(min(65536, 余))
            except OSError:
                return
            if not 块:
                return
            余 -= len(块)

    def _读body(self) -> dict:
        """读并解析 JSON 请求体。任何问题抛 `_请求错误`。"""
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
            self._排空(长度)
            raise _请求错误('请求体 %d 字节，超过上限 %d 字节'
                          % (长度, MAX_BODY), 状态=413)
        数据 = self.rfile.read(长度) if 长度 else b''
        if len(数据) != 长度:
            raise _请求错误('请求体不完整：声明 %d 字节，实收 %d 字节'
                          % (长度, len(数据)))
        try:
            文本 = 数据.decode('utf-8')
        except UnicodeDecodeError as e:
            raise _请求错误('请求体不是合法 UTF-8：%s' % e)
        try:
            body = json.loads(文本) if 文本.strip() else None
        except ValueError as e:
            raise _请求错误('请求体不是合法 JSON：%s' % e)
        if not isinstance(body, dict):
            raise _请求错误('请求体必须是 JSON 对象（得到 %s）'
                          % ('空' if body is None else type(body).__name__))
        return body

    # -- 静态文件 --

    def _解析静态路径(self, 请求路径: str) -> str:
        """把 URL path 映射到 `static/` 下的真实文件，越界抛 `_请求错误`。

        三道闸：
          1. 拒绝盘符前缀与绝对路径（`os.path.join` 遇到它们会整段丢弃前缀）；
          2. `realpath` 归一化（吃掉 `..`，并且**跟随符号链接**）；
          3. `commonpath` 确认归一化后的真实路径仍在 `static/` 真实路径之内。

        第 2 步用 `realpath` 而不是 `normpath` 是关键：`normpath` 只做字符串
        运算，`static/link → /etc` 这种符号链接逃逸它拦不住。
        """
        路径 = urllib.parse.unquote(请求路径)
        if 路径 in ('', '/'):
            路径 = '/index.html'
        相对 = 路径.lstrip('/')
        if not 相对:
            raise _请求错误('路径不合法', 状态=403)
        if os.path.isabs(相对) or _盘符.match(相对):
            raise _请求错误('拒绝绝对路径', 状态=403)
        根 = os.path.realpath(static_root())
        目标 = os.path.realpath(os.path.join(根, 相对))
        try:
            if os.path.commonpath([根, 目标]) != 根:
                raise _请求错误('拒绝越界访问', 状态=403)
        except ValueError:
            # 跨盘（`C:` vs `D:`）时 commonpath 直接抛 ValueError —— 那必然越界。
            raise _请求错误('拒绝越界访问', 状态=403)
        if not os.path.isfile(目标):
            raise _请求错误('找不到 %s' % 请求路径, 状态=404)
        return 目标

    def _发静态(self, 请求路径: str) -> None:
        目标 = self._解析静态路径(请求路径)
        try:
            with open(目标, 'rb') as f:
                数据 = f.read()
        except OSError as e:
            raise _请求错误('读不到静态文件：%s' % e, 状态=500)
        类型 = _MIME.get(os.path.splitext(目标)[1].lower(),
                       'application/octet-stream')
        self._发送(200, 数据, 类型)

    # -- 路由 --

    def do_GET(self) -> None:
        self._分发(self._GET)

    def do_HEAD(self) -> None:
        self._分发(self._GET)

    def do_POST(self) -> None:
        self._分发(self._POST)

    def _GET(self) -> None:
        原始路径 = urllib.parse.urlsplit(self.path).path
        路径 = urllib.parse.unquote(原始路径)
        if 路径 == '/api/blocks':
            self._发JSON(200, 块索引())
        elif 路径 == '/api/能力':
            self._发JSON(200, 能力())
        elif 路径.startswith('/api/'):
            raise _请求错误('未知端点 %s（GET 只有 /api/blocks、/api/能力）'
                          % 路径, 状态=404)
        else:
            # 静态走**原始**（未 unquote）路径：`_解析静态路径` 内部只 unquote 一次，
            # 提前 unquote 会让 `%252e` 这类二次编码逃逸绕过校验。
            self._发静态(原始路径)

    def _POST(self) -> None:
        路径 = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        处理 = _POST路由.get(路径)
        if 处理 is None:
            raise _请求错误('未知端点 %s（POST 只有 %s）'
                          % (路径, '、'.join(sorted(_POST路由))), 状态=404)
        self._发JSON(200, 处理(self._读body()))

    def _分发(self, 处理) -> None:
        """统一的异常边界：调用方的错 → 4xx，我们的错 → 500。

        刻意**不**吞异常后回 200 空壳：那样前端拿到一份看着正常的空响应，
        排查成本比一个诚实的 500 高得多。
        """
        try:
            处理()
        except _请求错误 as e:
            # 413 之后客户端可能还在往连接里灌 body，长连接会读串；直接断。
            self._发错误(e.状态, e.原因, 关闭=(e.状态 == 413))
        except Exception as e:                                 # noqa: BLE001
            _LOG.exception('处理 %s %s 时内部错误', self.command, self.path)
            self._发错误(500, '服务器内部错误：%s：%s' % (type(e).__name__, e))

    # -- 日志 --

    def log_message(self, fmt: str, *args) -> None:
        """访问日志走 `logging`，别污染 stdout。

        默认实现直接 `sys.stderr.write`，在被当库嵌入（比如测试里）时会把
        噪声灌满 pytest 的捕获缓冲。交给 logging 后，调用方可以自己决定级别。
        """
        _LOG.info('%s - %s', self.address_string(), fmt % args)

    def log_error(self, fmt: str, *args) -> None:
        _LOG.warning('%s - %s', self.address_string(), fmt % args)


def build_server(host: str = DEFAULT_HOST,
                 port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """建一个未启动的服务实例。

    `port=0` 让内核分配空闲端口（测试用；写死 5000 会因为端口被占而 flaky）。
    实际端口在 `server.server_address[1]`。
    """
    return ThreadingHTTPServer((host, port), JiKuaiHandler)


def main(argv=None) -> int:
    """启动 Web UI 本地开发服务。返回退出码（0 正常 / 非 0 异常）。"""
    p = argparse.ArgumentParser(
        description='极快 Web UI 本地开发服务（无鉴权，仅限本机）')
    p.add_argument('--地址', '--host', dest='host', default=DEFAULT_HOST,
                   help='监听地址，默认 %s（不要改成 0.0.0.0）' % DEFAULT_HOST)
    p.add_argument('--端口', '--port', dest='port', type=int,
                   default=DEFAULT_PORT, help='监听端口，默认 %d' % DEFAULT_PORT)
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    srv = build_server(args.host, args.port)
    实际地址, 实际端口 = srv.server_address[0], srv.server_address[1]
    print('极快 Web UI 已启动：http://%s:%d/' % (实际地址, 实际端口), file=sys.stderr)
    print(SAFETY_NOTICE, file=sys.stderr)
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        print('⚠ 你把监听地址设成了 %s —— 这台机器所在网络里的任何人都能'
              '在你的机器上执行任意代码。确定要这样？' % args.host, file=sys.stderr)

    # 预热：在 serve_forever 之前把惰性单例初始化完毕，消除首个请求的
    # 竞态窗口（多线程 handler 同时触发 _glue / _cached_retriever / _LEVELS）。
    # 预热失败不阻断启动——首请求自己再惰性初始化一次只是慢一点，比服务起不来好。
    # `retrieval` 的 import 也放在 try 内：它同样可能失败（缺依赖 / 索引损坏），
    # 而 import 炸在 try 外就变成裸崩，起不了服务。
    try:
        from jikuai.ai import retrieval
        _glue()
        retrieval.retrieve('预热', top=1)
        schema.level_table()
    except Exception as e:                                  # noqa: BLE001
        _LOG.warning('预热失败，将退化为按需初始化：%s', e)

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。', file=sys.stderr)
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
