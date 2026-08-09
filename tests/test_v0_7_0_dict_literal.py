# -*- coding: utf-8 -*-
"""v0.7.0 · 字典字面量 `{...}` 与内建动词 `去空白` 冒烟测试。

覆盖范围：
- 空字典 / 单键值 / 多键值 / 嵌套字典
- 全角与半角括号：`{}` 与 `「」`
- `.键` 与 `[键]` 两种访问方式
- 遍历字典迭代键
- `去空白` 去掉首尾空白（对齐 Python `str.strip`）
"""

import io
import contextlib

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.main import run_source


def _run(src):
    """执行一段极快源码，返回 stdout 字符串。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_source(src)
    return buf.getvalue()


def test_dict_literal_basic():
    out = _run('定义赵d={"姓名": "张三", "年龄": 28}。\n'
               '打印 赵d.姓名。\n'
               '打印 赵d["年龄"]。\n')
    assert out == "张三\n28\n"


def test_dict_literal_empty():
    out = _run('定义赵d={}。\n打印 长度 赵d。\n')
    assert out == "0\n"


def test_dict_literal_nested():
    out = _run('定义赵d={"外": {"内": "值"}, "表": 列 1 2 3}。\n'
               '打印 赵d.外.内。\n'
               '打印 (转字符串 赵d.表)。\n')
    assert out == "值\n[1, 2, 3]\n"


def test_dict_literal_iter_keys_preserves_order():
    """遍历字典迭代**键**，且按插入顺序（Python 3.7+ 语义）。"""
    out = _run('定义赵d={"a": 1, "b": 2, "c": 3}。\n'
               '遍历 赵键 于 赵d：\n'
               '  打印 赵键。\n'
               '。\n')
    assert out == "a\nb\nc\n"


def test_dict_literal_fullwidth_braces():
    """全角「」应等价于 ASCII {}。"""
    out = _run('定义赵d=「"k": "v"」。\n打印 赵d.k。\n')
    assert out == "v\n"


def test_dict_literal_trailing_comma_ok():
    """末尾逗号与换行分隔应被接受。"""
    out = _run('定义赵d={"a": 1,\n"b": 2,\n}。\n打印 加 赵d.a 赵d.b。\n')
    assert out == "3\n"


def test_verb_qukongbai_strips_whitespace():
    out = _run('打印 拼接 "[" (去空白 "  你好  ") "]"。\n')
    assert out == "[你好]\n"


def test_verb_qukongbai_only_ends():
    """只去首尾空白，中间保留。"""
    out = _run('打印 拼接 "[" (去空白 " a b ") "]"。\n')
    assert out == "[a b]\n"
