# -*- coding: utf-8 -*-
"""极快语言 · LSP 服务器（ADR-15）。

独立发行包 `jikuai-lsp`，物理隔离于主包 `jikuai`。
主包不得 import 本包；本包可 import `jikuai`。

版本号真源在 `._version`（W118 · v0.24.0 起与主包同号），不在这里写死。
"""

from ._version import __version__
from .server import main

__all__ = ["__version__", "main"]
