# -*- coding: utf-8 -*-
"""极快语言 · LSP 桩（v0.5.0 · ADR-15）。

独立发行包 `jikuai-lsp`，物理隔离于主包 `jikuai`。
主包不得 import 本包；本包可 import `jikuai`。
"""

__version__ = "0.5.0"

from .server import main

__all__ = ["__version__", "main"]
