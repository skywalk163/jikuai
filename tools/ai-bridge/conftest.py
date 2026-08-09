# -*- coding: utf-8 -*-
"""`tools/ai-bridge/` 的 pytest 前置钩子——防止 `select.py` 遮蔽标准库。

《块选择协议 v0》把选块器定名为 `select.py`，而 `select` 恰好是 Python
标准库里的 I/O 多路复用模块。pytest 默认的 prepend 导入模式会把测试文件
所在目录（也就是本目录）插到 `sys.path` 首位，此后任何 `import select`
都会命中我们的文件。POSIX 上 `selectors` → `subprocess` 都依赖真正的
`select`，一旦被遮蔽会以完全无关的形式炸掉。

对策：conftest 加载期（早于任何测试用例）先把本目录从 `sys.path` 临时摘掉，
`import select` 一次把**标准库的** select 钉进 `sys.modules`，再恢复
`sys.path`。之后 `import select` 直接命中模块缓存，不再走路径查找。

`test_bridge.py` 与 `demos/_公用.py` 都用 `importlib` 按**绝对文件路径**
载入 `select.py`（挂成「块选择器」这个不冲突的名字），所以本对策不影响它们。
"""

import os
import sys

_HERE = os.path.abspath(os.path.dirname(__file__))


def _钉住标准库select():
    保存 = list(sys.path)
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p or os.curdir) != _HERE]
    try:
        import select  # noqa: F401  只为把标准库版本写进 sys.modules
    except ImportError:
        # Windows 上 select 依然存在；真出问题也不该让整个测试会话挂掉
        pass
    finally:
        sys.path[:] = 保存


_钉住标准库select()
