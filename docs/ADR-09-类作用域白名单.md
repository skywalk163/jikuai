# ADR-09 · 类作用域限定白名单（v0.4.0 · M1）

- 状态：Accepted
- 日期：2026-08-07
- 决策者：架构师（承接 CEO 交付要求）
- 落地环节：v0.4.0 · M1 · T-01 ~ T-08 / T-11
- 相关：ADR-06（白名单最优先，方案 A）、ADR-01（`_prescan_definitions` 扩容）

---

## 1. 背景

v0.3.1 起 ADR-06 采用「白名单最优先」的方案 A：`_prescan_definitions` 把
`定义/函数/方法/类 X` 及类内 `自身.X=` 字段名登记进一个**扁平的全域集合**
`self._user_defs`，然后 `_read_han` 入口的 `_try_user_def_strict` 拿这个集合
去做严格前缀匹配。

该方案根治了 T-01（`赵阶乘` 被切碎）与 D-04（`方法 取值` 定义端可用、调用
端被切碎）两类问题，但引入了「方案 A 副作用」：一旦某个内建动词名（如 `取值`
/`长度`/`求和`）被登记进白名单，它在**同一次分词**的**全域**都被识别为 IDENT，
包括类外顶层语句、其他类内的方法体、REPL 同一会话的后续输入。副作用登记在
v0.3.1 与 v0.3.2 CHANGELOG，v0.3.2 未修，v0.4.0 · M1 需根治。

用户视角：写「电商购物车」类里想有一个 `方法 长度`，会导致同一文件的顶层
`打印 长度 列 1 2 3` 抛「未定义标识符：长度」——语义泄漏、不可用。

## 2. 目标 / 非目标

**目标**

1. 类内 `方法 X` / `自身.X=` 的白名单效力**仅在该类字符区间内**生效。
2. 类外恢复内建动词/关键字语义（AC-66 / AC-70）。
3. `实例.成员` 无论跨类都能整体识别（AC-67）。
4. REPL 跨行调用类内成员的既有行为（AC-45 / AC-69）不能回归。
5. 保持 R-E「白名单单入口」原则：`_read_han` 仍只有 `_try_user_def_strict`
   一处白名单判定；旧扁平集合路径全部走同一入口。
6. 提供 `JIKUAI_LEGACY_ADR06=1` 环境变量作为紧急回退开关（T-06）。
7. 156 项 v0.3.2 回归零回归。

**非目标**

- 不改动 parser、evaluator、module_loader（M1 不动 parser/evaluator/module_loader）。
- 不引入语法级作用域声明（如 `私有 方法`），保持 Python 家族的运行期语义。
- 不实现类字段 `私有` / `公开` 的访问控制。

## 3. 方案候选

### X1 · 类作用域限定白名单（选定）

`_prescan_definitions` 输出 `ScopeMap`，为每条 `方法 X` / `自身.X=` 登记
`scope_start`/`scope_end`（该类字符区间）；`_try_user_def_strict` 在匹配前
按 `self.pos` 用 `scope_map.visible_at(pos)` 过滤候选名字。**仅改 lexer 内部
数据结构与候选过滤，`_read_han` 单入口不变、优先级不变。**

**优点**

- 语义天然：作用域正是「字符区间」这个平面概念，与 `_class_regions()` 已有
  基础设施同构，无需重造轮子。
- 侵入面最小：parser/evaluator 无感知；对既有 5 层匹配优先级零改动。
- 可回退：`legacy=True` 让 `visible_at` 退化为 `all_names()`，等价旧行为。

**缺点**

- `_class_regions` 仍是启发式（行文本 `类 X：` + 缩进匹配）；虽然 v0.3.2 已切
  到掩码源 `_scan_src`，极端场景（多类嵌套、Tab/空格混合）仍可能收窄区间。
  作为已知限制在文档层跟踪，不阻断 M1。

### X2 · Parser 权威作用域

改为在 parser 完成一遍轻量解析后回填 `ClassDef` 节点的字符区间，替代
`_class_regions` 启发式定位。

**优点**：区间来源权威；类嵌套/花式缩进不再是问题。
**缺点**：把 lexer 变成两阶段（先粗切、后回填）；parser 需要在**尚未获得
白名单保护**的 token 流上先跑一遍，容易踩本轮想避免的鸡生蛋问题（比如
`方法 取值` 在没有白名单时会被切成 `方法 + 取值 VERB`，parser 可能定不到
类边界）；工作量 ≥ 2 天。**M1 不采用**。

### X3 · 语法级私有作用域

引入新关键字 `私有 方法 X` / `私有 字段 X` 声明成员的类内可见性，从语义
层显式解决冲突。

**优点**：用户显式声明，编译期即可检查冲突。
**缺点**：破坏「零配置写中文」的产品定位；新增关键字影响所有既有 `.jk`
文件的语法兼容性；对 PRD 未提出的问题（可见性控制）过度设计。**M1 不采用**。

---

## 4. 方案对比矩阵

| 维度                     | X1（选定）          | X2 Parser 权威      | X3 私有关键字        |
|--------------------------|---------------------|---------------------|----------------------|
| 代码改动量               | 低（仅 lexer 内）   | 中（lexer+parser）  | 高（tokens+parser+evaluator） |
| 破坏既有代码             | 无                  | 无                  | 潜在（关键字冲突）   |
| 语法兼容                 | 100%                | 100%                | 需迁移               |
| AC-66 类外恢复内建       | ✅                  | ✅                  | ✅                   |
| AC-67 `.成员` 松弛       | ✅（DOT 前置探测）  | ✅（AST 精确定位）  | ✅（成员表符号表）   |
| AC-68 跨类作用域隔离     | ✅                  | ✅                  | ✅                   |
| AC-69 REPL 跨输入        | ✅（session_defs 全域注入） | 需 REPL 特化处理 | ✅                   |
| 类区间定位来源           | `_class_regions` 启发式（已切 `_scan_src`） | parser AST | 关键字标注       |
| 回退开关                 | `JIKUAI_LEGACY_ADR06=1` | 需实现            | 无（一旦改语法就回不去） |
| 156 项回归风险           | 低                  | 中（parser 双跑）   | 高                   |
| 后续演进                 | 可继续升级为 X2     | 已是终态            | 已是终态             |
| **M1 结论**              | **采用**            | 记入 M3 或更晚      | 拒绝                 |

---

## 5. 选型决议

**采用 X1，理由：**

1. **风险最低、收益最大**：把 v0.3.1 已有的 `_class_regions` 复用起来，只把
   `_prescan_definitions` 的返回类型从 `set[str]` 升级为携带作用域信息的
   `ScopeMap`。156 项回归可控。
2. **保留 R-E 单入口**：白名单查询仍集中在 `_try_user_def_strict`，唯一变化
   是候选集合从「全部」变为「visible_at(pos)」。旧行为通过 `legacy` 开关
   1 秒切回。
3. **可演进**：一旦 M3 需要更权威的类边界，把 `ScopeMap` 的构造源从
   启发式切换到 parser AST 即可，接口不变。
4. **拒绝 X2 / X3 的现实原因**：M1 严禁改动 parser/evaluator/module_loader
   （引导语明确红线）；X3 破坏语法兼容属最严重的越界。

---

## 6. 数据模型

```python
@dataclass(frozen=True)
class DefEntry:
    name: str
    kind: str          # 'class'|'func'|'define'|'method'|'field'|'external'
    scope_start: int   # 源码字符偏移，闭
    scope_end: int     # 开区间终点；-1 = EOF
    owner_class: str | None

class ScopeMap:
    def add(self, entry: DefEntry) -> None: ...
    def add_global(self, name: str, kind='external', owner_class=None) -> None: ...
    def visible_at(self, offset: int) -> frozenset[str]: ...
    def all_names(self) -> frozenset[str]: ...
```

**作用域规则（预扫描登记表）**

| 标记       | kind     | scope_start        | scope_end        | owner_class |
|------------|----------|--------------------|------------------|-------------|
| `定义 X`   | define   | 标记字位置         | -1（EOF）        | None        |
| `函数 X`   | func     | 标记字位置         | -1（EOF）        | None        |
| `类 X`     | class    | 标记字位置         | -1（EOF）        | None        |
| `方法 X`   | method   | 所属类块起点       | 所属类块终点     | 类名        |
| `自身.X=`  | field    | 所属类块起点       | 所属类块终点     | 类名        |
| `external_defs` · `str`（旧契约） | external | 0 | -1 | None |
| `external_defs` · `(name,'class'/'func'/'define',None)` | 同 kind | 0 | -1 | None |
| `external_defs` · `(name,'method'/'field',类名)`，本次源码**有**该类块 | 同 kind | 该类块起点 | 该类块终点 | 类名 |
| `external_defs` · `(name,'method'/'field',类名)`，本次源码**无**该类块 | 同 kind | 0 | 0（空区间） | 类名 |

`legacy=True`（`JIKUAI_LEGACY_ADR06=1`）时 `visible_at` 无视作用域直接返回
`all_names()`，与旧扁平集合行为等价。

**空区间 `[0, 0)` 语义（DEF-02）**：该名字在任何位置都**不可见**（`visible_at`
不返回它），但仍进入 `all_names()`，因此只能通过 §7 的 `.成员` 松弛路径命中。
这是 REPL 跨输入调用类内成员的承载机制。

---

## 7. 白名单命中松弛：成员访问点

`_try_user_def_strict` 在检查候选前先看 `self.tokens[-1]`：若前一 token 是
`DOT`，视为「成员访问点」，候选集合松弛为「本次分词的**全部**用户定义名」
（不做作用域过滤）。原因：`实例.成员` 从语法看必然是名字，无论跨类都应
整体识别；否则 AC-67 无解，`赵a.长度` 类外调用会被切成 `赵a . VERB(长度)`。

### REPL 跨输入（DEF-02 修订）

**修订前（有缺陷）**：`external_defs` 一律 `add_global` 注入。这把类作用域
名字提升为会话全域，ADR-06 副作用在 REPL 路径下未根治——REPL 中定义含
`方法 长度` 的类后，下一行顶层 `打印 长度 列 1 2 3。` 报「未定义的标识符：
长度」，而同样代码在文件模式下正确输出 `3`（DEF-02，QA 判定为高危阻塞项）。

**修订后**：`ReplSession._session_defs` 从平坦 `frozenset[str]` 改为
`(name, kind, owner_class)` 三元组集合，由
`Lexer.get_user_def_signatures()` 导出、`Lexer._register_external_defs()`
按 kind 分流消费：

| 注入项 | 本次分词的作用域 |
|--------|------------------|
| `(名, 'class'/'func'/'define', None)` | 全域可见（顶层 def 全局可见） |
| `(名, 'method'/'field', 类名)`，本次源码有该类块 | 该类块字符区间 |
| `(名, 'method'/'field', 类名)`，本次源码无该类块 | 空区间 → 仅 `.成员` 松弛可命中 |
| `str`（旧契约，直接调用 `tokenize(src, external_defs={'X'})`） | 全域可见 |

因此：
- `打印 长度 列 1 2 3。` 顶层 → `长度` 不可见 → 走内建动词 → `3`（AC-69 原文）
- `赵c.长度。` → DOT 松弛 → `长度` 整体识别为 IDENT → 走类内方法（AC-45 / AC-67）

`get_user_defs()` 保留为平坦 frozenset 契约（v0.3.x 测试直接依赖），仅
REPL 改走 `get_user_def_signatures()`。

---

## 8. 实施步骤（映射 WBS）

- T-01 定义 `DefEntry` / `ScopeMap`（`src/jikuai/lexer.py` 顶部）
- T-02 `_prescan_definitions` 返回 `ScopeMap`
- T-03 类区间复用 `_class_regions`；新增 `_class_name_at` 解析类名
- T-04 `_try_user_def_strict` 按 `self.pos` 查 `ScopeMap.visible_at`
- T-05 REPL：无需修改 `repl_session.py`，`get_user_defs()` 契约保持
  平坦 frozenset；`external_defs` 在 `Lexer.__init__` 中登记为全域
- T-06 `JIKUAI_LEGACY_ADR06=1` 环境变量回退
- T-07 `tests/test_v0_4_0_adr09.py` 覆盖 AC-66..70
- T-08 `docs/元数解析规范.md`（另单独交付）
- T-11 全量回归：pytest 156 → 164 全绿；examples 全 exit 0

---

## 9. 已知限制与后续演进

1. `_class_regions` 仍是启发式；极端标点/缩进构造仍可能收窄区间。**风险等级：
   低**。规避：v0.3.2 已切到 `_scan_src`，字符串/注释不再污染；M3 可评估
   升级为 parser 权威（X2）。
2. **成员访问松弛**仅识别前一 token 是 DOT。若未来引入更多访问语法
  （如 `?.`、`::`），需同步扩展。
3. 顶层 `定义 X` 的 `scope_start` 是**定义点**而非 0——即定义之前引用无法
   命中。这与既有行为等价（Python 也如此），不视作回归。

---

## 10. 回退策略

生产环境如遇 X1 引发的未知回归：

```
$env:JIKUAI_LEGACY_ADR06="1"   # PowerShell
export JIKUAI_LEGACY_ADR06=1   # bash
```

即可让 `ScopeMap.visible_at` 退化为 `all_names()`，等价 v0.3.2 扁平白名单
行为。`tests/test_v0_4_0_adr09.py::test_legacy_env_switch_smoke` 守护该路径。
