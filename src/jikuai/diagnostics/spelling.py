# -*- coding: utf-8 -*-
"""极快语言 · 拼写纠错（ADR-14 · US-M4-02）。

与 `errors.spelling_suggestion`（单候选、`max_distance=1`）的关系：

    - `errors.spelling_suggestion` 是 v0.4.x 起的既有公开 API，**签名与语义
      保持原样不动**（见《基线校正说明》偏差 B），仅作兼容外壳。
    - 本模块提供新的多候选能力：编辑距离阈值默认 **2**，返回按确定性规则
      排序的 `Suggestion` 元组，供 `Diagnostic.suggestions` 直接使用。

排序与上限规则（AC-M4-02-02 要求文档声明，此处为权威定义）：

    1. 先按编辑距离升序（距离最小者优先）。
    2. 距离相同时按候选文本的 Unicode 码点序升序（保证跨平台结果一致，
       不受字典哈希顺序影响）。
    3. 最多返回 ``MAX_SUGGESTIONS`` 条（默认 3）。**并列候选不会被截断到
       只剩一条**：若第 N 条与第 N+1 条距离相同，则同距离的候选整组保留，
       即实际返回条数可以超过 MAX_SUGGESTIONS，以满足「并列候选全部列出」。

超出阈值即不给建议（AC-M4-02-03）：距离 > `max_distance` 的候选一律不入选，
避免产生噪声建议。
"""

from __future__ import annotations

from typing import Iterable, List, Tuple

from ..errors import _levenshtein
from .model import Suggestion

#: 建议条数软上限。并列（同距离）候选整组保留，故实际条数可能略超。
MAX_SUGGESTIONS = 3

#: 默认编辑距离阈值（US-M4-02 要求 ≤ 2）。
DEFAULT_MAX_DISTANCE = 2


def suggest(
    name: str,
    candidates: Iterable[str],
    max_distance: int = DEFAULT_MAX_DISTANCE,
    limit: int = MAX_SUGGESTIONS,
) -> Tuple[Suggestion, ...]:
    """返回 `name` 的拼写建议元组，按 (距离, 文本) 确定性排序。

    - 距离 > `max_distance` 的候选被丢弃（AC-M4-02-03）。
    - 与 `name` 完全相同的候选被丢弃（距离 0 不是"建议"）。
    - `limit` 是软上限：截断时若末位存在并列距离，整组保留（AC-M4-02-02）。
    """
    scored: List[Tuple[int, str]] = []
    for c in candidates:
        if c == name:
            continue
        d = _levenshtein(name, c)
        if d <= max_distance:
            scored.append((d, c))

    if not scored:
        return ()

    scored.sort(key=lambda pair: (pair[0], pair[1]))

    if limit is not None and limit > 0 and len(scored) > limit:
        # 软截断：保留到 limit，但若第 limit 位与第 limit-1 位同距离，
        # 则把同距离的并列候选整组带上，避免"并列候选只列一半"。
        cutoff_distance = scored[limit - 1][0]
        kept = [pair for pair in scored if pair[0] <= cutoff_distance]
        scored = kept

    return tuple(Suggestion(text=text, distance=dist) for dist, text in scored)


def best(
    name: str,
    candidates: Iterable[str],
    max_distance: int = DEFAULT_MAX_DISTANCE,
) -> Suggestion | None:
    """返回距离最小的单条建议（并列时取文本码点序最小者），无则 None。"""
    got = suggest(name, candidates, max_distance=max_distance, limit=1)
    return got[0] if got else None


def format_suggestions(suggestions: Tuple[Suggestion, ...]) -> str:
    """把建议元组渲染为用户可读的一行提示。

    文案（v0.5.0 起，裁决 D-03）：``您是否想输入 `打印`？``
    多候选时用「或」连接：``您是否想输入 `打印` 或 `抛出`？``

    注意：**文案不是稳定契约**，测试请断言 `Diagnostic.suggestions` 结构，
    不要匹配这里的字符串（ADR-14 硬约束）。
    """
    if not suggestions:
        return ""
    quoted = [f"`{s.text}`" for s in suggestions]
    if len(quoted) == 1:
        body = quoted[0]
    else:
        body = " 或 ".join(quoted)
    return f"您是否想输入 {body}？"
