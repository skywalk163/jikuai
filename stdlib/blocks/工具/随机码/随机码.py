# -*- coding: utf-8 -*-
"""工具.随机码 的 Python 背衬实现。用 `secrets` 保证可抗猜测。"""

import secrets
import string

_字符 = string.ascii_letters + string.digits


def 掷码(位数):
    """生成指定位数的随机字母数字码。用 secrets 而非 random 避免可预测。"""
    n = int(位数)
    if n < 1:
        return ''
    return ''.join(secrets.choice(_字符) for _ in range(n))
