# -*- coding: utf-8 -*-
"""极快语言 · 诊断内核（v0.5.0 · ADR-14）。

本包是极快语言诊断信息的**唯一真源**。CLI 与 LSP 均为纯投影消费者，
不得各自造消息文本或语义。

对外契约（F1 冻结点，v0.5.0 M4 末生效）：
    - Position / Span / Suggestion / Diagnostic 数据模型
    - Severity 字符串枚举（"错误" / "警告" / "提示"）
    - DiagnosticSink 协议 + ListSink / NullSink 两个内建实现
    - codes 模块：错误码常量集合与元数据表

设计红线：
    - 本包**不得** import `evaluator`（`JiKuaiError` 定义在 evaluator 中），
      避免与运行时循环耦合。
    - 错误码是稳定契约，一经发布只增不改不复用；渲染文案不是契约。
      测试应断言 `Diagnostic.code` 与结构化字段，而非匹配文案字符串。
"""

from .model import (
    Diagnostic,
    Position,
    Severity,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    SEVERITY_HINT,
    Span,
    Suggestion,
    to_lsp_severity,
)
from .sink import DiagnosticSink, ListSink, NullSink, make_default_sink
from .spelling import (
    DEFAULT_MAX_DISTANCE,
    MAX_SUGGESTIONS,
    best,
    format_suggestions,
    suggest,
)
from .reporter import render_all_text, render_json, render_text
from .adapters import from_error_info, to_error_info, to_lsp_diagnostic
from .static_check import check_program
from . import codes

__all__ = [
    # 数据模型
    'Diagnostic', 'Position', 'Span', 'Suggestion',
    'Severity', 'SEVERITY_ERROR', 'SEVERITY_WARNING', 'SEVERITY_HINT',
    'to_lsp_severity',
    # Sink
    'DiagnosticSink', 'ListSink', 'NullSink', 'make_default_sink',
    # 拼写建议
    'suggest', 'best', 'format_suggestions',
    'DEFAULT_MAX_DISTANCE', 'MAX_SUGGESTIONS',
    # 渲染 / 投影
    'render_text', 'render_json', 'render_all_text',
    'from_error_info', 'to_error_info', 'to_lsp_diagnostic',
    # 静态检查
    'check_program',
    # 子模块
    'codes',
]
