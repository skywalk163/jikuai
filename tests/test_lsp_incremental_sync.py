# -*- coding: utf-8 -*-
"""v0.15.0 W14 · LSP 增量同步（Incremental sync）协议级测试。

核心断言：**多次增量编辑后的诊断结果，与把同一份最终文本一次性 Full sync
进去的诊断结果逐字段一致**。只断言「没报错」是不够的——文本被增量改坏了
也可能恰好还能编译过，那种测试是空转。
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
    did_open, wait_diagnostics, write_frame,
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
    reason="jikuai_lsp 依赖不可用，跳过增量同步测试",
)


@pytest.fixture
def lsp():
    proc = start_lsp_process()
    resp = initialize(proc)
    assert resp is not None
    initialized(proc)
    yield proc
    stop_lsp_process(proc)


def _change(proc, uri, version, changes):
    """发送 textDocument/didChange。"""
    write_frame(proc.stdin, {
        "jsonrpc": "2.0",
        "method": "textDocument/didChange",
        "params": {
            "textDocument": {"uri": uri, "version": version},
            "contentChanges": changes,
        },
    })


def _rng(l1, c1, l2, c2):
    return {"start": {"line": l1, "character": c1},
            "end": {"line": l2, "character": c2}}


def _diags_of(notif):
    """从 publishDiagnostics 通知里取 diagnostics 数组。"""
    assert notif is not None, "未收到 publishDiagnostics"
    return notif['params']['diagnostics']


class TestIncrementalApply:
    """增量编辑本身生效。"""

    def test_incremental_replace_keeps_valid(self, lsp):
        """行内替换后代码仍合法，诊断为空。"""
        uri = "file:///tmp/incr_valid.jk"
        did_open(lsp, uri, "打印 1。\n")
        assert _diags_of(wait_diagnostics(lsp, uri)) == []
        # '打印 1。' 里 '1' 在 UTF-16 列 3；替换成 "你好" 字符串字面量
        _change(lsp, uri, 2, [{"range": _rng(0, 3, 0, 4), "text": '"你好"'}])
        assert _diags_of(wait_diagnostics(lsp, uri)) == []

    def test_incremental_replace_introduces_error(self, lsp):
        """增量替换引入语法错误，诊断非空且 code 以 JK-E 开头。"""
        uri = "file:///tmp/incr_err.jk"
        did_open(lsp, uri, "打印 1。\n")
        assert _diags_of(wait_diagnostics(lsp, uri)) == []
        # 把第 0 行整行（0..5）换成 '定义'，制造 ParseError
        _change(lsp, uri, 2, [{"range": _rng(0, 0, 0, 5), "text": "定义"}])
        diags = _diags_of(wait_diagnostics(lsp, uri))
        assert diags, "语法错误应产生诊断"
        assert diags[0]['code'].startswith('JK-E')

    def test_full_change_without_range_still_accepted(self, lsp):
        """无 range 的 contentChange 仍按全文替换处理（LSP 规范允许）。

        这正是 `test_v0_5_0_lsp_stub.test_did_change_publishes_diagnostics`
        的写法，声明 change=2 之后必须继续兼容。
        """
        uri = "file:///tmp/incr_norange.jk"
        did_open(lsp, uri, "打印 1。\n")
        wait_diagnostics(lsp, uri)
        _change(lsp, uri, 2, [{"text": "定义\n"}])
        assert _diags_of(wait_diagnostics(lsp, uri)), "全文替换成错误代码应有诊断"


class TestIncrementalEqualsFull:
    """增量路径与 Full 路径结果等价（W14 的核心 DoD）。"""

    #: 增量编辑序列的最终文本。刻意包含一个语法错误行，
    #: 让「诊断一致」这条断言不是在比两个空数组。
    最终文本 = "打印 3。\n定义\n"

    def test_three_incremental_edits_match_full_sync(self, lsp):
        """3 次增量编辑 → 与一次性 Full sync 同一文本的诊断逐条相同。"""
        # ---- A 路：增量 ----
        uri_a = "file:///tmp/eq_incr.jk"
        did_open(lsp, uri_a, "打印 1。\n打印 2。\n")
        assert _diags_of(wait_diagnostics(lsp, uri_a)) == []

        # 编辑 1：第 0 行 '1' → '3'
        _change(lsp, uri_a, 2, [{"range": _rng(0, 3, 0, 4), "text": "3"}])
        assert _diags_of(wait_diagnostics(lsp, uri_a)) == []

        # 编辑 2：第 1 行 '打印 2。' 整行 → '定义'
        _change(lsp, uri_a, 3, [{"range": _rng(1, 0, 1, 5), "text": "定义"}])
        diags_incr = _diags_of(wait_diagnostics(lsp, uri_a))

        # 编辑 3：空替换（no-op），验证幂等不破坏文本
        _change(lsp, uri_a, 4, [{"range": _rng(0, 0, 0, 0), "text": ""}])
        diags_incr = _diags_of(wait_diagnostics(lsp, uri_a))

        # ---- B 路：Full ----
        uri_b = "file:///tmp/eq_full.jk"
        did_open(lsp, uri_b, self.最终文本)
        diags_full = _diags_of(wait_diagnostics(lsp, uri_b))

        # 诊断本身不含 uri，可直接逐条比对
        assert diags_incr == diags_full, (
            f"增量与 Full 诊断不一致：\n增量={diags_incr}\nFull={diags_full}")
        # 且不是在比两个空数组
        assert diags_full, "最终文本应带语法错误，否则本用例无鉴别力"

    def test_multiline_incremental_edit_matches_full(self, lsp):
        """跨行增量替换（删两行换一行）与 Full sync 等价。"""
        uri_a = "file:///tmp/eq_multi_incr.jk"
        did_open(lsp, uri_a, "打印 1。\n打印 2。\n打印 3。\n")
        assert _diags_of(wait_diagnostics(lsp, uri_a)) == []

        # 把第 0~1 行（含换行）整体换成 '定义\n'
        _change(lsp, uri_a, 2, [{"range": _rng(0, 0, 2, 0), "text": "定义\n"}])
        diags_incr = _diags_of(wait_diagnostics(lsp, uri_a))

        uri_b = "file:///tmp/eq_multi_full.jk"
        did_open(lsp, uri_b, "定义\n打印 3。\n")
        diags_full = _diags_of(wait_diagnostics(lsp, uri_b))

        assert diags_incr == diags_full, (
            f"跨行增量与 Full 不一致：\n增量={diags_incr}\nFull={diags_full}")
        assert diags_full

    def test_two_changes_in_one_notification(self, lsp):
        """一条 didChange 里带多个 change，按顺序累积后与 Full 等价。

        LSP 规范：同一通知内的多个 change 必须**按序**应用，后一个的
        range 是基于前一个应用后的文本。
        """
        uri_a = "file:///tmp/eq_batch_incr.jk"
        did_open(lsp, uri_a, "打印 1。\n打印 2。\n")
        assert _diags_of(wait_diagnostics(lsp, uri_a)) == []

        _change(lsp, uri_a, 2, [
            {"range": _rng(0, 3, 0, 4), "text": "3"},   # 1 → 3
            {"range": _rng(1, 0, 1, 5), "text": "定义"},  # 第 1 行 → 定义
        ])
        diags_incr = _diags_of(wait_diagnostics(lsp, uri_a))

        uri_b = "file:///tmp/eq_batch_full.jk"
        did_open(lsp, uri_b, self.最终文本)
        diags_full = _diags_of(wait_diagnostics(lsp, uri_b))

        assert diags_incr == diags_full, (
            f"批量增量与 Full 不一致：\n增量={diags_incr}\nFull={diags_full}")
        assert diags_full
