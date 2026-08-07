# 极快 JiKuai · 变更日志

## v0.3.2（2026-08-07）

关闭三项 v0.3.1 遗留 Known Issues：D-10 / D-11 / D-12。v0.3.1 140 项测试零回归，
新增 16 项 v0.3.2 验收测试，共 156 项全绿；11 个示例（含 `python -m jikuai` 入口）
退出码全部为 0。

### 修复

- **D-10（parser + evaluator）** 变参动词后接中缀表达式泄漏 Python 异常文本：
  - **方案 A · parser 中缀合并**（`parser._parse_argument`）：动词参数在读完
    primary/verb-call 后允许合并右侧中缀二元动词，使 `打印 郑数 加 2` 解析为
    `打印(加(郑数, 2))`，与用户直觉一致。
  - **方案 B · evaluator 元数守卫**（`evaluator._eval_Call` → 新增
    `_check_verb_arity`）：内建动词实参数量与 `keywords.VERB_ARITY` 声明不匹配时
    抛携带 `ErrorInfo(SYNTAX)` 的 `JiKuaiError`，文案 `动词「X」需要 N 个参数，
    实际收到 M 个`。变参（`-1`）与副词（`-2`）跳过校验；错误消息中不含 `lambda` /
    `_setup_builtins` / `positional argument` 等 Python 实现细节。
- **D-11** `python -m jikuai` 不可用：新增 `src/jikuai/__main__.py`（3 行），
  等价委托 `jikuai.main:main`。三种入口 `jk` / `python -m jikuai` /
  `python -m jikuai.main` 完全一致。
- **D-12** `_class_regions` / `_prescan_self_fields` 未走掩码源码：改用
  `self._scan_src`（注释/字符串内容已掩码为空格，长度与换行结构与原文一致）。
  多行字符串里的 `类 X：\n 自身.伪 = 1\n。` 与 `-- 类 X：\n-- 自身.Y = 1` 注释
  不再污染 `user_defs` 白名单。真实类体内的字段仍正常收集。

### 语义变化（本轮显式披露）

D-10 方案 A 让 parser 在动词参数位置也能吸收右侧中缀二元动词，导致以下写法从
**抛异常**变为**成功执行**（无脚本会因此坏掉；旧行为本身就是 v0.3.1 前泄漏 Python
异常文本的 bug，用户不可能依赖）：

| 写法 | v0.3.1 及以前 | v0.3.2 |
|------|----------------|--------|
| `打印 郑数 加 2` | 泄漏 `TypeError` | 打印 `7`（等价 `打印(加(郑数, 2))`） |
| `打印 郑数 乘 郑数` | 泄漏 `TypeError` | 打印 `25` |
| `列 1 2 加 3` | 泄漏 `TypeError` | `[1, 5]`（等价 `列(1, 加(2, 3))`） |

已确认 `stdlib/*.jk` 与 `examples/*.jk` 无脚本依赖旧的报错行为。若用户希望旧的
"独立列表元素"直觉，请显式加句号分隔或用管道逗号分段（`列 1 2 3, 加 3` 等）。

### 变更

- 版本号 → `0.3.2`（三处：`main.py::VERSION`、`__init__.py::__version__`、
  `pyproject.toml::version`；`test_ac36_version_is_beta` 已同步）。
- README.md 新增「三种等价入口」说明段（`jk` / `python -m jikuai` /
  `python -m jikuai.main`，均归一到 `jikuai.main:main`）。
- ADR-06 方案 A 副作用（"user_defs 全域生效"）文档状态保持不变，本轮不修复
  （需要架构层 X1/X2/X3 选型决策）。

---

## v0.3.1（2026-08-07）

关闭四项 Known Issues：D-04 / D-05 / D-08 / D-09。106 项存量测试零回归，
新增 34 项 v0.3.1 验收测试（AC-37 ~ AC-65 + 契约用例），共 140 项全绿；
11 个示例退出码全部为 0。

### 修复

- **D-04 + D-09（ADR-06）** 方法/字段名撞内建动词导致调用点分词失败：
  - `lexer._read_han` 分词优先级调换为「**用户定义名白名单最优先**」（方案 A）：
    `_try_user_def_strict` → `_try_longest_keyword` → 百家姓 → 中文数字 → 一般标识符。
    删除原先位于 keyword 之后的 `_try_user_def` 调用点（DP-4 / R-E：禁止双路径并存）。
  - `_try_user_def_strict` 实现 R-A 严格匹配：按名字长度降序、完整匹配、
    右边界校验（防止 `返回值` 因登记了 `返回` 被截断），仅在 `_read_han` 入口触发。
  - `_prescan_definitions` 扩容（D-09）：除 `定义/函数/方法/类 X` 外，额外扫描
    **`类` 块作用域内**的 `自身.X =` 字段赋值名（R-D：类外不纳入）。
  - 新增 `Lexer(source, external_defs=...)` 与 `get_user_defs()`；
    `repl_session` 新增会话级白名单 `_session_defs`，跨输入累积后注入下一次分词，
    使上一次定义的方法/字段名在后续输入的调用点不再被切碎（AC-45）。
  - 预扫描前先对源码做注释/字符串**掩码**（`_mask_source`），避免 `-- 定义函数`
    这类注释把关键字本身登记进白名单。
- **D-08（ADR-07）** 带参方法无法调用：新增 `BoundMethod`（`__slots__=(instance,
  method_def, closure_env)`，`arity` 属性）。`_eval_MemberAccess` 按元数分流：
  0 参方法「访问即调用」（M-01，兼容 `oop.jk`），≥1 参方法返回 `BoundMethod`；
  `_eval_FuncCall` 对 `obj.成员(...)` 走 `auto_invoke=False` 使 `赵狗.叫声()`
  等价 `赵狗.叫声`（M-04）。DP-3：`BoundMethod` 不可赋值/传参/返回，报
  `类型错误：方法不能作为值使用，请直接调用：X.Y(参数)`（在 `_eval_Define` /
  `_eval_Assign` / `_eval_Return` / 动词参数求值处 `_reject_bound_method` 守护）。
- **D-05（ADR-08）** 顶层 `返回`/`跳出`/`跳过` 显示 `内部错误：0`：
  `Evaluator.eval` 最外层捕获三种控制流信号，转为携带 `ErrorInfo` 的 SYNTAX 诊断
  （固定文案）。REPL 顶层同样走中文诊断。R-C：`_eval_FuncCall` / `_invoke_method` /
  循环内部的信号捕获保持不变，嵌套函数与闭包内合法 `返回` 不受影响。
    - `「返回」只能在函数或方法体内使用。`
    - `「跳出」只能在循环体内使用。`
    - `「跳过」只能在循环体内使用。`

### 变更

- 版本号 → `0.3.1`（三处：`main.py::VERSION`、`__init__.py::__version__`、
  `pyproject.toml::version`；`test_ac36_version_is_beta` 守护，全量扫描无残留 `0.3.0-beta`）。

### ADR-06 方案 A 的副作用（**同次分词全域生效**，用户须知）

一旦某内建动词名被登记进 `user_defs` 白名单，**该名字在同次分词的全域范围内**
（包含类定义之外的顶层语句、其他方法体内、REPL 同一会话的后续输入）**都失去
内建动词语义、被整体识别为 IDENT**。这是方案 A「白名单最优先」的固有结构性代价，
不局限于"方法体内"或"定义所在类内"。

QA 实测取证的爆炸半径（LIMIT 反证探针）：

| 探针 | 场景 | 现象 |
|------|------|------|
| LIMIT-1 | 类含 `方法 长度`，**类外顶层** `打印 长度 郑列` | `名称错误：未定义的标识符：长度` |
| LIMIT-2 | 类含字段 `自身.求和`，**类外顶层** `打印 求和 郑数列` | `名称错误：未定义的标识符：求和` |
| LIMIT-3 | 类含 `方法 长度`，**同类另一方法体内** `长度 自身.吴项` | `名称错误：未定义的标识符：长度` |
| LIMIT-4 | 对照：常规命名 `周计数`，方法体 `长度 自身.周项` | `3` ✔ |

**用户规避写法**（按推荐度排序，均已经代码验证）：

1. **命名避开内建动词名/字**（推荐）：优先双字非动词词，如 `王计数` / `王长度` / `王取值` 代替 `方法 长度` / `方法 取值`；字段名同理。这是最稳且**唯一在同文件内可解**的写法。
2. **拆分源文件 / REPL 拆分会话**：把「含动词名的类定义」与「使用同名内建动词的顶层脚本」放到不同 `.jk` 文件（不同次分词）；REPL 中新开一个 `ReplSession` 即可让被覆盖的内建动词恢复语义。
3. **⚠️ 括号写法 `打印(长度(郑列))` 无法规避**：由于名字已在分词阶段被整体识别为 IDENT，`长度` 会走 `FuncCall(Ident('长度'), ...)` 的路径，在 `Evaluator` 中按环境变量而非 `verbs` 内建表解析，报 `名称错误：未定义的标识符：长度`。经实测确认，**此路径不是有效规避方式**，请勿依赖。修复方向已登记 v0.3.2 的 D-10 / 分词层重构。

### v0.3.1 遗留 Known Issues（**已于 v0.3.2 全部关闭**，正文保留供追溯）

#### D-10（中）变参动词后接中缀表达式泄漏 Python 异常文本

- **复现**：
  ```
  定义 郑数 = 5。
  打印 郑数 加 2。
  ```
  实测输出：`类型错误：Evaluator._setup_builtins.<locals>.<lambda>() missing 1 required positional argument: 'b'`
- **根因**：parser 变参贪心与二元动词元数结算在中缀位置发生冲突 —— `打印`（变参）吞噬 `郑数`，随后遇到中缀 `加` 时另一操作数未及时收集，导致 `加` 实参不足；Evaluator 直接把 Python `TypeError` 消息透出。
- **性质**：**非 v0.3.1 引入**（分词结果已 QA 取证正确，逃逸在 parser/evaluator 层）。
- **排期**：v0.3.2。**修复方向**：`_call_function` / 内建 `_eval_Call` 在实参数量与动词元数不匹配时抛携带 `ErrorInfo` 的 SYNTAX 中文诊断，文案形如 `语法错误：动词「加」需要 2 个参数，实际收到 1 个`；同时 parser 层收紧变参动词与中缀动词组合时的分界（可能是在变参贪心结束前遇到二元 VERB 立即让位）。

#### D-11（低）`python -m jikuai` 不可用

- **现象**：`python -m jikuai` 报 `No module named jikuai`（缺 `src/jikuai/__main__.py`）。
- **官方入口**：`jk`（脚本入口）与 `python -m jikuai.main`。
- **排期**：v0.3.2。**修复方向**：新增 `src/jikuai/__main__.py`（内容 `from .main import main; main()`），或在 README「安装与使用」小节显式锁定"唯一入口"并说明 `python -m jikuai` 不受支持。

#### D-12（低）`_class_regions` 采用行文本启发式定位

- **现象**：`lexer._class_regions()` 用行文本规则（`类` 开头、缩进 ≤ 的 `。` 收尾）定位类块区间，未走 parser。理论上在极端标点构造（如字符串字面量单独一行只含 `。`、混合制表符/空格缩进等）下可能收窄扫描区间。
- **安全侧倾**：只可能**漏登记** `自身.X` 字段（退化为 v0.3.1 前的 D-09 现象），**不会误切**已定义的字段名，也不会污染类外语义。
- **排期**：v0.3.2。**修复方向**：把 `_class_regions` 改为在 parser 完成一遍轻量解析后基于 `ClassDef` 节点位置回填；或将 `自身.X` 字段收集彻底改到 `_scan_self_fields` 的 AST 阶段（`_prescan_definitions` 只承担关键字后紧邻 IDENT 的部分）。

---

## v0.3.0-beta（2026-08-07）

### 修复

- **T-01（ADR-01）** lexer 姓氏标识符与动词后缀切分冲突：`_prescan_definitions()` 扩展到 `函数 X` / `方法 X` / `类 X`，把定义名纳入 `user_defs` 白名单，使含动词字的名字（如 `赵阶乘`）不被切碎。定义 X 路径行为零回归。
- **T-02（ADR-02）** 构造器继承链回溯：`ClassDef` 新增 `ctor_defined` 标记；新增 `Evaluator._resolve_ctor()` 沿 `parent` 链定位构造器；`ClassDef` 求值时静态扫描 `自身.X = ...` 得出 `declared_fields` 并沿父链合并；显式空构造器**不**回溯父构造器；声明过但未初始化的字段返回空(nil)。
- **示例回归**：`examples/functions.jk` 与 `examples/oop.jk` 从"技术债豁免"转入常规回归，退出码 0。

### 新增

- **M2-2** REPL 增强（`src/jikuai/repl_session.py` 新文件）：
  - 多行续行状态机（IDLE / CONTINUE）
  - 历史持久化 `~/.jikuai_history`（readline / pyreadline3；缺失静默降级）
  - Tab 补全（关键字 ∪ 动词 ∪ 全局变量）
  - `帮助` 命令（分类简介、单动词用法、未知项提示）
- **ASCII 半角逗号 `,`** 与全角 `，` 等价，可用于管道与参数分隔（与全半角括号双写策略同源，实现期追认扩展；README「语法备注」已登记留痕）。
- `parser.py::UnexpectedEOFError`（`ParseError` 子类）用于 REPL 判定输入未完；块结构 `_parse_if` / `_parse_while` / `_parse_for` / `_parse_repeat` / `_parse_funcdef` / `_parse_classdef` / `_parse_try` 在 EOF 遇到未闭合块时抛该错。

### 变更

- REPL 续行判定改为 **parser 权威**（ADR-03 修正 · R-1）：
  | `parse(buffer)` 结果 | 判定 | REPL 行为 |
  |---|---|---|
  | 抛 `UnexpectedEOFError` | 未闭合 | 显示 `... `，继续收集 |
  | 成功 | 完整 | 立即执行 |
  | 抛其他语法错误 | 真错误 | 中文诊断输出，清空缓冲 |

  上一轮基于 lexer 的 net `block_depth` 判定已**完全删除**（曾在类构造器闭合处提前 flush，D-01）。lexer 侧不再暴露 `closure_state` / `ClosureState`。
- REPL 续行态下**空行 → 取消整个多行缓冲**并打印 `已取消多行输入`，回到主提示符（AC-23 修订 · R-2）。
- 版本号 → `0.3.0-beta`（三处：`main.py::VERSION`、`__init__.py::__version__`、`pyproject.toml::version`；`test_ac36_version_is_beta` 与 `test_version_consistency` 守护）。

---

## Known Issues（v0.3.0-beta 时排期 v0.3.1；均已于 v0.3.1 关闭）

> 以下四条 D-04 / D-05 / D-08 / D-09 已在 v0.3.1 关闭，保留正文供追溯参考。

### D-04 方法名撞内建动词导致解析失败（v0.3.1 已关闭）

- **现象**：方法/字段名整体等于或内嵌 `VERB_ARITY` 中的动词字时，在**调用点**分词失败。定义处（含 `方法 X` 白名单）可能能过，但另一次独立 tokenize 的 `对象.方法名` 会被切成 `IDENT . VERB ...`，报 `对象 X 无属性/方法：Y`。
- **受影响写法**：
  - 名字整体是动词：`方法 取值` / `方法 长度` / `方法 排序` / `方法 反转` / `方法 求和` / 等
  - 名字内嵌动词字：`方法 王加一`（`加`）/ `方法 李乘积`（`乘`）/ `方法 赵取值表`（`取值`）
  - REPL 中跨行调用上述方法必然复现；单文件内定义与调用同处一次分词，风险较低
- **规避方式**：
  1. 命名避开内建动词字，优先双字非动词词（`王显示` / `王递增` / `王计数`）
  2. 已有代码可改名，或把定义与调用置于同一 `.jk` 文件
  3. 字段名同规则；属性访问走已初始化的 `自身.值` 更稳
- **修复方向（v0.3.1）**：把 `user_defs` 提升为跨输入的会话级符号表；调整 `_read_han` 中 VERB 匹配与 `_try_user_def` 的优先级，使已知的用户名整体命中优先于动词。

### D-05 顶层 `返回` / `跳出` 显示 `内部错误：0`（v0.3.1 已关闭）

- **现象**：在函数/循环体外直接输入 `返回 0。` 或 `跳出。` 等，REPL 打印 `内部错误：0`（`跳出` 打印 `内部错误：`），而非可读的中文诊断。
- **性质说明**：这是**诊断缺失**，不是解释器损坏。`ReturnSignal` / `BreakSignal` / `ContinueSignal` 是控制流用的 Python 异常、不继承 `JiKuaiError`，逃逸到顶层后落到 REPL 的 `except Exception` 兜底分支，打印 `str(异常对象)`（`ReturnSignal(0)` 的字符串就是 `0`）。会话状态未受影响，可继续正常使用。
- **规避方式**：`返回` 只用于 `函数`/`方法` 体内；`跳出` / `跳过` 只用于 `当` / `遍历` / `重复` 体内。
- **修复方向（v0.3.1）**：在 `Evaluator.eval` 顶层与 REPL 求值处捕获三种控制流信号，转换为携带 `ErrorInfo` 的 `JiKuaiError`（类别 `SYNTAX`），例如"`返回` 只能用在函数或方法体内"。
