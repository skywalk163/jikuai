# -*- coding: utf-8 -*-
"""v0.5.0 · M4-P1 诊断内核契约测试（G9 门禁）。

覆盖任务：
    T-M4-D01  diagnostics 包对外符号
    T-M4-D02  数据模型（Position / Span / Suggestion / Diagnostic）
    T-M4-D03  错误码表 codes.py
    T-M4-D04  Sink 协议与 ListSink / NullSink

覆盖 AC 与门禁：
    AC-M4-01-03 诊断可复现（sort_key 决定性 + drain 稳定排序）
    G8         回退开关 JIKUAI_DIAGNOSTICS=off 有守护
    G9         诊断内核字段完整、码表分段合法、兼容红线（errors.py 公开符号保留）

这些用例只测契约本身，**不**依赖 lexer/parser/evaluator，因此可在
诊断内核合入后立即绿灯，不受 D05..D08 进度影响。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ---------------------------------------------------------------------------
# T-M4-D01 · 包对外符号
# ---------------------------------------------------------------------------

def test_diagnostics_package_exports():
    """诊断包的 __all__ 至少覆盖数据模型 + Sink + codes 子模块。"""
    from jikuai import diagnostics as d

    expected = {
        'Diagnostic', 'Position', 'Span', 'Suggestion',
        'Severity', 'SEVERITY_ERROR', 'SEVERITY_WARNING', 'SEVERITY_HINT',
        'DiagnosticSink', 'ListSink', 'NullSink', 'make_default_sink',
        'codes',
    }
    assert expected.issubset(set(d.__all__))
    # 每个符号都能真的取到
    for name in expected:
        assert getattr(d, name) is not None


def test_diagnostics_does_not_import_evaluator():
    """诊断层不得依赖 evaluator（否则与运行时循环耦合，见基线偏差 A）。

    这里做**静态源码扫描**而非运行期 sys.modules 检查：`import
    jikuai.diagnostics` 必然先执行 `jikuai/__init__.py`，而后者为了导出嵌入
    API 会 `from .evaluator import JiKuaiError`，因此运行期 sys.modules 里
    一定有 evaluator，无法据此判断耦合。真正要守住的是
    `diagnostics/` 自身的源码不出现对 evaluator 的引用。
    """
    import ast as pyast

    pkg_dir = os.path.join(
        os.path.dirname(__file__), '..', 'src', 'jikuai', 'diagnostics'
    )
    offenders = []
    for name in os.listdir(pkg_dir):
        if not name.endswith('.py'):
            continue
        path = os.path.join(pkg_dir, name)
        with open(path, 'r', encoding='utf-8') as f:
            tree = pyast.parse(f.read(), filename=path)
        for node in pyast.walk(tree):
            if isinstance(node, pyast.ImportFrom):
                if node.module and 'evaluator' in node.module:
                    offenders.append(f"{name}: from {node.module} import ...")
            elif isinstance(node, pyast.Import):
                for alias in node.names:
                    if 'evaluator' in alias.name:
                        offenders.append(f"{name}: import {alias.name}")

    assert not offenders, (
        "diagnostics/ 引用了 evaluator，违反 ADR-14 循环耦合红线：\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# T-M4-D02 · 数据模型
# ---------------------------------------------------------------------------

def test_position_is_frozen_and_1_based():
    from jikuai.diagnostics import Position
    p = Position(line=1, column=1)
    assert (p.line, p.column) == (1, 1)
    with pytest.raises(Exception):
        # frozen dataclass 禁止赋值
        p.line = 2  # type: ignore[misc]


def test_position_rejects_zero_or_negative():
    from jikuai.diagnostics import Position
    with pytest.raises(ValueError):
        Position(line=0, column=1)
    with pytest.raises(ValueError):
        Position(line=1, column=0)


def test_span_point_and_end_after_start():
    from jikuai.diagnostics import Position, Span
    p = Span.point(3, 5, file="a.jk")
    assert p.start == p.end == Position(3, 5)
    assert p.file == "a.jk"

    # end 严格早于 start 应报错
    with pytest.raises(ValueError):
        Span(start=Position(2, 3), end=Position(2, 1))


def test_diagnostic_required_fields_and_defaults():
    from jikuai.diagnostics import (
        Diagnostic, Span, SEVERITY_WARNING,
    )
    from jikuai.errors import ErrorCategory

    d = Diagnostic(
        code="JK-W1001",
        severity=SEVERITY_WARNING,
        category=ErrorCategory.SYNTAX,
        message="副词内部遇到未知动词",
        span=Span.point(1, 1),
    )
    # 字段默认值
    assert d.subject is None
    assert d.suggestions == ()
    assert d.notes == ()


def test_diagnostic_sort_key_is_deterministic():
    """sort_key 稳定排序：不同 file/line/column/code 均可比较，不出现 None 参与比较。"""
    from jikuai.diagnostics import (
        Diagnostic, Span, SEVERITY_ERROR,
    )
    from jikuai.errors import ErrorCategory

    def mk(code, line, col, file):
        return Diagnostic(
            code=code, severity=SEVERITY_ERROR,
            category=ErrorCategory.NAME, message="x",
            span=Span.point(line, col, file=file),
        )

    diags = [
        mk("JK-E2002", 3, 1, "b.jk"),
        mk("JK-E2001", 2, 5, None),      # file=None 仍可参与排序
        mk("JK-E2001", 2, 5, "a.jk"),
        mk("JK-E2001", 2, 1, "a.jk"),
    ]
    keys = [d.sort_key() for d in diags]
    # 排序应稳定且不抛异常
    sorted_diags = sorted(diags, key=Diagnostic.sort_key)
    # None → "" 后 "" < "a.jk" < "b.jk"
    assert sorted_diags[0].span.file is None
    assert sorted_diags[-1].span.file == "b.jk"
    # 决定性：多次排序等价
    for _ in range(3):
        assert sorted(diags, key=Diagnostic.sort_key) == sorted_diags


def test_diagnostic_rejects_bad_code_and_severity():
    from jikuai.diagnostics import Diagnostic, Span
    from jikuai.errors import ErrorCategory

    with pytest.raises(ValueError):
        Diagnostic(code="BAD-001", severity="错误",
                   category=ErrorCategory.NAME, message="x", span=Span.point(1, 1))
    with pytest.raises(ValueError):
        Diagnostic(code="JK-E2001", severity="fatal",  # type: ignore[arg-type]
                   category=ErrorCategory.NAME, message="x", span=Span.point(1, 1))


# ---------------------------------------------------------------------------
# T-M4-D03 · 错误码表
# ---------------------------------------------------------------------------

def test_code_table_segment_boundaries():
    """所有登记错误码段位在 0..9 之间，且序号在 0..999。"""
    from jikuai.diagnostics import codes
    for code, info in codes.CODE_TABLE.items():
        seg = codes.segment_of(code)
        assert 0 <= seg <= 9, f"{code} 段位越界"
        num = int(code[4:])
        assert 0 <= num % 1000 <= 999, f"{code} 序号越界"
        assert info.code == code
        assert info.template  # 非空模板


def test_code_segment_semantic_mapping():
    """段位与关键码对齐：JK-W1001 在 1 段，JK-E2002 在 2 段，等。"""
    from jikuai.diagnostics import codes
    assert codes.segment_of(codes.JK_W1001) == 1
    assert codes.segment_of(codes.JK_E2002) == 2
    assert codes.segment_of(codes.JK_E5001) == 5
    assert codes.segment_of(codes.JK_E5002) == 5
    assert codes.segment_of(codes.JK_W9001) == 9


def test_is_valid_code_and_reject_malformed():
    from jikuai.diagnostics import codes
    assert codes.is_valid_code("JK-E0001")
    assert codes.is_valid_code("JK-W9999")
    assert not codes.is_valid_code("XX-E0001")
    assert not codes.is_valid_code("JK-Z0001")
    assert not codes.is_valid_code("JK-E00001")
    assert not codes.is_valid_code("JK-E1")


def test_all_codes_have_unique_values():
    """码只增不改不复用：每个常量的字符串值在表中唯一。"""
    from jikuai.diagnostics import codes
    seen = {}
    for name in dir(codes):
        if name.startswith("JK_"):
            value = getattr(codes, name)
            assert value not in seen, f"{name} 与 {seen[value]} 共用同一码 {value}"
            seen[value] = name


# ---------------------------------------------------------------------------
# T-M4-D04 · Sink
# ---------------------------------------------------------------------------

def _sample_diag(code="JK-E2001", line=1, col=1, file=None):
    from jikuai.diagnostics import Diagnostic, Span, SEVERITY_ERROR
    from jikuai.errors import ErrorCategory
    return Diagnostic(
        code=code, severity=SEVERITY_ERROR,
        category=ErrorCategory.NAME, message="x",
        span=Span.point(line, col, file=file),
    )


def test_list_sink_drain_sorts_and_clears():
    from jikuai.diagnostics import ListSink
    sink = ListSink()
    sink.emit(_sample_diag(code="JK-E2002", line=3, col=1, file="a.jk"))
    sink.emit(_sample_diag(code="JK-E2001", line=1, col=5, file="a.jk"))
    sink.emit(_sample_diag(code="JK-E2001", line=1, col=1, file="a.jk"))

    out = sink.drain()
    # 排序按 (file, line, column, code)
    assert [d.span.start.line for d in out] == [1, 1, 3]
    assert [d.span.start.column for d in out] == [1, 5, 1]
    # drain 清空
    assert sink.drain() == []


def test_list_sink_drain_is_reproducible():
    """AC-M4-01-03：同批诊断两次 drain 结果字段完全一致（可复现）。"""
    from jikuai.diagnostics import ListSink

    def run():
        s = ListSink()
        for d in [
            _sample_diag(code="JK-E2001", line=2, col=3, file="a.jk"),
            _sample_diag(code="JK-W1001", line=1, col=1, file="a.jk"),
        ]:
            s.emit(d)
        return s.drain()

    r1, r2 = run(), run()
    assert r1 == r2


def test_list_sink_peek_does_not_clear():
    from jikuai.diagnostics import ListSink
    s = ListSink()
    s.emit(_sample_diag())
    assert len(s.peek()) == 1
    assert len(s) == 1
    assert len(s.drain()) == 1
    assert len(s) == 0


def test_null_sink_discards_everything():
    from jikuai.diagnostics import NullSink
    sink = NullSink()
    for _ in range(5):
        sink.emit(_sample_diag())
    # NullSink 无 drain，但也不该抛异常
    assert isinstance(sink, NullSink)


def test_default_sink_respects_env_off(monkeypatch):
    """G8 守护：JIKUAI_DIAGNOSTICS=off → NullSink。"""
    from jikuai.diagnostics import make_default_sink, ListSink, NullSink

    monkeypatch.delenv("JIKUAI_DIAGNOSTICS", raising=False)
    assert isinstance(make_default_sink(), ListSink)

    monkeypatch.setenv("JIKUAI_DIAGNOSTICS", "off")
    assert isinstance(make_default_sink(), NullSink)

    monkeypatch.setenv("JIKUAI_DIAGNOSTICS", "OFF")
    assert isinstance(make_default_sink(), NullSink)

    monkeypatch.setenv("JIKUAI_DIAGNOSTICS", "on")
    assert isinstance(make_default_sink(), ListSink)


def test_sink_protocol_duck_typing():
    """DiagnosticSink 是 Protocol：任何有 emit 方法的对象都算实现。"""
    from jikuai.diagnostics import DiagnosticSink, ListSink

    class Custom:
        def __init__(self): self.received = []
        def emit(self, d): self.received.append(d)

    c = Custom()
    assert isinstance(c, DiagnosticSink)  # runtime_checkable
    c.emit(_sample_diag())
    assert len(c.received) == 1

    assert isinstance(ListSink(), DiagnosticSink)


# ---------------------------------------------------------------------------
# G9 兼容红线：errors.py 现有公开符号必须原样保留
# ---------------------------------------------------------------------------

def test_errors_module_public_symbols_preserved():
    """诊断内核合入后，errors.py 的既有公开符号一个都不能少。"""
    from jikuai import errors
    from jikuai.errors import (
        ErrorCategory, ErrorInfo, ErrorFormatter, spelling_suggestion,
    )
    from jikuai import JiKuaiError  # 从包顶层导出

    # 5 个原始成员必须都在
    for member in ("LEXER", "SYNTAX", "NAME", "TYPE", "RUNTIME"):
        assert hasattr(ErrorCategory, member)

    # ErrorInfo 字段
    info = ErrorInfo(
        category=ErrorCategory.NAME,
        message="x", line=1, col=1,
    )
    assert info.line == 1
    formatted = ErrorFormatter.format(info)
    assert "第 1 行" in formatted

    # spelling_suggestion 保持单候选签名（兼容外壳，见基线偏差 B）
    assert spelling_suggestion("打因", ["打印", "抛出"]) == "打印"
    assert spelling_suggestion("完全不同", ["打印"]) is None

    # JiKuaiError 依然可从顶层拿到
    assert issubclass(JiKuaiError, Exception)

    # errors 模块本身依然可以 import
    assert errors is not None
