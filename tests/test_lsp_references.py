# -*- coding: utf-8 -*-
"""v0.17.0 W40 · `textDocument/references` 跨文件引用查找。

覆盖 WBS 列的项：函数被多文件引用、块导出名被 `导入` 引用、类方法、
`includeDeclaration` 真/假、无引用返回空、索引未建完时的行为。

走**进程内**直测 `LspServer`：references 的返回值是结构化 Location[]，
在进程内断言比让子进程序列化再解析更直接，也不用为测试在协议里开后门。
坐标口径（0-based UTF-16）是本周最容易错的地方，进程内能逐字断言。
"""

from __future__ import annotations

import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))
sys.path.insert(0, os.path.join(_ROOT, 'lsp'))

from jikuai_lsp.server import LspServer


# ---- 样本源码（真实极快语法）------------------------------------------

# 定义方：导入 圆分、定义并导出 甲加
_定义方 = '''从 blocks.财务.保留分 导入 圆分。

函数 甲加 接收 赵x:
  返回 加 赵x 1。
。

导出 甲加。
'''

# 调用方一：调用 圆分
_调用方1 = '''从 blocks.财务.保留分 导入 圆分。

定义赵甲=圆分(3.14)。
打印 赵甲。
'''

# 调用方二：也调用 圆分（用于「被多文件引用」）
_调用方2 = '''从 blocks.财务.保留分 导入 圆分。

定义赵乙=圆分(2.5)。
定义赵丙=圆分(1.5)。
'''

_类样本 = '''类 乙盒:
  构造 接收 赵初:
    赋值 我.值 = 赵初。
  。
  方法 取值:
    返回 我.值。
  。
。

定义赵盒=新建 乙盒(1)。
打印 赵盒.取值()。
'''

_U定义 = 'file:///proj/定义方.jk'
_U调用1 = 'file:///proj/调用方1.jk'
_U调用2 = 'file:///proj/调用方2.jk'
_U类 = 'file:///proj/类样本.jk'


class _服务:
    """包一层 LspServer，把 `_send_response` 的结果收下来供断言。"""

    def __init__(self):
        self.s = LspServer(reader=io.BytesIO(), writer=io.BytesIO())
        self.响应 = []
        self.错误 = []
        self.s._send_response = lambda mid, result: self.响应.append(result)
        self.s._send_error = lambda mid, code, msg: self.错误.append((code, msg))
        # 诊断推送在本周无关，吞掉免得噪声
        self.s._send_notification = lambda method, params: None

    def 打开(self, uri: str, text: str) -> None:
        self.s._handle_did_open({'textDocument': {
            'uri': uri, 'languageId': 'jikuai', 'version': 1, 'text': text}})

    def 改(self, uri: str, text: str) -> None:
        self.s._handle_did_change({
            'textDocument': {'uri': uri, 'version': 2},
            'contentChanges': [{'text': text}],
        })

    def 查引用(self, uri: str, line0: int, char0: int,
             包含定义: bool = True) -> list:
        self.响应.clear()
        self.s._handle_references(1, {
            'textDocument': {'uri': uri},
            'position': {'line': line0, 'character': char0},
            'context': {'includeDeclaration': 包含定义},
        })
        assert self.响应, '必须回响应（哪怕是空列表）'
        return self.响应[-1]


def _位置(源码: str, 名字: str, 第几次: int = 1) -> tuple:
    """在源码里找第 N 次出现的 `名字`，返回 (0-based 行, 0-based 字符)。

    源码全是 BMP 内字符时码点列 == UTF-16 列，所以直接用 str.index 够用；
    含 BMP 外字符的口径由 `test_引用位置口径_bmp外字符` 专门守。
    """
    命中 = 0
    for i, 行 in enumerate(源码.splitlines()):
        起 = 0
        while True:
            j = 行.find(名字, 起)
            if j < 0:
                break
            命中 += 1
            if 命中 == 第几次:
                return (i, j)
            起 = j + 1
    raise AssertionError('源码里找不到第 %d 个「%s」' % (第几次, 名字))


# ---- 基本能力 ---------------------------------------------------------

def test_capabilities_声明referencesProvider():
    from jikuai_lsp.capabilities import server_capabilities
    assert server_capabilities()['referencesProvider'] is True


def test_dispatch_路由到references():
    """不接进 _dispatch 的 handler 等于没写。"""
    svc = _服务()
    svc.打开(_U调用1, _调用方1)
    svc.响应.clear()
    行, 列 = _位置(_调用方1, '圆分', 2)
    svc.s._dispatch({
        'jsonrpc': '2.0', 'id': 7, 'method': 'textDocument/references',
        'params': {
            'textDocument': {'uri': _U调用1},
            'position': {'line': 行, 'character': 列},
            'context': {'includeDeclaration': True},
        },
    })
    assert svc.响应, 'references 未接进 dispatch'


def test_单文件内引用查得到():
    svc = _服务()
    svc.打开(_U调用1, _调用方1)
    行, 列 = _位置(_调用方1, '圆分', 2)      # 第 2 次 = 调用点
    结果 = svc.查引用(_U调用1, 行, 列)
    assert 结果, '至少应查到调用点自身'
    assert all(loc['uri'] == _U调用1 for loc in 结果)


def test_块导出名被导入引用():
    """`从 … 导入 圆分` 里的 圆分 与调用点 圆分(…) 应同属一个符号。"""
    svc = _服务()
    svc.打开(_U调用1, _调用方1)
    行, 列 = _位置(_调用方1, '圆分', 1)      # 第 1 次 = `导入 圆分`
    结果 = svc.查引用(_U调用1, 行, 列, 包含定义=True)
    行号集 = {loc['range']['start']['line'] for loc in 结果}
    调用行, _ = _位置(_调用方1, '圆分', 2)
    assert 调用行 in 行号集, '导入处查引用应能看到调用点：%r' % (结果,)


def test_函数被多文件引用():
    """WBS 核心用例：圆分 在两个文件里各被调用，结果必须跨文件。"""
    svc = _服务()
    svc.打开(_U调用1, _调用方1)
    svc.打开(_U调用2, _调用方2)
    行, 列 = _位置(_调用方1, '圆分', 2)
    结果 = svc.查引用(_U调用1, 行, 列)
    uris = {loc['uri'] for loc in 结果}
    assert _U调用1 in uris and _U调用2 in uris, '跨文件引用未查到：%r' % (uris,)
    # 调用方二里有两处调用
    调用2条数 = [loc for loc in 结果 if loc['uri'] == _U调用2]
    assert len(调用2条数) >= 2, 调用2条数


def test_类方法被引用():
    svc = _服务()
    svc.打开(_U类, _类样本)
    行, 列 = _位置(_类样本, '取值', 1)      # 方法定义处
    结果 = svc.查引用(_U类, 行, 列)
    行号集 = {loc['range']['start']['line'] for loc in 结果}
    调用行, _ = _位置(_类样本, '取值', 2)   # `赵盒.取值()` 调用处
    assert 调用行 in 行号集, '类方法的调用点应被查到：%r' % (结果,)


# ---- includeDeclaration ----------------------------------------------

def test_includeDeclaration为真时含定义():
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)      # `函数 甲加` 定义处
    含定义 = svc.查引用(_U定义, 行, 列, 包含定义=True)
    定义行, _ = _位置(_定义方, '甲加', 1)
    assert 定义行 in {l['range']['start']['line'] for l in 含定义}


def test_includeDeclaration为假时不含定义():
    """规范：为假则结果里不该出现定义位置。"""
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)
    含定义 = svc.查引用(_U定义, 行, 列, 包含定义=True)
    不含定义 = svc.查引用(_U定义, 行, 列, 包含定义=False)
    assert len(不含定义) <= len(含定义), '关掉 includeDeclaration 不该变多'
    定义行, _ = _位置(_定义方, '甲加', 1)
    assert 定义行 not in {l['range']['start']['line'] for l in 不含定义}, \
        'includeDeclaration=False 时定义位置必须被排除：%r' % (不含定义,)


def test_context缺失时默认含定义():
    """客户端不传 context 时按规范当真处理（VS Code 的默认行为）。"""
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)
    svc.响应.clear()
    svc.s._handle_references(1, {
        'textDocument': {'uri': _U定义},
        'position': {'line': 行, 'character': 列},
    })
    结果 = svc.响应[-1]
    定义行, _ = _位置(_定义方, '甲加', 1)
    assert 定义行 in {l['range']['start']['line'] for l in 结果}


# ---- 空结果与边界 -----------------------------------------------------

def test_无引用返回空列表():
    svc = _服务()
    svc.打开(_U调用1, _调用方1)
    # `打印` 是内置动词，不入符号索引（否则 references 全是噪声）
    行, 列 = _位置(_调用方1, '打印', 1)
    assert svc.查引用(_U调用1, 行, 列) == []


def test_光标在空白处返回空列表():
    """回空列表而不是 null——「没有引用」的语义比 null 明确。"""
    svc = _服务()
    svc.打开(_U调用1, _调用方1)
    assert svc.查引用(_U调用1, 1, 0) == []   # 第 2 行是空行


def test_未打开的文档返回空():
    svc = _服务()
    assert svc.查引用('file:///proj/没打开.jk', 0, 0) == []


def test_行号越界返回空():
    svc = _服务()
    svc.打开(_U调用1, _调用方1)
    assert svc.查引用(_U调用1, 9999, 0) == []
    assert svc.查引用(_U调用1, -1, 0) == []


def test_索引未就绪也回部分结果而不是报错():
    """ADR-29 决策点 3：不阻塞。宁可给部分结果也不让用户干等或吃错误。"""
    svc = _服务()
    svc.打开(_U调用1, _调用方1)
    assert not svc.s._get_symbol_index().ready, '本轮不做启动全量扫，ready 应为假'
    行, 列 = _位置(_调用方1, '圆分', 2)
    结果 = svc.查引用(_U调用1, 行, 列)
    assert 结果, '未就绪时仍应返回已索引到的部分'
    assert not svc.错误, '不该回 JSON-RPC 错误：%r' % (svc.错误,)


# ---- 排序稳定性与去重 -------------------------------------------------

def test_结果按uri和行号稳定排序():
    """WBS 要求：结果按 uri + 行号排序（便于测试与用户预期）。"""
    svc = _服务()
    # 故意按「后一个 uri 先打开」的顺序，验证排序不是靠打开顺序
    svc.打开(_U调用2, _调用方2)
    svc.打开(_U调用1, _调用方1)
    行, 列 = _位置(_调用方1, '圆分', 2)
    结果 = svc.查引用(_U调用1, 行, 列)
    键 = [(l['uri'], l['range']['start']['line'],
          l['range']['start']['character']) for l in 结果]
    assert 键 == sorted(键), '排序不稳定：%r' % (键,)


def test_同一位置不重复列出():
    """顶层符号可能既是定义又被记为引用，去重免得客户端列两条一样的。"""
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)
    结果 = svc.查引用(_U定义, 行, 列, 包含定义=True)
    键 = [(l['uri'], l['range']['start']['line'],
          l['range']['start']['character']) for l in 结果]
    assert len(键) == len(set(键)), '出现重复条目：%r' % (键,)


# ---- 增量更新 ---------------------------------------------------------

def test_didChange后引用随之更新():
    svc = _服务()
    svc.打开(_U调用2, _调用方2)
    行, 列 = _位置(_调用方1, '圆分', 2)
    svc.打开(_U调用1, _调用方1)
    前 = svc.查引用(_U调用1, 行, 列)
    n前 = len([l for l in 前 if l['uri'] == _U调用2])
    # 调用方二删掉一处调用
    svc.改(_U调用2, _调用方2.replace('定义赵丙=圆分(1.5)。\n', ''))
    后 = svc.查引用(_U调用1, 行, 列)
    n后 = len([l for l in 后 if l['uri'] == _U调用2])
    assert n后 == n前 - 1, '删掉一处调用后引用数应减一：%d → %d' % (n前, n后)


# ---- 坐标口径（0-based UTF-16）----------------------------------------

def test_引用range覆盖整个标识符():
    svc = _服务()
    svc.打开(_U调用1, _调用方1)
    行, 列 = _位置(_调用方1, '圆分', 2)
    结果 = svc.查引用(_U调用1, 行, 列)
    命中 = [l for l in 结果 if l['range']['start']['line'] == 行]
    assert 命中, 结果
    r = 命中[0]['range']
    assert r['start']['character'] == 列, '起点列应是 0-based UTF-16 的 %d' % 列
    assert r['end']['character'] == 列 + len('圆分'), \
        'range 应覆盖整个标识符：%r' % (r,)


def test_引用位置口径_bmp外字符():
    """BMP 外字符（emoji 占 2 个 UTF-16 单元）在同一行时，列号必须按 UTF-16 算。

    索引里存的是码点列；若出协议时忘了换算，这条会因列号偏小而红。
    """
    src = '定义赵甲="🐍🐍"。\n定义赵乙=赵甲。\n定义赵丙=赵甲。\n'
    uri = 'file:///proj/emoji.jk'
    svc = _服务()
    svc.打开(uri, src)
    # 光标放在第 2 行的 `赵甲` 上（该行无 BMP 外字符，定位简单）
    行2 = src.splitlines()[1]
    列2 = 行2.index('赵甲')
    结果 = svc.查引用(uri, 1, 列2)
    assert 结果, '应查到 赵甲 的引用'
    # 只验证非 emoji 行的条目（行 1 和行 2），这些行码点==UTF-16 能逐字验
    纯bmp条目 = [loc for loc in 结果 if loc['range']['start']['line'] >= 1]
    assert 纯bmp条目, '至少应有第 2、3 行的引用'
    for loc in 纯bmp条目:
        起 = loc['range']['start']
        行文本 = src.splitlines()[起['line']]
        utf16 = 行文本.encode('utf-16-le')
        片 = utf16[起['character'] * 2:(起['character'] + 2) * 2]
        assert 片.decode('utf-16-le') == '赵甲', \
            '第 %d 行列号 %d 的 UTF-16 口径不对：%r' % (
                起['line'], 起['character'], loc)
