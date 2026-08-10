# -*- coding: utf-8 -*-
"""极快语言 · service 层 · 跨文件符号索引（v0.17.0 W39 · ADR-29 落地）。

`textDocument/references`（W40）与 `textDocument/rename`（W41）的共同底座：
给定一个名字，查它在哪些文件定义、在哪些文件被引用。

放在 `service/` 而不是 `lsp/` 的理由：三通道（CLI / LSP / Web）都可能要查
符号——例如未来的 `jk 块 查引用`。`lsp/` 是协议适配层，不该独占底座。

**口径**（ADR-29 决策点，别再混——v0.16.0 W32 在这上面踩过）：
位置一律存 **1-based 码点** 的 `line` / `col`（与 `ast_nodes.Node` 同源），
到 LSP 边界才由 `service.position.codepoint_to_utf16` 换算成 0-based UTF-16。
本模块**不做**任何 UTF-16 换算。

**线程安全**：`SessionHost` 会在后台线程做全量构建，同时前台可能来查询，
因此 `SymbolIndex` 的读写都在 `RLock` 下。ADR-29 硬约束：不许同步阻塞
`initialize`。

实现约束：只用标准库（`src/jikuai/` 运行时零第三方依赖是既有约定）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

__all__ = [
    'Symbol', 'Reference', 'SymbolIndex',
    'build_file_symbols', 'top_level_symbol_of',
    'SYMBOL_KIND_FUNCTION', 'SYMBOL_KIND_CLASS', 'SYMBOL_KIND_MODULE',
    'SYMBOL_KIND_METHOD', 'SYMBOL_KIND_VARIABLE',
    'MAX_SYMBOLS',
]

# LSP SymbolKind 子集（与 lsp/jikuai_lsp/server.py 的 _SYMBOL_KIND_* 同值）。
# 放在 service 层：符号种类是索引数据的一部分，不是 LSP 适配层的私货。
SYMBOL_KIND_CLASS = 5
SYMBOL_KIND_METHOD = 6
SYMBOL_KIND_FUNCTION = 12
SYMBOL_KIND_VARIABLE = 13
SYMBOL_KIND_MODULE = 2

#: 符号数上限（ADR-29 决策点 5）。约 500 个中等文件 × 100 符号/文件。
#: 超限行为是**降级 + 明确告知**，不是静默丢弃——见 `SymbolIndex.over_limit`。
MAX_SYMBOLS = 50_000


@dataclass(frozen=True)
class Symbol:
    """一个符号的**定义**位置。`line`/`col` 是 1-based 码点。"""
    name: str
    kind: int
    uri: str
    line: int
    col: int
    end_line: int = 0
    #: 容器名。顶层符号为 None；类方法为所属类名。
    #: `references`/`rename` 用它区分「顶层函数 甲」和「类乙的方法 甲」。
    container: Optional[str] = None

    @property
    def is_top_level(self) -> bool:
        return self.container is None


@dataclass(frozen=True)
class Reference:
    """一个符号的**引用**位置。`line`/`col` 是 1-based 码点。"""
    name: str
    uri: str
    line: int
    col: int


# ---- AST 遍历 ---------------------------------------------------------

def _ast_mod():
    """惰性导入 `ast_nodes`。service 层不在 import 时拉起前端。"""
    from .. import ast_nodes
    return ast_nodes


def _pos(node) -> Tuple[int, int]:
    """取节点的 1-based 码点位置；解析器没标注时落 (1, 1) 而不是 (0, 0)。

    落 1 而不是 0：`col=0` 会让 `codepoint_to_utf16` 直接返回 0，与「行首」
    这个真实位置撞车，出错时反而更难看出是缺位置信息。
    """
    return (getattr(node, 'line', 0) or 1, getattr(node, 'col', 0) or 1)


def top_level_symbol_of(node) -> Optional[Symbol]:
    """把一个**顶层** AST 节点投影成 `Symbol`；不认识返回 None。

    这是 `documentSymbol`（W32）与本模块共用的**唯一**一份「哪些节点算符号」
    判定。W32 原本把这套 isinstance 链写在 `server.py` 里，W39 抽到这里，
    `server.py` 只保留 LSP Range/selectionRange 的格式化——两处各写一份
    isinstance 链是 ADR-29 明确要避免的。

    `uri` 由调用方填（本函数不知道自己在处理哪个文件），这里先留空串。
    """
    A = _ast_mod()
    line, col = _pos(node)
    if isinstance(node, A.FuncDef):
        return Symbol(node.name, SYMBOL_KIND_FUNCTION, '', line, col,
                      getattr(node, 'end_line', 0) or line)
    if isinstance(node, A.ClassDef):
        return Symbol(node.name, SYMBOL_KIND_CLASS, '', line, col,
                      getattr(node, 'end_line', 0) or line)
    if isinstance(node, A.Import):
        module = getattr(node, 'module', '') or ''
        if not module:
            return None
        return Symbol(module, SYMBOL_KIND_MODULE, '', line, col, line)
    return None


def _walk_refs(node, out: List[Reference], uri: str) -> None:
    """递归收集引用。

    收什么：
      - `Ident.name`         —— 变量/函数名的裸使用
      - `FuncCall.func`      —— 若是 Ident 则由上面那条覆盖（不重复记）
      - `NewInstance.class_name` —— `新建 类名` 是对类的引用
      - `MemberAccess.attr`  —— `对象.方法` 是对方法名的引用
      - `Assign.target`      —— 赋值也算引用（rename 必须改到，否则改坏代码）

    **不收** `Call.verb`：那是内置动词（`加`/`打印`…），不是用户符号，
    收进来会让 `references_to('加')` 返回整个仓库，纯噪声。

    为什么手写递归而不用通用 visitor：`ast_nodes` 没有 `iter_child_nodes`
    之类的通用遍历，而节点种类是有限且稳定的（`ast_nodes.py` 全文 236 行）。
    手写一份显式的、能逐条讲清「收/不收」理由的遍历，比引入一层反射式
    通用遍历更好审。
    """
    if node is None:
        return
    A = _ast_mod()

    if isinstance(node, A.Ident):
        line, col = _pos(node)
        out.append(Reference(node.name, uri, line, col))
        return
    if isinstance(node, A.NewInstance):
        line, col = _pos(node)
        if node.class_name:
            out.append(Reference(node.class_name, uri, line, col))
        for a in (node.args or []):
            _walk_refs(a, out, uri)
        return
    if isinstance(node, A.MemberAccess):
        _walk_refs(node.obj, out, uri)
        if node.attr:
            line, col = _pos(node)
            out.append(Reference(node.attr, uri, line, col))
        return

    # 其余节点：按字段递归。列出字段名而不是 __dict__ 全扫，
    # 免得把 `params: List[str]`（纯字符串，不是节点）当节点递归。
    for 字段 in ('value', 'obj', 'index', 'cond', 'iterable', 'count',
                 'inner', 'accumulator', 'func', 'target'):
        子 = getattr(node, 字段, None)
        if 子 is not None and isinstance(子, A.Node):
            _walk_refs(子, out, uri)
    for 字段 in ('args', 'items', 'stages', 'body', 'then_branch',
                 'else_branch', 'ctor_body', 'catch_body', 'finally_body'):
        子表 = getattr(node, 字段, None)
        if not 子表:
            continue
        for 子 in 子表:
            if isinstance(子, A.Node):
                _walk_refs(子, out, uri)
            elif isinstance(子, (tuple, list)):
                # DictLit.items 是 (键, 值) 二元组
                for 项 in 子:
                    if isinstance(项, A.Node):
                        _walk_refs(项, out, uri)
    # If.elif_branches 是 [(cond, body), ...]
    for cond, body in (getattr(node, 'elif_branches', None) or []):
        _walk_refs(cond, out, uri)
        for 子 in (body or []):
            _walk_refs(子, out, uri)
    # ClassDef.methods 是 {name: FuncDef}
    methods = getattr(node, 'methods', None)
    if isinstance(methods, dict):
        for m in methods.values():
            _walk_refs(m, out, uri)


def _collect_defs(ast, uri: str) -> List[Symbol]:
    """收集**定义**（ADR-29 决策点 1 的粒度）。

    收：顶层函数 / 顶层类 / 类方法 / 顶层变量（`定义`/`赋值`）/ 块导出名
        （`从 … 导入 X` 的 X、`导出 X` 的 X）
    不收：局部变量（函数体或构造体内的 `定义`/`赋值`）、形参
          —— 作用域内改名不需要跨文件，收进来只会让 rename 误伤。
    """
    A = _ast_mod()
    defs: List[Symbol] = []
    for node in (getattr(ast, 'body', None) or []):
        sym = top_level_symbol_of(node)
        if sym is not None:
            # 用 dataclasses.replace 补 uri；frozen dataclass 不能就地改
            defs.append(Symbol(sym.name, sym.kind, uri, sym.line, sym.col,
                               sym.end_line))
        if isinstance(node, A.ClassDef):
            # 类方法：`methods` 是 {名: FuncDef}
            for 方法名, fd in (node.methods or {}).items():
                line, col = _pos(fd)
                defs.append(Symbol(方法名, SYMBOL_KIND_METHOD, uri, line, col,
                                   getattr(fd, 'end_line', 0) or line,
                                   container=node.name))
        elif isinstance(node, A.Define):
            # 顶层 `定义 X = …`。函数体内的 Define 不会走到这里
            # （这个 for 只遍历 Program.body），正是我们要的作用域过滤。
            line, col = _pos(node)
            defs.append(Symbol(node.name, SYMBOL_KIND_VARIABLE, uri,
                               line, col, line))
        elif isinstance(node, A.Assign):
            目标 = node.target
            if isinstance(目标, A.Ident) and 目标.name:
                line, col = _pos(node)
                defs.append(Symbol(目标.name, SYMBOL_KIND_VARIABLE, uri,
                                   line, col, line))
        elif isinstance(node, A.Import):
            # `从 blocks.财务.个税 导入 缴税` → `缴税` 在本文件可见，
            # 是跨文件 references 的关键锚点（W40 的用例之一）。
            for 名 in (getattr(node, 'names', None) or []):
                if 名:
                    line, col = _pos(node)
                    defs.append(Symbol(名, SYMBOL_KIND_FUNCTION, uri,
                                       line, col, line))
        elif isinstance(node, A.Export):
            for 名 in (getattr(node, 'names', None) or []):
                if 名:
                    line, col = _pos(node)
                    defs.append(Symbol(名, SYMBOL_KIND_FUNCTION, uri,
                                       line, col, line))
    return defs


def build_file_symbols(uri: str, text: str) -> Tuple[List[Symbol], List[Reference]]:
    """解析一个文件，返回 `(定义列表, 引用列表)`。

    编译失败不抛：返回两个空列表。理由——用户正在打字时文件常常是语法不完整的，
    让索引因此抛异常会把整个后台构建线程打挂。`compile_source` 内部已把
    ParseError 兜底成诊断，多数情况下仍能拿到可用的 AST。
    """
    if not text:
        return ([], [])
    try:
        from ..frontend import compile_source
        ast = compile_source(text, file=uri).ast
    except Exception:
        return ([], [])
    if ast is None:
        return ([], [])
    defs = _collect_defs(ast, uri)
    refs: List[Reference] = []
    for node in (getattr(ast, 'body', None) or []):
        _walk_refs(node, refs, uri)
    return (defs, refs)


# ---- 索引 -------------------------------------------------------------

class SymbolIndex:
    """跨文件符号索引。线程安全（后台构建 + 前台查询并发）。

    结构（ADR-29 决策点 4）：
      `_defs`     name → [Symbol]        正向：谁定义了这个名字
      `_refs`     name → [Reference]     反向索引：谁引用了这个名字
      `_uri_defs` uri  → [Symbol]        按文件反查，`remove_file` 用
      `_uri_refs` uri  → [Reference]

    反向索引按「被引用方 → 引用方集合」组织，所以改一个文件只需要
    `remove_file` + `add_file` 它自己，不用重建全表。
    """

    def __init__(self, max_symbols: int = MAX_SYMBOLS):
        self._lock = threading.RLock()
        self._defs: Dict[str, List[Symbol]] = {}
        self._refs: Dict[str, List[Reference]] = {}
        self._uri_defs: Dict[str, List[Symbol]] = {}
        self._uri_refs: Dict[str, List[Reference]] = {}
        self._max = max_symbols
        self._over_limit = False
        self._ready = False

    # -- 写 --

    def add_file(self, uri: str, text: str) -> None:
        """（重新）索引一个文件。已存在则先移除旧条目，天然幂等。"""
        defs, refs = build_file_symbols(uri, text)
        self.set_file(uri, defs, refs)

    def set_file(self, uri: str, defs: List[Symbol],
                 refs: List[Reference]) -> None:
        """直接灌入已算好的定义/引用（后台线程算、主线程灌，避免锁内解析）。"""
        with self._lock:
            self.remove_file(uri)
            if self._symbol_count_locked() + len(defs) > self._max:
                # ADR-29 决策点 5：超限就停止收录并置降级标记，
                # 由调用方（LSP server）负责 window/showMessage 告知用户。
                self._over_limit = True
                return
            if defs:
                self._uri_defs[uri] = list(defs)
                for s in defs:
                    self._defs.setdefault(s.name, []).append(s)
            if refs:
                self._uri_refs[uri] = list(refs)
                for r in refs:
                    self._refs.setdefault(r.name, []).append(r)

    def remove_file(self, uri: str) -> None:
        """移除一个文件的全部条目（文件删除 / 重新索引前的清理）。"""
        with self._lock:
            for s in self._uri_defs.pop(uri, []):
                桶 = self._defs.get(s.name)
                if 桶 is None:
                    continue
                剩 = [x for x in 桶 if x.uri != uri]
                if 剩:
                    self._defs[s.name] = 剩
                else:
                    self._defs.pop(s.name, None)
            for r in self._uri_refs.pop(uri, []):
                桶 = self._refs.get(r.name)
                if 桶 is None:
                    continue
                剩 = [x for x in 桶 if x.uri != uri]
                if 剩:
                    self._refs[r.name] = 剩
                else:
                    self._refs.pop(r.name, None)

    def mark_ready(self) -> None:
        """首次全量构建完成。查询方据此决定要不要回「正在索引」。"""
        with self._lock:
            self._ready = True

    # -- 读 --

    def definitions_of(self, name: str) -> List[Symbol]:
        """返回 `name` 的所有定义，按 (uri, line, col) 稳定排序。

        排序是契约：W40 的 references 要求「结果按 uri + 行号排序」，
        不稳定的顺序会让测试与用户预期都飘。
        """
        with self._lock:
            桶 = list(self._defs.get(name, ()))
        return sorted(桶, key=lambda s: (s.uri, s.line, s.col))

    #: `lookup` 是 `definitions_of` 的别名（ADR-29 里两个名字都出现过）。
    lookup = definitions_of

    def references_to(self, name: str) -> List[Reference]:
        """返回 `name` 的所有引用，按 (uri, line, col) 稳定排序。"""
        with self._lock:
            桶 = list(self._refs.get(name, ()))
        return sorted(桶, key=lambda r: (r.uri, r.line, r.col))

    def all_symbols(self) -> List[Symbol]:
        with self._lock:
            全部 = [s for 桶 in self._defs.values() for s in 桶]
        return sorted(全部, key=lambda s: (s.uri, s.line, s.col))

    def symbols_in(self, uri: str) -> List[Symbol]:
        with self._lock:
            桶 = list(self._uri_defs.get(uri, ()))
        return sorted(桶, key=lambda s: (s.line, s.col))

    def indexed_uris(self) -> List[str]:
        with self._lock:
            return sorted(set(self._uri_defs) | set(self._uri_refs))

    def _symbol_count_locked(self) -> int:
        return sum(len(v) for v in self._uri_defs.values())

    def symbol_count(self) -> int:
        with self._lock:
            return self._symbol_count_locked()

    @property
    def over_limit(self) -> bool:
        """是否已触发 ADR-29 的符号数超限降级。"""
        with self._lock:
            return self._over_limit

    @property
    def ready(self) -> bool:
        """首次全量构建是否完成。未完成时查询结果是**部分的**。"""
        with self._lock:
            return self._ready

    def clear(self) -> None:
        with self._lock:
            self._defs.clear()
            self._refs.clear()
            self._uri_defs.clear()
            self._uri_refs.clear()
            self._over_limit = False
            self._ready = False
