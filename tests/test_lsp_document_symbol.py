# -*- coding: utf-8 -*-
"""v0.16.0 W32 · LSP textDocument/documentSymbol 协议级测试。

覆盖：函数符号、类符号、导入符号、空文件、多符号排序。
"""

from __future__ import annotations

import os
import sys
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))
sys.path.insert(0, os.path.join(_ROOT, 'lsp'))
sys.path.insert(0, os.path.join(_ROOT, 'tests'))

from lsp_helpers import (
    start_lsp_process, stop_lsp_process, initialize, initialized,
    did_open, wait_diagnostics, write_frame, read_until,
)


def _lsp_available():
    try:
        import importlib
        importlib.import_module('jikuai_lsp')
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _lsp_available(),
    reason="jikuai_lsp 依赖不可用，跳过 documentSymbol 测试",
)


def request_document_symbol(proc, uri: str, msg_id: int = 50):
    """发 textDocument/documentSymbol 请求，返回响应。"""
    write_frame(proc.stdin, {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "textDocument/documentSymbol",
        "params": {
            "textDocument": {"uri": uri},
        },
    })
    return read_until(
        proc.stdout,
        lambda m: m.get('id') == msg_id and ('result' in m or 'error' in m),
        max_msgs=10,
    )


@pytest.fixture
def lsp():
    proc = start_lsp_process()
    resp = initialize(proc)
    assert resp is not None
    initialized(proc)
    yield proc
    stop_lsp_process(proc)


class TestFunctionSymbol:
    """函数定义符号。"""

    def test_single_function(self, lsp):
        """单个函数定义应返回一个 Function 类型符号。"""
        uri = "file:///tmp/docsym_func.jk"
        src = "函数 求和 接收 甲 乙：\n  返回 加 甲 乙。\n。\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        resp = request_document_symbol(lsp, uri, msg_id=500)
        assert resp is not None
        result = resp.get('result')
        assert isinstance(result, list)
        funcs = [s for s in result if s.get('kind') == 12]  # Function
        assert len(funcs) >= 1
        assert funcs[0]['name'] == '求和'

    def test_function_has_range(self, lsp):
        """函数符号必须包含 range 和 selectionRange。"""
        uri = "file:///tmp/docsym_func_range.jk"
        src = "函数 平方 接收 甲：\n  返回 乘 甲 甲。\n。\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        resp = request_document_symbol(lsp, uri, msg_id=501)
        assert resp is not None
        result = resp.get('result')
        assert len(result) >= 1
        sym = result[0]
        assert 'range' in sym
        assert 'selectionRange' in sym
        assert 'start' in sym['range']
        assert 'end' in sym['range']


class TestClassSymbol:
    """类定义符号。"""

    def test_single_class(self, lsp):
        """单个类定义应返回一个 Class 类型符号。"""
        uri = "file:///tmp/docsym_class.jk"
        src = "类 动物：\n  构造 接收 名字：\n    定义 自身.名字 赋值 名字。\n  。\n。\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        resp = request_document_symbol(lsp, uri, msg_id=510)
        assert resp is not None
        result = resp.get('result')
        assert isinstance(result, list)
        classes = [s for s in result if s.get('kind') == 5]  # Class
        assert len(classes) >= 1
        assert classes[0]['name'] == '动物'


class TestImportSymbol:
    """导入符号。"""

    def test_single_import(self, lsp):
        """导入语句应产生 Module 类型符号。"""
        uri = "file:///tmp/docsym_import.jk"
        src = "导入 blocks.数据.求和。\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        resp = request_document_symbol(lsp, uri, msg_id=520)
        assert resp is not None
        result = resp.get('result')
        assert isinstance(result, list)
        imports = [s for s in result if s.get('kind') == 2]  # Module
        assert len(imports) >= 1
        assert 'blocks.数据.求和' in imports[0]['name']


class TestEmptyFile:
    """空文件。"""

    def test_empty_returns_empty_list(self, lsp):
        """空文件应返回空列表。"""
        uri = "file:///tmp/docsym_empty.jk"
        did_open(lsp, uri, "")
        wait_diagnostics(lsp, uri)
        resp = request_document_symbol(lsp, uri, msg_id=530)
        assert resp is not None
        result = resp.get('result')
        assert isinstance(result, list)
        assert len(result) == 0


class TestMultipleSymbols:
    """多符号排序——应与源码书写顺序一致。"""

    def test_order_matches_source(self, lsp):
        """多个符号的返回顺序应与源码书写顺序一致。"""
        uri = "file:///tmp/docsym_multi.jk"
        src = (
            "导入 blocks.数据.求和。\n"
            "函数 算平均 接收 甲：\n  返回 甲。\n。\n"
            "函数 算总和 接收 乙：\n  返回 乙。\n。\n"
        )
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        resp = request_document_symbol(lsp, uri, msg_id=540)
        assert resp is not None
        result = resp.get('result')
        assert isinstance(result, list)
        assert len(result) >= 3
        # 顺序：导入 → 算平均 → 算总和
        names = [s['name'] for s in result]
        assert names.index('blocks.数据.求和') < names.index('算平均')
        assert names.index('算平均') < names.index('算总和')
