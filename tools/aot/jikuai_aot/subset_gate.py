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
    AdverbCall, Assign, Call, DictLit, For, FuncCall, FuncDef, Import, ListLit,
    Node, Program, Repeat, Return, While,
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
#: **W104（ADR-37 第一切片）**把 `ListLit` / `DictLit` / `Index` 移出本集合：
#: 容器现在有真的堆运行时（`JKList` / `JKDict`，见 codegen 的 `_C_RUNTIME`）。
#: 但只放行到 ADR-37 划的那条线上，四类顺延项靠**别的机制**继续拒：
#:   - `MemberAccess`（`.成员`）—— 仍在本集合里，顺延，见 ADR-37 §2.1；
#:   - 下标**写**（`列表[i] = 值`）—— 靠 `_collect` 里 `Assign` 的
#:     「赋值目标不是 Ident」判定，顺延，见 ADR-37 §2.3；
#:   - 容器作字典键 —— 靠上下文规则 `容器作字典键`，见 ADR-37 §2.4；
#:   - 循环体内构造容器 —— 靠上下文规则 `循环体内构造容器`，见 ADR-37 §2.2。
#:
#: 仍然不支持的都有明确的运行时依赖：`Lambda` 要环境捕获、
#: `MemberAccess` 要对象模型、类与异常要对象模型与栈展开。
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
})

#: 节点类型名 → 对外中文特性名。
#:
#: 这是一张**纯粹的「节点类型 → 中文叫法」查表**，不等于「不支持清单」：
#: `Index` 已进子集（读），但 `Assign` 分支报「赋值目标 索引访问」时还要用它
#: 取中文名。`describe_subset()` 会按 `UNSUPPORTED_NODE_TYPES` 过滤后才对外，
#: 免得文档把已支持的特性列成不支持。
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
    "Index": "索引访问",
}

#: 各不支持特性的补充说明（写进 Diagnostic.notes，也用于 docs/AOT.md 对齐）。
_NODE_FEATURE_NOTES = {
    "ClassDef": "类与继承需要运行时对象模型，AOT 后端没有堆对象与方法派发。",
    "NewInstance": "类与继承需要运行时对象模型，AOT 后端没有堆对象与方法派发。",
    "MemberAccess": "成员访问依赖对象模型（类实例的字段/方法、继承链、模块导出名），"
                    "不只是「字典取键」那一条；把它纳入切片等于顺手实现一个最小对象"
                    "模型。**顺延项，见 ADR-37 §2.1**（等 v0.23.0 对象模型 ADR）。"
                    "字典取值请写 `字典[键]`（Index），那个已在子集内。",
    "Try": "异常需要栈展开机制，AOT 后端未实现。",
    "Throw": "异常需要栈展开机制，AOT 后端未实现。",
    "Pipeline": "管道是极快的核心范式，但需要值传递与惰性求值支撑，暂不在 AOT 子集内。",
    "AdverbCall": "副词是高阶函数（映射/筛选/归约），需要函数值与列表运行时，暂不在 AOT 子集内。",
    "Import": "模块导入（含 蟒: pybridge）需要 Python 运行时在场，与 AOT 目标冲突。",
    "Export": "模块导出只在模块语境有意义，AOT 只编译单文件顶层程序。",
    "Lambda": "闭包需要环境捕获（逃逸分析或显式 env 结构体），AOT 后端未实现；"
              "请改用顶层 `函数` 定义。",
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

#: **W104 / ADR-37 §2.4**：容器作字典键。键的相等与哈希只对标量定义。
DICT_KEY_FEATURE: str = "容器作字典键"

#: **W104 / ADR-37 §2.2**：在循环体内构造容器。第一切片的内存策略是
#: 「malloc 不 free」，循环里反复建容器会让内存只涨不落。
LOOP_CONTAINER_FEATURE: str = "循环体内构造容器"

#: **上下文相关**的不支持特性（T2a / T2b / W104）。这些特性靠节点类型判不出来，
#: 必须结合所处位置或形态：同一个 `FuncDef` 写在顶层合法、写在函数体里就是闭包；
#: 同一个 `For` 遍历 `范围(...)` 合法、遍历变量就要可迭代对象运行时；
#: 同一个 `【1，2】` 写在顶层合法、写在循环体里就会让 arena 无界增长。
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
    DICT_KEY_FEATURE:
        "字典键第一切片限标量（字符串/数字/布尔/空/人民币）：相等与哈希只对标量"
        "定义好了，容器作键要先定义「容器相等」语义。**顺延，见 ADR-37 §2.4**"
        "（「字典键限标量」），不是 bug。",
    LOOP_CONTAINER_FEATURE:
        "第一切片的容器是 malloc 后**全程不回收**的 arena（ADR-37 §2.2：不做"
        "引用计数也不做 GC）。不回收的 arena 语义下循环内建容器会让内存只涨不落，"
        "所以循环体内构造容器**顺延，见 ADR-37 §2.2** 升级触发线 (a)：命中它即"
        "启动内存管理 ADR。注意 `遍历 变量 于 【字面量列表】` 的遍历源不受此限 —— "
        "那个列表是就地展开成栈上数组的，不落堆。",
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
    子集外特性：同一个 `FuncDef` 写在顶层是支持的，写在函数体里就是闭包；
    W104 起还有「容器作字典键」「循环体内构造容器」两条，见 ADR-37 §2.4/§2.2。

    `unsupported_feature_names` 按 `UNSUPPORTED_NODE_TYPES` **过滤**后才导出 ——
    `_NODE_FEATURE_NAMES` 里还留着已进子集的 `Index`（`Assign` 分支报
    「赋值目标 索引访问」时要用它取中文名），不过滤会把它误报成不支持特性。
    """
    return {
        "supported_verbs": sorted(SUPPORTED_VERBS),
        "unsupported_node_types": sorted(UNSUPPORTED_NODE_TYPES),
        "unsupported_feature_names": {
            name: label
            for name, label in sorted(_NODE_FEATURE_NAMES.items())
            if name in UNSUPPORTED_NODE_TYPES
        },
        "unsupported_contextual_features": dict(
            sorted(_CONTEXT_FEATURE_NOTES.items())),
    }



# ---------------------------------------------------------------------------
# 遍历核心
# ---------------------------------------------------------------------------

def _collect(node: Node, in_function: bool = False,
             program_top: bool = False, in_loop: bool = False):
    """前序深度优先产出 (特性名, 主体名, 说明, 节点) 四元组。

    命中不支持节点后**不再下钻**其子树：一个类定义只报一条，而不是把类体里
    每个语句都报一遍。未命中的节点继续递归。

    上下文参数（T2a / T2b / W104）
    -----------------------------
    `in_function`   当前是否位于某个 `FuncDef` 体内 —— 决定 `返回` 合法性，
                    以及再遇到 `FuncDef` 时按「嵌套函数（闭包）」拒绝。
    `program_top`   当前节点是否为 `Program.body` 的**直接**成员 —— 只有这一
                    层的 `FuncDef` 在子集内。写在 `如果`/`当`/`重复` 块里的
                    函数定义是条件定义，静态编译期定不下绑定，一律拒绝。
    `in_loop`       当前是否位于 `遍历`/`当`/`重复` 的**循环体**内 —— 决定
                    `ListLit`/`DictLit` 是否命中 `循环体内构造容器`
                    （ADR-37 §2.2：容器 malloc 后不回收，循环里建就只涨不落）。
                    只有**体**算循环内；`重复 N 次` 的 N、`遍历 ... 于` 的遍历源
                    都在进入循环前求值一次，不算。

    `in_loop` 的**已知盲区**（记在这里而不是假装没有）：函数体一律按
    `in_loop=False` 检查，所以「顶层函数里造容器 + 循环里反复调它」这条路径
    绕过本规则。堵它要做调用图分析，超出第一切片；已写进 docs/AOT.md 局限。
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
        # 函数体：进入函数语境，且不再是「顶层」。
        # in_loop 归零 —— 函数体自己不是循环体（盲区见上面 docstring）。
        for stmt in node.body:
            for item in _collect(stmt, in_function=True, program_top=False,
                                 in_loop=False):
                yield item
        return

    if isinstance(node, Return) and not in_function:
        feature = "函数外的「返回」"
        yield (feature, "返回", _CONTEXT_FEATURE_NOTES[feature], node)
        return

    if isinstance(node, (ListLit, DictLit)):
        # W104（ADR-37 第一切片）：容器字面量已进子集，但有两条上下文红线。
        #
        # 红线一（§2.2）：循环体内构造容器。第一切片 malloc 不 free，循环里
        # 每轮新建一个容器 = 内存只涨不落。注意这里**收不到** `遍历 变量 于
        # 【字面量列表】` 的那个遍历源 —— For 分支只把它的 items 递下来，
        # ListLit 节点本身不进 `_collect`，因为它会被就地展开成栈上数组。
        if in_loop:
            yield (LOOP_CONTAINER_FEATURE, type_name,
                   _CONTEXT_FEATURE_NOTES[LOOP_CONTAINER_FEATURE], node)
            return
        # 红线二（§2.4）：容器作字典键。键的相等/哈希只对标量定义。
        # 只查字面量形态；「变量里装着容器」要到运行期才知道，由 C 运行时的
        # `jk_dict_set` 兜（文案与解释器 `_eval_DictLit` 的 ADR-23b 检查一致）。
        if isinstance(node, DictLit):
            for pair in node.items:
                key_node = pair[0]
                if isinstance(key_node, (ListLit, DictLit)):
                    yield (DICT_KEY_FEATURE, type(key_node).__name__,
                           _CONTEXT_FEATURE_NOTES[DICT_KEY_FEATURE], key_node)
                    return
        # 元素 / 键 / 值本身仍要逐个过门禁：它们可以是变量、算术、嵌套容器，
        # 但不能是子集外特性。
        for child in _iter_child_nodes(node):
            for item in _collect(child, in_function=in_function,
                                 program_top=False, in_loop=in_loop):
                yield item
        return

    if isinstance(node, For):
        # T2b：只接受两种**静态可展开**的遍历源，其余一律拒绝。
        # 必须在这里自己递归 —— 交给下面的通用下钻会误判：`范围` 不在动词白名单
        # 里，而它作为遍历源是合法特例（codegen 会就地展开成计数 for）；字面量
        # 列表遍历源同理会展开成栈上数组，所以**不能**让它去撞上面那条
        # 「循环体内构造容器」规则（它压根不落堆）。
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
        # 函数调用，但不能是子集外特性。这些都在进入循环**前**求值一次，
        # 所以沿用外层的 in_loop，而循环体才是 in_loop=True。
        for child in children:
            for item in _collect(child, in_function=in_function,
                                 program_top=False, in_loop=in_loop):
                yield item
        for stmt in node.body:
            for item in _collect(stmt, in_function=in_function,
                                 program_top=False, in_loop=True):
                yield item
        return

    if isinstance(node, While):
        # 条件每轮都重新求值，语义上就在循环内，所以也按 in_loop=True 查。
        for child in [node.cond] + list(node.body):
            for item in _collect(child, in_function=in_function,
                                 program_top=False, in_loop=True):
                yield item
        return

    if isinstance(node, Repeat):
        # 次数只在进入前求值一次（对齐 codegen 的 `_emit_repeat`），不算循环内。
        for item in _collect(node.count, in_function=in_function,
                             program_top=False, in_loop=in_loop):
            yield item
        for stmt in node.body:
            for item in _collect(stmt, in_function=in_function,
                                 program_top=False, in_loop=True):
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
                                 program_top=False, in_loop=in_loop):
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
        # 赋值目标必须是简单标识符。
        #   - `对象.成员 = 值`：MemberAccess 目标仍在 UNSUPPORTED_NODE_TYPES 里；
        #   - `列表[i] = 值`：Index 已进子集（**读**），但**写**顺延，靠这条挡。
        # 两者都在这里报「赋值目标 X」，比等下钻时报节点名更贴近用户写的那行。
        target = getattr(node, "target", None)
        if target is not None and type(target).__name__ != "Ident":
            tgt_type = type(target).__name__
            if tgt_type == "Index":
                note = ("AOT 只支持赋值给简单变量名（Ident）。下标**写**"
                        "（`列表[i] = 值` / `字典[键] = 值`）是**顺延项，见 "
                        "ADR-37 §2.3**：写是就地可变，一进循环就踩 §2.2 的内存"
                        "升级触发线 (b)，所以留到内存管理 ADR 里与「可变 + 回收」"
                        "一起做，不是 bug。下标**读**（`列表[i]` 取值）已在子集内。")
            else:
                note = "AOT 只支持赋值给简单变量名（Ident）。"
            yield (
                "赋值目标 {}".format(_NODE_FEATURE_NAMES.get(tgt_type, tgt_type)),
                tgt_type,
                note,
                node,
            )
            return

    child_top = isinstance(node, Program)
    for child in _iter_child_nodes(node):
        for item in _collect(child, in_function=in_function,
                             program_top=child_top, in_loop=in_loop):
            yield item

