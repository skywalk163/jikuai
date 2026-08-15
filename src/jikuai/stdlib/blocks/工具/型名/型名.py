# -*- coding: utf-8 -*-
"""工具.型名 的 Python 背衬实现。

内建动词 `类名` 只接受用户类实例，对 int/str 这类原生值会报「需要对象实例」。
本块要覆盖所有值（含原生类型），所以下沉到背衬，把 Python 类型名映射成中文。
"""

_映射 = {
    'int': '整数',
    'float': '小数',
    'str': '字符串',
    'bool': '布尔',
    'list': '列表',
    'dict': '字典',
    'tuple': '列表',
    'NoneType': '空',
}


def 型别(值):
    """返回值的运行时类型名（中文）。未知类型回退到 Python 类型名。"""
    name = type(值).__name__
    return _映射.get(name, name)
