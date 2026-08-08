# -*- coding: utf-8 -*-
"""极快语言 · LSP 服务器（v0.6.0 · M5 · T-M5-L05..L06）。

正式接入 service.SessionHost 编排诊断，并新增 completion + hover。
自实现 JSON-RPC over stdio（transport.py），不依赖 pygls。
主包 jikuai 惰性导入，保证物理隔离（sys.modules 无 pygls / jikuai_lsp 泄漏）。
"""

from __future__ import annotations

import sys
import logging
from typing import Any, Dict, List

from .transport import read_message, write_message
from .capabilities import server_capabilities

# 惰性导入主包：运行期才需要，避免模块级副作用。
_jikuai_imported = False
_SessionHost = None
_TextDocumentStore = None
_utf16_to_codepoint = None
_to_lsp_diagnostic = None
_complete_lsp = None
_verb_documentation = None
_keyword_documentation = None


def _ensure_jikuai():
    """惰性导入主包的 service / completion / diagnostics 层。"""
    global _jikuai_imported, _SessionHost, _TextDocumentStore
    global _utf16_to_codepoint, _to_lsp_diagnostic
    global _complete_lsp, _verb_documentation, _keyword_documentation
    if _jikuai_imported:
        return
    from jikuai.service import SessionHost, TextDocumentStore
    from jikuai.service.position import utf16_to_codepoint
    from jikuai.diagnostics.adapters import to_lsp_diagnostic
    from jikuai.completion import (
        complete_lsp, verb_documentation, keyword_documentation)
    _SessionHost = SessionHost
    _TextDocumentStore = TextDocumentStore
    _utf16_to_codepoint = utf16_to_codepoint
    _to_lsp_diagnostic = to_lsp_diagnostic
    _complete_lsp = complete_lsp
    _verb_documentation = verb_documentation
    _keyword_documentation = keyword_documentation
    _jikuai_imported = True


_TOKEN_BOUNDARY = set(' \t\r\n。，、：:；;=（）()【】[]「」{}<>+-*/%!?"\'`|&^~@$#\\')


def _token_at(line_text: str, cp_col: int) -> str:
    """取 1-based 码点列所在的 token（连续非边界字符段）。"""
    if not line_text:
        return ''
    idx = cp_col - 1
    if idx < 0:
        idx = 0
    if idx >= len(line_text):
        idx = len(line_text) - 1
    if line_text[idx] in _TOKEN_BOUNDARY:
        idx -= 1
        if idx < 0 or line_text[idx] in _TOKEN_BOUNDARY:
            return ''
    start = idx
    while start > 0 and line_text[start - 1] not in _TOKEN_BOUNDARY:
        start -= 1
    end = idx
    while end + 1 < len(line_text) and line_text[end + 1] not in _TOKEN_BOUNDARY:
        end += 1
    return line_text[start:end + 1]


class LspServer:
    """极快 LSP 服务器（v0.6.0 · F3 冻结）。

    支持：initialize / shutdown / exit / didOpen / didChange / didClose /
    textDocument/completion / textDocument/hover / publishDiagnostics。
    """

    def __init__(self, reader=None, writer=None):
        self._reader = reader or sys.stdin.buffer
        self._writer = writer or sys.stdout.buffer
        self._host = None          # SessionHost 惰性创建
        self._initialized = False
        self._shutdown_requested = False
        self._running = True
        self._logger = logging.getLogger("jikuai_lsp")

    def _get_host(self):
        """惰性创建 SessionHost（首次使用时才导入主包）。"""
        if self._host is None:
            _ensure_jikuai()
            self._host = _SessionHost(_TextDocumentStore())
        return self._host

    # ───── 主循环 ─────

    def run(self) -> int:
        """运行 LSP 消息循环，返回退出码（0=正常退出）。"""
        while self._running:
            msg = read_message(self._reader)
            if msg is None:
                break
            try:
                self._dispatch(msg)
            except Exception as e:
                # 单条消息处理失败不终止服务；请求则回 InternalError
                self._logger.exception("dispatch 异常：%s", e)
                msg_id = msg.get("id")
                if msg_id is not None:
                    self._send_error(msg_id, -32603, f"内部错误：{e}")
        return 0

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        """路由一条 JSON-RPC 消息。"""
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            self._handle_initialize(msg_id, msg.get("params", {}))
        elif method == "initialized":
            self._initialized = True
        elif method == "shutdown":
            self._handle_shutdown(msg_id)
        elif method == "exit":
            self._running = False
        elif method == "textDocument/didOpen":
            self._handle_did_open(msg.get("params", {}))
        elif method == "textDocument/didChange":
            self._handle_did_change(msg.get("params", {}))
        elif method == "textDocument/didClose":
            self._handle_did_close(msg.get("params", {}))
        elif method == "textDocument/completion":
            self._handle_completion(msg_id, msg.get("params", {}))
        elif method == "textDocument/hover":
            self._handle_hover(msg_id, msg.get("params", {}))
        elif msg_id is not None:
            self._send_error(msg_id, -32601, f"方法未实现：{method}")
        # 未知通知静默忽略（LSP 规范要求）

    # ───── 生命周期 ─────

    def _handle_initialize(self, msg_id: Any, params: Dict) -> None:
        """返回 F3 冻结的 capabilities + serverInfo。"""
        self._send_response(msg_id, {
            "capabilities": server_capabilities(),
            "serverInfo": {"name": "jikuai-lsp", "version": "0.6.0"},
        })

    def _handle_shutdown(self, msg_id: Any) -> None:
        self._shutdown_requested = True
        self._send_response(msg_id, None)

    # ───── 文本同步 ─────

    def _handle_did_open(self, params: Dict) -> None:
        td = params.get("textDocument", {})
        uri = td.get("uri", "")
        host = self._get_host()
        host.store.did_open(uri, td.get("text", ""), td.get("version", 0))
        host.invalidate(uri)
        self._publish_diagnostics(uri)

    def _handle_did_change(self, params: Dict) -> None:
        td = params.get("textDocument", {})
        uri = td.get("uri", "")
        host = self._get_host()
        # F3 声明 Full sync，故按 sync_kind=1 处理
        host.store.did_change(
            uri, td.get("version", 0), params.get("contentChanges", []), sync_kind=1)
        host.invalidate(uri)
        self._publish_diagnostics(uri)

    def _handle_did_close(self, params: Dict) -> None:
        uri = params.get("textDocument", {}).get("uri", "")
        host = self._get_host()
        host.store.did_close(uri)
        host.invalidate(uri)
        # 清除编辑器上的诊断标记
        self._send_notification("textDocument/publishDiagnostics",
                                {"uri": uri, "diagnostics": []})

    # ───── 诊断推送（T-M5-L05） ─────

    def _publish_diagnostics(self, uri: str) -> None:
        """走 SessionHost.compile_and_diagnose 编译并推送诊断。

        正式路径可同时报告 ParseError（error）与 JK-W1001（副词透传警告）。
        列换算经 line_text_provider 走 UTF-16 口径。
        """
        host = self._get_host()
        out: List[Dict] = []
        try:
            diags = host.compile_and_diagnose(uri)
            provider = host.store.line_text_provider(uri)
            for d in diags:
                out.append(_to_lsp_diagnostic(d, line_text_provider=provider))
        except Exception as e:
            # frontend 未兜底的异常（如 ParseError）→ 投影 e.info 为最小诊断
            self._logger.debug("编译异常：%s", e)
            info = getattr(e, 'info', None)
            if info is not None:
                try:
                    from jikuai.diagnostics import codes as _codes
                    from jikuai.diagnostics.adapters import from_error_info
                    diag = from_error_info(info, _codes.JK_E1001)
                    provider = host.store.line_text_provider(uri)
                    out.append(_to_lsp_diagnostic(diag, line_text_provider=provider))
                except Exception:
                    pass
        self._send_notification("textDocument/publishDiagnostics",
                                {"uri": uri, "diagnostics": out})

    # ───── Completion（T-M5-L06） ─────

    def _handle_completion(self, msg_id: Any, params: Dict) -> None:
        """LSP 位置 → service.position 换算 1-based 码点 → completion.complete。"""
        _ensure_jikuai()
        host = self._get_host()
        uri = params.get("textDocument", {}).get("uri", "")
        pos = params.get("position", {})
        lsp_line = int(pos.get("line", 0))
        lsp_char = int(pos.get("character", 0))
        text = host.store.get(uri) or ""
        lines = host.store.lines_of(uri) or []
        line_text = lines[lsp_line] if 0 <= lsp_line < len(lines) else ""
        cp_line = lsp_line + 1
        cp_col = _utf16_to_codepoint(line_text, lsp_char)
        items = _complete_lsp(text, cp_line, cp_col)
        self._send_response(msg_id, {"isIncomplete": False, "items": items})

    # ───── Hover（T-M5-L06） ─────

    def _handle_hover(self, msg_id: Any, params: Dict) -> None:
        """光标下 token 是内建动词/关键字 → 返回中文说明；否则返回 null。"""
        _ensure_jikuai()
        host = self._get_host()
        uri = params.get("textDocument", {}).get("uri", "")
        pos = params.get("position", {})
        lsp_line = int(pos.get("line", 0))
        lsp_char = int(pos.get("character", 0))
        lines = host.store.lines_of(uri) or []
        if lsp_line < 0 or lsp_line >= len(lines):
            self._send_response(msg_id, None)
            return
        line_text = lines[lsp_line]
        cp_col = _utf16_to_codepoint(line_text, lsp_char)
        token = _token_at(line_text, cp_col)
        if not token:
            self._send_response(msg_id, None)
            return
        doc = _verb_documentation(token)
        if doc is None:
            doc = _keyword_documentation(token)
        if doc is None:
            self._send_response(msg_id, None)
            return
        self._send_response(msg_id, {
            "contents": {"kind": "markdown", "value": doc},
        })

    # ───── JSON-RPC 发送 ─────

    def _send_response(self, msg_id: Any, result: Any) -> None:
        write_message(self._writer,
                      {"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _send_error(self, msg_id: Any, code: int, message: str) -> None:
        write_message(self._writer, {
            "jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}})

    def _send_notification(self, method: str, params: Dict) -> None:
        write_message(self._writer,
                      {"jsonrpc": "2.0", "method": method, "params": params})


def main() -> int:
    """入口：启动 LSP 服务器，返回退出码。"""
    logging.basicConfig(
        level=logging.DEBUG,
        format="[jikuai-lsp] %(levelname)s %(message)s",
        stream=sys.stderr,  # LSP 规范：stdout 留给协议，日志走 stderr
    )
    return LspServer().run()