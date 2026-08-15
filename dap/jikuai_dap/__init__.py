# -*- coding: utf-8 -*-
"""极快语言 DAP 适配器（M6-P3 · ADR-20）。

独立发行包 `jikuai-dap`，物理隔离于主包 `jikuai`：
    - 主包不得 import 本包（反向依赖禁止）
    - 本包可 import `jikuai`（求值器 + service 层）

版本号真源在 `._version`（W118 · v0.24.0 起与主包同号），不在这里写死。
"""

from ._version import __version__
from .adapter import main

__all__ = ["__version__", "main"]
