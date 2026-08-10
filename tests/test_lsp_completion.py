# -*- coding: utf-8 -*-
"""v0.15.0 W13 · LSP textDocument/completion 协议级测试。

覆盖：动词补全、关键字补全、块导入路径补全、中文触发字符 `，`。
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
    did_open, wait_diagnostics, request_completion,
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
    reason="jikuai_lsp 依赖不可用，跳过 LSP 补全测试",
)


@pytest.fixture
def lsp():
    proc = start_lsp_process()
    resp = initialize(proc)
    assert resp is not None, "initialize 失败"
    initialized(proc)
    yield proc
    stop_lsp_process(proc)


class TestVerbCompletion:
    """动词补全。"""

    def test_verb_prefix_match(self, lsp):
        """输入 '打' 应补全出 '打印'。"""
        uri = "file:///tmp/comp_verb.jk"
        did_open(lsp, uri, "打\n")
        wait_diagnostics(lsp, uri)
        resp = request_completion(lsp, uri, line=0, character=1, msg_id=100)
        assert resp is not None
        result = resp.get('result')
        assert result is not None
        items = result.get('items', [])
        labels = [it['label'] for it in items]
        assert '打印' in labels, f"期望含 '打印'，实际 {labels}"

    def test_verb_completion_has_kind(self, lsp):
        """动词补全的 kind 应是 Function(3)。"""
        uri = "file:///tmp/comp_verb_kind.jk"
        did_open(lsp, uri, "打\n")
        wait_diagnostics(lsp, uri)
        resp = request_completion(lsp, uri, line=0, character=1, msg_id=101)
        assert resp is not None
        items = resp['result']['items']
        打印_items = [it for it in items if it['label'] == '打印']
        assert len(打印_items) >= 1
        assert 打印_items[0]['kind'] == 3  # Function

    def test_multi_verb_prefix(self, lsp):
        """输入 '求' 应补全出 '求和'。"""
        uri = "file:///tmp/comp_multi.jk"
        did_open(lsp, uri, "求\n")
        wait_diagnostics(lsp, uri)
        resp = request_completion(lsp, uri, line=0, character=1, msg_id=102)
        assert resp is not None
        labels = [it['label'] for it in resp['result']['items']]
        assert '求和' in labels, f"期望含 '求和'，实际 {labels}"


class TestKeywordCompletion:
    """关键字补全。"""

    def test_keyword_prefix(self, lsp):
        """输入 '如' 应补全出 '如果'。"""
        uri = "file:///tmp/comp_kw.jk"
        did_open(lsp, uri, "如\n")
        wait_diagnostics(lsp, uri)
        resp = request_completion(lsp, uri, line=0, character=1, msg_id=110)
        assert resp is not None
        labels = [it['label'] for it in resp['result']['items']]
        assert '如果' in labels

    def test_keyword_kind(self, lsp):
        """关键字补全的 kind 应是 Keyword(14)。"""
        uri = "file:///tmp/comp_kw_kind.jk"
        did_open(lsp, uri, "如\n")
        wait_diagnostics(lsp, uri)
        resp = request_completion(lsp, uri, line=0, character=1, msg_id=111)
        assert resp is not None
        如果_items = [it for it in resp['result']['items'] if it['label'] == '如果']
        assert len(如果_items) >= 1
        assert 如果_items[0]['kind'] == 14  # Keyword


class TestModuleImportCompletion:
    """导入模块后 `模块名.` 触发成员补全。"""

    def test_module_member_completion(self, lsp):
        """导入 简繁 后 `简繁.` 补全出 转简体/转繁体。"""
        uri = "file:///tmp/comp_module.jk"
        src = "导入 简繁\n简繁.\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        # 光标在 `简繁.` 的 `.` 之后：line=1, character=3 (0-based UTF-16)
        resp = request_completion(lsp, uri, line=1, character=3, msg_id=120)
        assert resp is not None
        items = resp['result']['items']
        labels = [it['label'] for it in items]
        assert '转简体' in labels or '转繁体' in labels, \
            f"期望含模块导出，实际 {labels}"


class TestTriggerCharComma:
    """中文逗号 `，` 作为触发字符后的行为。"""

    def test_completion_after_chinese_comma(self, lsp):
        """在 `，` 之后触发补全不崩溃（可能返回空）。"""
        uri = "file:///tmp/comp_comma.jk"
        src = "定义 甲 赋值 列 1 2 3，\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        # `，` 之后请求补全：行 0, 列在 `，` 之后
        # "定义 甲 赋值 列 1 2 3，" 共 12 个码点，UTF-16 也是 12
        resp = request_completion(lsp, uri, line=0, character=12, msg_id=130)
        assert resp is not None
        # 不需要有结果，但不能报错
        assert 'result' in resp, f"触发字符 `，` 后补全报错：{resp.get('error')}"


class TestCompletionEmpty:
    """空前缀返回空列表（LSP 口径特殊行为）。"""

    def test_empty_prefix_returns_empty(self, lsp):
        """光标在行首，前缀为空时应返回空 items 列表。"""
        uri = "file:///tmp/comp_empty.jk"
        did_open(lsp, uri, "\n")
        wait_diagnostics(lsp, uri)
        resp = request_completion(lsp, uri, line=0, character=0, msg_id=140)
        assert resp is not None
        items = resp['result']['items']
        assert items == [], f"空前缀应返回空列表，实际 {len(items)} 条"
