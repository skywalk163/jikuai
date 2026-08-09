# -*- coding: utf-8 -*-
"""数据.载入 的 Python 背衬实现（ADR-16 §3.3 混合模块）。

为什么需要 .py 背衬：
  内建动词 `读取`/`写入` 在 keywords.py 里声明了元数，但 evaluator 的
  `_setup_builtins()` 并未提供实现，运行期调用会抛「未知动词：读取」。
  文件 I/O 因此无法用纯 .jk 表达，下沉到本背衬。`.jk` 只做门面与 `导出`。

安全：仅按调用方给定路径读文本，不做路径穿越防护——块生态默认可信代码。
"""


def 装载(路径):
    """读取文本文件内容为字符串（UTF-8）。"""
    with open(路径, 'r', encoding='utf-8') as f:
        return f.read()
