# -*- coding: utf-8 -*-
"""v0.6.0 · M5 · T-M5-S03：中文分词测试（正向最大匹配）。"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib.util
import pytest

from jikuai.main import run_source


def _load_seg():
    stdlib = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'stdlib'))
    path = os.path.join(stdlib, '分词.py')
    spec = importlib.util.spec_from_file_location('py_seg', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

SEG = _load_seg()


# ===================== 词典规模与质量 =====================

def test_词典至少500条():
    assert SEG.dictionary_size() >= 500

def test_词典无单字词():
    for w in SEG.all_words():
        assert len(w) >= 2, w

def test_词典条目全为汉字():
    for w in SEG.all_words():
        assert all('\u4e00' <= ch <= '\u9fff' for ch in w), w

def test_最长词长度与词典一致():
    assert SEG.max_word_length() == max(len(w) for w in SEG.all_words())

def test_词典是frozenset不可变():
    assert isinstance(SEG.all_words(), frozenset)
    with pytest.raises(AttributeError):
        SEG.all_words().add('伪造词')


# ===================== 正向最大匹配 =====================

def test_基本切分():
    assert SEG.segment('中国程序员') == ['中国', '程序员']

def test_长词优先():
    # 编程语言（4字）优先于 编程（2字）
    assert '编程语言' in SEG.segment('中文编程语言')

def test_整句切分():
    r = SEG.segment('中国程序员的中文编程语言')
    assert r == ['中国', '程序员', '的', '中文', '编程语言']

def test_词典外汉字单字兜底():
    r = SEG.segment('欢迎')
    assert r == ['欢', '迎']


# ===================== 兜底策略 B1/B2/B3 =====================

def test_B1_空白不产出词项():
    assert SEG.segment('中国 程序员') == ['中国', '程序员']
    assert SEG.segment('  中国\t程序员\n') == ['中国', '程序员']

def test_B2_半角字母数字整体成词():
    assert SEG.segment('JiKuai') == ['JiKuai']
    assert SEG.segment('北京2026') == ['北京', '2026']
    # 版本 在词典内，先被 FMM 切出；v0 走 B2；中国 再回主路径
    assert SEG.segment('版本v0中国') == ['版本', 'v0', '中国']

def test_B3_标点单字成词():
    assert SEG.segment('。，、') == ['。', '，', '、']
    assert SEG.segment('中国!') == ['中国', '!']

def test_纯标点每个标点单独一项():
    text = '。。，！？'
    assert SEG.segment(text) == list(text)


# ===================== 边界条件 =====================

def test_空字符串返回空列表():
    assert SEG.segment('') == []

def test_None返回空列表():
    assert SEG.segment(None) == []

def test_纯空白返回空列表():
    assert SEG.segment('   ') == []

def test_非字符串输入归一():
    assert SEG.segment(2026) == ['2026']

def test_单字输入():
    assert SEG.segment('中') == ['中']


# ===================== 返回值独立性 =====================

def test_返回新列表调用方修改不污染():
    r = SEG.segment('中国程序员')
    r.append('污染')
    assert SEG.segment('中国程序员') == ['中国', '程序员']

def test_两次调用返回不同对象():
    a = SEG.segment('中国')
    b = SEG.segment('中国')
    assert a == b
    assert a is not b


# ===================== 极快侧集成 =====================

def test_jk_分词():
    assert run_source('导入 分词。\n分词.分词("中国程序员")。') == ['中国', '程序员']

def test_jk_空输入():
    assert run_source('导入 分词。\n分词.分词("")。') == []

def test_jk_长句():
    r = run_source('导入 分词。\n分词.分词("中国程序员的中文编程语言")。')
    assert r == ['中国', '程序员', '的', '中文', '编程语言']