# -*- coding: utf-8 -*-
"""极快语言 · 编译期静态诊断（ADR-14 · US-M4-01）。

本模块对 **AST** 做只读遍历，把可在编译期发现的问题 `emit` 给 Sink。
当前覆盖：

    JK-W1001  副词（皆 / 只 / 归）内部接了非内建动词。
              极快的副词只识别内建动词，遇到用户函数或拼错的动词会
              「按原值透传」——代码不报错但不产生预期效果，是新手高频坑。
              编译期给出警告，让这种静默失效变得可见（US-M4-01）。

依赖红线（ADR-14）：本模块**只 import `keywords` 与 `ast_nodes`**，
两者均不依赖 `evaluator`，因此诊断层保持与运行时解耦。
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..ast_nodes import AdverbCall, Call, Ident, Node, Program
from ..keywords import ADVERBS, VERB_ARITY
from .codes import CODE_TABLE, JK_W1001
from .model import Diagnostic, Position, Span
from .sink import DiagnosticSink

#: 副词内部允许出现的"已知动词"集合：内建动词 + 副词自身（支持副词嵌套）。
_KNOWN_VERBS = set(VERB_ARITY) | set(ADVERBS)


def _pos(node: Node) -> Position:
    """从 AST 节点取 1-based 位置；节点未带位置（line/col=0）时退化到 (1,1)。"""
    line = getattr(node, "line", 0) or 1
    col = getattr(node, "col", 0) or 1
    return Position(line=max(1, line), column=max(1, col))


def _iter_child_nodes(node: Node) -> Iterable[Node]:
    """产出一个节点直接持有的所有子 AST 节点（用于通用递归遍历）。

    不依赖各节点字段的硬编码清单，而是反射式扫描：任何 Node 或
    Node 列表字段都视为子节点。这样新增 AST 节点类型时无需改这里。
    """
    for value in vars(node).values():
        if isinstance(value, Node):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Node):
                    yield item
                elif isinstance(item, tuple):
                    # If.elif_branches 形如 [(cond, body), ...]
                    for sub in item:
                        if isinstance(sub, Node):
                            yield sub
                        elif isinstance(sub, list):
                            for n in sub:
                                if isinstance(n, Node):
                                    yield n
        elif isinstance(value, dict):
            # ClassDef.methods: name -> FuncDef
            for v in value.values():
                if isinstance(v, Node):
                    yield v


def _adverb_inner_verb(node: AdverbCall) -> Optional[str]:
    """取副词内部被调用的动词名；无法判定时返回 None（不误报）。

    解析器的两种产物：
      - 内部是已识别的内建动词 → `inner` 是 `Call(verb=...)`（如 皆乘 2）；
      - 内部是未识别名字（用户函数或拼错动词）→ `inner` 退化为
        `Ident(name=...)`（如 皆大 / 皆王甲）——这正是原值透传的情形。
    """
    inner = node.inner
    if isinstance(inner, Call):
        return inner.verb
    if isinstance(inner, Ident):
        return inner.name
    return None


def check_program(
    program: Program,
    sink: DiagnosticSink,
    file: Optional[str] = None,
) -> None:
    """遍历整棵 AST，把静态诊断 emit 给 sink。

    幂等 & 无副作用：只读 AST，不修改任何节点；同一 program 多次调用
    产出等价诊断（配合 ListSink.drain 的稳定排序即可复现，AC-M4-01-03）。
    """
    _walk(program, sink, file)


def _walk(node: Node, sink: DiagnosticSink, file: Optional[str]) -> None:
    if isinstance(node, AdverbCall):
        _check_adverb(node, sink, file)
    for child in _iter_child_nodes(node):
        _walk(child, sink, file)


def _check_adverb(node: AdverbCall, sink: DiagnosticSink, file: Optional[str]) -> None:
    verb = _adverb_inner_verb(node)
    # verb 为 None（inner 不是 Call）不下结论；verb 是已知内建动词/副词则正常。
    if verb is None or verb in _KNOWN_VERBS:
        return

    meta = CODE_TABLE[JK_W1001]
    pos = _pos(node)
    message = (
        f"副词 {node.adverb!r} 内部遇到未知动词 {verb!r}，"
        f"将按原值透传，不产生预期效果"
    )
    sink.emit(
        Diagnostic(
            code=JK_W1001,
            severity=meta.severity,
            category=meta.category,
            message=message,
            span=Span(start=pos, end=pos, file=file),
            subject=node.adverb,
            notes=(
                f"内建动词内部才会被副词识别；{verb!r} 可能是用户函数或拼写有误。",
            ),
        )
    )
