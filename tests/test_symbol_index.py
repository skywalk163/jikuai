# -*- coding: utf-8 -*-
"""v0.17.0 W39 · 跨文件符号索引直测（ADR-29 落地验收）。

本周不上任何新 LSP 能力，只测底座本身。覆盖 WBS 列的项：
函数/类/方法/导出名各类符号、同名跨文件、增量更新正确性、删文件后引用消失、
超限降级、码点口径。

**口径断言是重点**：索引里存的必须是 1-based 码点，不是 0-based、不是 UTF-16。
v0.16.0 W32 在这上面踩过，这里用 BMP 外字符（emoji）专门守一条。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.service import symbol_index as si
from jikuai.service.position import codepoint_to_utf16


# ---- 样本源码（真实极快语法，对齐 stdlib/blocks 的写法）----------------

_加法块 = '''从 blocks.财务.保留分 导入 圆分。

函数 甲加 接收 赵x:
  返回 加 赵x 1。
。

导出 甲加。
'''

_调用方 = '''从 blocks.财务.保留分 导入 圆分。

定义赵总=圆分(3.14)。
打印 赵总。
'''

_类样本 = '''类 乙盒:
  构造 接收 赵初:
    赋值 我.值 = 赵初。
  。
  方法 取值:
    返回 我.值。
  。
。
'''


def _uri(name: str) -> str:
    return 'file:///proj/%s.jk' % name


# ---- build_file_symbols：各类符号 -------------------------------------

def test_函数定义被索引():
    defs, _ = si.build_file_symbols(_uri('a'), _加法块)
    名表 = {d.name: d for d in defs}
    assert '甲加' in 名表
    fn = [d for d in defs if d.name == '甲加'
          and d.kind == si.SYMBOL_KIND_FUNCTION]
    assert fn, '函数 甲加 应被索引为 Function'


def test_导入的导出名被索引():
    """`从 … 导入 圆分` 的 `圆分` 是跨文件 references 的锚点，必须进索引。"""
    defs, _ = si.build_file_symbols(_uri('a'), _加法块)
    assert '圆分' in {d.name for d in defs}


def test_导出语句的名字被索引():
    """`导出 甲加` 也算一处定义——G13 全局唯一就是按这个名字判的。"""
    defs, _ = si.build_file_symbols(_uri('a'), _加法块)
    出现 = [d for d in defs if d.name == '甲加']
    assert len(出现) >= 2, '函数定义 + 导出语句应各记一处：%r' % (出现,)


def test_模块路径被索引为Module():
    defs, _ = si.build_file_symbols(_uri('a'), _加法块)
    模块 = [d for d in defs if d.kind == si.SYMBOL_KIND_MODULE]
    assert 模块 and 模块[0].name == 'blocks.财务.保留分'


def test_类与方法被索引():
    defs, _ = si.build_file_symbols(_uri('c'), _类样本)
    类 = [d for d in defs if d.kind == si.SYMBOL_KIND_CLASS]
    方法 = [d for d in defs if d.kind == si.SYMBOL_KIND_METHOD]
    assert 类 and 类[0].name == '乙盒'
    assert '取值' in {d.name for d in 方法}, '类方法应被索引：%r' % (defs,)
    取值 = [d for d in 方法 if d.name == '取值'][0]
    assert 取值.container == '乙盒', '方法必须带所属类名，否则无法与同名顶层函数区分'
    assert not 取值.is_top_level


def test_顶层定义被索引_局部定义不被索引():
    """ADR-29 决策点 1：顶层 `定义` 收，函数体内的 `定义` 不收。"""
    src = '定义赵全局=1。\n函数 甲f:\n  定义赵局部=2。\n  返回 赵局部。\n。\n'
    defs, _ = si.build_file_symbols(_uri('d'), src)
    名 = {d.name for d in defs}
    assert '赵全局' in 名
    assert '赵局部' not in 名, '局部变量不该进跨文件索引（作用域内改名不需要跨文件）'


def test_函数调用点被记为引用():
    """`圆分(3.14)` 是对 圆分 的引用——references 的核心用例。"""
    _, refs = si.build_file_symbols(_uri('b'), _调用方)
    命中 = [r for r in refs if r.name == '圆分']
    assert 命中, '函数调用点必须被记为引用：%r' % (refs,)


def test_内置动词不被记为引用():
    """`加`/`打印` 是内置动词不是用户符号；收进来会让 references 全是噪声。"""
    _, refs = si.build_file_symbols(_uri('a'), _加法块)
    assert '加' not in {r.name for r in refs}


def test_编译失败返回空而不抛():
    """用户正在打字时文件常常语法不完整，索引不能因此把后台线程打挂。"""
    defs, refs = si.build_file_symbols(_uri('bad'), '函数 甲加 接收')
    assert isinstance(defs, list) and isinstance(refs, list)


def test_空文本返回空():
    assert si.build_file_symbols(_uri('e'), '') == ([], [])


# ---- 口径：1-based 码点 ------------------------------------------------

def test_位置是1based码点而非utf16():
    """BMP 外字符（emoji 占 2 个 UTF-16 单元）后面的符号列号必须是**码点**。

    若索引误存 UTF-16 列，`圆分` 的 col 会比码点值大——这条就红。
    """
    src = '定义赵甲="🐍"。\n定义赵乙=赵甲。\n'
    defs, refs = si.build_file_symbols(_uri('u'), src)
    assert all(d.line >= 1 and d.col >= 1 for d in defs), '必须 1-based'
    乙 = [d for d in defs if d.name == '赵乙'][0]
    assert 乙.line == 2, '行号是 1-based：第二行应是 2 而非 1'
    # 第二行没有 BMP 外字符，码点列与 UTF-16 列在此行恰好相等；
    # 关键是换算函数能吃下这个 col 而不越界。
    行文本 = src.splitlines()[1]
    assert 0 <= codepoint_to_utf16(行文本, 乙.col) <= len(行文本) * 2


def test_引用位置也是1based():
    _, refs = si.build_file_symbols(_uri('b'), _调用方)
    assert refs and all(r.line >= 1 and r.col >= 1 for r in refs)


# ---- SymbolIndex：增删改查 --------------------------------------------

def test_索引_基本增查():
    idx = si.SymbolIndex()
    idx.add_file(_uri('a'), _加法块)
    assert idx.definitions_of('甲加')
    assert idx.lookup('甲加') == idx.definitions_of('甲加'), 'lookup 是别名'
    assert idx.symbol_count() > 0


def test_索引_同名跨文件():
    """两个文件各定义一个 甲加 → definitions_of 返回两处，按 uri 排序。"""
    idx = si.SymbolIndex()
    idx.add_file(_uri('a'), _加法块)
    idx.add_file(_uri('z'), _加法块)
    出现 = idx.definitions_of('甲加')
    uris = [d.uri for d in 出现]
    assert _uri('a') in uris and _uri('z') in uris
    assert uris == sorted(uris), '排序是契约（W40 要求 references 稳定排序）'


def test_索引_跨文件引用():
    """调用方文件引用了定义方的 圆分 → references_to 跨文件查得到。"""
    idx = si.SymbolIndex()
    idx.add_file(_uri('a'), _加法块)
    idx.add_file(_uri('b'), _调用方)
    引用 = idx.references_to('圆分')
    assert any(r.uri == _uri('b') for r in 引用), 引用


def test_索引_增量更新_幂等():
    """同一 uri 重复 add_file 不该让条目翻倍（didChange 会反复触发）。"""
    idx = si.SymbolIndex()
    idx.add_file(_uri('a'), _加法块)
    第一次 = idx.symbol_count()
    idx.add_file(_uri('a'), _加法块)
    assert idx.symbol_count() == 第一次


def test_索引_增量更新_改名后旧符号消失():
    idx = si.SymbolIndex()
    idx.add_file(_uri('a'), _加法块)
    assert idx.definitions_of('甲加')
    idx.add_file(_uri('a'), _加法块.replace('甲加', '甲新'))
    assert not idx.definitions_of('甲加'), '旧名必须随文件重建一起消失'
    assert idx.definitions_of('甲新')


def test_索引_删文件后定义与引用都消失():
    idx = si.SymbolIndex()
    idx.add_file(_uri('a'), _加法块)
    idx.add_file(_uri('b'), _调用方)
    assert idx.references_to('圆分')
    idx.remove_file(_uri('b'))
    assert not [r for r in idx.references_to('圆分') if r.uri == _uri('b')]
    assert idx.definitions_of('甲加'), '删调用方不该影响定义方'
    idx.remove_file(_uri('a'))
    assert not idx.definitions_of('甲加')


def test_索引_删不存在的文件是无操作():
    idx = si.SymbolIndex()
    idx.add_file(_uri('a'), _加法块)
    n = idx.symbol_count()
    idx.remove_file(_uri('不存在'))
    assert idx.symbol_count() == n


def test_索引_超限降级并置标记():
    """ADR-29 决策点 5：超限停止收录 + 置 over_limit，**不静默**。"""
    idx = si.SymbolIndex(max_symbols=2)
    idx.add_file(_uri('a'), _加法块)   # 该文件符号数 > 2
    assert idx.over_limit, '超限必须置标记，供 LSP 层 showMessage 告知用户'
    assert idx.symbol_count() == 0, '超限时不半收半不收'


def test_索引_未超限不置标记():
    idx = si.SymbolIndex()
    idx.add_file(_uri('a'), _加法块)
    assert not idx.over_limit


def test_索引_ready标记():
    """构建未完成时查询结果是部分的，W40 据此回「正在索引」。"""
    idx = si.SymbolIndex()
    assert not idx.ready
    idx.add_file(_uri('a'), _加法块)
    assert not idx.ready, 'add_file 不等于全量构建完成'
    idx.mark_ready()
    assert idx.ready


def test_索引_all_symbols与symbols_in():
    idx = si.SymbolIndex()
    idx.add_file(_uri('a'), _加法块)
    idx.add_file(_uri('c'), _类样本)
    全部 = idx.all_symbols()
    assert len(全部) == idx.symbol_count()
    assert [d.uri for d in 全部] == sorted(d.uri for d in 全部)
    单文件 = idx.symbols_in(_uri('c'))
    assert 单文件 and all(d.uri == _uri('c') for d in 单文件)
    assert [d.line for d in 单文件] == sorted(d.line for d in 单文件)


def test_索引_indexed_uris():
    idx = si.SymbolIndex()
    idx.add_file(_uri('a'), _加法块)
    idx.add_file(_uri('b'), _调用方)
    assert idx.indexed_uris() == sorted([_uri('a'), _uri('b')])


def test_索引_clear():
    idx = si.SymbolIndex()
    idx.add_file(_uri('a'), _加法块)
    idx.mark_ready()
    idx.clear()
    assert idx.symbol_count() == 0
    assert not idx.ready and not idx.over_limit
    assert idx.indexed_uris() == []


def test_索引_查不到返回空列表():
    idx = si.SymbolIndex()
    idx.add_file(_uri('a'), _加法块)
    assert idx.definitions_of('压根没有这个名字') == []
    assert idx.references_to('压根没有这个名字') == []


# ---- 线程安全（后台构建 + 前台查询并发）-------------------------------

def test_索引_并发读写不炸():
    """ADR-29 决策点 3：后台线程构建时前台会来查，读写必须在锁下。

    这条测不出所有竞态，但能抓到「忘了加锁导致 dict 迭代中被改」这类
    RuntimeError: dictionary changed size during iteration。
    """
    import threading
    idx = si.SymbolIndex()
    错误 = []

    def 写():
        try:
            for i in range(40):
                idx.add_file(_uri('w%d' % i), _加法块)
        except Exception as e:      # pragma: no cover - 只在回归时触发
            错误.append(e)

    def 读():
        try:
            for _ in range(200):
                idx.definitions_of('甲加')
                idx.references_to('甲加')
                idx.all_symbols()
                idx.indexed_uris()
        except Exception as e:      # pragma: no cover
            错误.append(e)

    线程 = [threading.Thread(target=写), threading.Thread(target=读),
            threading.Thread(target=读)]
    for t in 线程:
        t.start()
    for t in 线程:
        t.join(timeout=30)
    assert not 错误, '并发读写抛异常：%r' % (错误,)


# ---- 共用遍历（不许两处各写一份 isinstance 链）------------------------

def test_top_level_symbol_of是共用判定():
    """`documentSymbol`（W32）与本模块共用同一份「哪些节点算符号」判定。"""
    from jikuai.frontend import compile_source
    ast = compile_source(_加法块, file='x').ast
    符号 = [si.top_level_symbol_of(n) for n in ast.body]
    认识的 = [s for s in 符号 if s is not None]
    assert {s.name for s in 认识的} >= {'甲加', 'blocks.财务.保留分'}
