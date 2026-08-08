# -*- coding: utf-8 -*-
"""极快语言包。

同时是 Python → 极快的嵌入入口（v0.4.0 M2 · ADR-10）：

    import jikuai

    mod = jikuai.load("脚本.jk")     # 加载为模块对象，访问其 `导出` 的名字
    jikuai.run_source('打印 加 3 5。')  # 直接跑一段源码
    jikuai.run_file('脚本.jk')        # 跑整个文件

    try:
        mod.某函数(3)
    except jikuai.JiKuaiError as e:  # 保留中文文案与 e.info（ErrorInfo）
        print(e.info.category.value, e.info.message)

AC-104：`import jikuai` 只做模块导入，**不**触发任何 `load`，
也不建立全局可变状态；`load` 每次返回独立的模块对象。
"""

from .errors import ErrorCategory, ErrorFormatter, ErrorInfo
from .evaluator import JiKuaiError
from .main import main, repl, run_file, run_source
from .pybridge import load

__version__ = "0.4.1"
__all__ = [
    'run_source', 'run_file', 'repl', 'main',
    'load', 'JiKuaiError', 'ErrorInfo', 'ErrorCategory', 'ErrorFormatter',
]
