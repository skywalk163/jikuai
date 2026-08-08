# -*- coding: utf-8 -*-
"""极快语言 · 诊断适配层（ADR-14）。

三类投影，全部是纯函数：

    - `from_error_info` / `to_error_info`：与 v0.4.x 既有 `ErrorInfo` 互转，
      让旧调用路径（`ParseError.info` / `JiKuaiError.info`）可以无痛接入
      新内核，同时保证嵌入 API 的兼容红线不破。
    - `to_lsp_diagnostic`：投影为 LSP `Diagnostic` 字典。

坐标口径转换说明：
    极快内部用 **1-based 行 + 1-based Unicode 码点列**；
    LSP 用 **0-based 行 + 0-based UTF-16 code unit 列**。
    行的 -1 偏移在本层完成；列的 UTF-16 换算需要行文本，故通过
    `line_text_provider` 回调注入（由 L3 `service/` 层在 M5 提供）。
    未提供回调时退化为「码点数 - 1」，对 BMP 内字符（含全部常用汉字）等价。
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

from ..errors import ErrorCategory, ErrorInfo
from .codes import CODE_TABLE
from .model import Diagnostic, Position, Span, Suggestion, to_lsp_severity
from .spelling import format_suggestions

#: 给定 1-based 行号返回该行文本；用于 UTF-16 列换算。
LineTextProvider = Callable[[int], Optional[str]]


def from_error_info(
    info: ErrorInfo,
    code: str,
    file: Optional[str] = None,
    end_column: Optional[int] = None,
) -> Diagnostic:
    """把旧的 `ErrorInfo` 提升为 `Diagnostic`。

    `ErrorInfo` 没有结束位置，故 `end_column` 缺省时构造零宽 Span。
    severity / category 优先取码表登记值，保证与码表一致。
    """
    meta = CODE_TABLE.get(code)
    severity = meta.severity if meta else "错误"
    category = meta.category if meta else info.category

    start = Position(info.line, info.col)
    end = Position(info.line, end_column) if end_column else start

    suggestions: Tuple[Suggestion, ...] = ()
    if info.suggestion:
        suggestions = (Suggestion(text=info.suggestion, distance=1),)

    return Diagnostic(
        code=code,
        severity=severity,
        category=category,
        message=info.message,
        span=Span(start=start, end=end, file=file),
        suggestions=suggestions,
    )


def to_error_info(diagnostic: Diagnostic, source_line: str = "") -> ErrorInfo:
    """把 `Diagnostic` 降级为 `ErrorInfo`，供既有嵌入 API 消费。

    多候选建议在降级时只保留第一条（`ErrorInfo.suggestion` 是单值字段），
    这是有意的信息损失——需要完整候选请直接消费 `Diagnostic.suggestions`。
    """
    return ErrorInfo(
        category=diagnostic.category,
        message=diagnostic.message,
        line=diagnostic.span.start.line,
        col=diagnostic.span.start.column,
        source_line=source_line,
        suggestion=(
            diagnostic.suggestions[0].text if diagnostic.suggestions else None
        ),
    )


def _to_utf16_column(
    line: int, column: int, line_text_provider: Optional[LineTextProvider]
) -> int:
    """1-based 码点列 → 0-based UTF-16 code unit 列。"""
    zero_based = column - 1
    if line_text_provider is None:
        return zero_based
    text = line_text_provider(line)
    if text is None:
        return zero_based
    prefix = text[:zero_based]
    # 每个 BMP 外字符（如部分生僻字、emoji）占 2 个 UTF-16 code unit
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in prefix)


def to_lsp_diagnostic(
    diagnostic: Diagnostic,
    line_text_provider: Optional[LineTextProvider] = None,
) -> Dict:
    """投影为 LSP `Diagnostic` 字典（0-based 行 + UTF-16 列）。

    `relatedInformation` 与 `codeDescription` 暂不填充，留待 M5 LSP 正式实现。
    """
    d = diagnostic
    message = d.message
    hint = format_suggestions(d.suggestions)
    if hint:
        message = f"{message}\n{hint}"

    return {
        "range": {
            "start": {
                "line": d.span.start.line - 1,
                "character": _to_utf16_column(
                    d.span.start.line, d.span.start.column, line_text_provider
                ),
            },
            "end": {
                "line": d.span.end.line - 1,
                "character": _to_utf16_column(
                    d.span.end.line, d.span.end.column, line_text_provider
                ),
            },
        },
        "severity": to_lsp_severity(d.severity),
        "code": d.code,
        "source": "jikuai",
        "message": message,
    }
