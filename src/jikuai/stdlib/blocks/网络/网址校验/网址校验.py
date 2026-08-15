# -*- coding: utf-8 -*-
"""网络.网址校验 的 Python 背衬实现。

用 urllib.parse.urlparse 拆分 URL，要求同时含 scheme 与 netloc。
"""
from urllib.parse import urlparse


def 核址(网址):
    """URL 合法性检查：需要非空 scheme 与 netloc。"""
    if not isinstance(网址, str) or not 网址.strip():
        return False
    try:
        r = urlparse(网址)
    except Exception:
        return False
    return bool(r.scheme) and bool(r.netloc)
