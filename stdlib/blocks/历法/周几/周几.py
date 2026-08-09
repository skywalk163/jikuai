# -*- coding: utf-8 -*-
"""历法.周几 的 Python 背衬实现。"""

from datetime import date

_名 = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']


def 星期(年, 月, 日):
    """返回该公历日期的星期中文名。"""
    return _名[date(int(年), int(月), int(日)).weekday()]
