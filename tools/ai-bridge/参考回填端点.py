# -*- coding: utf-8 -*-
"""参考回填端点 —— `jk 块 问 --模型 <端点>` 的对拍实现（v0.29.0 W181）。

## 它是什么，更要紧的是它不是什么

`jk 块 问` 要一个 HTTP 端点：收上下文包，回一份**裸回填响应 JSON**
（`{需求, 方案, 模型}`，多一个键就被 `schema.validate_filled_envelope` 拒）。
在 v0.28.0 之前，这条链路**从未跑过一个独立进程的端点**——`tests/
test_v0_27_0_w158_w159_规划CLI.py` 里那个 server 与被测 CLI 同进程，
socket、编码、鉴权头都在同一个解释器里打转。本文件补的就是那一段。

**它不是模型，也不假装是。** 它按机械规则出回填，`模型` 字段如实写
`参考端点·<模式>`，绝不写成某个 LLM 的名字。所以：

- 它能证明的是 **契约与传输**：独立进程 + 真 socket + 真 Bearer 头 + 真
  `validate_filled` + 真 `组`/`跑` 这一条链路通不通。
- 它**不能**证明「某个真实模型能回填对」。那是另一件事，仍挂账（`docs/
  BACKLOG.md` §12.4）。**别拿本文件跑绿了就去把那条挂账划掉。**

## 三种模式（`--模式`）

- ``标量``：从包的 `候选` 里挑一个**入参全是标量**的块，造一步方案。挑不到就
  **回 422 并说明**——刻意不硬凑一个能过形状校验但语义乱套的方案（AGENTS.md
  第四节第 4 条：没命中就别硬凑）。
- ``录像``：按 `需求` **逐字**匹配 `规划录像/清单.json`，转投那份人工方案。
  录像用到的块必须**全在本次包的 `候选` 里**（规则 2），装不下就回 422 并
  列出缺哪些块——那正是「白名单可承载率」在真跑里的样子，不许悄悄绕过。
  转投时 `模型` 改写成 `参考端点·录像转投（原：…）`：录像本体是人工产物，
  经本端点走一趟不会让它变成模型产物，这个来源不能被洗掉。
- ``幻觉``：故意回一个不存在的块名。用来在**真进程**上证明
  `validate_filled` 规则 2 拦得住，而不是只在同进程 mock 上证明过。

## 安全姿态（与 `tools/web/demo_server.py` 不是同一档，别混）

- **只出 JSON，不执行任何东西**：本文件没有 `run_source`、没有 `subprocess`、
  不 import `glue`。它的攻击面是「回一段 JSON」，所以**不学 demo_server 的
  「未设 Token 就拒绝启动」**——那条闸是为「会执行代码的端点」立的，照抄到
  一个只读端点上属于形式主义。
- **监听地址硬编码 `127.0.0.1`，不给 `--主机`**：想暴露到网络就得改代码，
  这比给个默认值安全的开关可靠。
- **Token 从 `JIKUAI_PLANNER_TOKEN` 取**（与客户端同一个变量：两边都在本机
  同一个 shell 里跑，这是对拍工具的便利，**不是生产做法**——生产里端点侧的
  凭据必须独立配置）。设了就校验 Bearer（`hmac.compare_digest`），不设就不
  校验并往 stderr 印一行提醒。**非 ASCII Token 启动即拒**：HTTP 头值只收
  latin-1，带中文的 Token 会让**客户端**在 `putheader` 抛
  `UnicodeEncodeError`，报错落在调用方、离病根很远（v0.28.0 W164 的教训）。

## 用法

    python tools/ai-bridge/参考回填端点.py --模式 录像            # 随机端口
    python tools/ai-bridge/参考回填端点.py --模式 标量 --端口 8931
    python tools/ai-bridge/参考回填端点.py --模式 幻觉 --次数 1    # 服务一次就退

启动后往 stdout 印一行 ``端点=http://127.0.0.1:<端口>/fill``（**路径刻意是
ASCII**：`问` 的请求行必须是 ASCII，中文路径会在 `http.client` 就炸）。
零第三方依赖。
"""

import argparse
import hmac
import json
import os
import sys

_HERE = os.path.abspath(os.path.dirname(__file__))
# **这段必须排在 `http.server`/`socketserver` 导入之前**：`tools/ai-bridge/select.py`
# 会遮蔽标准库 `select`，而运行脚本时 Python 把脚本目录塞到 `sys.path[0]`。
# `http.server` 链式 import `socketserver → selectors → select`，一旦在重排前
# 导入，`select` 就以**本地那个**进了 `sys.modules` 缓存，之后再重排也没用——
# `serve_forever()` 一进 `selectors` 就 `AttributeError: module 'select' has no
# attribute 'select'`，进程当场退，客户端只看到 `WinError 10061 连接被拒绝`。
# 做法同 `bench_planner.py:126`，但**位置比它更靠前**（那支不 import socketserver）。
sys.path[:] = ([p for p in sys.path if os.path.abspath(p) != _HERE]
               + [p for p in sys.path if os.path.abspath(p) == _HERE])
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402
from socketserver import ThreadingMixIn  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

from jikuai.service import schema  # noqa: E402
from jikuai.service.schema import (  # noqa: E402
    STEP_REQUIRED, STEP_OPTIONAL, PLAN_REQUIRED, PLAN_OPTIONAL,
    SLOT_REQUIRED, CONTEXT_CANDIDATE_REQUIRED,
    CONTEXT_ENVELOPE_REQUIRED, FILLED_ENVELOPE_REQUIRED,
)

#: 协议字段名一律从 schema 常量整元组解包（同 planner.py:52 的做法）：
#: 协议真加了字段这里会当场 ValueError，而不是静默少读一个键。
_F块, _F领域, _F导出名 = STEP_REQUIRED
_F参数, _F说明, _F命名空间 = STEP_OPTIONAL
_F步骤, = PLAN_REQUIRED
_F需求, _F共享, _F打印 = PLAN_OPTIONAL
_F槽名, _F槽类型 = SLOT_REQUIRED
(_F名称, _F候选领域, _F层级, _F候选导出名, _F描述, _F分数, _F路径,
 _F输入槽, _F输出类型) = CONTEXT_CANDIDATE_REQUIRED
_C需求, _C语义命中, _C候选, _C回填契约, _C拒答建议 = CONTEXT_ENVELOPE_REQUIRED
_R需求, _R方案, _R模型 = FILLED_ENVELOPE_REQUIRED

#: `共享[]` 的键。schema 把这个键集写在 `validate_plan` 里没抽成模块常量，
#: 故这里与 `planner.py:67` 同法直读——三处一致，改协议时一起改。
_S共享名, _S共享值, _S共享类型 = '名', '值', '类型'

#: 标量类型词。`_造标量回填` 只认这一种，因为它要能凭空给出实参值。
_标量 = '数'

#: 录像清单（`规划录像/清单.json`）里本文件要读的键。**不是协议字段**，
#: 是清单自己的落盘格式，所以不从 schema 取。
_M录像, _M需求, _M文件, _M块, _M模型 = '录像', '需求', '文件', '块', '模型'

#: 鉴权 Token 的环境变量。与 `blocks_cli._ENV问密钥` 同名，见模块文档。
ENV令牌 = 'JIKUAI_PLANNER_TOKEN'

#: 三种模式。`标量` 是缺省——它不依赖任何外部文件。
模式表 = ('标量', '录像', '幻觉')

#: 端点自认造不出回填时用的状态码。刻意不是 200+空对象：那会让 CLI 报
#: 「校验拒绝」，把「端点没这个能力」误读成「端点填错了」。
状态_无能力 = 422

录像目录 = os.path.join(_HERE, '规划录像')


def _模型标识(模式: str, 原模型: str = '') -> str:
    """回填来源标识。**永远带「参考端点」前缀**，不许伪装成某个模型名。"""
    if 原模型:
        return '参考端点·录像转投（原：%s）' % 原模型
    return '参考端点·%s' % 模式


def 造标量回填(包: Dict[str, Any], 模型: Optional[str] = None):
    """挑一个入参**全是标量**的候选，造一份能过五条硬规则的一步方案。

    返回 `(回填, None)` 或 `(None, 理由)`。刻意从包里**动态挑**而不是写死块名：
    检索排序会随块库变，写死等于给自己埋一颗定时炸弹（口径同
    `tests/test_v0_27_0_w158_w159_规划CLI.py` 里的 `_造回填`）。
    """
    for c in (包.get(_C候选) or []):
        槽 = c.get(_F输入槽) or []
        if 槽 and all(s.get(_F槽类型) == _标量 for s in 槽):
            共享 = [{_S共享名: '赵参%d' % (i + 1), _S共享值: '1',
                     _S共享类型: _标量} for i in range(len(槽))]
            方案 = {
                _F需求: 包.get(_C需求),
                _F共享: 共享,
                _F步骤: [{_F块: c.get(_F名称), _F领域: c.get(_F候选领域),
                          _F导出名: c.get(_F候选导出名),
                          _F参数: [x[_S共享名] for x in 共享]}],
            }
            return schema.make_filled_envelope(
                包.get(_C需求), 方案, 模型 or _模型标识('标量')), None
    return None, ('本包 %d 条候选里没有「入参全是标量」的块，参考端点造不出实参。'
                  '这不是校验失败，是端点自认无能力——换 `--模式 录像`，或者这道题'
                  '本来就该由人/模型来填' % len(包.get(_C候选) or []))


def 读录像清单(目录: str = 录像目录) -> List[Dict[str, Any]]:
    """读 `规划录像/清单.json` 的 `录像` 数组。读不到就返回空表。"""
    路径 = os.path.join(目录, '清单.json')
    try:
        with open(路径, encoding='utf-8') as f:
            return json.load(f).get(_M录像) or []
    except (OSError, ValueError):
        return []


def 造录像回填(包: Dict[str, Any], 目录: str = 录像目录):
    """按 `需求` 逐字匹配录像并转投。返回 `(回填, None)` 或 `(None, 理由)`。

    两道自查，缺一个都会让「真跑绿了」变成假消息：

    1. **逐字匹配**问句。不做模糊匹配——差一个字就该说没有，而不是投一份
       邻近问句的方案上去。
    2. 录像方案用到的块必须**全在本次包的 `候选` 里**。装不下就回 422 并列出
       缺的块名：那是 `白名单可承载率` 在真跑里的具体样子（v0.28.0 W174 实测
       K=8 只有 60.0%），把它悄悄绕过等于给自己发假绿灯。
    """
    需求 = 包.get(_C需求)
    候选名 = {c.get(_F名称) for c in (包.get(_C候选) or [])}
    命中 = [条 for 条 in 读录像清单(目录) if 条.get(_M需求) == 需求]
    if not 命中:
        return None, ('录像库里没有与本问句**逐字相同**的录像（%d 份里一份都没匹配）。'
                      '参考端点刻意不做模糊匹配：投一份邻近问句的方案上去，跑出来的'
                      '数会像对的' % len(读录像清单(目录)))
    条 = 命中[0]
    缺 = sorted(set(条.get(_M块) or []) - 候选名)
    if 缺:
        return None, ('录像 %s 用到的块有 %d 个不在本次上下文包的候选里：%s。'
                      '这是白名单承载不下，不是端点填错——把 `--top` 调大再来，'
                      '或者认下这条在当前 K 下装不进去'
                      % (条.get(_M文件), len(缺), '、'.join(缺)))
    try:
        with open(os.path.join(目录, 条[_M文件]), encoding='utf-8') as f:
            录像 = json.load(f)
    except (OSError, ValueError, KeyError) as e:
        return None, '录像文件读不出来：%s' % e
    return schema.make_filled_envelope(
        录像.get(_R需求), 录像.get(_R方案),
        _模型标识('录像', 录像.get(_R模型) or '未标注')), None


def 造幻觉回填(包: Dict[str, Any]):
    """故意回一个不存在的块名，用来在真进程上证明规则 2 拦得住。"""
    方案 = {_F步骤: [{_F块: '并不存在的块', _F领域: '制造',
                      _F导出名: '瞎编', _F参数: []}]}
    return schema.make_filled_envelope(
        包.get(_C需求), 方案, _模型标识('幻觉')), None


def 构建回填(包: Dict[str, Any], 模式: str, 录像目录名: str = 录像目录
            ) -> Tuple[int, str]:
    """按模式出 `(HTTP 状态码, 响应体文本)`。

    造不出回填时给 `状态_无能力`（422）+ 一句中文理由，**不给 200 + 空对象**。
    """
    if 模式 == '录像':
        回填, 理由 = 造录像回填(包, 录像目录名)
    elif 模式 == '幻觉':
        回填, 理由 = 造幻觉回填(包)
    else:
        回填, 理由 = 造标量回填(包)
    if 回填 is None:
        return 状态_无能力, json.dumps({'理由': 理由}, ensure_ascii=False)
    return 200, json.dumps(回填, ensure_ascii=False)


class 处理器(BaseHTTPRequestHandler):
    """只认 POST 的回填端点。`模式`/`令牌`/`录像目录` 由 `起服务` 注入。"""

    模式 = '标量'
    令牌 = ''
    录像目录 = 录像目录

    def _回(self, 码: int, 体: str):
        raw = 体.encode('utf-8')
        self.send_response(码)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _过鉴权(self) -> bool:
        """令牌为空 = 本端点不校验。校验时走 `compare_digest`，不用 `==`。"""
        if not type(self).令牌:
            return True
        头 = self.headers.get('Authorization') or ''
        前缀 = 'Bearer '
        给的 = 头[len(前缀):] if 头.startswith(前缀) else ''
        return bool(给的) and hmac.compare_digest(给的, type(self).令牌)

    def do_GET(self):
        self._回(405, json.dumps({'理由': '本端点只收 POST'}, ensure_ascii=False))

    def do_POST(self):
        if not self._过鉴权():
            self._回(401, json.dumps({'理由': 'Bearer 令牌不对或没给'},
                                     ensure_ascii=False))
            return
        n = int(self.headers.get('Content-Length') or 0)
        try:
            包 = json.loads(self.rfile.read(n).decode('utf-8'))
        except (ValueError, UnicodeDecodeError) as e:
            self._回(400, json.dumps({'理由': '请求体不是合法 JSON：%s' % e},
                                     ensure_ascii=False))
            return
        if not isinstance(包, dict):
            self._回(400, json.dumps({'理由': '请求体不是上下文包对象'},
                                     ensure_ascii=False))
            return
        码, 体 = 构建回填(包, type(self).模式, type(self).录像目录)
        self._回(码, 体)

    def log_message(self, *args):
        """吞掉默认访问日志：它会把真跑脚本的 stderr 搅乱。"""


class _静默服务(ThreadingMixIn, HTTPServer):
    """并发 + 不往 stderr 吐连接异常栈（口径同 W158 测试里的 `_静默服务`）。"""

    daemon_threads = True

    def handle_error(self, request, client_address):
        """连接层异常不打栈：客户端的连接生死不是本端点的职责。"""


def 取令牌(环境=None) -> str:
    """从环境变量取 Token。**非 ASCII 直接 ValueError**（见模块文档）。"""
    值 = (环境 if 环境 is not None else os.environ).get(ENV令牌, '').strip()
    if 值 and not 值.isascii():
        raise ValueError(
            '%s 含非 ASCII 字符。HTTP 头值只收 latin-1，这种 Token 会让**客户端**'
            '在发请求时抛 UnicodeEncodeError——报错落在调用方、离病根很远，'
            '所以这里启动就拒' % ENV令牌)
    return 值


def 起服务(端口: int = 0, 模式: str = '标量', 令牌: str = '',
          录像目录名: str = 录像目录):
    """起服务并返回 `(server, url)`。**主机硬编码 `127.0.0.1`**。

    调用方负责 `serve_forever` 与 `shutdown`/`server_close`。
    """
    if 模式 not in 模式表:
        raise ValueError('模式只能是 %s，得到 %r' % ('/'.join(模式表), 模式))
    类 = type('_端点', (处理器,), {'模式': 模式, '令牌': 令牌,
                                   '录像目录': 录像目录名})
    srv = _静默服务(('127.0.0.1', int(端口)), 类)
    return srv, 'http://127.0.0.1:%d/fill' % srv.server_address[1]


def 主(argv=None) -> int:
    p = argparse.ArgumentParser(
        description='参考回填端点（对拍用，不是模型）')
    p.add_argument('--模式', default='标量', choices=list(模式表))
    p.add_argument('--端口', type=int, default=0, help='0 = 随机端口')
    p.add_argument('--次数', type=int, default=0,
                   help='服务 N 个请求后退出；0 = 不退')
    a = p.parse_args(argv)
    try:
        令牌 = 取令牌()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    srv, url = 起服务(a.端口, a.模式, 令牌)
    if not 令牌:
        print('提醒：未设 %s，本端点不校验鉴权。它只回 JSON、不执行任何代码，'
              '且只监听 127.0.0.1' % ENV令牌, file=sys.stderr)
    print('端点=%s' % url, flush=True)
    print('模式=%s' % a.模式, flush=True)
    try:
        if a.次数 > 0:
            for _ in range(a.次数):
                srv.handle_request()
        else:
            srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(主())
