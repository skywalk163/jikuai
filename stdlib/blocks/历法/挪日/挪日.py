# -*- coding: utf-8 -*-
"""历法.挪日 的 Python 背衬实现。"""

from datetime import date, timedelta


def 移日(年, 月, 日, 天数):
    """从 (年,月,日) 前进 天数 天（负数为后退），返回 [年,月,日] 列表。"""
    d = date(int(年), int(月), int(日)) + timedelta(days=int(天数))
    return [d.year, d.month, d.day]
