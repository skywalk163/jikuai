# -*- coding: utf-8 -*-
"""v0.5.0 · T-M4-D06/D07 静态诊断与投影测试（US-M4-01 · G9）。

覆盖 AC：
    AC-M4-01-01  副词内部非内建动词 → JK-W1001，含副词名与位置
    AC-M4-01-02  正常源码不产生 JK-W1001（无误报）
    AC-M4-01-03  同源码两次检查输出完全一致（可复现）

以及诊断投影（reporter / adapters）的纯函数行为。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.diagnostics import (
    ListSink,
    Position,
    Span,
    check_program,
    codes,
    render_json,
    render_text,
    to_lsp_diagnostic,
)
from jikuai.lexer import tokenize
from jikuai.parser import parse


def _diagnose(source: str, file=None):
    """编译源码并跑静态检查，返回稳定排序后的诊断列表。"""
    ast = parse(tokenize(source))
    sink = ListSink()
    check_program(ast, sink, file=file)
    return sink.drain()


# ---------------------------------------------------------------------------
# AC-M4-01-01 · 副词透传诊断
# ---------------------------------------------------------------------------

def test_ac_m4_01_01_adverb_unknown_verb_warns():
    """"皆" 后接非内建动词 "大" → JK-W1001，消息含副词名与该动词名。"""
    diags = _diagnose('列 1 2 3，皆大。')
    w1001 = [d for d in diags if d.code == codes.JK_W1001]
    assert w1001, f"应产生 JK-W1001，实际得到 {[d.code for d in diags]}"

    d = w1001[0]
    assert d.severity == "警告"
    assert d.subject == "皆"          # 触发主体是副词名
    assert "皆" in d.message
    assert "大" in d.message
    # 位置字段存在且合法（1-based）
    assert d.span.start.line >= 1
    assert d.span.start.column >= 1
    # notes 提供可操作的说明
    assert d.notes and any("内建动词" in n for n in d.notes)


def test_ac_m4_01_01_span_carries_file():
    diags = _diagnose('列 1 2 3，皆大。', file="示例.jk")
    w1001 = [d for d in diags if d.code == codes.JK_W1001]
    assert w1001
    assert w1001[0].span.file == "示例.jk"


def test_adverb_with_user_function_also_warns():
    """副词只识别内建动词，用户函数同样会被原值透传，须给出提示。"""
    source = (
        '函数 王甲：接收 赵数：\n'
        '  返回 乘 赵数 2。\n'
        '。\n'
        '列 1 2 3，皆王甲。\n'
    )
    diags = _diagnose(source)
    assert any(d.code == codes.JK_W1001 for d in diags)


# ---------------------------------------------------------------------------
# AC-M4-01-02 · 无误报
# ---------------------------------------------------------------------------

def test_ac_m4_01_02_builtin_verb_adverb_no_warning():
    """副词接内建动词（乘 / 大于 / 加）是正常用法，不得报警。"""
    for source in (
        '列 1 2 3，皆乘 2。',
        '列 1 2 3 4 5，只大于 2。',
        '列 1 2 3，归加 0。',
        '列 1 2 3 4 5，皆乘2，只大于6，归加0。',
    ):
        diags = _diagnose(source)
        assert not [d for d in diags if d.code == codes.JK_W1001], (
            f"正常用法误报：{source}"
        )


def test_ac_m4_01_02_no_adverb_no_warning():
    """完全不含副词的源码不产生任何 JK-W1001。"""
    diags = _diagnose('打印 加 3 5。')
    assert not [d for d in diags if d.code == codes.JK_W1001]


def test_existing_pipeline_examples_are_clean():
    """仓库既有管道示例不应触发副词透传告警（防误报回归）。"""
    root = os.path.join(os.path.dirname(__file__), '..')
    pipeline_dir = os.path.join(root, 'examples', 'pipelines')
    if not os.path.isdir(pipeline_dir):
        return
    for name in sorted(os.listdir(pipeline_dir)):
        if not name.endswith('.jk'):
            continue
        path = os.path.join(pipeline_dir, name)
        with open(path, 'r', encoding='utf-8') as f:
            source = f.read()
        diags = _diagnose(source, file=name)
        offenders = [d for d in diags if d.code == codes.JK_W1001]
        assert not offenders, (
            f"{name} 触发了 JK-W1001：" +
            "; ".join(d.message for d in offenders)
        )


# ---------------------------------------------------------------------------
# AC-M4-01-03 · 可复现
# ---------------------------------------------------------------------------

def test_ac_m4_01_03_diagnostics_are_reproducible():
    """同源码两次检查，诊断序列的所有字段完全一致。"""
    source = '列 1 2 3，皆大。\n列 4 5 6，只小。\n'
    first = _diagnose(source, file="a.jk")
    for _ in range(3):
        assert _diagnose(source, file="a.jk") == first


def test_check_program_does_not_mutate_ast():
    """静态检查只读 AST：跑两遍后再跑一遍结果不变，说明没有改过节点。"""
    ast = parse(tokenize('列 1 2 3，皆大。'))
    s1, s2 = ListSink(), ListSink()
    check_program(ast, s1)
    check_program(ast, s2)
    assert s1.drain() == s2.drain()


# ---------------------------------------------------------------------------
# T-M4-D07 · reporter 纯投影
# ---------------------------------------------------------------------------

def test_render_text_contains_location_category_and_code():
    diags = _diagnose('列 1 2 3，皆大。')
    d = [x for x in diags if x.code == codes.JK_W1001][0]
    text = render_text(d, source_lines=['列 1 2 3，皆大。'])
    assert f"第 {d.span.start.line} 行" in text
    assert d.category.value in text
    assert "^" in text
    assert f"[{d.code}]" in text


def test_render_text_without_source_lines():
    diags = _diagnose('列 1 2 3，皆大。')
    d = diags[0]
    text = render_text(d)
    assert "^" not in text          # 无源码则不画指示符
    assert d.message in text


def test_render_json_shape_is_stable():
    diags = _diagnose('列 1 2 3，皆大。', file="a.jk")
    d = [x for x in diags if x.code == codes.JK_W1001][0]
    obj = render_json(d)
    assert obj["code"] == codes.JK_W1001
    assert obj["severity"] == "警告"
    assert obj["severityLsp"] == 2
    assert obj["file"] == "a.jk"
    assert obj["range"]["start"]["line"] == d.span.start.line
    assert obj["range"]["start"]["column"] == d.span.start.column
    assert isinstance(obj["suggestions"], list)
    assert isinstance(obj["notes"], list)
    # 必须可 JSON 序列化
    import json
    json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------------------
# T-M4-D07 · adapters 双向投影
# ---------------------------------------------------------------------------

def test_to_lsp_diagnostic_zero_based_and_severity():
    diags = _diagnose('列 1 2 3，皆大。')
    d = [x for x in diags if x.code == codes.JK_W1001][0]
    lsp = to_lsp_diagnostic(d)
    assert lsp["range"]["start"]["line"] == d.span.start.line - 1
    assert lsp["range"]["start"]["character"] == d.span.start.column - 1
    assert lsp["severity"] == 2
    assert lsp["code"] == codes.JK_W1001
    assert lsp["source"] == "jikuai"


def test_to_lsp_diagnostic_utf16_column_conversion():
    """BMP 外字符（如 emoji）在 UTF-16 下占 2 个 code unit。"""
    from jikuai.diagnostics import Diagnostic, SEVERITY_ERROR
    from jikuai.errors import ErrorCategory

    line_text = "😀😀abc"          # 前两个字符各占 2 个 UTF-16 单元
    d = Diagnostic(
        code=codes.JK_E2001, severity=SEVERITY_ERROR,
        category=ErrorCategory.NAME, message="x",
        span=Span.point(1, 3),      # 1-based 码点列 3 → 前缀是 "😀😀"
    )
    lsp = to_lsp_diagnostic(d, line_text_provider=lambda ln: line_text)
    assert lsp["range"]["start"]["character"] == 4      # 2 + 2
    # 不给 provider 时退化为码点列 - 1
    assert to_lsp_diagnostic(d)["range"]["start"]["character"] == 2


def test_error_info_round_trip_preserves_core_fields():
    from jikuai.diagnostics import from_error_info, to_error_info
    from jikuai.errors import ErrorCategory, ErrorInfo

    info = ErrorInfo(
        category=ErrorCategory.NAME,
        message="未定义的标识符：赵丙",
        line=3, col=1, source_line="赵丙。", suggestion="赵甲",
    )
    d = from_error_info(info, codes.JK_E2001, file="a.jk")
    assert d.code == codes.JK_E2001
    assert d.severity == "错误"
    assert d.span.start == Position(3, 1)
    assert d.span.file == "a.jk"
    assert [s.text for s in d.suggestions] == ["赵甲"]

    back = to_error_info(d, source_line="赵丙。")
    assert back.line == info.line
    assert back.col == info.col
    assert back.message == info.message
    assert back.suggestion == "赵甲"


def test_from_error_info_with_end_column_builds_range():
    from jikuai.diagnostics import from_error_info
    from jikuai.errors import ErrorCategory, ErrorInfo

    info = ErrorInfo(category=ErrorCategory.NAME, message="x", line=2, col=3)
    d = from_error_info(info, codes.JK_E2001, end_column=7)
    assert d.span.start == Position(2, 3)
    assert d.span.end == Position(2, 7)


def test_lsp_message_carries_suggestion_hint():
    """建议会拼进 LSP message，让编辑器不需要额外解析 suggestions。"""
    from jikuai.diagnostics import from_error_info
    from jikuai.errors import ErrorCategory, ErrorInfo

    info = ErrorInfo(
        category=ErrorCategory.NAME, message="未知动词：打因",
        line=1, col=1, suggestion="打印",
    )
    d = from_error_info(info, codes.JK_E2002)
    lsp = to_lsp_diagnostic(d)
    assert "打印" in lsp["message"]
