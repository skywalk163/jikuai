# -*- coding: utf-8 -*-
"""极快 AOT · 受支持子集门禁（T-M6-A01 · ADR-19）。

**本模块是 M6-P4 支线唯一不可降级项（D-07 第 5 条）。**

职责：对 `jikuai.frontend.compile_source` 产出的 AST 做**只读**静态遍历，
判定整棵程序是否落在 ADR-19 划定的 AOT 受支持子集内。命中子集外特性时
emit 一条 `JK-E7001` 诊断（含具体特性名 + 位置），并让 `check()` 返回
False —— 驱动器据此拒绝产出任何编译产物（AC-M6-06-03）。

设计红线：
    - 只 import `jikuai.ast_nodes` / `jikuai.diagnostics`，不 import
      `evaluator`，与运行时解耦（沿用 ADR-14 诊断层依赖约束）。
    - 遍历采用反射式 `_iter_child_nodes`（照搬 `diagnostics/static_check.py`
      的思路，**不修改**那个文件），新增 AST 节点类型时无需改这里。
    - 命中不支持节点后**不再下钻**：只报最外层那一条，避免一个类定义炸出
      十几条重复诊断。
    - 白名单语义：`SUPPORTED_VERBS` 之外的任何内建动词都视为不支持。
      宁可漏支持，不可错编译。
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from jikuai.ast_nodes import AdverbCall, Assign, Call, Import, Node, Program
from jikuai.diagnostics.codes import CODE_TABLE, JK_E7001
from jikuai.diagnostics.model import Diagnostic, Position, Span
from jikuai.diagnostics.sink import DiagnosticSink

# ---------------------------------------------------------------------------
# ADR-19 受支持子集定义
# ---------------------------------------------------------------------------

#: 受支持的内建动词白名单（ADR-19）。
#:
#: 注意：`人民币` 动词**不在**白名单内 —— 受支持的只有人民币**字面量**
#: （`￥99.90` → MoneyLit）。`人民币 9.9` 会被解析成 `Call(verb="人民币")`，
#: 属于「未在支持列表里的内建动词」，命中 JK-E7001。
SUPPORTED_VERBS: frozenset = frozenset({
    # 算术（二元）
    "加", "减", "乘", "除", "取余", "幂", "整除",
    # 口语化算术别名
    "加上", "减去", "乘以", "除以",
    # 一元算术
    "负", "绝对值",
    # 比较
    "等于", "不等于", "大于", "小于", "大于等于", "小于等于",
    # 逻辑
    "且", "或", "非",
    # 输出
    "打印",
})

#: 不支持的 AST 节点类型名（类名字符串）。命中即 JK-E7001。
UNSUPPORTED_NODE_TYPES: frozenset = frozenset({
    # 类与继承
    "ClassDef", "NewInstance", "MemberAccess",
    # 异常
    "Try", "Throw",
    # 管道与副词
    "Pipeline", "AdverbCall",
    # 模块与互操作
    "Import", "Export",
    # 闭包与用户函数
    "Lambda", "FuncDef", "FuncCall",
    # 控制流（第一版子集不含）
    "If", "While", "For", "Repeat", "Break", "Continue", "Return",
    # 复合数据（第一版子集不含）
    "ListLit", "Index",
})

#: 节点类型名 → 对外中文特性名。诊断消息必须含这个名字，便于用户定位。
_NODE_FEATURE_NAMES = {
    "ClassDef": "类定义",
    "NewInstance": "类实例化",
    "MemberAccess": "成员访问",
    "Try": "异常捕获",
    "Throw": "抛出异常",
    "Pipeline": "管道",
    "AdverbCall": "副词",
    "Import": "模块导入",
    "Export": "模块导出",
    "Lambda": "匿名函数",
    "FuncDef": "函数定义",
    "FuncCall": "用户函数调用",
    "If": "条件分支",
    "While": "当循环",
    "For": "遍历循环",
    "Repeat": "重复循环",
    "Break": "跳出",
    "Continue": "跳过",
    "Return": "返回",
    "ListLit": "列表字面量",
    "Index": "索引访问",
}

#: 各不支持特性的补充说明（写进 Diagnostic.notes，也用于 docs/AOT.md 对齐）。
_NODE_FEATURE_NOTES = {
    "ClassDef": "类与继承需要运行时对象模型，第一版 AOT 后端没有堆对象与方法派发。",
    "NewInstance": "类与继承需要运行时对象模型，第一版 AOT 后端没有堆对象与方法派发。",
    "MemberAccess": "成员访问依赖对象模型，第一版 AOT 后端未实现。",
    "Try": "异常需要栈展开机制，第一版 AOT 后端未实现。",
    "Throw": "异常需要栈展开机制，第一版 AOT 后端未实现。",
    "Pipeline": "管道是极快的核心范式，但需要值传递与惰性求值支撑，暂不在 AOT 子集内。",
    "AdverbCall": "副词是高阶函数（映射/筛选/归约），需要函数值与列表运行时，暂不在 AOT 子集内。",
    "Import": "模块导入（含 蟒: pybridge）需要 Python 运行时在场，与 AOT 目标冲突。",
    "Export": "模块导出只在模块语境有意义，AOT 只编译单文件顶层程序。",
    "Lambda": "闭包需要环境捕获，第一版 AOT 后端未实现。",
    "FuncDef": "用户函数需要调用栈与作用域链，第一版 AOT 后端未实现（下一步优先项）。",
    "FuncCall": "用户函数需要调用栈与作用域链，第一版 AOT 后端未实现（下一步优先项）。",
    "If": "控制流不在第一版子集内（下一步优先项，见 docs/AOT.md 下一步建议）。",
    "While": "控制流不在第一版子集内（下一步优先项，见 docs/AOT.md 下一步建议）。",
    "For": "控制流不在第一版子集内（下一步优先项，见 docs/AOT.md 下一步建议）。",
    "Repeat": "控制流不在第一版子集内（下一步优先项，见 docs/AOT.md 下一步建议）。",
    "Break": "控制流不在第一版子集内。",
    "Continue": "控制流不在第一版子集内。",
    "Return": "控制流不在第一版子集内。",
    "ListLit": "列表需要堆分配与泛型容器运行时，第一版 AOT 后端未实现。",
    "Index": "索引访问依赖列表/字典运行时，第一版 AOT 后端未实现。",
}


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _pos(node: Node) -> Position:
    """从 AST 节点取 1-based 位置；节点未带位置（line/col=0）时退化到 (1,1)。"""
    line = getattr(node, "line", 0) or 1
    col = getattr(node, "col", 0) or 1
    return Position(line=max(1, int(line)), column=max(1, int(col)))


def _iter_child_nodes(node: Node) -> Iterable[Node]:
    """产出一个节点直接持有的所有子 AST 节点（反射式，与 static_check 同构）。"""
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


def _feature_of_node(node: Node) -> Tuple[str, str, str]:
    """判定不支持节点的 (特性名, 主体名, 补充说明)。

    对副词与 Python 互操作导入做特化，使消息更贴近用户写的那行代码：
        AdverbCall(adverb="皆")   → 「副词 皆」
        Import(kind="python")     → 「Python 互操作导入（蟒:）」
    """
    type_name = type(node).__name__
    base = _NODE_FEATURE_NAMES.get(type_name, type_name)
    note = _NODE_FEATURE_NOTES.get(type_name, "")

    if isinstance(node, AdverbCall):
        adverb = getattr(node, "adverb", "") or ""
        return ("副词 {}".format(adverb).strip(), adverb or base, note)

    if isinstance(node, Import):
        module = getattr(node, "module", "") or ""
        if getattr(node, "kind", "jk") == "python":
            return ("Python 互操作导入（蟒:）", module, note)
        return ("模块导入", module, note)

    subject = getattr(node, "name", None) or getattr(node, "class_name", None) or base
    return (base, str(subject), note)


def _make_diagnostic(feature: str, subject: str, note: str,
                     node: Node, file: Optional[str]) -> Diagnostic:
    """按 JK-E7001 元数据表构造诊断。消息模板：超出 AOT 受支持子集：{feature}。"""
    meta = CODE_TABLE[JK_E7001]
    pos = _pos(node)
    notes: List[str] = []
    if note:
        notes.append(note)
    notes.append("AOT 是实验性功能（Experimental），受支持子集见 docs/AOT.md。")
    return Diagnostic(
        code=JK_E7001,
        severity=meta.severity,
        category=meta.category,
        message="超出 AOT 受支持子集：{}".format(feature),
        span=Span(start=pos, end=pos, file=file),
        subject=subject,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# 对外契约
# ---------------------------------------------------------------------------

def check(program: Program, sink: DiagnosticSink,
          file: Optional[str] = None) -> bool:
    """静态遍历 AST。命中不支持节点或动词 → emit JK-E7001 并返回 False。

    幂等 & 无副作用：只读 AST，不修改任何节点。同一 program 多次调用产出
    等价诊断序列（配合 ListSink.drain 的稳定排序即可复现）。

    参数
        program  compile_source(...).ast
        sink     诊断汇聚点，任意实现了 emit 的对象（如 ListSink）
        file     源文件路径，写入 Span.file，供 CLI/LSP 定位

    返回
        True  = 整棵程序都在受支持子集内，可以进 codegen
        False = 至少一条 JK-E7001，**必须**拒绝产出产物
    """
    ok = True
    for feature, subject, note, node in _collect(program):
        ok = False
        sink.emit(_make_diagnostic(feature, subject, note, node, file))
    return ok


def unsupported_reasons(program: Program) -> List[Tuple[str, int, int]]:
    """返回 (原因, 行, 列) 列表，便于报告与批量统计。

    顺序与 AST 遍历顺序一致（前序、深度优先），因此对同一份源码是决定性的。
    """
    result: List[Tuple[str, int, int]] = []
    for feature, _subject, _note, node in _collect(program):
        pos = _pos(node)
        result.append((feature, pos.line, pos.column))
    return result


def is_supported(program: Program) -> bool:
    """便捷判定：程序是否完全落在受支持子集内（不产出诊断）。"""
    for _ in _collect(program):
        return False
    return True


def describe_subset() -> dict:
    """导出子集描述，供 docs/AOT.md 与测试做「文档 ↔ 代码」一致性核对。"""
    return {
        "supported_verbs": sorted(SUPPORTED_VERBS),
        "unsupported_node_types": sorted(UNSUPPORTED_NODE_TYPES),
        "unsupported_feature_names": dict(sorted(_NODE_FEATURE_NAMES.items())),
    }


# ---------------------------------------------------------------------------
# 遍历核心
# ---------------------------------------------------------------------------

def _collect(node: Node):
    """前序深度优先产出 (特性名, 主体名, 说明, 节点) 四元组。

    命中不支持节点后**不再下钻**其子树：一个类定义只报一条，而不是把类体里
    每个语句都报一遍。未命中的节点继续递归。
    """
    type_name = type(node).__name__

    if type_name in UNSUPPORTED_NODE_TYPES:
        feature, subject, note = _feature_of_node(node)
        yield (feature, subject, note, node)
        return

    if isinstance(node, Call):
        verb = getattr(node, "verb", "")
        if verb not in SUPPORTED_VERBS:
            yield (
                "内建动词 {}".format(verb),
                verb,
                "该动词不在 AOT 受支持动词白名单内；白名单见 "
                "subset_gate.SUPPORTED_VERBS。",
                node,
            )
            return

    if isinstance(node, Assign):
        # 赋值目标必须是简单标识符。MemberAccess / Index 目标本身也在
        # UNSUPPORTED_NODE_TYPES 里，会在下钻时命中；这里给出更贴切的消息。
        target = getattr(node, "target", None)
        if target is not None and type(target).__name__ != "Ident":
            tgt_type = type(target).__name__
            yield (
                "赋值目标 {}".format(_NODE_FEATURE_NAMES.get(tgt_type, tgt_type)),
                tgt_type,
                "AOT 只支持赋值给简单变量名（Ident）。",
                node,
            )
            return

    for child in _iter_child_nodes(node):
        for item in _collect(child):
            yield item