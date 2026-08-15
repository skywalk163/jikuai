# -*- coding: utf-8 -*-
"""工具.深拷贝 的 Python 背衬实现。"""

import copy


def 深摹(值):
    """对值做深拷贝。列表/字典嵌套结构安全隔离，改副本不影响原值。"""
    return copy.deepcopy(值)
