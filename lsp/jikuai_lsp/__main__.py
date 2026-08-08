# -*- coding: utf-8 -*-
"""允许 `python -m jikuai_lsp` 启动 LSP 桩服务器。"""

import sys
from .server import main

if __name__ == "__main__":
    sys.exit(main())
