# -*- coding: utf-8 -*-
"""网络.表单串 的 Python 背衬实现。

urlencode 默认把空格编码成 `+`，符合 x-www-form-urlencoded 惯例。
对键排序，保证输出稳定（签名计算/测试可重复）。
"""
from urllib.parse import urlencode


def 表串(对):
    """字典 → x-www-form-urlencoded 字符串（键排序）。"""
    if not isinstance(对, dict):
        return ''
    items = sorted(对.items(), key=lambda kv: str(kv[0]))
    return urlencode(items)
