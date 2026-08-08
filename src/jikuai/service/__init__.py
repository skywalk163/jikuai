# -*- coding: utf-8 -*-
"""极快语言 · service 层（M5 · T-M5-L01..L03）。

本包提供 LSP / DAP 共用的会话服务：
    - TextDocumentStore：文档缓存
    - SessionHost：编译与诊断编排
    - position：UTF-16 ↔ 码点位置换算
"""

from .position import codepoint_to_utf16, utf16_to_codepoint
from .text_document_store import TextDocumentStore
from .session_host import SessionHost

__all__ = [
    "codepoint_to_utf16",
    "utf16_to_codepoint",
    "TextDocumentStore",
    "SessionHost",
]
