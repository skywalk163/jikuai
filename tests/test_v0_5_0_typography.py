# -*- coding: utf-8 -*-
"""v0.5.0 · M4 · T-M4-S06：中文排版格式化测试（AC-M4-05-01..03）。"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib.util

import pytest

from jikuai.main import run_source


def _load_typo():
    stdlib = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'stdlib'))
    path = os.path.join(stdlib, '排版.py')
    spec = importlib.util.spec_from_file_location('py_typo', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

TYPO = _load_typo()


# ===================== AC-M4-05-01：中西文间距插入 =====================

def test_中英相邻插空格():
    assert TYPO.insert_spacing('极快JiKuai很快') == '极快 JiKuai 很快'


def test_中数字相邻插空格():
    assert TYPO.insert_spacing('第1个') == '第 1 个'
    assert TYPO.insert_spacing('版本v0.5是最新') == '版本 v0.5 是最新'


def test_已有空格不重复插入():
    assert TYPO.insert_spacing('极快 JiKuai 很快') == '极快 JiKuai 很快'
    assert TYPO.insert_spacing('第 1 个') == '第 1 个'


def test_两侧都处理():
    # 中在前、英在后
    assert '中 A' in TYPO.insert_spacing('中A')
    # 英在前、中在后
    assert 'A 中' in TYPO.insert_spacing('A中')


def test_纯ASCII输入不变():
    assert TYPO.insert_spacing('Hello, World!') == 'Hello, World!'


def test_纯中文输入不变():
    assert TYPO.insert_spacing('中国计算机学会') == '中国计算机学会'


def test_短输入不崩():
    assert TYPO.insert_spacing('') == ''
    assert TYPO.insert_spacing('a') == 'a'
    assert TYPO.insert_spacing('中') == '中'


def test_None输入返回空字符串():
    assert TYPO.insert_spacing(None) == ''


# ===================== AC-M4-05-02：幂等性（硬要求） =====================

@pytest.mark.parametrize('text', [
    '极快JiKuai是第1个适合中国国情的中文编程语言,值得一试!',
    '版本v0.5发布,支持中英文混排',
    '中国计算机学会2026年年会',
    'Hello, World 123!',
    '',
    '   ',
    '中',
    '中,英, 字符',
])
def test_AC_M4_05_02_规范化文本幂等(text):
    once = TYPO.normalize_text(text)
    twice = TYPO.normalize_text(once)
    assert once == twice


@pytest.mark.parametrize('text', [
    '极快JiKuai',
    '第1个',
    '已有 空格',
    'Hello World',
    '',
])
def test_插入间距幂等(text):
    once = TYPO.insert_spacing(text)
    twice = TYPO.insert_spacing(once)
    assert once == twice


@pytest.mark.parametrize('text', [
    '中文标点,句号.',
    'a, b, c',
    '3.14',
    '连续  空格   多个',
    '中文 , 前空格',
])
def test_规范标点幂等(text):
    once = TYPO.normalize_punctuation(text)
    twice = TYPO.normalize_punctuation(once)
    assert once == twice


# ===================== AC-M4-05-03：标点规范化 =====================

def test_R2b_紧跟中文的半角标点转全角():
    assert TYPO.normalize_punctuation('你好,世界.') == '你好，世界。'
    assert TYPO.normalize_punctuation('对吗?对!') == '对吗？对！'
    assert TYPO.normalize_punctuation('要点:第一;第二') == '要点：第一；第二'


def test_R2b_不紧跟中文的半角标点保留():
    # 3.14 中的 . 前面不是中文
    assert TYPO.normalize_punctuation('圆周率 3.14') == '圆周率 3.14'
    # a, b 中的 , 前面不是中文
    assert TYPO.normalize_punctuation('列表 a, b, c') == '列表 a, b, c'


def test_R2a_连续空格折叠():
    assert TYPO.normalize_punctuation('多      空格') == '多 空格'
    assert TYPO.normalize_punctuation('  两个空格  ') == ' 两个空格 '


def test_R2c_全角标点两侧收紧():
    assert TYPO.normalize_punctuation('你好 ， 世界') == '你好，世界'
    assert TYPO.normalize_punctuation('问句 ？') == '问句？'


def test_None与空输入():
    assert TYPO.normalize_punctuation(None) == ''
    assert TYPO.normalize_punctuation('') == ''
    assert TYPO.normalize_text(None) == ''


# ===================== 主 API 集成 =====================

def test_normalize_text_综合样例():
    src = '极快JiKuai是第1个适合中国国情的中文编程语言,值得一试!'
    expected = '极快 JiKuai 是第 1 个适合中国国情的中文编程语言，值得一试！'
    assert TYPO.normalize_text(src) == expected


def test_极快侧_规范化文本():
    assert run_source('导入 排版。\n排版.规范化文本("极快JiKuai很快")。') == '极快 JiKuai 很快'


def test_极快侧_插入间距():
    assert run_source('导入 排版。\n排版.插入间距("v0.5发布")。') == 'v0.5 发布'


def test_极快侧_规范标点():
    result = run_source('导入 排版。\n排版.规范标点("你好,世界.")。')
    assert result == '你好，世界。'


def test_punctuation_rules_可解析():
    rules = TYPO.punctuation_rules()
    assert 'R2a' in rules
    assert 'R2b' in rules
    assert 'R2c' in rules
    assert isinstance(rules['R2b'], dict)
