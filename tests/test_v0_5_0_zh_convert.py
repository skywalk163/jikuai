# -*- coding: utf-8 -*-
"""v0.5.0 · M4 · T-M4-S06：简繁转换测试（AC-M4-04-01..04）。"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib.util

import pytest

from jikuai.main import run_source

# ---------------------------------------------------------------------------
# 直接加载 简繁.py 进行单元测试（不走 module_loader 的集成路径）
# ---------------------------------------------------------------------------

def _load_jianfan():
    stdlib = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'stdlib'))
    path = os.path.join(stdlib, '简繁.py')
    spec = importlib.util.spec_from_file_location('jf', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

JF = _load_jianfan()


# ===================== 简→繁 =====================

def test_常用字简转繁():
    assert JF.to_traditional('中国计算机') == '中國計算機'
    assert JF.to_traditional('发展') == '發展'
    assert JF.to_traditional('经济') == '經濟'


def test_整句简转繁():
    result = JF.to_traditional('极快是适合中国国情的编程语言')
    assert '國' in result
    assert '適' in result
    assert '語' in result


# ===================== 繁→简 =====================

def test_常用字繁转简():
    assert JF.to_simplified('國家標準') == '国家标准'
    assert JF.to_simplified('計算機') == '计算机'


def test_整句繁转简():
    result = JF.to_simplified('極快是適合中國國情的編程語言')
    assert '国' in result
    assert '适' in result
    assert '语' in result


# ===================== AC-M4-04-03：无可转换字符恒等 =====================

@pytest.mark.parametrize('text', [
    'Hello, World 123!',
    '3.14159',
    '   ',
    '!!!@#$%^&*()',
    '\t\n',
    '',
])
def test_AC_M4_04_03_无可转换字符恒等(text):
    assert JF.to_traditional(text) == text
    assert JF.to_simplified(text) == text


def test_None输入返回空字符串():
    assert JF.to_traditional(None) == ''
    assert JF.to_simplified(None) == ''


# ===================== 一简对多繁歧义口径 =====================

def test_歧义字_发_选定_發():
    # 发 → 發（发生义），不是 髮
    assert JF.to_traditional('发') == '發'


def test_歧义字_后_选定_後():
    assert JF.to_traditional('后') == '後'


def test_歧义字_干_选定_幹():
    assert JF.to_traditional('干') == '幹'


def test_歧义字_台_选定_臺():
    assert JF.to_traditional('台') == '臺'


def test_歧义字_里_选定_裡():
    assert JF.to_traditional('里') == '裡'


def test_AMBIGUOUS_CHOICES_口径表():
    choices = JF.ambiguous_choices()
    assert '发' in choices
    assert choices['发']['选定'] == '發'
    assert '髮' in choices['发']['未覆盖']


def test_未覆盖繁体字仍能转简体():
    # 髮 不是 to_traditional 产出的字，但 to_simplified 能把它转回
    assert JF.to_simplified('髮') == '发'
    assert JF.to_simplified('乾') == '干'


# ===================== 幂等性 =====================

@pytest.mark.parametrize('text', [
    '中国计算机学会发布国家标准',
    '简繁转换幂等性测试用例abc123',
    '發展經濟計算機',
])
def test_简繁双向幂等(text):
    # 简→繁→简 对被覆盖字符恒等
    t = JF.to_traditional(text)
    s = JF.to_simplified(t)
    # 再转一次应该没变化
    assert JF.to_traditional(s) == JF.to_traditional(text)
    assert JF.to_simplified(JF.to_simplified(text)) == JF.to_simplified(text)


def test_转繁体幂等():
    # 已经是繁体的输入再转一次不应变化（被表覆盖的繁体字不在 S2T 里）
    # 注意：某些繁体字可能碰巧也是其他简体字的映射源……
    # 因此只用不冲突的纯繁体句子测试
    t = JF.to_traditional('经济发展标准')
    assert JF.to_traditional(t) == t  # 繁体输入再转繁体不变


# ===================== 映射规模 =====================

def test_mapping_size_合理范围():
    s2t, t2s = JF.mapping_size()
    assert s2t >= 1100  # 约 1200 条
    assert t2s >= 1100
    assert s2t < 5000   # 上界：不应意外膨胀
    assert t2s < 5000


# ===================== 集成：极快侧调用 =====================

def test_极快侧简转繁():
    result = run_source('导入 简繁。\n简繁.转繁体("国家标准")。')
    assert result == '國家標準'


def test_极快侧繁转简():
    result = run_source('导入 简繁。\n简繁.转简体("國家標準")。')
    assert result == '国家标准'
