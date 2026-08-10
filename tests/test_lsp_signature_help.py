# -*- coding: utf-8 -*-
"""v0.16.0 W32 · LSP textDocument/signatureHelp 协议级测试。

覆盖：内建动词签名、参数高亮位置、可变元数动词、副词签名、无效位置返回 null。
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
    reason="jikuai_lsp 依赖不可用，跳过 signatureHelp 测试",
)


def request_signature_help(proc, uri: str, line: int, character: int,
                           msg_id: int = 60):
    """发 textDocument/signatureHelp 请求，返回响应。"""
    write_frame(proc.stdin, {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "textDocument/signatureHelp",
        "params": {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
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


class TestBuiltinVerbSignature:
    """内建动词签名。"""

    def test_binary_verb_signature(self, lsp):
        """二元动词 `加` 应返回含 2 个参数的签名。"""
        uri = "file:///tmp/sighelp_add.jk"
        # `加 1 2。` → 光标在空格后（`加 `之后第一个参数位置）
        src = "加 1 2。\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        # character 2 = 紧接 `加 ` 的空格后，第一参数位置
        resp = request_signature_help(lsp, uri, line=0, character=2, msg_id=600)
        assert resp is not None
        result = resp.get('result')
        assert result is not None, "二元动词应返回 signatureHelp"
        sigs = result.get('signatures', [])
        assert len(sigs) >= 1
        sig = sigs[0]
        assert '加' in sig.get('label', '')
        params = sig.get('parameters', [])
        assert len(params) == 2

    def test_active_parameter_first(self, lsp):
        """光标在第一个参数位置 → activeParameter = 0。"""
        uri = "file:///tmp/sighelp_active0.jk"
        src = "加 1 2。\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        resp = request_signature_help(lsp, uri, line=0, character=2, msg_id=601)
        assert resp is not None
        result = resp.get('result')
        assert result is not None
        # 光标刚过动词空格，在第一个参数
        assert result.get('activeParameter') == 0

    def test_active_parameter_second(self, lsp):
        """光标在第二个参数位置 → activeParameter = 1。"""
        uri = "file:///tmp/sighelp_active1.jk"
        src = "加 1 2。\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        # UTF-16 列：加=0 空格=1 1=2 空格=3 2=4 → char 4 落在第二参数
        resp = request_signature_help(lsp, uri, line=0, character=4, msg_id=602)
        assert resp is not None
        result = resp.get('result')
        assert result is not None
        assert result.get('activeParameter') == 1


class TestVariableArityVerb:
    """可变元数动词签名。"""

    def test_variadic_verb_signature(self, lsp):
        """可变元数动词（如 `打印`）应返回含 `…argN` 的参数。"""
        uri = "file:///tmp/sighelp_variadic.jk"
        src = "打印 1 2 3。\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        # char 3 = 紧随 `打印 ` 的空格后
        resp = request_signature_help(lsp, uri, line=0, character=3, msg_id=610)
        assert resp is not None
        result = resp.get('result')
        assert result is not None
        sig = result['signatures'][0]
        assert '打印' in sig['label']
        params = sig.get('parameters', [])
        assert len(params) >= 2  # arg1 + …argN


class TestNoSignatureAtInvalidPosition:
    """无效位置返回 null。"""

    def test_no_verb_returns_null(self, lsp):
        """光标处无动词调用 → signatureHelp 返回 null。"""
        uri = "file:///tmp/sighelp_null.jk"
        src = "定义 甲 赋值 1。\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        # `定义` 不在 VERB_ARITY → 返回 null
        resp = request_signature_help(lsp, uri, line=0, character=5, msg_id=620)
        assert resp is not None
        result = resp.get('result')
        assert result is None, f"无动词位置应返回 null，实际 {result}"

    def test_out_of_range_line_returns_null(self, lsp):
        """越界行 → signatureHelp 返回 null。"""
        uri = "file:///tmp/sighelp_oob.jk"
        src = "加 1 2。\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        resp = request_signature_help(lsp, uri, line=99, character=0, msg_id=621)
        assert resp is not None
        result = resp.get('result')
        assert result is None
