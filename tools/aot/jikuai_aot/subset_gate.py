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
    - 大部分判定只看**节点类型**（`UNSUPPORTED_NODE_TYPES`）；T2a 起另有一小
      组**上下文相关**判定（`_CONTEXT_FEATURE_NOTES`）：`FuncDef` 写在顶层
      合法、写在函数体里就是闭包，必须结合位置才判得出来。
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from jikuai.ast_nodes import (
    AdverbCall, Assign, Call, For, FuncCall, FuncDef, Import, ListLit, Node,
    Program, Return,
)
from jikuai.diagnostics.codes import CODE_TABLE, JK_E7001
from jikuai.diagnostics.model import Diagnostic, Position, Span
from jikuai.diagnostics.sink import DiagnosticSink

# ---------------------------------------------------------------------------
# ADR-19 受支持子集定义
# ---------------------------------------------------------------------------

#: 受支持的内建动词白名单（ADR-19 · M9-3 扩容）。
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
#:
#: M9-3 把**控制流**移出本集合：`If` / `While` / `Repeat` / `Break` /
#: `Continue` 现在可以编译。它们只需要 C 的 if/while/break/continue，
#: 不需要堆对象、调用栈或泛型容器，是子集扩容里性价比最高的一档。
#:
#: T2a 把**用户函数**移出本集合：`FuncDef` / `FuncCall` / `Return` 现在可以
#: 编译成 C 函数（签名统一 `JKValue f(JKValue...)`）。但只有**顶层、无捕获**
#: 的函数在子集内 —— 嵌套函数定义与闭包仍命中 JK-E7001，判定见
#: `_collect` 里的上下文规则，不是靠这个集合。
#:
#: T2b 把 **`For`（遍历）** 变成**上下文相关**：`遍历 变量 于 范围(...)` 与
#: `遍历 变量 于 【字面量列表】` 现在可以编译成 C 的计数 for 循环 / 数组遍历；
#: 但遍历任意可迭代对象（变量、函数返回值等）仍命中 JK-E7001，判定见
#: `_collect` 里的上下文规则，不再靠这个集合。
#:
#: 仍然不支持的都有明确的运行时依赖：`Lambda` 要环境捕获、
#: `ListLit`/`Index` 要堆容器（`For` 里的字面量列表是就地展开的特例，不落堆）、
#: 类与异常要对象模型与栈展开。
UNSUPPORTED_NODE_TYPES: frozenset = frozenset({
    # 类与继承
    "ClassDef", "NewInstance", "MemberAccess",
    # 异常
    "Try", "Throw",
    # 管道与副词
    "Pipeline", "AdverbCall",
    # 模块与互操作
    "Import", "Export",
    # 闭包（需要环境捕获，代价比无捕获函数高一个量级）
    "Lambda",
    # 复合数据（第一版子集不含；`For` 里的字面量列表遍历是就地展开的特例）
    "ListLit", "DictLit", "Index",
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
    "ListLit": "列表字面量",
    "DictLit": "字典字面量",
    "Index": "索引访问",
}

#: 各不支持特性的补充说明（写进 Diagnostic.notes，也用于 docs/AOT.md 对齐）。
_NODE_FEATURE_NOTES = {
    "ClassDef": "类与继承需要运行时对象模型，AOT 后端没有堆对象与方法派发。",
    "NewInstance": "类与继承需要运行时对象模型，AOT 后端没有堆对象与方法派发。",
    "MemberAccess": "成员访问依赖对象模型，AOT 后端未实现。",
    "Try": "异常需要栈展开机制，AOT 后端未实现。",
    "Throw": "异常需要栈展开机制，AOT 后端未实现。",
    "Pipeline": "管道是极快的核心范式，但需要值传递与惰性求值支撑，暂不在 AOT 子集内。",
    "AdverbCall": "副词是高阶函数（映射/筛选/归约），需要函数值与列表运行时，暂不在 AOT 子集内。",
    "Import": "模块导入（含 蟒: pybridge）需要 Python 运行时在场，与 AOT 目标冲突。",
    "Export": "模块导出只在模块语境有意义，AOT 只编译单文件顶层程序。",
    "Lambda": "闭包需要环境捕获（逃逸分析或显式 env 结构体），AOT 后端未实现；"
              "请改用顶层 `函数` 定义。",
    "ListLit": "列表需要堆分配与泛型容器运行时，AOT 后端未实现。",
    "DictLit": "字典需要哈希表运行时，AOT 后端未实现。",
    "Index": "索引访问依赖列表/字典运行时，AOT 后端未实现。",
}

# ---------------------------------------------------------------------------
# T2b：`遍历`（For）的受支持形态
# ---------------------------------------------------------------------------

#: 范围动词名。它**不在** `SUPPORTED_VERBS` 里 —— 只有作为 `遍历 ... 于` 的
#: 可迭代对象时才被特殊接受（由 codegen 降级成 C 的计数 for，不产生列表值）。
#: 写成 `定义 赵表 = 范围(1, 10)` 仍然命中「内建动词 范围」：那需要真的列表。
RANGE_VERB: str = "范围"

#: `范围` 的合法参数个数，与 Python range 同构：止 / 起,止 / 起,止,步。
RANGE_ARITY: frozenset = frozenset({1, 2, 3})

#: 遍历不受支持形态的对外特性名。把可用写法直接写进消息，用户不必翻文档。
FOR_ITERABLE_FEATURE: str = (
    "AOT 仅支持 `遍历 变量 于 范围(...)` 或字面量列表遍历"
)

#: `范围` 参数个数不合法。
FOR_RANGE_ARITY_FEATURE: str = "范围(...) 的参数个数（AOT 支持 1~3 个参数）"

#: **上下文相关**的不支持特性（T2a / T2b）。这些特性靠节点类型判不出来，必须
#: 结合所处位置或形态：同一个 `FuncDef` 写在顶层合法、写在函数体里就是闭包；
#: 同一个 `For` 遍历 `范围(...)` 合法、遍历变量就要可迭代对象运行时。
#:
#: 键即诊断消息里出现的对外特性名，值为写进 notes 的说明。
_CONTEXT_FEATURE_NOTES = {
    "嵌套函数定义": "AOT 第一版只支持**顶层无捕获**函数：嵌套函数会捕获外层局部变量，"
                    "需要闭包环境（逃逸分析或 env 结构体），后端未实现。",
    "非顶层函数定义": "AOT 只支持写在程序顶层的 `函数` 定义。写在 `如果`/`当`/`重复` "
                      "块里的函数定义是条件定义，静态编译期无法确定绑定，暂不支持。",
    "函数外的「返回」": "`返回` 只在函数体内有意义；顶层的 `返回` 在解释器里也会报"
                        "「「返回」只能在函数或方法体内使用」。",
    "间接函数调用": "AOT 没有函数值/函数指针表：只支持 `名字(实参...)` 这种直接按名"
                    "调用顶层函数的形式。",
    FOR_ITERABLE_FEATURE:
        "遍历任意可迭代对象需要迭代器协议与堆容器运行时。范围遍历降级成 C 的"
        "计数 for、字面量列表遍历降级成栈上数组 + 下标 for，两者都不落堆，"
        "所以在子集内；遍历变量、函数返回值等则不在。",
    FOR_RANGE_ARITY_FEATURE:
        "`范围` 与 Python 的 range 同构：1 个参数是 `范围(止)`，2 个是 "
        "`范围(起, 止)`，3 个是 `范围(起, 止, 步)`。0 个或超过 3 个参数"
        "在解释器里也是错误。",
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
    """导出子集描述，供 docs/AOT.md 与测试做「文档 ↔ 代码」一致性核对。

    `unsupported_contextual_features`（T2a 新增）列出**靠节点类型判不出来**的
    子集外特性：同一个 `FuncDef` 写在顶层是支持的，写在函数体里就是闭包。
    """
    return {
        "supported_verbs": sorted(SUPPORTED_VERBS),
        "unsupported_node_types": sorted(UNSUPPORTED_NODE_TYPES),
        "unsupported_feature_names": dict(sorted(_NODE_FEATURE_NAMES.items())),
        "unsupported_contextual_features": dict(
            sorted(_CONTEXT_FEATURE_NOTES.items())),
    }


# ---------------------------------------------------------------------------
# 遍历核心
# ---------------------------------------------------------------------------

def _collect(node: Node, in_function: bool = False,
             program_top: bool = False):
    """前序深度优先产出 (特性名, 主体名, 说明, 节点) 四元组。

    命中不支持节点后**不再下钻**其子树：一个类定义只报一条，而不是把类体里
    每个语句都报一遍。未命中的节点继续递归。

    上下文参数（T2a）
    ----------------
    `in_function`   当前是否位于某个 `FuncDef` 体内 —— 决定 `返回` 合法性，
                    以及再遇到 `FuncDef` 时按「嵌套函数（闭包）」拒绝。
    `program_top`   当前节点是否为 `Program.body` 的**直接**成员 —— 只有这一
                    层的 `FuncDef` 在子集内。写在 `如果`/`当`/`重复` 块里的
                    函数定义是条件定义，静态编译期定不下绑定，一律拒绝。
    """
    type_name = type(node).__name__

    if type_name in UNSUPPORTED_NODE_TYPES:
        feature, subject, note = _feature_of_node(node)
        yield (feature, subject, note, node)
        return

    if isinstance(node, FuncDef):
        # 顶层无捕获函数在子集内；嵌套 / 条件定义不在。
        if in_function:
            feature = "嵌套函数定义"
        elif not program_top:
            feature = "非顶层函数定义"
        else:
            feature = None
        if feature is not None:
            yield (feature, node.name, _CONTEXT_FEATURE_NOTES[feature], node)
            return
        # 函数体：进入函数语境，且不再是「顶层」
        for stmt in node.body:
            for item in _collect(stmt, in_function=True, program_top=False):
                yield item
        return

    if isinstance(node, Return) and not in_function:
        feature = "函数外的「返回」"
        yield (feature, "返回", _CONTEXT_FEATURE_NOTES[feature], node)
        return

    if isinstance(node, For):
        # T2b：只接受两种**静态可展开**的遍历源，其余一律拒绝。
        # 必须在这里自己递归 —— 交给下面的通用下钻会误判：`范围` 不在动词白名单
        # 里、`ListLit` 还在 UNSUPPORTED_NODE_TYPES 里，而它们作为遍历源是合法
        # 特例（codegen 会就地展开成计数 for / 栈上数组），不能按普通表达式判。
        iterable = node.iterable
        children: List[Node] = []
        if isinstance(iterable, Call) and getattr(iterable, "verb", "") == RANGE_VERB:
            if len(iterable.args) not in RANGE_ARITY:
                yield (FOR_RANGE_ARITY_FEATURE, RANGE_VERB,
                       _CONTEXT_FEATURE_NOTES[FOR_RANGE_ARITY_FEATURE], iterable)
                return
            children = list(iterable.args)
        elif isinstance(iterable, ListLit):
            children = list(iterable.items)
        else:
            yield (FOR_ITERABLE_FEATURE, type(iterable).__name__,
                   _CONTEXT_FEATURE_NOTES[FOR_ITERABLE_FEATURE], node)
            return
        # 起/止/步 与列表元素本身仍要逐个过门禁：它们可以是变量、算术表达式、
        # 函数调用，但不能是子集外特性。循环体同理。
        for child in children + list(node.body):
            for item in _collect(child, in_function=in_function,
                                 program_top=False):
                yield item
        return

    if isinstance(node, FuncCall):
        # 只支持 `名字(实参...)`：被调方必须是简单标识符。
        # `对象.方法()` 的 MemberAccess 本身也在 UNSUPPORTED_NODE_TYPES 里，
        # 但先在这里给出更贴切的消息。
        callee_type = type(node.func).__name__
        if callee_type != "Ident":
            feature = "间接函数调用"
            yield (feature, callee_type, _CONTEXT_FEATURE_NOTES[feature], node)
            return
        for arg in node.args:
            for item in _collect(arg, in_function=in_function,
                                 program_top=False):
                yield item
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

    child_top = isinstance(node, Program)
    for child in _iter_child_nodes(node):
        for item in _collect(child, in_function=in_function,
                             program_top=child_top):
            yield item
