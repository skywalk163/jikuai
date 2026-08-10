# ADR-29：跨文件符号表（v0.17.0 W38）

- 日期：2026-08-10
- 状态：accepted
- 驱动：BACKLOG §1「LSP references / rename 需跨文件底座」

---

## 背景

v0.16.0 的 `documentSymbol` 只返回**当前文件**的顶层符号。要支持
`textDocument/references`（W40）和 `textDocument/rename`（W41），需要一张
跨文件的符号索引：给定一个名字，能查到它在哪些文件定义、在哪些文件被引用。

## 决策点

### 1. 索引粒度

**索引什么**：
- 函数定义（`FuncDef.name`）
- 类定义（`ClassDef.name`）
- 类方法（`ClassDef.methods` 字典的 key）
- 顶层变量赋值（`Assign` 节点的左侧，当前 `documentSymbol` 未发射——本轮新增）
- 块导出名（`Import` 节点的 `names`，即 `从 blocks.x.y 导入 z` 中的 `z`）

**不索引**：
- 局部变量（函数体内的 `Assign`）——作用域内改名不需要跨文件
- 形参——同理

**理由**：极快没有模块级 `let`/`const` 和局部作用域声明的语法区分，判定「是否局部」
的规则是：出现在 `FuncDef.body` 或 `ClassDef.ctor_body` 里的 `Assign` 视为局部。
顶层 `Assign`（`Program.body` 直接子节点）视为模块级。

### 2. 索引范围

- `workspaceFolders`（多根支持，W38 同步接线）
- `blocks_root()`（标准库块目录，总是扫）
- 已打开文档（`TextDocumentStore` 中有的 uri）

**三种情形**：
- **单根**（最常见）：`workspaceFolders[0]` = 项目根，递归扫 `*.jk` 文件
- **多根**：所有 `workspaceFolders` 各自递归扫
- **无根**（直接打开单文件）：只索引已打开文档 + `blocks_root()`

### 3. 构建时机

- **启动后异步全量扫**：`initialized` 通知到达后，在后台线程做首次全量索引构建
  - **不许同步阻塞 `initialize`**（硬约束）——否则大仓库开编辑器就卡住
  - 构建期间 `documentSymbol` / `hover` / `completion` 的现有逻辑不受影响（它们
    只读单文件 AST）
  - `references` / `rename` 在索引未就绪时返回 `null`（LSP 允许）并发送
    `window/showMessage` 通知「符号索引正在构建中」
- **增量更新**：`textDocument/didChange` 和 `textDocument/didOpen` 触发单文件重建
  - 只重建该文件的符号表条目 + 它作为引用方的那部分引用图

### 4. 失效策略

- 文件改动（`didChange`）→ 只重建该 uri 的符号定义 + 引用
- 文件删除（`didClose` 且文件不在 workspace 里）→ 移除该 uri 的全部条目
- `workspaceFolders` 变更（`workspace/didChangeWorkspaceFolders`）→ 增量增删对应根
- **反向索引结构**：`被引用符号名 → set[Location(uri, line, col)]`
  改一个文件只更新它作为引用方那部分，不用重建全部

### 5. 内存上限

- 符号数上限：**50,000**（对应约 500 个中等文件 × 100 符号/文件）
- 超限行为：
  - 停止添加新符号，降级为「仅当前文件」
  - 发 `window/showMessage`（info 级别）告知用户：「符号索引已达上限，跨文件
    references/rename 降级为仅当前文件。请减少工作区规模或拆分 workspaceFolders。」
  - **不静默退化**——用户必须能看到降级发生了

### 6. 与 `module_loader` 的关系

`从 blocks.X.Y 导入 Z` 的 dotpath 解析复用 `jikuai.module_loader.ModuleLoader`
的 `resolve_import(module_dotpath)` → 文件路径。符号索引只负责记录「哪个 uri
定义了 Z」和「哪些 uri 引用了 Z」，路径解析不另写一份。

---

## 数据结构草案

```python
@dataclass
class SymbolDef:
    """一个符号的定义位置。"""
    name: str
    kind: int          # LSP SymbolKind（Function=12, Class=5, Variable=13, ...）
    uri: str
    line: int          # 1-based 码点口径（AST 原始值）
    col: int           # 1-based 码点口径
    end_line: int      # 定义体末行（用于 range）

@dataclass
class SymbolRef:
    """一个符号的引用位置。"""
    name: str
    uri: str
    line: int
    col: int

class SymbolIndex:
    # 定义：name → list[SymbolDef]（同名可能跨文件存在）
    _defs: Dict[str, List[SymbolDef]]
    # 引用：name → list[SymbolRef]
    _refs: Dict[str, List[SymbolRef]]
    # 按 uri 反查（用于 remove_file）
    _uri_defs: Dict[str, List[SymbolDef]]
    _uri_refs: Dict[str, List[SymbolRef]]

    def add_file(self, uri: str, text: str) -> None: ...
    def remove_file(self, uri: str) -> None: ...
    def definitions_of(self, name: str) -> List[SymbolDef]: ...
    def references_to(self, name: str) -> List[SymbolRef]: ...
    def all_symbols(self) -> Iterable[SymbolDef]: ...
    def lookup(self, name: str) -> List[SymbolDef]: ...  # alias for definitions_of
    def is_ready(self) -> bool: ...
    def symbol_count(self) -> int: ...
```

位置一律存**1-based 码点**口径（与 AST 同源），到 LSP 边界才经
`position.codepoint_to_utf16` 换算（v0.16.0 W32 踩过的口径问题）。

---

## 不做的事

- **不索引 `stdlib/blocks/` 内部的函数/类**——块对外暴露的接口只是 `导出名`，
  内部实现不该被 rename 影响
- **不做跨块的 rename**——改块导出名会牵动 `块.json` + G13 全局唯一，超出 LSP
  层的职责范围（W41 会显式拒绝）
- **不做语义分析**——符号索引是纯文本级/AST 级的名字匹配，不做类型推导

---

## 风险

- 异步构建复杂度——预案：若失控退化为「惰性建索引：首次 references 请求时才建」
- 大仓库初次扫描慢——预案：限制扫描文件数（与符号上限联动），超限降级
