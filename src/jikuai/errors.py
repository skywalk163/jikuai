# -*- coding: utf-8 -*-
"""极快语言 - 错误定位与格式化。"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List


class ErrorCategory(Enum):
    LEXER = "词法错误"
    SYNTAX = "语法错误"
    NAME = "名称错误"
    TYPE = "类型错误"
    RUNTIME = "运行错误"


@dataclass
class ErrorInfo:
    """结构化错误信息。"""
    category: ErrorCategory
    message: str
    line: int              # 1-based
    col: int               # 1-based, code point 序号
    source_line: str = ""  # 该行源码原文
    suggestion: Optional[str] = None


class ErrorFormatter:
    """将 ErrorInfo 格式化为用户友好的多行错误报告。"""

    @staticmethod
    def format(info: ErrorInfo) -> str:
        parts = []
        # 第 N 行，第 M 列：<类别>：<消息>
        parts.append(f"第 {info.line} 行，第 {info.col} 列：{info.category.value}：{info.message}")
        # 源码原文
        if info.source_line:
            parts.append(f"  {info.source_line}")
            # 指示符（^）
            # col 是 1-based code point 序号，需要对齐到显示位置
            pointer = "  " + " " * (info.col - 1) + "^"
            parts.append(pointer)
        # 建议
        if info.suggestion:
            parts.append(f"建议：是否想输入 \"{info.suggestion}\"？")
        return "\n".join(parts)


def spelling_suggestion(name: str, candidates: List[str], max_distance: int = 1) -> Optional[str]:
    """基于 Levenshtein 编辑距离给出拼写建议。

    返回编辑距离 <= max_distance 的最近候选，否则 None。
    """
    best = None
    best_dist = max_distance + 1
    for c in candidates:
        d = _levenshtein(name, c)
        if d <= max_distance and d < best_dist:
            best = c
            best_dist = d
    return best


def _levenshtein(s: str, t: str) -> int:
    """计算两个字符串的 Levenshtein 编辑距离。"""
    if len(s) < len(t):
        return _levenshtein(t, s)
    if len(t) == 0:
        return len(s)
    prev = list(range(len(t) + 1))
    for i, sc in enumerate(s):
        curr = [i + 1]
        for j, tc in enumerate(t):
            if sc == tc:
                curr.append(prev[j])
            else:
                curr.append(1 + min(prev[j], prev[j + 1], curr[j]))
        prev = curr
    return prev[-1]
