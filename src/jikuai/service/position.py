# -*- coding: utf-8 -*-
"""极快语言 · service 层 · 位置换算（M5 · T-M5-L02）。

LSP 使用 0-based 行 + 0-based UTF-16 code unit 列；
极快内部使用 1-based 行 + 1-based Unicode 码点列。
本模块提供两者之间的双向换算。

BMP 外字符（码点 > 0xFFFF，如 emoji 🐍、生僻汉字 𠀀）在 UTF-16
编码中占 2 个 code unit（代理对），需要特殊处理。
"""

from __future__ import annotations


def codepoint_to_utf16(line_text: str, codepoint_col: int) -> int:
    """1-based 码点列 → 0-based UTF-16 code unit 列。

    参数：
        line_text: 该行的完整文本（不含换行符）
        codepoint_col: 1-based 码点列号

    返回：
        0-based UTF-16 code unit 偏移

    边界处理：
        - codepoint_col <= 0 → 返回 0
        - codepoint_col 超过行长度 → 返回行文本的 UTF-16 总宽度
        - 空行 → 返回 0
    """
    if not line_text or codepoint_col <= 0:
        return 0
    # 取前 (codepoint_col - 1) 个码点的前缀
    zero_based = codepoint_col - 1
    prefix = line_text[:zero_based]
    # 每个 BMP 外字符占 2 个 UTF-16 code unit，BMP 内占 1 个
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in prefix)


def utf16_to_codepoint(line_text: str, utf16_col: int) -> int:
    """0-based UTF-16 code unit 列 → 1-based 码点列。

    参数：
        line_text: 该行的完整文本（不含换行符）
        utf16_col: 0-based UTF-16 code unit 偏移

    返回：
        1-based 码点列号

    边界处理：
        - utf16_col <= 0 → 返回 1
        - utf16_col 超过行文本 UTF-16 总宽度 → 返回 len(line_text) + 1
        - 空行 → 返回 1
    """
    if not line_text or utf16_col <= 0:
        return 1
    consumed = 0
    for i, ch in enumerate(line_text):
        if consumed >= utf16_col:
            return i + 1
        consumed += 2 if ord(ch) > 0xFFFF else 1
    # utf16_col 在行末或超出 → 返回行尾后一位
    return len(line_text) + 1
