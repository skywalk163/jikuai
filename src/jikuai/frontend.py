# -*- coding: utf-8 -*-
"""极快语言 · 编译前端编排（v0.5.0 · ADR-17 两遍分词）。

`compile_source` 把「分词 → 解析 → 静态诊断」串成一条链，并实现 ADR-06 X2
的两遍分词闭环：

    Pass1: tokenize(source) 用行文本启发式定位类块 → parse → AST
    判定: AST 不含 ClassDef，或 JIKUAI_LEGACY_ADR06=1 → 直接用 Pass1（单遍）
    Pass2: 从 Pass1 AST 提取权威 ClassRegionTable → tokenize(source, class_regions=表)
           → 比较两遍 token 序列
             收敛（结构等价）→ 复用 Pass1 AST（无需再 parse）
             未收敛        → emit JK-W9001，回退取 Pass1 AST（不崩）
    最后: check_program(ast, sink) 收集静态诊断（如副词透传 JK-W1001）

性能（Spike-ADR06 结论）：无条件两遍会使编译阶段 +87%，故这里以
「AST 无 ClassDef 即跳过 Pass2」把开销压到接近 0——绝大多数脚本不含类。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .ast_nodes import ClassDef, Node, Program
from .diagnostics import ListSink, codes
from .diagnostics.model import Diagnostic, Position, Span
from .diagnostics.sink import DiagnosticSink
from .diagnostics.static_check import check_program
from .lexer import tokenize
from .parser import parse


@dataclass
class CompileResult:
    """一次编译的产物。"""
    tokens: list
    ast: Program
    diagnostics: List[Diagnostic] = field(default_factory=list)
    two_pass: bool = False          # 是否真的走了第二遍
    converged: bool = True          # 两遍是否收敛（未走两遍时恒 True）


# ---------------------------------------------------------------------------
# 行偏移与类区间
# ---------------------------------------------------------------------------

def _line_offsets(source: str) -> List[int]:
    """返回每一行（0-based 行号）的起始字符偏移。

    与 `lexer._heuristic_class_regions` 使用同一算法，保证两遍分词的区间
    坐标系一致（掩码保持长度不变，故掩码源码与原始源码偏移相同）。
    """
    offsets = []
    p = 0
    for line in source.split('\n'):
        offsets.append(p)
        p += len(line) + 1
    return offsets


def _max_line_in_subtree(node: Node) -> int:
    """递归求一个 AST 子树中出现的最大行号（用于推导类块终点）。"""
    best = getattr(node, 'line', 0) or 0
    for value in vars(node).values():
        if isinstance(value, Node):
            best = max(best, _max_line_in_subtree(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Node):
                    best = max(best, _max_line_in_subtree(item))
                elif isinstance(item, tuple):
                    for sub in item:
                        if isinstance(sub, Node):
                            best = max(best, _max_line_in_subtree(sub))
                        elif isinstance(sub, list):
                            for n in sub:
                                if isinstance(n, Node):
                                    best = max(best, _max_line_in_subtree(n))
        elif isinstance(value, dict):
            for v in value.values():
                if isinstance(v, Node):
                    best = max(best, _max_line_in_subtree(v))
    return best


def _find_class_defs(program: Program) -> List[ClassDef]:
    """收集 AST 中所有 ClassDef（当前语言类只在顶层，稳妥起见仍递归）。"""
    found: List[ClassDef] = []

    def walk(node: Node) -> None:
        if isinstance(node, ClassDef):
            found.append(node)
        for value in vars(node).values():
            if isinstance(value, Node):
                walk(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, Node):
                        walk(item)
            elif isinstance(value, dict):
                for v in value.values():
                    if isinstance(v, Node):
                        walk(v)

    walk(program)
    return found


def build_class_region_table(
    program: Program, source: str
) -> List[Tuple[int, int]]:
    """从 Pass1 AST 提取权威类块字符区间 [(start_char, end_char), ...]。

    这是 ADR-06 X2 的核心：用 parser 的结构化结果替代 lexer 的行文本启发式。
    区间起点 = `类` 关键字所在行的行首偏移；终点 = ClassDef.end_line 那一行的行末
    （含该行内容长度），覆盖到类块结束的独立 `。` 行。

    若 ClassDef.line/end_line 为 0（parser 未标注行号），回退到源码文本搜索。
    """
    offsets = _line_offsets(source)
    lines = source.split('\n')
    total_lines = len(lines)
    regions: List[Tuple[int, int]] = []

    for cls in _find_class_defs(program):
        start_line = getattr(cls, 'line', 0) or 0    # 1-based
        end_line = getattr(cls, 'end_line', 0) or 0  # 1-based

        if start_line == 0:
            # parser 未标注行号：回退到文本搜索找到 `类 <name>` 所在行
            for idx, ln in enumerate(lines):
                if ln.lstrip().startswith('类') and cls.name in ln:
                    start_line = idx + 1
                    break
            if start_line == 0:
                continue

        if end_line == 0:
            end_line = start_line

        start_idx = start_line - 1
        end_idx = min(end_line - 1, total_lines - 1)
        if 0 <= start_idx < len(offsets) and 0 <= end_idx < total_lines:
            start_char = offsets[start_idx]
            end_char = offsets[end_idx] + len(lines[end_idx])
            regions.append((start_char, end_char))

    regions.sort()
    return regions


# ---------------------------------------------------------------------------
# token 结构等价比较
# ---------------------------------------------------------------------------

def _token_signature(tokens) -> List[tuple]:
    """token 序列的结构签名：只看 (type, value)，忽略位置，用于收敛判定。"""
    sig = []
    for t in tokens:
        ttype = getattr(t, 'type', None)
        tval = getattr(t, 'value', None)
        sig.append((ttype, tval))
    return sig


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _legacy_single_pass() -> bool:
    return os.environ.get('JIKUAI_LEGACY_ADR06') == '1'


def compile_source(
    source: str,
    file: Optional[str] = None,
    sink: Optional[DiagnosticSink] = None,
) -> CompileResult:
    """两遍分词编排 + 静态诊断。返回 CompileResult。

    sink 为 None 时内部用 ListSink 收集，并把 drain 结果填入 CompileResult。
    传入外部 sink 时诊断直接 emit 给它，CompileResult.diagnostics 为空列表
    （由调用方自行 drain）。
    """
    own_sink = sink is None
    active_sink: DiagnosticSink = ListSink() if own_sink else sink

    # ---- Pass 1 ----
    tokens1 = tokenize(source)
    ast1 = parse(tokens1)

    two_pass = False
    converged = True
    final_tokens = tokens1

    class_defs = _find_class_defs(ast1)
    if class_defs and not _legacy_single_pass():
        # ---- Pass 2：权威区间重扫 ----
        two_pass = True
        table = build_class_region_table(ast1, source)
        tokens2 = tokenize(source, class_regions=table)
        if _token_signature(tokens1) == _token_signature(tokens2):
            converged = True
            final_tokens = tokens2
        else:
            # 未收敛：发 JK-W9001 兜底，取 Pass1 结果继续（不崩）
            converged = False
            final_tokens = tokens1
            meta = codes.CODE_TABLE[codes.JK_W9001]
            pos = Position(1, 1)
            active_sink.emit(Diagnostic(
                code=codes.JK_W9001,
                severity=meta.severity,
                category=meta.category,
                message=meta.template,
                span=Span(start=pos, end=pos, file=file),
                notes=("两遍分词的类块边界判定不一致，已回退首遍结果；"
                       "如遇异常可设 JIKUAI_LEGACY_ADR06=1 强制单遍。",),
            ))

    # ---- 静态诊断 ----
    check_program(ast1, active_sink, file=file)

    diagnostics: List[Diagnostic] = []
    if own_sink and isinstance(active_sink, ListSink):
        diagnostics = active_sink.drain()

    return CompileResult(
        tokens=final_tokens,
        ast=ast1,
        diagnostics=diagnostics,
        two_pass=two_pass,
        converged=converged,
    )


def diagnose(source: str, file: Optional[str] = None) -> List[Diagnostic]:
    """便捷函数：编译并返回稳定排序后的诊断列表（不执行）。"""
    return compile_source(source, file=file).diagnostics
