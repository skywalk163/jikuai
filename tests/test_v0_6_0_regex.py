# -*- coding: utf-8 -*-
"""v0.6.0 · M5 · T-M5-S01：中文正则测试。"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib.util
import pytest

from jikuai.main import run_source


def _load_regex():
    stdlib = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'stdlib'))
    path = os.path.join(stdlib, '正则.py')
    spec = importlib.util.spec_from_file_location('py_regex', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

RE = _load_regex()


# ===================== 匹配 =====================

def test_match_fullmatch_汉字范围():
    assert RE.match("[一-十]+", "一二三") is True

def test_match_fullmatch_failure():
    assert RE.match("[a-z]+", "Hello") is False

def test_match_empty():
    assert RE.match(".*", "") is True

def test_match_none_input():
    assert RE.match(".", None) is False


# ===================== 搜索 =====================

def test_search_found():
    r = RE.search(r"\d+", "abc123def")
    assert r is not None
    assert r["文本"] == "123"
    assert r["起始"] == 3
    assert r["结束"] == 6

def test_search_not_found():
    assert RE.search("xyz", "abc") is None

def test_search_none_input():
    assert RE.search(".", None) is None

def test_search_chinese():
    r = RE.search("[一-十]+", "hello一二三world")
    assert r["文本"] == "一二三"


# ===================== 替换（replace） =====================

def test_replace_all():
    assert RE.replace("世界", "中国", "你好，世界！世界你好。") == "你好，中国！中国你好。"

def test_replace_no_match():
    assert RE.replace("xyz", "abc", "hello") == "hello"

def test_replace_none_text():
    assert RE.replace(".", "x", None) == ""


# ===================== 编译 =====================

def test_compile_returns_dict():
    c = RE.compile_pattern(r"\d+")
    assert "源" in c
    assert "_编译对象" in c

def test_compile_reuse():
    c = RE.compile_pattern(r"\d+")
    r = RE.search(c, "abc123")
    assert r["文本"] == "123"


# ===================== 中文别名 \汉 =====================

def test_han_alias():
    assert RE.match(r"\汉+", "中国人民") is True
    assert RE.match(r"\汉+", "abc") is False

def test_han_alias_search():
    r = RE.search(r"\汉+", "abc中文def")
    assert r["文本"] == "中文"


# ===================== 错误处理 =====================

def test_invalid_pattern_raises():
    with pytest.raises(ValueError, match="语法错误"):
        RE.match("[unclosed", "test")

def test_bad_type_raises():
    with pytest.raises(TypeError):
        RE.match(123, "test")


# ===================== 极快侧集成 =====================

def test_jk_匹配():
    assert run_source('导入 正则。\n正则.匹配("中.", "中国")。') is True

def test_jk_搜索():
    # 注意：.jk 字符串字面量会吃掉未知转义的反斜杠，故 jk 源码里要写 \\d。
    # 这里 Python 层再转义一层，落到 jk 源码就是 "\\d+"。
    r = run_source('导入 正则。\n正则.搜索("\\\\d+", "abc123")。')
    assert r["文本"] == "123"


def test_jk_单反斜杠被吃掉_契约():
    # 语言级行为（lexer._read_string）：未知转义丢反斜杠，"\d" -> "d"。
    # 因此这条搜索按字面量 d 匹配，abc123 里没有 d → 空。
    assert run_source('导入 正则。\n正则.搜索("\\d+", "abc123")。') is None
    assert run_source('导入 正则。\n正则.搜索("\\d+", "abcd")。')["文本"] == "d"


def test_jk_汉字别名():
    r = run_source('导入 正则。\n正则.搜索("\\\\汉+", "abc中国def")。')
    assert r["文本"] == "中国"


def test_jk_字符类规避转义():
    # 推荐写法：用字符类，完全绕开反斜杠转义问题
    r = run_source('导入 正则。\n正则.搜索("[0-9]+", "abc123")。')
    assert r["文本"] == "123"
    r2 = run_source('导入 正则。\n正则.搜索("[一-鿿]+", "abc中国def")。')
    assert r2["文本"] == "中国"

def test_jk_替代():
    r = run_source('导入 正则。\n正则.替代("世界", "中国", "你好世界")。')
    assert r == "你好中国"

def test_jk_编译():
    r = run_source('导入 正则。\n定义 甲 = 正则.编译("[0-9]+")。\n正则.搜索(甲, "v3更新")。')
    assert r["文本"] == "3"