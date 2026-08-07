# -*- coding: utf-8 -*-
"""极快语言 - AST 节点定义。"""

from dataclasses import dataclass, field
from typing import List, Optional, Any


class Node:
    """AST 节点基类。

    line/col 为源码位置（1-based）。以类属性形式提供默认值，避免影响
    子类 @dataclass 字段顺序；解析器构造节点后按需赋值。
    """
    line: int = 0
    col: int = 0


# ---------- 字面量与标识符 ----------

@dataclass
class NumberLit(Node):
    value: Any                 # int 或 float


@dataclass
class StringLit(Node):
    value: str


@dataclass
class MoneyLit(Node):
    """人民币金额字面量。"""
    value: float


@dataclass
class BoolLit(Node):
    value: bool


@dataclass
class NilLit(Node):
    pass


@dataclass
class Ident(Node):
    name: str


# ---------- 表达式 ----------

@dataclass
class Call(Node):
    """动词调用：verb(args...) 或 通过元数驱动免括号调用。"""
    verb: str
    args: List[Node] = field(default_factory=list)


@dataclass
class FuncCall(Node):
    """用户函数调用（含括号或紧邻参数）。"""
    func: Node
    args: List[Node] = field(default_factory=list)


@dataclass
class Pipeline(Node):
    """管道表达式：a，b，c。"""
    stages: List[Node]


@dataclass
class AdverbCall(Node):
    """副词高阶调用：皆/只/归 + Call。"""
    adverb: str
    inner: Node                # 通常是 Call
    accumulator: Optional[Node] = None   # 归 特有


@dataclass
class MemberAccess(Node):
    """成员访问：obj.attr"""
    obj: Node
    attr: str


@dataclass
class Index(Node):
    """索引：list[i]"""
    obj: Node
    index: Node


@dataclass
class ListLit(Node):
    items: List[Node] = field(default_factory=list)


@dataclass
class Lambda(Node):
    params: List[str]
    body: List[Node]


# ---------- 语句 ----------

@dataclass
class Define(Node):
    """定义变量或函数：定义 X = expr。"""
    name: str
    value: Node


@dataclass
class Assign(Node):
    """赋值：赋值 X = expr。"""
    target: Node               # Ident / MemberAccess / Index
    value: Node


@dataclass
class If(Node):
    cond: Node
    then_branch: List[Node]
    elif_branches: List = field(default_factory=list)  # [(cond, body), ...]
    else_branch: Optional[List[Node]] = None


@dataclass
class While(Node):
    cond: Node
    body: List[Node]


@dataclass
class For(Node):
    var: str
    iterable: Node
    body: List[Node]


@dataclass
class Repeat(Node):
    count: Node
    body: List[Node]


@dataclass
class Break(Node):
    pass


@dataclass
class Continue(Node):
    pass


@dataclass
class Return(Node):
    value: Optional[Node] = None


@dataclass
class FuncDef(Node):
    name: str
    params: List[str]
    body: List[Node]


@dataclass
class ClassDef(Node):
    name: str
    parent: Optional[str]
    ctor_params: List[str]
    ctor_body: List[Node]
    methods: dict = field(default_factory=dict)   # name -> FuncDef
    ctor_defined: bool = False                     # 是否显式定义了 构造（即使空体）


@dataclass
class NewInstance(Node):
    class_name: str
    args: List[Node]


@dataclass
class Try(Node):
    body: List[Node]
    catch_var: Optional[str]
    catch_body: Optional[List[Node]]
    finally_body: Optional[List[Node]]


@dataclass
class Throw(Node):
    value: Node


@dataclass
class Import(Node):
    module: str
    names: Optional[List[str]] = None
    alias: Optional[str] = None


@dataclass
class Export(Node):
    """导出语句：导出 名字1 名字2。"""
    names: List[str] = field(default_factory=list)


@dataclass
class Program(Node):
    body: List[Node] = field(default_factory=list)
