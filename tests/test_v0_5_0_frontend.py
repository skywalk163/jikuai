# -*- coding: utf-8 -*-
"""v0.5.0 · T-M4-A01..A05 frontend 两遍分词与 ADR-06 X2 闭环测试。

覆盖：
    - 单遍 / 两遍分支：无类文件跳过 Pass2，含类文件走 Pass2
    - ClassRegionTable 从 AST 正确提取字符区间
    - token 序列结构等价的收敛判定
    - Pass2 不收敛时 emit JK-W9001，取 Pass1 AST 不崩
    - JIKUAI_LEGACY_ADR06=1 强制单遍
    - static_check 与两遍分词的集成：仍能正确报出 JK-W1001
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from jikuai.diagnostics import ListSink, codes
from jikuai.frontend import (
    CompileResult,
    build_class_region_table,
    compile_source,
    diagnose,
)
from jikuai.lexer import tokenize
from jikuai.parser import parse


# ---------------------------------------------------------------------------
# T-M4-A01 · 单遍 / 两遍分支
# ---------------------------------------------------------------------------

def test_no_class_takes_single_pass():
    result = compile_source('打印 加 3 5。')
    assert isinstance(result, CompileResult)
    assert result.two_pass is False
    assert result.converged is True
    assert result.ast is not None


def test_class_definition_triggers_two_pass():
    source = (
        '类 王：\n'
        '  构造：接收 赵值：\n'
        '    定义 自身.某 = 赵值。\n'
        '  。\n'
        '。\n'
    )
    result = compile_source(source)
    assert result.two_pass is True
    # 良构类块两遍分词应当收敛
    assert result.converged is True


def test_legacy_flag_forces_single_pass(monkeypatch):
    monkeypatch.setenv('JIKUAI_LEGACY_ADR06', '1')
    source = (
        '类 王：\n'
        '  构造：接收 赵值：\n'
        '    定义 自身.某 = 赵值。\n'
        '  。\n'
        '。\n'
    )
    result = compile_source(source)
    assert result.two_pass is False


# ---------------------------------------------------------------------------
# T-M4-A02 · ClassRegionTable 提取
# ---------------------------------------------------------------------------

def test_class_region_table_covers_class_block():
    source = (
        '打印 "开头"。\n'
        '类 王：\n'
        '  构造：接收 赵值：\n'
        '    定义 自身.某 = 赵值。\n'
        '  。\n'
        '。\n'
        '打印 "结尾"。\n'
    )
    ast = parse(tokenize(source))
    regions = build_class_region_table(ast, source)
    assert len(regions) == 1
    start, end = regions[0]
    # 区间起点应指向 "类" 关键字所在行的行首
    line_starts = [0]
    for line in source.split('\n'):
        line_starts.append(line_starts[-1] + len(line) + 1)
    class_line_idx = source.split('\n').index('类 王：')
    assert start == line_starts[class_line_idx]
    # 区间终点位于类块结束以后，包含收尾 `。`
    assert end > start
    assert '。' in source[start:end]


def test_no_class_yields_empty_region_table():
    source = '打印 加 3 5。'
    ast = parse(tokenize(source))
    assert build_class_region_table(ast, source) == []


# ---------------------------------------------------------------------------
# T-M4-A04 · JK-W9001 兜底
# ---------------------------------------------------------------------------

def test_pass2_divergence_emits_jk_w9001(monkeypatch):
    """构造 Pass2 与 Pass1 token 不等，验证兜底路径。

    做法：monkeypatch `frontend.tokenize`，让 Pass2 调用返回一个已知不同的序列，
    Pass1 走真实 tokenize。这样触发未收敛分支，验证：
      - result.two_pass=True, converged=False
      - 诊断含 JK-W9001
      - result.ast 仍为 Pass1 AST（不崩）
    """
    from jikuai import frontend
    real_tokenize = frontend.tokenize

    call_state = {'n': 0}

    def fake_tokenize(src, external_defs=None, class_regions=None):
        call_state['n'] += 1
        result = real_tokenize(src, external_defs=external_defs,
                               class_regions=class_regions)
        if class_regions is not None:
            # 篡改 Pass2 结果：删除末尾 EOF 之外的一个 token
            trimmed = list(result)
            if len(trimmed) >= 2:
                trimmed.pop(0)
            return trimmed
        return result

    monkeypatch.setattr(frontend, 'tokenize', fake_tokenize)

    source = (
        '类 王：\n'
        '  构造：接收 赵值：\n'
        '    定义 自身.某 = 赵值。\n'
        '  。\n'
        '。\n'
    )
    result = compile_source(source)
    assert result.two_pass is True
    assert result.converged is False
    assert any(d.code == codes.JK_W9001 for d in result.diagnostics)
    assert result.ast is not None


# ---------------------------------------------------------------------------
# T-M4-A05 · static_check 集成
# ---------------------------------------------------------------------------

def test_diagnose_convenience_returns_static_diagnostics():
    diags = diagnose('列 1 2 3，皆大。')
    assert any(d.code == codes.JK_W1001 for d in diags), (
        f"应含 JK-W1001，实际 {[d.code for d in diags]}"
    )


def test_external_sink_receives_diagnostics():
    sink = ListSink()
    source = '列 1 2 3，皆大。'
    result = compile_source(source, sink=sink)
    # 用了外部 sink，CompileResult.diagnostics 应为空
    assert result.diagnostics == []
    drained = sink.drain()
    assert any(d.code == codes.JK_W1001 for d in drained)


# ---------------------------------------------------------------------------
# 集成回归：既有 examples 走 frontend 不产生 JK-W9001 且不改变 AST 行为
# ---------------------------------------------------------------------------

def test_existing_examples_compile_without_divergence():
    """既有 examples 里的类文件走两遍分词应收敛，不产生 JK-W9001。"""
    root = os.path.join(os.path.dirname(__file__), '..', 'examples')
    files = ['oop.jk', '小张的一天.jk']
    for name in files:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        with open(path, 'r', encoding='utf-8') as f:
            source = f.read()
        result = compile_source(source, file=name)
        assert not any(
            d.code == codes.JK_W9001 for d in result.diagnostics
        ), f"{name} 触发了 JK-W9001，两遍分词未收敛"


# ---------------------------------------------------------------------------
# ADR-06 X2：run_source 端到端不改变现有语义
# ---------------------------------------------------------------------------

def test_run_source_still_returns_last_value():
    """run_source 改造后仍返回 eval 结果，且警告诊断不影响返回值。"""
    from jikuai.main import run_source

    # 无诊断
    assert run_source('加 3 5。') == 8
    # 有 JK-W1001 警告仍能正常返回列表结果
    result = run_source('列 1 2 3，皆大。')
    assert isinstance(result, list)


def test_run_source_diagnostics_off_silences_stderr(monkeypatch, capsys):
    """G8 守护：JIKUAI_DIAGNOSTICS=off 时 warning 不打印。"""
    from jikuai.main import run_source

    monkeypatch.setenv('JIKUAI_DIAGNOSTICS', 'off')
    run_source('列 1 2 3，皆大。')
    captured = capsys.readouterr()
    assert 'JK-W1001' not in captured.err
