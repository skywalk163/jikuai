# -*- coding: utf-8 -*-
"""极快语言 · service 层 · 文档存储（M5 · T-M5-L01）。

维护 LSP 客户端打开的文档缓存：uri → (text, version, lines)。
支持 Full + Incremental 两种同步策略。

Incremental 同步的行/列换算依赖 `service.position` 模块。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .position import utf16_to_codepoint


# LSP TextDocumentSyncKind 常量
SYNC_NONE = 0
SYNC_FULL = 1
SYNC_INCREMENTAL = 2


class TextDocumentStore:
    """LSP 文档缓存。

    每个文档以 uri 为键，存储完整文本、版本号与行列表。
    行列表按需重建（文本变更后失效重算）。
    """

    def __init__(self):
        # uri → (text, version)
        self._docs: Dict[str, Tuple[str, int]] = {}
        # uri → lines 缓存（惰性构建）
        self._lines_cache: Dict[str, List[str]] = {}

    # ─────── 生命周期 ───────

    def did_open(self, uri: str, text: str, version: int = 0) -> None:
        """处理 textDocument/didOpen。"""
        self._docs[uri] = (text, version)
        self._lines_cache.pop(uri, None)

    def did_change(
        self,
        uri: str,
        version: int,
        content_changes: List[Dict],
        sync_kind: int = SYNC_FULL,
    ) -> None:
        """处理 textDocument/didChange。

        sync_kind=1（Full）：取最后一个 change 的 text 替换整篇。
        sync_kind=2（Incremental）：逐个 change 应用增量编辑。
        """
        if sync_kind == SYNC_FULL:
            # Full sync：直接取最后一个 change 的 text
            text = content_changes[-1].get("text", "") if content_changes else ""
            self._docs[uri] = (text, version)
            self._lines_cache.pop(uri, None)
        elif sync_kind == SYNC_INCREMENTAL:
            text = self.get(uri) or ""
            for change in content_changes:
                rng = change.get("range")
                new_text = change.get("text", "")
                if rng is None:
                    # 无 range = 全量替换（容错）
                    text = new_text
                else:
                    text = self._apply_change(text, rng, new_text)
            self._docs[uri] = (text, version)
            self._lines_cache.pop(uri, None)
        else:
            # sync_kind=0 不接收变更；容错按 Full 处理
            text = content_changes[-1].get("text", "") if content_changes else ""
            self._docs[uri] = (text, version)
            self._lines_cache.pop(uri, None)

    def did_close(self, uri: str) -> None:
        """处理 textDocument/didClose。"""
        self._docs.pop(uri, None)
        self._lines_cache.pop(uri, None)

    # ─────── 查询接口 ───────

    def get(self, uri: str) -> Optional[str]:
        """返回文档全文，不存在返回 None。"""
        entry = self._docs.get(uri)
        return entry[0] if entry else None

    def lines_of(self, uri: str) -> Optional[List[str]]:
        """返回文档按行分割的列表（splitlines），不存在返回 None。"""
        if uri not in self._docs:
            return None
        if uri not in self._lines_cache:
            text = self._docs[uri][0]
            self._lines_cache[uri] = text.splitlines()
        return self._lines_cache[uri]

    def version_of(self, uri: str) -> Optional[int]:
        """返回文档版本号，不存在返回 None。"""
        entry = self._docs.get(uri)
        return entry[1] if entry else None

    def line_text_provider(self, uri: str):
        """返回一个 line_text_provider 回调（1-based 行号 → 行文本）。

        供 `diagnostics.adapters.to_lsp_diagnostic` 使用。
        """
        lines = self.lines_of(uri)
        if lines is None:
            return None

        def provider(line_1based: int) -> Optional[str]:
            idx = line_1based - 1
            if 0 <= idx < len(lines):
                return lines[idx]
            return None

        return provider

    # ─────── 增量编辑内部实现 ───────

    @staticmethod
    def _apply_change(text: str, rng: Dict, new_text: str) -> str:
        """在文本上应用一个增量变更（LSP Range + 新文本）。

        Range 使用 0-based 行 + 0-based UTF-16 列。
        需要换算为码点偏移后执行替换。
        """
        lines = text.splitlines(True)  # 保留换行符
        start_line = rng["start"]["line"]
        start_char = rng["start"]["character"]
        end_line = rng["end"]["line"]
        end_char = rng["end"]["character"]

        # 换算 UTF-16 列到码点列
        def line_content(ln: int) -> str:
            """取行内容（不含尾部换行符）。"""
            if 0 <= ln < len(lines):
                return lines[ln].rstrip('\r\n')
            return ""

        start_col_cp = utf16_to_codepoint(line_content(start_line), start_char) - 1
        end_col_cp = utf16_to_codepoint(line_content(end_line), end_char) - 1

        # 计算起止的绝对字符偏移
        start_offset = 0
        for i in range(start_line):
            start_offset += len(lines[i]) if i < len(lines) else 0
        start_offset += start_col_cp

        end_offset = 0
        for i in range(end_line):
            end_offset += len(lines[i]) if i < len(lines) else 0
        end_offset += end_col_cp

        # 执行替换
        return text[:start_offset] + new_text + text[end_offset:]
