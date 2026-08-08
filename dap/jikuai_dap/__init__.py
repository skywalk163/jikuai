# -*- coding: utf-8 -*-
"""极快语言 DAP 适配器（M6-P3 · ADR-20）。

独立发行包 `jikuai-dap`，物理隔离于主包 `jikuai`：
    - 主包不得 import 本包（反向依赖禁止）
    - 本包可 import `jikuai`（求值器 + service 层）
"""

__version__ = "0.7.0"

from .adapter import main

__all__ = ["__version__", "main"]
