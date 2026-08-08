# -*- coding: utf-8 -*-
"""极快语言 · service 层 · 会话宿主（M5 · T-M5-L03）。

SessionHost 绑定一个 TextDocumentStore 与一个诊断缓存，
提供 `compile_and_diagnose(uri)` 一站式编译 + 诊断收集。

设计价值：
    - LSP Server 和未来 M6 DAP 共用此层，避免重复编排 frontend 调用
    - 诊断缓存让多次查询同一文档时免重编译（version 不变时直接返回）
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..diagnostics.model import Diagnostic
from ..frontend import compile_source
from .text_document_store import TextDocumentStore


class SessionHost:
    """会话宿主：管理文档存储与诊断缓存。

    诊断缓存键为 (uri, version)；version 变化时自动重编译。
    """

    def __init__(self, store: Optional[TextDocumentStore] = None):
        self.store = store if store is not None else TextDocumentStore()
        # uri → (version, diagnostics)
        self._diag_cache: Dict[str, tuple] = {}

    def compile_and_diagnose(self, uri: str) -> List[Diagnostic]:
        """编译文档并返回诊断列表。

        若文档 version 未变且缓存命中，直接返回缓存结果。
        文档不存在时返回空列表。
        """
        text = self.store.get(uri)
        if text is None:
            return []

        version = self.store.version_of(uri)
        cached = self._diag_cache.get(uri)
        if cached is not None and cached[0] == version:
            return cached[1]

        # 调用编译前端
        result = compile_source(text, file=uri)
        diagnostics = list(result.diagnostics)

        # 缓存诊断结果
        self._diag_cache[uri] = (version, diagnostics)
        return diagnostics

    def invalidate(self, uri: str) -> None:
        """显式失效某文档的诊断缓存。"""
        self._diag_cache.pop(uri, None)

    def invalidate_all(self) -> None:
        """失效所有缓存。"""
        self._diag_cache.clear()
