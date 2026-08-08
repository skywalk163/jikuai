# ADR-14 · 诊断内核（v0.5.0 · M4）

- 状态：Accepted
- 日期：2026-08-08
- 决策者：架构师（承接交付总监 M4 编排）
- 落地环节：v0.5.0 · M4 · P1 诊断支线
- 相关：ADR-09（类作用域白名单）、基线校正说明-v0.5.0（偏差 A/B）

---

## 1. 背景

现有 `src/jikuai/errors.py`（约 80 行）提供 `ErrorCategory`/`ErrorInfo`/`ErrorFormatter`/`spelling_suggestion` 四个公开符号，是 v0.3.x 以来错误报告的唯一基础设施。随着 v0.5.0 引入 LSP 支持、多级诊断与稳定错误码，该模块存在以下不足：

- **缺 severity**：`ErrorInfo` 只能表达 error，无法承载 warning/hint（如副词后接非内建动词的 `JK-W1001` 透传警告）
- **缺稳定错误码**：测试与工具链只能断言文案字符串，文案一改即 break
- **缺 end 位置**：LSP `Diagnostic` 需要 `Range(start, end)`，当前只有 `line`/`col` 单点
- **`spelling_suggestion` 只给单候选**：签名 `(name, candidates, max_distance=1) -> Optional[str]`，无法满足 US-M4-02「编辑距离 ≤2 + 多并列候选」
- **`JiKuaiError` 不在 `errors.py`**：实际定义在 `evaluator.py`（见基线校正偏差 A），`diagnostics/` 若依赖 `JiKuaiError` 将产生循环耦合

---

## 2. 方案候选

### A · 原地扩展 `ErrorInfo` 字段

在 `errors.py` 中给 `ErrorInfo` 追加 `severity`/`end_line`/`end_col`/`code`/`suggestions` 等字段。

- 优点：改动面小，单文件
- 缺点：`ErrorInfo` 已被外部消费（`__all__` 导出、测试直接构造），追加必填字段即 BREAKING；且该文件会膨胀到承担 formatter + sink + mapping 多重职责，违反单一职责

### B · 新建 `diagnostics/` 包 + `errors.py` 降级为兼容外壳（采纳）

新建 `src/jikuai/diagnostics/` 包，定义全新数据模型与 sink 协议；`errors.py` 保留所有公开符号不变，内部委托 `diagnostics/` 实现。

- 优点：新旧解耦、公开 API 不破坏、`diagnostics/` 独立可测、LSP/CLI 双消费者自然分层
- 缺点：多一层间接；迁移过渡期两套模型并存

### C · 完全重写，删除 `errors.py`

- 优点：干净利落
- 缺点：BREAKING CHANGE，所有调用方须改；`__all__` 导出的四个符号丢失；嵌入 API 断裂。**拒绝。**

---

## 3. 决议：方案 B

### 3.1 核心数据模型（F1 冻结内容）

以下契约在 F1（诊断契约冻结）门禁通过后，码与字段名不可变更。

**Severity**

```
Severity = "错误" | "警告" | "提示"
```

映射 LSP severity：`"错误"` → 1，`"警告"` → 2，`"提示"` → 3。

**Position**

```python
@dataclass(frozen=True)
class Position:
    line: int    # 1-based
    column: int  # 1-based，Unicode 码点序号
```

**Span**

```python
@dataclass(frozen=True)
class Span:
    start: Position
    end: Position        # 独占（exclusive）
    file: str | None     # None 表示当前输入（REPL/内联）
```

**Suggestion**

```python
@dataclass(frozen=True)
class Suggestion:
    text: str            # 建议的替换文本
    distance: int        # 编辑距离
    replace: Span | None # 若非 None，表示可自动替换的区间
```

**Diagnostic**

```python
@dataclass
class Diagnostic:
    code: str                         # 如 "JK-E2001"
    severity: str                     # Severity 字面量
    category: str                     # ErrorCategory.value（中文类别名）
    message: str                      # 人类可读消息
    span: Span                        # 主位置
    subject: str | None               # 触发诊断的主体（变量名/模块名等）
    suggestions: list[Suggestion]     # 可为空列表
    notes: list[str]                  # 补充说明，可为空列表

    def sort_key(self) -> tuple:
        """(file, line, column, code) 用于稳定排序"""
        return (self.span.file or "", self.span.start.line,
                self.span.start.column, self.code)
```

### 3.2 Sink 协议

**DiagnosticSink**（Protocol）

```python
class DiagnosticSink(Protocol):
    def emit(self, diagnostic: Diagnostic) -> None: ...
```

**ListSink**

```python
class ListSink:
    def emit(self, diagnostic: Diagnostic) -> None: ...
    def drain(self) -> list[Diagnostic]:
        """按 sort_key 稳定排序后返回并清空内部列表"""
        ...
```

**NullSink**

当环境变量 `JIKUAI_DIAGNOSTICS=off` 时启用，`emit` 为 no-op。

### 3.3 下游投影（纯函数，无状态）

- `render_text(diagnostic) -> str`：终端友好的多行文本输出
- `render_json(diagnostic) -> dict`：JSON 序列化（供 LSP transport 或 CI 消费）
- `to_lsp_diagnostic(diagnostic) -> dict`：符合 LSP 3.17 `Diagnostic` 结构
- `from_error_info(info: ErrorInfo) -> Diagnostic`：旧模型 → 新模型适配
- `to_error_info(diagnostic) -> ErrorInfo`：新模型 → 旧模型降级（丢失 severity/end/code）

### 3.4 两条硬约束

1. **错误码是稳定契约，渲染文案不是** —— 测试须断言结构化字段（`code`/`severity`/`subject`），不得断言 `message` 文案字符串的精确内容
2. **`diagnostics/` 不得 import `evaluator`** —— 避免与 `JiKuaiError`（定义在 `evaluator.py`）循环耦合。需要桥接时由调用方在外部完成转换

---

## 4. 影响面

- **`src/jikuai/errors.py`**：降级为兼容外壳，内部委托 `diagnostics/`；公开符号全保留（`ErrorInfo`/`ErrorFormatter`/`ErrorCategory`/`spelling_suggestion`）
- **`src/jikuai/keywords.py`**：错误码常量表可选放置于 `diagnostics/codes.py`，`keywords.py` 不改动
- **`src/jikuai/lexer.py`**、**`parser.py`**、**`evaluator.py`**：注入 `DiagnosticSink`，替代直接构造 `ErrorInfo` 抛出的路径（渐进迁移，旧路径保留到 M5）
- **`src/jikuai/main.py`**：`run_source`/`run_file` 内部创建 `ListSink` 并传入；异常捕获路径保持对 `JiKuaiError`/`ParseError` 的处理不变
- **`src/jikuai/__init__.py`**：`__all__` 追加 `Diagnostic`/`DiagnosticSink`/`ListSink`（不删除已有成员）

---

## 5. 兼容性红线

- `ErrorInfo`/`ErrorFormatter`/`ErrorCategory`/`spelling_suggestion` 全部保留，签名不变
- `ErrorCategory` 仅**追加**以下成员，不改动现有 5 个成员的名称与中文值：
  - `MODULE = "模块错误"`
  - `INTEROP = "互操作错误"`
  - `CONTRACT = "契约错误"`
  - `LIMITATION = "能力限制"`
- `ErrorFormatter.format(info)` 的输出格式保持 `"第 N 行，第 M 列：<类别>：<消息>"` + 源码行 + `"^"` 指示符不变
- 已知变更（非 BREAKING）：建议文案从 `'建议：是否想输入 "<xxx>"？'` 改为 `'您是否想输入 \`xxx\`？'`（引用 Summary 裁决 D-03，属 Changed 级别）

---

## 6. 验证方式

- F1 门禁通过条件：CLI 消费者（`main.py` 的 `render_text` 路径）与 LSP 桩消费者（`to_lsp_diagnostic` 路径）双实证均可产出结构化诊断
- 单元测试：断言 `Diagnostic` 的 `code`/`severity`/`subject`/`span` 字段，不断言 `message` 精确文案
- 集成测试：258 项既有测试全绿（`ErrorInfo` 兼容外壳保障）
- `from_error_info` / `to_error_info` 往返测试：任意 `ErrorInfo` 实例经 `from_error_info → to_error_info` 后与原实例字段一致（`code`/`suggestions` 丢失可接受）

---

## 7. 已知限制

- `ErrorFormatter.format` 的建议文案变更（D-03 裁决）可能导致依赖精确文案的第三方工具行为变化；已在 CHANGELOG 标注为 Changed
- `from_error_info` 转换时，旧 `ErrorInfo` 缺少 `code`/`severity`/`end` 信息，转换后的 `Diagnostic` 将使用默认值（`code="JK-E0000"`/`severity="错误"`/`end=start`）
- `diagnostics/` 包在 M4 仅提供 `ListSink` 和 `NullSink`；流式 sink（如 LSP 实时推送）留 M5 实现
- Span 的 `file` 字段在 REPL 模式下始终为 None；多文件诊断聚合留 M5
