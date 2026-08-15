# -*- coding: utf-8 -*-
"""历法.周岁 的 Python 背衬实现。"""


def 实岁(生年, 生月, 生日, 今年, 今月, 今日):
    """计算周岁：今日年份 − 出生年份；如果今年的生日还没到则减 1。"""
    age = int(今年) - int(生年)
    if (int(今月), int(今日)) < (int(生月), int(生日)):
        age -= 1
    return age
