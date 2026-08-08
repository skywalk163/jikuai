# -*- coding: utf-8 -*-
"""极快语言 · 诊断渲染（ADR-14 · CLI 侧纯投影）。

本模块只做 `Diagnostic` → 字符串 / dict 的**纯投影**，不含任何判断逻辑，
不生成新的语义。LSP 侧的投影在 `adapters.to_lsp_diagnostic`，两者共用
同一个 `Diagnostic` 真源，因此 CLI 与编辑器看到的内容必然一致。

文本格式（与 v0.4.x `ErrorFormatter.format` 保持同构，便于平滑过渡）：

    第 3 行，第 5 列：语法错误：副词 '皆' 内部遇到未知动词 '大'
      列1 2 3，皆大。
          ^
    您是否想输入 `大于`？
    [JK-W1001]
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from .model import Diagnostic, to_lsp_severity
from .spelling import format_suggestions


def render_text(
    diagnostic: Diagnostic,
    source_lines: Optional[Sequence[str]] = None,
    show_code: bool = True,
) -> str:
    """把诊断渲染为多行中文文本报告。

    `source_lines` 为按行切分的源码（不含换行符）；提供时会附上出错行原文
    与 ``^`` 指示符。缺省则只输出定位头与消息。
    """
    d = diagnostic
    parts: List[str] = [
        f"第 {d.span.start.line} 行，第 {d.span.start.column} 列："
        f"{d.category.value}：{d.message}"
    ]

    source_line = ""
    if source_lines is not None:
        idx = d.span.start.line - 1
        if 0 <= idx < len(source_lines):
            source_line = source_lines[idx]
    if source_line:
        parts.append(f"  {source_line}")
        parts.append("  " + " " * (d.span.start.column - 1) + "^")

    hint = format_suggestions(d.suggestions)
    if hint:
        parts.append(hint)

    for note in d.notes:
        parts.append(f"说明：{note}")

    if show_code:
        parts.append(f"[{d.code}]")

    return "\n".join(parts)


def render_json(diagnostic: Diagnostic) -> Dict:
    """把诊断投影为可 JSON 序列化的 dict（机器消费口径，如 CI / 编辑器）。

    坐标沿用极快口径：1-based 行、1-based Unicode 码点列。
    需要 LSP 的 0-based UTF-16 位置时请走 `adapters.to_lsp_diagnostic`。
    """
    d = diagnostic
    return {
        "code": d.code,
        "severity": d.severity,
        "severityLsp": to_lsp_severity(d.severity),
        "category": d.category.name,
        "categoryText": d.category.value,
        "message": d.message,
        "subject": d.subject,
        "file": d.span.file,
        "range": {
            "start": {"line": d.span.start.line, "column": d.span.start.column},
            "end": {"line": d.span.end.line, "column": d.span.end.column},
        },
        "suggestions": [
            {"text": s.text, "distance": s.distance} for s in d.suggestions
        ],
        "notes": list(d.notes),
    }


def render_all_text(
    diagnostics: Sequence[Diagnostic],
    source_lines: Optional[Sequence[str]] = None,
) -> str:
    """批量渲染，诊断之间用空行分隔。顺序由调用方（通常是 ListSink.drain）决定。"""
    return "\n\n".join(
        render_text(d, source_lines=source_lines) for d in diagnostics
    )
