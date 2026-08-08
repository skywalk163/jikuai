# -*- coding: utf-8 -*-
"""v0.5.0 · T-M4-D05 拼写纠错测试（US-M4-02）。

覆盖 AC：
    AC-M4-02-01  编辑距离 ≤2 → 给出建议，且为距离最小者
    AC-M4-02-02  并列候选全部列出；上限与排序规则与文档一致
    AC-M4-02-03  距离 >2 → 不给建议（无噪声）

按 ADR-14「错误码是稳定契约，渲染文案不是」，这里以断言
`Suggestion` 结构（text / distance / 顺序）为主，文案只做弱断言。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.diagnostics import spelling
from jikuai.keywords import VERB_ARITY


# ---------------------------------------------------------------------------
# AC-M4-02-01 · 距离 ≤2 给建议，且为最小者
# ---------------------------------------------------------------------------

def test_ac_m4_02_01_suggests_nearest_builtin_verb():
    """"打因" 与内建动词 "打印" 距离 1，应作为首个建议给出。"""
    got = spelling.suggest("打因", VERB_ARITY.keys())
    assert got, "距离 1 的候选必须给出建议"
    assert got[0].text == "打印"
    assert got[0].distance == 1
    # 首个建议必须是全局距离最小者
    assert got[0].distance == min(s.distance for s in got)


def test_ac_m4_02_01_distance_two_still_suggested():
    """距离恰好 2 仍在阈值内。"""
    got = spelling.suggest("abc", ["abcde", "zzzzz"])
    assert [s.text for s in got] == ["abcde"]
    assert got[0].distance == 2


def test_ac_m4_02_01_best_returns_single_nearest():
    got = spelling.best("打因", VERB_ARITY.keys())
    assert got is not None
    assert got.text == "打印"


# ---------------------------------------------------------------------------
# AC-M4-02-02 · 并列候选全部列出 + 排序规则确定
# ---------------------------------------------------------------------------

def test_ac_m4_02_02_ties_are_all_listed():
    """同距离的并列候选必须整组保留，不能只列一条。"""
    # 三个候选与 "aX" 距离都是 1
    got = spelling.suggest("aX", ["ab", "ac", "ad"])
    assert {s.text for s in got} == {"ab", "ac", "ad"}
    assert all(s.distance == 1 for s in got)


def test_ac_m4_02_02_tie_group_not_truncated_by_limit():
    """limit 是软上限：末位存在并列时整组带上，实际条数可超过 limit。"""
    candidates = ["ab", "ac", "ad", "ae", "af"]   # 与 "aX" 距离均为 1
    got = spelling.suggest("aX", candidates, limit=2)
    # 若硬截断会只剩 2 条；软截断应保留全部同距离候选
    assert len(got) == len(candidates)
    assert all(s.distance == 1 for s in got)


def test_ac_m4_02_02_sort_rule_distance_then_codepoint():
    """排序规则：距离升序 → 文本 Unicode 码点序升序（跨平台确定）。"""
    # "bc" 距离 1；"abcd" 距离 2
    got = spelling.suggest("ac", ["abcd", "bc", "ad"])
    distances = [s.distance for s in got]
    assert distances == sorted(distances), "距离必须升序"
    # 同距离组内按码点序
    d1 = [s.text for s in got if s.distance == 1]
    assert d1 == sorted(d1)


def test_ac_m4_02_02_documented_defaults():
    """上限与阈值的默认值是文档声明的一部分，锁死防漂移。"""
    assert spelling.DEFAULT_MAX_DISTANCE == 2
    assert spelling.MAX_SUGGESTIONS == 3


def test_suggest_is_deterministic_across_calls():
    """同输入多次调用结果完全一致（服务 AC-M4-01-03 的可复现性）。"""
    args = ("打因", list(VERB_ARITY.keys()))
    first = spelling.suggest(*args)
    for _ in range(3):
        assert spelling.suggest(*args) == first


# ---------------------------------------------------------------------------
# AC-M4-02-03 · 距离 >2 不给建议
# ---------------------------------------------------------------------------

def test_ac_m4_02_03_no_suggestion_beyond_threshold():
    got = spelling.suggest("完全不相干的一长串", VERB_ARITY.keys())
    assert got == (), "距离全部 >2 时不得给出任何建议"
    assert spelling.best("完全不相干的一长串", VERB_ARITY.keys()) is None


def test_exact_match_is_not_a_suggestion():
    """候选与输入完全相同（距离 0）不算"建议"。"""
    got = spelling.suggest("打印", VERB_ARITY.keys())
    assert all(s.text != "打印" for s in got)


def test_empty_candidates_returns_empty():
    assert spelling.suggest("打因", []) == ()


# ---------------------------------------------------------------------------
# 文案渲染（弱断言：文案非契约）
# ---------------------------------------------------------------------------

def test_format_suggestions_single_and_multi():
    from jikuai.diagnostics import Suggestion

    one = spelling.format_suggestions((Suggestion("打印", 1),))
    assert "打印" in one
    assert "是否想输入" in one

    two = spelling.format_suggestions(
        (Suggestion("打印", 1), Suggestion("抛出", 1))
    )
    assert "打印" in two and "抛出" in two

    assert spelling.format_suggestions(()) == ""
