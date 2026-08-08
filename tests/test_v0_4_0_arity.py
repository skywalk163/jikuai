# -*- coding: utf-8 -*-
"""v0.4.0 M1 尾段收口 · 元数（arity）边界专项 + parser 结构回归基线。

两块内容，对应 M2 两条硬前置：

  A-02 元数边界专项（`TestArityBoundaries`）
      QA 实测这 9 组运行时行为已正确，但此前测试套件无守护。本文件逐条以
      **求值结果**断言（不是只断言 token），锁死当前语义。

  A-08 parser 层回归基线（`TestParserBaseline`）
      锁定 ADR-12「元数守卫前移」改动**之前**的 AST 形态。断言方式为
      「repr 快照 + 结构化断言」双轨：
        - repr 快照：一行锁住整棵子树形态，ADR-12 落地时只需在此处刻意更新，
          diff 即是形态变更的完整证据；
        - 结构化断言：节点类型 / 动词名 / 子节点数量，失败时能直接读出
          「哪一维变了」，不必人肉比对长字符串。
      repr 稳定性依据：AST 节点都是 `@dataclass`，`line/col` 是基类**类属性**、
      不参与 `__repr__`，因此 repr 只反映结构与字面量。

注意（ADR-12 前的既有分层事实）：`加 3。` 的元数不足诊断由 **evaluator**
`_check_verb_arity` 抛出，parser 阶段照常产出 `Call(verb='加', args=[3])`。
本文件如实记录该现状——它正是 ADR-12 要前移的目标，基线必须先钉住。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from jikuai.ast_nodes import (
    AdverbCall, Call, Ident, NumberLit, Pipeline,
)
from jikuai.errors import ErrorCategory
from jikuai.evaluator import Evaluator, JiKuaiError
from jikuai.lexer import tokenize
from jikuai.parser import parse


def _run(src):
    """求值单段源码，返回最后一条语句的值。"""
    return Evaluator().eval(parse(tokenize(src)), source=src)


def _stmt(src):
    """解析单条语句源码，返回其唯一的顶层 AST 节点（不求值）。"""
    prog = parse(tokenize(src))
    assert len(prog.body) == 1, prog.body
    return prog.body[0]


# ============================================================
# A-02 · 9 组元数边界专项（期望值 = QA 实测 = 本机实测）
# ============================================================

# (用例编号, 源码, 期望求值结果)
ARITY_CASES = [
    ('A-02-1 三元定参 · 替换',       '替换 "aXc" "X" "b"。',   'abc'),
    ('A-02-2 副词 皆 + 二元动词',    '列 1 2 3，皆加1。',       [2, 3, 4]),
    ('A-02-3 副词 只 + 二元谓词',    '列 1 2 3 4，只大于2。',   [3, 4]),
    ('A-02-4 一元动词链式管道',      '列 3 1 2，排序，反转。',  [3, 2, 1]),
    ('A-02-5 一元动词 · 负',         '负 5。',                  -5),
    ('A-02-6 一元嵌套一元',          '绝对值 负 5。',           5),
    ('A-02-7 管道隐式首参 · 长度',   '列 1 2 3，长度。',        3),
    ('A-02-8 二元中缀 · 且',         '真 且 假。',              False),
    ('A-02-9 变参 · 范围（左闭右开）', '范围 1 5。',            [1, 2, 3, 4]),
]


class TestArityBoundaries:
    """9 组元数边界：逐条断言求值结果。"""

    @pytest.mark.parametrize('label,src,expected',
                             ARITY_CASES,
                             ids=[c[0].split()[0] for c in ARITY_CASES])
    def test_arity_boundary_evaluates_as_recorded(self, label, src, expected):
        got = _run(src)
        assert got == expected, f'{label} | {src!r} -> {got!r}，期望 {expected!r}'
        # 类型也要对齐：避免 True == 1 / [1,2] == (1,2) 之类的宽松通过
        assert type(got) is type(expected), (label, type(got), type(expected))

    def test_a02_9_range_is_half_open_not_inclusive(self):
        """A-02-9 补充语义锚点：`范围 1 5` 是左闭右开（不含 5），长度为 4。

        单独立一条，避免日后有人把 `范围` 改成闭区间时只当作"改了个期望值"。
        """
        got = _run('范围 1 5。')
        assert got[0] == 1 and got[-1] == 4, got
        assert len(got) == 4, got

    def test_a02_7_pipeline_implicit_first_arg_equals_prefix_form(self):
        """A-02-7 补充语义锚点：管道隐式首参与前缀写法等价。"""
        assert _run('列 1 2 3，长度。') == _run('长度 列 1 2 3。') == 3


# ============================================================
# A-08 · parser 结构回归基线（ADR-12 元数守卫前移 · 改动前形态）
# ============================================================

# (用例编号, 源码, AST repr 快照)
PARSER_BASELINE_SNAPSHOTS = [
    (
        'A-08-1 二元定参前缀调用',
        '加 3 5。',
        "Call(verb='加', args=[NumberLit(value=3), NumberLit(value=5)])",
    ),
    (
        'A-08-2 二元定参中缀调用',
        '3 加 5。',
        "Call(verb='加', args=[NumberLit(value=3), NumberLit(value=5)])",
    ),
    (
        'A-08-3 中缀合并（D-10 方案 A）',
        '打印 郑数 加 2。',
        "Call(verb='打印', args=[Call(verb='加', "
        "args=[Ident(name='郑数'), NumberLit(value=2)])])",
    ),
    (
        'A-08-4 管道 + 副词',
        '列 1 2 3，皆乘2。',
        "Pipeline(stages=[Call(verb='列', args=[NumberLit(value=1), "
        "NumberLit(value=2), NumberLit(value=3)]), "
        "AdverbCall(adverb='皆', inner=Call(verb='乘', "
        "args=[NumberLit(value=2)]), accumulator=None)])",
    ),
    (
        'A-08-5 元数不足（parser 阶段不拦，形态照常产出）',
        '加 3。',
        "Call(verb='加', args=[NumberLit(value=3)])",
    ),
]


class TestParserBaseline:
    """parser 层 AST 形态基线。ADR-12 落地时，本类的失败即变更清单。"""

    @pytest.mark.parametrize('label,src,snapshot',
                             PARSER_BASELINE_SNAPSHOTS,
                             ids=[c[0].split()[0]
                                  for c in PARSER_BASELINE_SNAPSHOTS])
    def test_ast_repr_snapshot(self, label, src, snapshot):
        assert repr(_stmt(src)) == snapshot, f'{label} | {src!r}'

    def test_a08_1_prefix_binary_call_structure(self):
        """`加 3 5。` → 二元定参**前缀**调用：Call('加', [3, 5])。"""
        node = _stmt('加 3 5。')
        assert isinstance(node, Call)
        assert node.verb == '加'
        assert len(node.args) == 2
        assert all(isinstance(a, NumberLit) for a in node.args), node.args
        assert [a.value for a in node.args] == [3, 5]

    def test_a08_2_infix_binary_call_structure(self):
        """`3 加 5。` → 二元定参**中缀**调用，归一到与前缀同一形态。"""
        node = _stmt('3 加 5。')
        assert isinstance(node, Call)
        assert node.verb == '加'
        assert len(node.args) == 2
        assert [a.value for a in node.args] == [3, 5]
        # 前缀与中缀在 AST 层不可区分（当前设计的既定事实）
        assert repr(node) == repr(_stmt('加 3 5。'))

    def test_a08_3_infix_merge_inside_verb_argument(self):
        """`打印 郑数 加 2。` → `打印(加(郑数, 2))`（D-10 方案 A 中缀合并）。

        关键点：`打印` 是变参动词，但参数位上的 `郑数 加 2` 必须先被中缀合并为
        一个 `Call`，而不是被摊平成 `打印(郑数, 加, 2)` 或 `打印(郑数)`。
        """
        node = _stmt('打印 郑数 加 2。')
        assert isinstance(node, Call)
        assert node.verb == '打印'
        assert len(node.args) == 1, node.args      # 合并后只有 1 个参数
        inner = node.args[0]
        assert isinstance(inner, Call)
        assert inner.verb == '加'
        assert len(inner.args) == 2
        assert isinstance(inner.args[0], Ident) and inner.args[0].name == '郑数'
        assert isinstance(inner.args[1], NumberLit) and inner.args[1].value == 2

    def test_a08_4_pipeline_with_adverb_structure(self):
        """`列 1 2 3，皆乘2。` → Pipeline[Call('列',×3), AdverbCall('皆', Call('乘',×1))]。

        副词占掉内部动词的第一个参数槽，因此 `乘` 只显式收 1 个参数。
        """
        node = _stmt('列 1 2 3，皆乘2。')
        assert isinstance(node, Pipeline)
        assert len(node.stages) == 2, node.stages
        head, tail = node.stages
        assert isinstance(head, Call) and head.verb == '列'
        assert len(head.args) == 3
        assert isinstance(tail, AdverbCall) and tail.adverb == '皆'
        assert tail.accumulator is None
        assert isinstance(tail.inner, Call) and tail.inner.verb == '乘'
        assert len(tail.inner.args) == 1, tail.inner.args

    def test_a08_5_insufficient_arity_is_evaluator_not_parser(self):
        """`加 3。` 的元数守卫位置基线：**parser 放行、evaluator 抛 SYNTAX**。

        ADR-12 要把守卫前移到 parser；本条钉住前移**之前**的分层事实，
        包括中文诊断原文，前移后诊断文案不应退化。
        """
        # parser 阶段：不抛，且照常产出 1 参 Call
        node = _stmt('加 3。')
        assert isinstance(node, Call) and node.verb == '加'
        assert len(node.args) == 1, node.args

        # evaluator 阶段：结构化 SYNTAX 中文诊断
        with pytest.raises(JiKuaiError) as ei:
            _run('加 3。')
        info = ei.value.info
        assert info is not None, ei.value
        assert info.category is ErrorCategory.SYNTAX, info
        assert info.message == '动词「加」需要 2 个参数，实际收到 1 个', info
        assert str(ei.value) == '语法错误：动词「加」需要 2 个参数，实际收到 1 个'
        # 不得泄漏 Python 实现细节
        for leak in ('lambda', 'TypeError', 'Traceback', '_setup_builtins',
                     'positional argument'):
            assert leak not in str(ei.value), leak
