# -*- coding: utf-8 -*-
"""允许 `python -m jikuai_dap` 启动 DAP 适配器。"""

import sys
from .adapter import main

if __name__ == "__main__":
    sys.exit(main())
