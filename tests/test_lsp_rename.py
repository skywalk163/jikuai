# -*- coding: utf-8 -*-
"""v0.17.0 W41 · `textDocument/rename` + `prepareRename`。

安全性优先于覆盖率：宁可拒绝改名，也不能改坏代码。覆盖 WBS 列的项：
单文件、跨文件、非原子新名被拒、块导出名被拒、prepareRename 边界、
空/重名冲突。

拒绝路径断言的不只是「拒了」，还断言**回的是可读中文错误**——
空编辑在 VS Code 里表现为「什么都没发生」，用户无从判断是拒绝还是坏了。
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


# 定义 + 使用 甲加（用户函数，不是块导出名）
_定义方 = '''函数 甲加 接收 赵x:
  返回 加 赵x 1。
。

定义赵结果=甲加(10)。
打印 赵结果。
'''

# 另一个文件里通过导入使用 甲加（跨文件 rename 用）
_调用方 = '''从 blocks.财务.保留分 导入 圆分。

定义赵甲=圆分(1.1)。
定义赵乙=圆分(2.2)。
'''

# 引用了块导出名 圆分（`圆分` 在 索引.json 里是 保留分 块的导出名）
_用块 = '''从 blocks.财务.保留分 导入 圆分。

定义赵值=圆分(3.14)。
'''

_U定义 = 'file:///proj/定义方.jk'
_U调用 = 'file:///proj/调用方.jk'
_U用块 = 'file:///proj/用块.jk'


class _服务:
    def __init__(self):
        self.s = LspServer(reader=io.BytesIO(), writer=io.BytesIO())
        self.响应 = []
        self.错误 = []
        self.s._send_response = lambda mid, result: self.响应.append(result)
        self.s._send_error = lambda mid, code, msg: self.错误.append((code, msg))
        self.s._send_notification = lambda method, params: None

    def 打开(self, uri, text):
        self.s._handle_did_open({'textDocument': {
            'uri': uri, 'languageId': 'jikuai', 'version': 1, 'text': text}})

    def prepare(self, uri, line0, char0):
        self.响应.clear(); self.错误.clear()
        self.s._handle_prepare_rename(1, {
            'textDocument': {'uri': uri},
            'position': {'line': line0, 'character': char0},
        })
        return self.响应[-1] if self.响应 else '__no_response__'

    def rename(self, uri, line0, char0, 新名):
        self.响应.clear(); self.错误.clear()
        self.s._handle_rename(1, {
            'textDocument': {'uri': uri},
            'position': {'line': line0, 'character': char0},
            'newName': 新名,
        })
        return (self.响应[-1] if self.响应 else None,
                self.错误[-1] if self.错误 else None)


def _位置(源码, 名字, 第几次=1):
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
    raise AssertionError('找不到第 %d 个「%s」' % (第几次, 名字))


# ---- capabilities -----------------------------------------------------

def test_capabilities_声明renameProvider():
    from jikuai_lsp.capabilities import server_capabilities
    caps = server_capabilities()
    assert caps['renameProvider'] == {'prepareProvider': True}


def test_dispatch_路由到rename与prepareRename():
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)
    svc.响应.clear()
    svc.s._dispatch({'jsonrpc': '2.0', 'id': 1,
                     'method': 'textDocument/prepareRename',
                     'params': {'textDocument': {'uri': _U定义},
                                'position': {'line': 行, 'character': 列}}})
    assert svc.响应, 'prepareRename 未接进 dispatch'
    svc.响应.clear()
    svc.s._dispatch({'jsonrpc': '2.0', 'id': 2,
                     'method': 'textDocument/rename',
                     'params': {'textDocument': {'uri': _U定义},
                                'position': {'line': 行, 'character': 列},
                                'newName': '赵新名'}})
    assert svc.响应 or svc.错误, 'rename 未接进 dispatch'


# ---- prepareRename ----------------------------------------------------

def test_prepare_可改名符号返回范围():
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)
    结果 = svc.prepare(_U定义, 行, 列)
    assert isinstance(结果, dict) and 'range' in 结果
    assert 结果.get('placeholder') == '甲加'
    r = 结果['range']
    assert r['start']['character'] == 列
    assert r['end']['character'] == 列 + len('甲加')


def test_prepare_空白处返回null():
    svc = _服务()
    svc.打开(_U定义, _定义方)
    # 第 3 行是 `。`——不是可改名符号
    结果 = svc.prepare(_U定义, 2, 0)
    assert 结果 is None


def test_prepare_内置动词返回null():
    """`打印` 是内置动词，不在符号索引里，不可改名。"""
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '打印', 1)
    assert svc.prepare(_U定义, 行, 列) is None


def test_prepare_块导出名返回null():
    """块导出名 圆分 本轮不可改名，prepare 阶段就该挡住。"""
    svc = _服务()
    svc.打开(_U用块, _用块)
    行, 列 = _位置(_用块, '圆分', 1)
    assert svc.prepare(_U用块, 行, 列) is None


# ---- rename 正例 ------------------------------------------------------

def test_rename_单文件():
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)
    编辑, 错 = svc.rename(_U定义, 行, 列, '赵新名')
    assert 错 is None, 错
    assert 编辑 and 'changes' in 编辑
    改动 = 编辑['changes'][_U定义]
    # 定义处 + 调用处，至少两处
    assert len(改动) >= 2
    assert all(e['newText'] == '赵新名' for e in 改动)


def test_rename_跨文件():
    """定义在甲文件、被乙文件引用的顶层变量，改名必须覆盖两个文件。

    用顶层变量而不是函数做跨文件用例：极快的裸函数引用（无 `导入`）会被
    分词器按动词切开（`甲加(1)` → `加` 作用于 `甲`），而带 `导入` 的名字
    又必然是块导出名（rename 本轮明确拒绝）。顶层变量是当前唯一能干净
    构造「跨文件同一用户符号」的形态。
    """
    甲 = 'file:///proj/共享定义.jk'
    乙 = 'file:///proj/共享使用.jk'
    乙源 = '打印 赵共享。\n定义赵另=赵共享。\n'
    svc = _服务()
    svc.打开(甲, '定义赵共享=1。\n')
    svc.打开(乙, 乙源)
    # 从乙文件的 `打印 赵共享` 行发起改名（空格隔开，_token_at 能切出）
    行, 列 = _位置(乙源, '赵共享', 1)
    编辑, 错 = svc.rename(乙, 行, 列, '赵新名')
    assert 错 is None, 错
    changes = 编辑['changes']
    assert 甲 in changes and 乙 in changes, \
        '改名必须覆盖两个文件，实际只有：%r' % (list(changes),)
    assert len(changes[乙]) == 2, '乙文件里有两处引用都要改：%r' % (changes[乙],)
    assert all(e['newText'] == '赵新名'
               for 表 in changes.values() for e in 表)


def test_rename_同文件内编辑按位置降序():
    """同一文件多处编辑按位置降序——客户端逐条应用时列号不偏移。

    定义方里 甲加 出现在函数定义和调用两处，两处编辑应降序。
    """
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)
    编辑, 错 = svc.rename(_U定义, 行, 列, '赵新名')
    assert 错 is None, 错
    起点 = [(e['range']['start']['line'], e['range']['start']['character'])
           for e in 编辑['changes'][_U定义]]
    assert len(起点) >= 2, '至少有定义+调用两处'
    assert 起点 == sorted(起点, reverse=True), '同文件编辑应按位置降序：%r' % (起点,)


# ---- rename 拒绝路径（都要有可读中文提示）-----------------------------

def test_rename_非原子新名被拒():
    """`块求和` 会被分词器切成 `块(IDENT)+求和(VERB)`，必须拒。"""
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)
    编辑, 错 = svc.rename(_U定义, 行, 列, '块求和')
    assert 编辑 is None, '非原子新名不该产出编辑'
    assert 错 is not None
    码, 消息 = 错
    assert '词法原子' in 消息 or '切' in 消息, '拒绝理由要可读：%r' % (消息,)


def test_rename_块导出名被拒():
    """改块导出名 圆分 会牵动 块.json + G13，本轮明确拒绝。"""
    svc = _服务()
    svc.打开(_U用块, _用块)
    行, 列 = _位置(_用块, '圆分', 2)   # 调用处
    编辑, 错 = svc.rename(_U用块, 行, 列, '赵圆分')
    assert 编辑 is None
    assert 错 is not None
    assert '块' in 错[1] and ('导出' in 错[1] or 'G13' in 错[1])


def test_rename_新名撞块导出名被拒():
    """把普通符号改成某个块的导出名（缴税）会撞名，拒。"""
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)
    编辑, 错 = svc.rename(_U定义, 行, 列, '缴税')
    assert 编辑 is None
    assert 错 is not None and '导出名' in 错[1]


def test_rename_空新名被拒():
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)
    编辑, 错 = svc.rename(_U定义, 行, 列, '   ')
    assert 编辑 is None and 错 is not None
    assert '空' in 错[1]


def test_rename_新名同原名被拒():
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)
    编辑, 错 = svc.rename(_U定义, 行, 列, '甲加')
    assert 编辑 is None and 错 is not None
    assert '相同' in 错[1]


def test_rename_光标不在符号上被拒():
    svc = _服务()
    svc.打开(_U定义, _定义方)
    编辑, 错 = svc.rename(_U定义, 2, 0, '赵新名')   # `。` 行
    assert 编辑 is None and 错 is not None


def test_rename_产出的编辑range覆盖标识符():
    """改名编辑的 range 必须精确覆盖旧标识符，否则会改坏相邻文本。"""
    svc = _服务()
    svc.打开(_U定义, _定义方)
    行, 列 = _位置(_定义方, '甲加', 1)
    编辑, 错 = svc.rename(_U定义, 行, 列, '赵新名')
    assert 错 is None
    for e in 编辑['changes'][_U定义]:
        r = e['range']
        assert r['end']['character'] - r['start']['character'] == len('甲加'), \
            'range 宽度应等于旧名长度：%r' % (r,)
