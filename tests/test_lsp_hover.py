# -*- coding: utf-8 -*-
"""v0.15.0 W13 · LSP textDocument/hover 协议级测试。

覆盖：动词悬停、关键字悬停、无效 token 返回 null。
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
    did_open, wait_diagnostics, request_hover,
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
    reason="jikuai_lsp 依赖不可用，跳过 LSP 悬停测试",
)


@pytest.fixture
def lsp():
    proc = start_lsp_process()
    resp = initialize(proc)
    assert resp is not None
    initialized(proc)
    yield proc
    stop_lsp_process(proc)


class TestVerbHover:
    """内建动词悬停。"""

    def test_hover_on_verb(self, lsp):
        """悬停在 '打印' 上返回 markdown 说明。"""
        uri = "file:///tmp/hover_verb.jk"
        did_open(lsp, uri, "打印 1。\n")
        wait_diagnostics(lsp, uri)
        resp = request_hover(lsp, uri, line=0, character=0, msg_id=200)
        assert resp is not None
        result = resp.get('result')
        assert result is not None, "动词应返回 hover"
        contents = result.get('contents')
        assert isinstance(contents, dict)
        assert contents.get('kind') == 'markdown'
        assert '打印' in contents.get('value', '')

    def test_hover_verb_mentions_builtin(self, lsp):
        """动词 hover 文本应含 '内建动词' 字样。"""
        uri = "file:///tmp/hover_verb2.jk"
        did_open(lsp, uri, "求和 甲。\n")
        wait_diagnostics(lsp, uri)
        resp = request_hover(lsp, uri, line=0, character=0, msg_id=201)
        assert resp is not None
        result = resp.get('result')
        assert result is not None
        assert '内建动词' in result['contents']['value']

    def test_hover_second_char_of_verb(self, lsp):
        """悬停在动词的第二个字上也应命中整个 token。"""
        uri = "file:///tmp/hover_verb3.jk"
        did_open(lsp, uri, "打印 1。\n")
        wait_diagnostics(lsp, uri)
        resp = request_hover(lsp, uri, line=0, character=1, msg_id=202)
        assert resp is not None
        result = resp.get('result')
        assert result is not None
        assert '打印' in result['contents']['value']


class TestKeywordHover:
    """关键字悬停。"""

    def test_hover_on_keyword(self, lsp):
        """悬停在 '如果' 上返回关键字说明。"""
        uri = "file:///tmp/hover_kw.jk"
        did_open(lsp, uri, "如果 真 那么\n")
        wait_diagnostics(lsp, uri)
        resp = request_hover(lsp, uri, line=0, character=0, msg_id=210)
        assert resp is not None
        result = resp.get('result')
        assert result is not None, "关键字应返回 hover"
        assert '关键字' in result['contents']['value']

    def test_hover_keyword_value_contains_name(self, lsp):
        """关键字 hover 文本含关键字本身。"""
        uri = "file:///tmp/hover_kw2.jk"
        did_open(lsp, uri, "如果 真 那么\n")
        wait_diagnostics(lsp, uri)
        resp = request_hover(lsp, uri, line=0, character=0, msg_id=211)
        assert resp is not None
        assert '如果' in resp['result']['contents']['value']


class TestInvalidHover:
    """无效 token / 空位置返回 null。"""

    def test_hover_on_user_identifier(self, lsp):
        """悬停在用户标识符（非动词非关键字）上返回 null。"""
        uri = "file:///tmp/hover_user.jk"
        # 张三 是百家姓标识符，不是动词也不是关键字
        did_open(lsp, uri, "定义 张三 赋值 1。\n")
        wait_diagnostics(lsp, uri)
        # 光标在 '张三' 上：'定义 张三...'，码点 3=张、4=三 → char 3
        resp = request_hover(lsp, uri, line=0, character=3, msg_id=220)
        assert resp is not None
        assert resp.get('result') is None, \
            f"用户标识符应返回 null，实际 {resp.get('result')}"

    def test_hover_out_of_range_line(self, lsp):
        """悬停在越界行返回 null。"""
        uri = "file:///tmp/hover_oob.jk"
        did_open(lsp, uri, "打印 1。\n")
        wait_diagnostics(lsp, uri)
        resp = request_hover(lsp, uri, line=99, character=0, msg_id=221)
        assert resp is not None
        assert resp.get('result') is None

    def test_hover_on_numeric_literal(self, lsp):
        """悬停在数字字面量上返回 null（数字不是动词/关键字）。"""
        uri = "file:///tmp/hover_num.jk"
        did_open(lsp, uri, "打印 1。\n")
        wait_diagnostics(lsp, uri)
        # 码点：打(0)印(1)空格(2)1(3)。(4)  数字 '1' 在 char 3
        resp = request_hover(lsp, uri, line=0, character=3, msg_id=222)
        assert resp is not None
        # '1' 不是动词也不是关键字 → null
        assert resp.get('result') is None
