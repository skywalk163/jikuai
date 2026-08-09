# 极快 JiKuai · 变更日志

## 未发布（2026-08-08）· M9 批次

**M9 批次**：一次性推进先前登记的其余四项改进方向。M8 包管理的收尾产出。

测试：755 → **1209 passed, 9 skipped**（新增 454 条：AOT 控制流子集扩容
+ 分词/关键字 fuzz + VSCode DAP 契约 + OOP 私有/反射）。原有用例零回归。

### M9-1 · 分词 fuzz 测试

- `tests/test_v0_8_0_lexer_fuzz.py`（413 例）：
  - 200 对随机关键字/动词/副词两两拼接（含四组：紧邻、语句上下文、
    三元组、字符串上下文），验证 `tokenize` 不抛非预期异常。
  - 100 组「百家姓 + 关键字前缀」组合，覆盖用户误把关键字当变量的场景。
  - 100 条随机合成的合法极快语句（5 种模板 × 随机 id/verb/num），做压力测试。
  - 13 条手工收集的已知边界 case（含 CHANGELOG 里登记过的
    「标识符夹带动词字」「中英混排」「注释后紧接关键字」等）。
- 全部通过，未暴露新的 crash 路径。这是长尾问题的**防护网**：
  以后任何触及 lexer 的改动都要先过 fuzz 层。

### M9-2 · VSCode 调试集成

前情：`dap/` 目录下 DAP 适配器 MVP 早已就绪（16 例契约测试通过），
但 VS Code 扩展没有 debug provider，用户按 F5 会报「找不到调试器」。

- **`editors/vscode/package.json`**：
  - 新增 `contributes.debuggers`（类型 `jikuai`），声明 `configurationAttributes`
    / `initialConfigurations` / `configurationSnippets`。
  - 新增 `contributes.breakpoints` for `jikuai`——没有这条 VS Code 不允许
    在 `.jk` 文件行首下断点。
  - `activationEvents` 追加 `onDebugResolve:jikuai`。
  - `categories` 追加 `Debuggers`。
- **`editors/vscode/src/extension.ts`**：
  - `JiKuaiDebugAdapterFactory`：把 launch 请求转成
    `python -m jikuai_dap` 子进程（`shell=false` + argv 数组）。
  - `JiKuaiDebugConfigurationProvider`：F5 无 launch.json 时补出默认配置，
    避免弹「未找到配置」。
  - launch 的 `pythonPath` 可覆盖扩展全局设置；cwd 兜底顺序为
    launch.cwd → 工作区根 → program 所在目录。
- **测试 `tests/test_v0_8_0_vscode_debug.py`**（13 例）：
  契约级校验（不启动真实 VS Code）。检查 `package.json` 的 debugger
  贡献结构 + `extension.ts` 关键 API 调用 + DAP 包物理布局。
  验证「命令用数组传参、无 `shell:true`」——从源头堵住命令注入。

### M9-3 · AOT 子集扩容：控制流

在实验性 AOT（`tools/aot`）里放开控制流子集，把 24 个动词从「顺序执行」
拓展成「顺序 + 条件 + 循环」。这是 AOT 用户能自然写的最小完备程序集。

- **`tools/aot/jikuai_aot/subset_gate.py`**：
  - `If` / `While` / `Repeat` / `Break` / `Continue` 移出 `UNSUPPORTED_NODE_TYPES`。
  - `describe_subset()` 相应更新，`docs/AOT.md` 一致性核对不破。
  - `Return` 单独保留在不支持集（AOT 尚不支持用户函数，`返回`
    脱离函数上下文没意义）。`For` 仍不支持（需可迭代对象运行时）。
- **`tools/aot/jikuai_aot/codegen.py`**：
  - `CCodegen` 加入 `_indent` / `_loop_depth` / `_tmp_seq` 状态。
  - `_emit_if` / `_emit_while` / `_emit_repeat` / `Break` / `Continue` 全套实现。
  - `如果` 链翻译为 `if (jk_truthy(...))` / `else if` / `else`，
    条件统一过 `jk_truthy()` 保证 Python 真值语义。
  - `重复 N 次` 用「计数只求值一次 + `for` 循环」，源表达式里的变量
    改动不影响剩余轮数（对齐解释器 `range(n)` 语义）。
  - `_collect_slots` 递归进嵌套块——循环体内 `定义` 的变量也拿槽位；
    定值分析从「必然赋值」放宽为「全程序某处赋值」，因为控制流让
    静态判死不可行，但真拼错的名字（从未出现在任何赋值左侧）仍报错。
  - `跳出`/`跳过` 出现在循环外时 codegen 报错兜底。
- **测试 `tests/test_v0_8_0_aot_controlflow.py`**：门禁接受 + codegen 结构
  + **有 C 编译器时**编译成原生二进制并与解释器输出**逐字节比对**
  （8 个端到端用例：if/elif、while 求和、嵌套循环、break/continue、
  RMB 累加）。这是唯一能证明「AOT 与解释器语义一致」的做法。
- 原 `test_v0_7_0_aot.py` 里「if/while/repeat 应被门禁拒绝」的三条负例
  相应更新为「for/return 仍被拒绝」。

### M9-4 · OOP 进阶：私有成员 + 反射

选择**运行时 + 命名约定**实现，不引入新关键字（否则要扩 lexer 的
最长匹配表，风险大）。

- **私有成员**（`src/jikuai/evaluator.py`）：
  - `_member_lookup` 新增判定：`attr` 以「私」开头时，接收者语法上
    必须是 `自身`（即 `Ident('自身')`），否则抛「私有成员不可从外部访问」。
  - 看的是**语法**不是运行时对象身份——原因写在 `_is_self_receiver`
    的 docstring 里：把实例存进字段再绕回来访问就能破防的漏洞。
  - `.私余额 = 100` / `自身.私方法()` 类内可用；类外访问一律拒绝。
- **反射**（`src/jikuai/keywords.py` + `evaluator.py`）：
  - `是否是` — 元数 2，`(实例, 类名字符串) → 真/假`，沿继承链判定。
    子类实例对父类名返回真（`isinstance` 语义）。非实例一律返回假，不报错。
  - `类名` — 元数 1，`实例 → 类名字符串`。非实例抛类型错误
    （返回空串会让调用方误以为存在空名类）。
- **测试 `tests/test_v0_8_0_oop_advanced.py`**（13 例）：
  账户/服务两个类作为夹具，覆盖私有字段/方法在类内可用、在类外被拒；
  反射的 isinstance 语义、非实例路径、按类型分流的实用模式；
  同时回归确认多态派发（最派生优先）没被破坏。

### M9-5 · 完整语法参考手册

- **`docs/语法参考.md`**（400+ 行）：按语法结构组织的规范性参考，
  13 章覆盖词法、字面量、表达式、语句、函数、面向对象、异常、模块、
  管道副词、内建动词表、中国特色能力、Python 互操作、已知边界。
- 与教程的分工：教程做循序渐进的入门（可执行、CI 验证），
  参考手册回答「某个语法怎么写、边界在哪」。
- 反映本轮所有变更：M8 包管理、M9-3 控制流子集、M9-4 私有+反射。
- 内建动词表按元数分组，权威表指回 `keywords.py` 的 `VERB_ARITY`。
- 已知边界一节从 CHANGELOG 与代码里如实汇总（含 AOT 子集清单）。


## 未发布（2026-08-08）

**M8 · 包管理工具**。补齐生态分发的最后一块基础设施：此前极快模块
只能靠手工拷贝或 `JIKUAI_PATH` 环境变量共享，没有清单、没有版本约束、
没有可重现的安装。设计参考 pip / npm / Cargo 的公共交集，
文件格式与命令全部中文化。

测试：726 → **755 passed, 1 skipped**，原有用例零回归。

### 新增

- **`jk 包` 子命令族**（`src/jikuai/pkg/`，7 个模块，核心包仍零运行时依赖）：
  - `初始化`(init) / `添加`(add) / `移除`(remove) / `装`(install) /
    `列表`(list) / `运行`(run)，各带英文别名。
  - `src/jikuai/pkg/semver.py`：三段式语义化版本 + 约束匹配。
    支持 `^` `~` `>=` `<=` `>` `<` `==` `*`，逗号表逻辑与。
    **预发布版本不被范围约束隐式命中**（对齐 npm / Cargo），
    `^1.0.0` 与 `*` 都不会装到 `2.0.0-rc1`，要装必须显式写出预发布号。
  - `src/jikuai/pkg/manifest.py`：清单 `包.json` 读写与校验。
    必填字段 `名称` / `版本`；包名白名单为中文+字母数字+`_`+`-`（1..64 字），
    拒绝点与路径分隔符（包名会拼进安装目录路径）；
    与内置标准库同名（`分词`/`排版`/…）的包名一律拒绝，防遮蔽。
    向上逐级查找清单，子目录里执行命令也能定位项目根。
  - `src/jikuai/pkg/lockfile.py`：锁文件 `包.锁`。条目按包名排序、
    **不写时间戳等易变字段**，同输入产出字节相同的文件，
    不制造无意义 git diff。`锁版本` 不匹配时拒读而非猜测语义。
  - `src/jikuai/pkg/sources.py`：`路径` / `仓库`(git) / `注册表` 三种来源。
    git 走 `shell=False` + 显式 argv + `--` 分隔符；
    校验和只哈希 `.jk`/`.py`/`.json` 源文件，跳过 `.git/` 等易变内容。
  - `src/jikuai/pkg/resolver.py`：广度优先遍历依赖图，
    **扁平单副本 + 首次遇到即锁定**，冲突在解析期报错而不是偷偷装两份
    （`node_modules` 式嵌套副本在极快的模块名解析模型下根本无法生效）。
    循环依赖给出完整链路。
  - `src/jikuai/pkg/installer.py`：物化到 `极快_包/`。
    先拷进 `.tmp-<名称>` 再 `os.replace`，中断不留半个包目录；
    Windows 上旧目录先挪 `.old-<名称>` 规避「非空目录无法替换」；
    `装` 会裁掉不再被依赖的包（对齐 `npm ci` 而非 `npm install`）。
- **`module_loader` 接入 `极快_包/`**：`_search_paths` 新增项目根的
  `极快_包/`，优先级在脚本同目录之后、`stdlib/` 之前；
  另加 `_resolve_package_entry()` 把包名解析到 `极快_包/<包名>/<入口>`
  （入口取自该包 `包.json`，缺省 `main.jk`，禁止靠 `..` 逃出包目录）。
  包目录形态排在扁平单文件之后，**升级到包管理不改变既有脚本行为**；
  没有 `包.json` 时整条包管理路径跳过，纯脚本用户零影响。
  项目根查找结果按起始目录缓存，避免每次 `导入` 都爬文件系统。
- **文档 `docs/包管理.md`**：命令表、清单格式、版本约束语义、
  模块解析优先级、解析策略取舍、安全边界、尚未实现清单。
- **测试 `tests/test_v0_8_0_pkg.py`**：29 例，全部离线（只用路径依赖，
  不碰网络与 git）。覆盖 semver 边界（`^0.x` 收紧、预发布不隐式命中）、
  清单校验负例、锁文件版本拒读、传递依赖安装、循环检测、裁剪、
  锁文件字节稳定性、CLI 各子命令返回码、以及子进程里
  `导入 甲` 真的从 `极快_包/甲/main.jk` 加载成功。
- **`.gitignore` 追加 `极快_包/`**：依赖目录可由 `包.锁` 完整还原，不入库。

### 已知边界（本次未做，仅记录）

- **中央注册表未上线**：纯版本约束依赖（`"丙": "^1.0.0"`）会报明确的
  「注册表尚未上线」错误，而非静默降级。中央仓库落地前用户必须显式
  声明 `路径` 或 `仓库` 来源——这比装出一副能工作的样子更诚实。
- `jk 包 发布` 待注册表先行；git 依赖只锁到标签，未锁 commit；
  无跨项目全局缓存。
- `jk 包 运行` 走 shell 执行（信任模型同 `npm run`）：
  不要运行来源不明的第三方清单里的脚本。

## 未发布 · 先前批次（2026-08-08）



**由「Reasonix 推理引擎 demo」实践驱动的语言补齐**。参考段言（DuanLang）
`demo/reasonix` 复刻一个等价 demo 时暴露出 4 处能力缺口，逐个补齐。

测试：693 → **710 passed, 1 skipped**，原有用例零回归。

### 新增

- **字典字面量语法 `{"键": 值, "键2": 值2}`**（此前 `字典` 只能靠
  `蟒:json.loads` 或 `提取身份证信息` 等内建动词间接产出）：
  - `src/jikuai/ast_nodes.py`：新增 `DictLit` 节点，`items` 为
    `(键表达式, 值表达式)` 列表，保持源码书写顺序。
  - `src/jikuai/parser.py`：`_parse_dict_literal()`；`_parse_primary` 接入
    `TokenType.LBRACE` 分支。键/值都是**表达式**（不含逗号管道），
    条目间允许逗号和/或换行分隔，末尾逗号可省略，`{}` 为空字典。
    全角 `「」` 与半角 `{}` 等价（沿用 `keywords.PUNCTUATION` 已有映射）。
  - `src/jikuai/evaluator.py`：`_eval_DictLit()`。
  - 访问沿用既有两条路径：`.键`（`_member_lookup` 的 dict 分支）与
    `字典["键"]`（`Index`）；`遍历` 字典迭代键。
- **内建动词 `去空白`（元数 1）**：等价 Python `str.strip()`。
  此前极快只有 `替换` / `子串`，处理「用户输入首尾空格」需手写循环。
  见 `keywords.VERB_ARITY` 与 `evaluator._setup_builtins`。
- **场景示例 `examples/scenarios/推理演示/`**：Reasonix 4 阶段
  Chain-of-Thought 推理引擎（理解问题 → 信息提取 → 逻辑推理 → 验证答案）。
  4 个中文文件名模块（`工具.jk` / `思考链.jk` / `提示词.jk` / `引擎.jk`）
  + `main.jk`，纯离线固定输入，输出作稳定快照。
- **测试 `tests/test_v0_7_0_dict_literal.py`**：字典字面量 8 例
  （空/单键/多键/嵌套/全角括号/末尾逗号/键序/`去空白`）。
- **ADR-22 · 类的构造器与方法体改用词法作用域**：`JiKuaiClass` 新增
  `def_env`（在 `_eval_ClassDef` 捕获类定义处环境）；`_invoke_method`
  与 `_eval_NewInstance` 以它为父环境，而非调用者的作用域。
  - 新增 `_method_scope(klass, method_name, fallback)`：沿继承链找到**定义**
    该方法的类，用它的 `def_env`——继承来的方法拿父类所在模块的作用域。
    解析顺序与 `JiKuaiInstance._find_method` 一致（最派生优先）。
  - 效果：跨模块使用对象时，方法体能看到**定义它的模块**里 `导入` / `定义`
    的名字。此前只能看到调用者作用域，逼得跨模块编排必须外提到顶层函数。
  - `examples/scenarios/推理演示/引擎.jk` 随之回归自然的 OO 写法：
    `方法 推理` / `方法 处理问题` 直接调用本模块 `导入` 的
    `创建思考链` / `生成分析阶段` / `格式化阶段` / `格式化答案`。
  - 构造器参数仍在**调用者**作用域求值（对齐 Python 的求值时机），
    只有构造器**体**走 `def_env`。
- **ADR-23a · `蟒:` 桥支持脚本同目录 `.py` 兜底**：`pybridge._load_sidecar`
  + `py_import(..., current_file=...)`。标准 `importlib.import_module` 抛
  `ImportError` 时，回退到发起导入的那个 `.jk` 文件同目录的 `<name>.py`。
  - 补齐与 `.jk` 模块加载器的对称性（后者早已把脚本目录纳入搜索路径），
    段言那种「helper `.py` 放脚本旁边直接导入」的写法现在成立。
  - 安全取舍：用 `spec_from_file_location` 隔离加载，**不改 `sys.path`**；
    含 `.` 的点分名一律跳过本地兜底，不允许拼出目录穿越；信任边界与
    `.jk` 脚本自身同级；`DENY_LIST` 对成员访问依旧生效。
  - 以「发起导入的 `.jk`」为基准（`ModuleLoader.load` 会把
    `ev._current_file` 切到模块自身路径），而非入口脚本。
- **测试 `tests/test_v0_7_0_scope_bridge_dictkey.py`**：ADR-22/23 共 16 例
  （跨模块方法/构造器/继承链作用域、反向的"看不到调用者局部"约束、
  同目录兜底命中与不命中、不污染 `sys.path`、点分名跳过、字典键类型）。

### 修复

- **`Index` 对字典按键取值**：`_eval_Index` 原先无条件 `obj[int(idx)]`，
  使 `字典["键"]` 抛 `ValueError`。现按 `isinstance(obj, dict)` 分流，
  字典不强转键、序列仍走整数下标。
- **多行列表字面量被插入 `空`**：`_parse_list_literal` 未跳过 `NEWLINE`，
  跨行书写的 `[...]` 会把换行当成元素解析成 `NilLit`。现与字典字面量
  一致地 `_skip_newlines()`。
- **动词吞参越过 `}`**：`_parse_verb_call` / `_parse_adverb` 的参数终止
  token 集合缺 `RBRACE`，使 `{"键": 拼接 "a" "b"}` 里的变参动词吃掉右花括号。
  三处终止集合统一补入 `TokenType.RBRACE`。
- **ADR-23b · 字典键不可哈希时给中文诊断**：`_eval_DictLit` 构造前用
  `hash(key)` 试探，失败则抛携带键所在行列的 `ErrorCategory.TYPE`
  诊断（"字典的键必须是不可变类型（字符串/数字/布尔/空）"），
  不再透出 Python 的 `unhashable type: 'list'` 原文。

### 已知边界（本次未改，仅记录）

- **标识符不接受中英混排**：`自身.AI可用` 会被切成属性 `AI` + 残余
  `可用`，报「无属性/方法：AI」。命名请纯中文或纯英文。
- **标识符不能夹带内建动词字**：`赵只在主程序里` 会在 `只`（副词）处断开，
  `助手.相加` 会在 `加`（动词）处断开。命名时避开
  `加/减/乘/除/等/大/小/长度/只/皆/归/求和/最终/…`。
- **`新建 类(...)` 后不能直接接 `.成员`**：`_parse_new_expr` 不走
  `_parse_postfix`，`打印 (新建 甲(1)).方法` 会把 `.` 解析成 `空`。
  先用 `定义` 接住实例再取成员。
- **`蟒:` 桥仍是黑名单而非沙箱**：ADR-21 的既有声明不变。同目录兜底
  没有放松这一点，但也没有收紧——不要用它执行不受信任的 `.py`。

## v0.6.0（2026-08-08）

M5 里程碑：**LSP 语言服务正式实现 + 三个中文特色标准库模块 + 安全边界声明**。
四条并行支线（P1 LSP / P2 VS Code / P3 标准库 / P4 安全声明）合并交付。

测试：408 → **533 passed**，原有用例零回归（G3 基线只增不减）。

### 新增

- **会话与位置服务层 `src/jikuai/service/`（L3，LSP 与 DAP 共用）**：
  - `text_document_store.py`：`TextDocumentStore` 维护 uri → (text, version, lines)，
    处理 `didOpen` / `didChange` / `didClose` 生命周期。
  - `position.py`：`codepoint_to_utf16` / `utf16_to_codepoint` 双向换算。极快内部用
    1-based Unicode 码点列，LSP 用 0-based UTF-16 单元列，BMP 外字符（emoji、
    生僻汉字）占 2 个单元。
  - `session_host.py`：`SessionHost` 绑定文档存储与诊断缓存，
    `compile_and_diagnose(uri)` 调用 `frontend.compile_source` 并缓存结果。
    这一层的抽出让 M6 的 DAP 可以直接复用（ADR-20）。
- **`src/jikuai/completion.py`**：从 `repl_session.CompletionEngine` 提取为纯函数
  API，REPL 与 LSP 共用同一套候选生成逻辑，行为不再两处漂移。
- **LSP 正式实现（ADR-15 · F3 冻结）**：`lsp/jikuai_lsp/server.py` 从 M4 协议桩
  升级为正式服务，能力集：
  - `textDocumentSync`：`{ openClose: true, change: 1 }`（Full sync）
  - `completionProvider`：`{ resolveProvider: false, triggerCharacters: [".", "，"] }`
  - `hoverProvider`：`true`（内建动词返回中文说明 + 元数）
  - `positionEncoding`：`"utf-16"`
  - `publishDiagnostics`：同时推送错误（`ParseError`）与警告（`JK-W1001` 副词透传）
- **标准库 · 中文正则 `stdlib/正则.jk` + `.py`**：导出 `匹配` / `搜索` / `替代` /
  `编译`。`搜索` 返回 `{文本, 起始, 结束}` 字典。支持字面量、字符类（含 `[一-十]`
  中文范围）、量词、分组、`|`，以及中文别名 `\汉`；不支持反向引用与断言。
  无命中返回空/假而非报错。
- **标准库 · 成语断言 `stdlib/成语.jk` + `.py`**：导出 `是成语` / `成语释义`。
  内置 **313 条**常用成语（版本 `v0.6.0-300`），`frozenset` + `dict` O(1) 查找，
  零第三方依赖。
- **标准库 · 中文分词 `stdlib/分词.jk` + `.py`**：导出 `分词`。内置 **565 条**
  常用词，最长 5 字，单字词不入库。正向最大匹配；兜底策略：空白不产出 /
  半角字母数字整体成词 / 其余单字成词。**幂等且无全域副作用**（G12）。
- **安全边界声明（ADR-21 · US-M5-08）**：`docs/安全边界.md` 作为权威声明，
  覆盖 pybridge / AOT 产物 / DAP 调试器 / 模块加载四块的信任前提。
  `pybridge.py` docstring 与 `README.md` 同步声明。

### 变更

- **`pybridge.py` docstring 重写安全边界段**：从「安全约束」列表升级为完整的
  「不提供完整沙箱隔离」声明 —— 明确 `DENY_LIST` 是黑名单缓解手段、列出
  `importlib` 等已知绕过路径、区分适用场景与禁用场景、给出进程级/容器级
  隔离的替代方案。
- **`README.md` Python 互操作段**：置顶安全声明，指向 `docs/安全边界.md`。
- **`repl_session.CompletionEngine`**：内部改为委托 `completion` 模块，
  对外行为不变。

### 门禁

- G1 全量测试全绿：533 passed
- G2 示例逐文件 exit 0：新增 3 个 stdlib 示例（正则/成语/分词），全部通过
- G3 测试数只增不减：408 → 533
- G4 零破坏性回归：原有用例一条不红
- G10 标准库契约：`工具` / `校验` / `简繁` / `排版` / `正则` / `成语` / `分词`
  七个模块导出集合 == `docs/标准库.md` 声明
- G11 LSP 契约：pytest + subprocess 协议级测试，完全脱离手工 VS Code；
  initialize / didOpen→publishDiagnostics / completion / hover / shutdown+exit 全通
- G12 分词幂等：AC-M5-07-01/02/03 三条全绿。除行为断言外，另加**静态防回归**——
  正则扫描 `分词.py` 断言无 `global` 语句，且词典必须是 `frozenset`

### 冻结点

- **F3 LSP 能力集冻结**：上述 capabilities 结构由 `capabilities.freeze_signature()`
  返回规范化 dict，测试断言其稳定性。后续变更需走 ADR。
- **F4 标准库公共 API 冻结**：七个模块的导出符号进入 v0.7 兼容承诺范围，
  是 M6 AOT 试验的前置条件。

### 已知限制与语言层遗留

- **`正则` 的替换 API 命名为 `替代` 而非 `替换`**：`替换` 是内建动词
  （`VERB_ARITY['替换'] = 3`），`lexer._try_longest_keyword()` 做最长关键字匹配，
  会把 `替换` 及任何以 `替换` 开头的名字切成 `VERB` token；而 `parser` 的成员访问
  要求 `.` 之后必须是 `IDENT`，因此 `正则.替换(...)` 过不了语法分析。
  **这是语言层约束而非命名偏好**。根治需要一个 ADR 允许 parser 在 `.` 之后接受
  VERB token —— 已登记为待裁决项。
- **`.jk` 字符串字面量吞未知转义的反斜杠**：`lexer._read_string` 的
  `esc_map.get(esc, esc)` 对未知转义丢掉反斜杠，所以 `.jk` 里 `"\d+"` 实际等于
  `"d+"`。这是既有语言行为，已在 `docs/标准库.md` 加「反斜杠陷阱（必读）」小节，
  推荐用 `[0-9]` / `[一-鿿]` 字符类规避，并加了一条测试把该行为钉成契约。
- 简繁对照表约 1230 条，覆盖高频字，冷僻字原样透传；不做词汇级差异转换。
- LSP hover 只覆盖内建动词与关键字；用户定义函数缺乏 docstring 基础设施。
- LSP 文本同步为 Full 而非 Incremental，大文件编辑时每次重传全文。
- `编译` 返回的字典含内部键 `_编译对象`（`re.Pattern`），属实现细节，
  不作为稳定 API。

## v0.5.0（2026-08-08）


M4 里程碑：**诊断内核 + 标准库契约 + ADR-06 X2 闭环 + LSP 协议桩**。
四条并行支线（P1 诊断 / P2 标准库 / P3 两遍分词 / P4 LSP 桩）合并交付。

测试：258 → **408 passed**，原有用例零回归（G3 基线只增不减）。

### 新增

- **诊断内核 `src/jikuai/diagnostics/`（ADR-14 · F1 冻结契约）**：极快诊断的
  唯一真源，CLI 与 LSP 均为纯投影消费者。
  - `model.py`：`Position` / `Span`（end 独占）/ `Suggestion` / `Diagnostic`，
    全部 `frozen` 不可变；`Diagnostic.sort_key()` 提供决定性排序。
  - `codes.py`：错误码表 `JK-{E|W}{段位}{序号}`，段位 0xxx 词法 / 1xxx 语法 /
    2xxx 名称 / 3xxx 元数 / 4xxx 类型 / 5xxx 模块 / 6xxx 互操作 / 7xxx AOT /
    8xxx 调试 / 9xxx 内部。**码一经发布只增不改不复用。**
  - `sink.py`：`DiagnosticSink` 协议 + `ListSink`（drain 稳定排序）/ `NullSink`。
  - `spelling.py`：多候选拼写纠错，编辑距离 ≤2，排序规则「距离升序 → 文本码点序」，
    并列候选整组保留（不被 `MAX_SUGGESTIONS` 硬截断）。
  - `static_check.py`：编译期静态诊断，当前覆盖 `JK-W1001`（副词内部接非内建
    动词的原值透传）。
  - `reporter.py` / `adapters.py`：`render_text` / `render_json` /
    `to_lsp_diagnostic` / `from_error_info` / `to_error_info` 纯投影函数。
- **`JK-W1001` 副词透传编译期提示**：`皆` / `只` / `归` 内部接用户函数或拼错的
  动词时，代码不报错但按原值透传、不产生预期效果——这是新手高频坑，现在编译期
  会给出带位置的警告。警告不影响退出码，程序照常执行。
- **两遍分词编排 `src/jikuai/frontend.py`（ADR-17 · ADR-06 X2）**：
  `compile_source` 串联「分词 → 解析 → 静态诊断」。Pass1 用行文本启发式定位
  类块并解析出 AST，从 AST 提取**权威 `ClassRegionTable`** 后 Pass2 重扫，
  token 序列结构等价即收敛；未收敛则发 `JK-W9001` 并回退首遍结果，不崩。
  **性能优化**：AST 不含 `ClassDef` 时直接跳过 Pass2（Spike 实测无条件两遍
  会使编译阶段 +87%，绝大多数脚本不含类）。
- **`lexer.tokenize(source, external_defs=None, class_regions=None)`**：新增
  `class_regions` 可选参数接收权威类区间。为 `None` 时走原行文本启发式，
  与 v0.4.x 字节级等价。
- **标准库契约（ADR-16 · G10）**：
  - `src/jikuai/stdlib_contract.py`：静态解析 `.jk` 的 `导出` 语句，
    提供 `parse_exports` / `declared_exports` / `list_stdlib_modules` /
    `has_python_backing` / `default_stdlib_dir`。
  - `scripts/check_stdlib_contract.py`：比对实际导出与 `docs/标准库.md` 声明，
    不一致退出码 1；支持 `--json`。
  - **混合模块加载**：`module_loader` 用 `importlib.util.spec_from_file_location`
    隔离加载同名 `.py`（不污染 `sys.path`），把其公共可调用对象注入 `.jk` 模块
    环境。`.jk` 是唯一对外门面，`.py` 为内部实现；与 `蟒:` 前缀的 `sys.path`
    语义互不干扰。
- **标准库新模块**：
  - `stdlib/简繁.jk` + `.py`：`转繁体` / `转简体`，内置约 1230 条常用字映射；
    10 组一简对多繁的固定口径见 `docs/标准库.md`。无可转换字符时输出恒等于输入。
  - `stdlib/排版.jk` + `.py`：`规范化文本` / `插入间距` / `规范标点`，中英文间距、
    全半角标点规范化，**保证幂等**。
- **LSP 协议桩 `lsp/`（ADR-15）**：独立发行包 `jikuai-lsp`，自实现
  JSON-RPC over stdio（`transport.py`）。`python -m jikuai_lsp` 可启动，
  支持 `initialize` / `didOpen` / `didChange` / `shutdown` / `exit`，
  `publishDiagnostics` 推送**真实诊断**（`ParseError.info` → `from_error_info`
  → `to_lsp_diagnostic`，含 UTF-16 列换算）。主包不依赖 `lsp/`，反向单向依赖。
- **回退开关 `JIKUAI_DIAGNOSTICS=off`**：`make_default_sink()` 返回 `NullSink`，
  关闭诊断收集与 stderr 输出（G8 新增守护点）。
- **文档**：`docs/基线校正说明-v0.5.0.md`、`docs/ADR-14-诊断内核.md`、
  `docs/ADR-16-标准库契约.md`、`docs/ADR-21-pybridge安全边界.md`、
  `docs/诊断编码表.md`、`docs/路线图-v0.5.0.md`、`docs/标准库.md`。
- **示例**：`examples/stdlib/简繁示例.jk`、`examples/stdlib/排版示例.jk`。

### 变更

- **`errors.py` 降级为兼容外壳**：`ErrorCategory` / `ErrorInfo` /
  `ErrorFormatter` / `spelling_suggestion` 全部公开符号与签名保持不变（嵌入 API
  兼容红线），内部建议文案渲染委托 `diagnostics.spelling.format_suggestions`。
- **`ErrorCategory` 追加 4 个成员**：`MODULE` / `INTEROP` / `CONTRACT` /
  `LIMITATION`。原有 5 个成员的名称与中文值不变。
- **诊断建议文案**：`建议：是否想输入 "x"？` → `您是否想输入 \`x\`？`（裁决 D-03）。
  按 ADR-14「**错误码是稳定契约，渲染文案不是**」，属 `Changed` 而非 BREAKING；
  相应地把测试中对旧文案的精确字符串断言改为对结构化字段断言。
- **`main.run_source(source, evaluator=None, file=None)`**：新增可选 `file`
  参数；编译改走 `frontend.compile_source`；警告类诊断输出到 stderr，
  不影响返回值与退出码。
- **`module_loader` 错误消息带码**：`找不到模块：X` → `[JK-E5001] 找不到模块：X`；
  `模块 X 未导出：Y` → `[JK-E5002] 模块 X 未导出：Y`。消息主体不变。

### 修复

- **`parser` 未标注 `ClassDef` 位置**：`ClassDef` 节点的 `line` / `col` 一直是 0
  （从未走 `_loc`），导致任何依赖类块行号的下游分析都拿不到位置。现用类名 token
  标注 `line` / `col`，并新增 `end_line` 记录类块收尾 `。` 所在行。这是实现
  ADR-06 X2 权威区间时暴露出的既有缺陷。
- **`lexer._class_regions()` 重复计算**：该方法在 `__init__` 期间被
  `_prescan_definitions` / `_class_regions_by_name` / `_prescan_self_fields`
  三处调用，每次都重新全文扫描。现加结果缓存。
- **`scripts/check_stdlib_contract.py` 在 Windows 下输出编码错误**：控制台默认
  GBK 导致被 `subprocess` 以 UTF-8 捕获时解码失败。现强制 stdout/stderr 用 UTF-8。

### ADR

- **ADR-14 诊断内核**：新建 `diagnostics/` 为唯一真源，`errors.py` 降级为兼容
  外壳（候选 A 原地扩字段 / **B 新建包** / C 完全重写中选 B）。两条硬约束：
  错误码是契约文案不是；`diagnostics/` 不得 import `evaluator`（后者持有
  `JiKuaiError`，会形成循环耦合）——由静态源码扫描测试守护。
- **ADR-15 LSP 技术栈**：本机 pygls 为 2.x，API 与 1.x 差异大；M4 桩只需 4 个
  生命周期方法 + 2 个通知，故自实现约 60 行 JSON-RPC 帧格式。pygls 登记在
  `lsp/pyproject.toml` 的 optional-dependencies，M5 可平滑切换。
- **ADR-16 标准库契约**：**沿用现有运行期 `导出` 语句**作为唯一导出声明机制，
  不引入 `__导出__` 变量（基线核对发现真实机制是 `导出` 语句 +
  `evaluator._current_exports` + `ModuleValue`）。`stdlib/` 固定在仓库根，
  因 `module_loader._search_paths()` 依赖 `'..','..','stdlib'` 上溯逻辑，移动会
  破坏解析。
- **ADR-17 ADR-06 X2 闭环**：两遍分词 + 权威 `ClassRegionTable` + 收敛检测 +
  `JK-W9001` 兜底 + `JIKUAI_LEGACY_ADR06=1` 强制单遍。
- **ADR-21 pybridge 安全边界**：文档级 ADR，明确 pybridge **不提供完整沙箱
  隔离**，`DENY_LIST` 仅为黑名单缓解，`importlib` 等间接路径可绕过。适用于运行
  自己或可信来源的 Python 代码；**不适用于执行不受信任的第三方代码**。

### 冻结点

- **F1 诊断契约冻结**：`Diagnostic` / `Span` / `Sink` 数据结构 + 错误码表。
  通过判据含「CLI + LSP 桩双消费者实证」——LSP 桩推送的是真实诊断，非空数组。
- **F2 标准库契约冻结**：`导出` 声明机制 + `JK-E5001` / `JK-E5002` 错误码。

### 门禁

- G1 全量测试全绿：408 passed
- G2 示例逐文件 exit 0：22 个 `.jk`（含新增 2 个）全部通过
- G3 测试数只增不减：258 → 408
- G4 零破坏性回归：原有用例一条不红
- G8 回退开关有守护：`JIKUAI_LEGACY_ADR06`（既有）+ `JIKUAI_DIAGNOSTICS=off`（新增）
- G9 诊断内核契约：字段完整性 / 码表分段 / 可复现性 / 兼容红线均有断言
- G10 标准库契约：`工具` / `校验` / `简繁` / `排版` 四个模块导出集合 == 文档声明

### 性能（D-06 触发条件 T1 量测结论）

用 `scripts/bench_compile.py` 按中位数（60 轮、预热 1 轮）消噪量测：

- 原路径 `tokenize+parse`：42.82 ms
- frontend 两遍（含"无类跳过 Pass2"优化）：47.98 ms（+12.1%，含静态诊断开销）
- **两遍分词机制本身的净开销：仅 +5.9% 编译阶段**（26 个样本文件含 1 个类文件）

编译阶段只占整体执行的一小部分；258 基线子集全量耗时 5.03s，两遍机制的
2.5ms 净开销可忽略。**未触及 D-06 的 T1 阈值（总套件回归 >10%），ADR-06 X2
真正闭环，无需降级。**

### 已知限制

- 简繁对照表约 1230 条，覆盖高频字，冷僻字原样透传；不做词汇级差异转换
  （如「软件/軟體」）。
- `规范标点` 只对紧邻表意文字的半角标点转全角，`3.14` / `a, b` 保持原样。
- 纯 `.py` 标准库模块（`历法`）没有 `.jk` 门面，不参与 G10 硬失败，仅提示。
- LSP 桩未声明 `completionProvider` / `hoverProvider`——LSP 契约「声明即承诺
  响应」，M4 未实现故不提前声明，留待 M5。
- `lexer` 抛出的 `JiKuaiError`（如非法字符）不携带标准 `ErrorInfo` 位置格式，
  LSP 桩暂不将其投影为诊断，M5 需让 lexer 改走 `DiagnosticSink` 路径。

## v0.4.1（2026-08-08）


GA 后遗留清理版（patch）。仅修 bug 与测试命名，无语言语义/接口新增。

### 修复

- **D-13（P0）· `尝试`/`捕获`/`最终` 吞控制流信号**：`evaluator._eval_Try`
  的 `except Exception` 兜底分支会把控制流信号 `ReturnSignal` /
  `BreakSignal` / `ContinueSignal` 一并捕获，导致：
  - 函数体内 `尝试：返回 X。捕获 e：返回 Y。` 实际返回 `Y`（应为 `X`）；
  - 循环体内 `尝试：…跳出/跳过。` 被 `捕获` 分支吞掉，循环不中断。

  修复：在 `_eval_Try` 的 except 链最前面加 `except (ReturnSignal,
  BreakSignal, ContinueSignal): raise`，让三种信号早于 `JiKuaiError`
  与 `Exception` 兜底透传给外层函数/循环处理；`最终` 分支即使在信号透传
  时仍会执行（Python `finally` 语义保证）。与 ADR-08「控制流信号在
  evaluator 顶层专门拦截」一致——`尝试` 结构同样应透传而非吞掉。

  影响：`examples/scenarios/管道数据清洗.jk` 里为规避此 bug 采用的
  「标记变量 + 块外返回」写法（`赵可转`）已随本轮**简化为直接**
  `尝试 { 转整数 X。返回 真。} 捕获 e { 返回 假。}`，脚本 stdout 与 v0.4.0
  逐字一致；等价用例另在 `tests/test_v0_4_1_d13.py` 中覆盖。

### 测试

- 新增 `tests/test_v0_4_1_d13.py`（6 条）：覆盖函数内 `返回` 透传、循环内
  `跳出`/`跳过` 透传、`最终` 分支在信号透传时仍执行、真 `JiKuaiError`
  仍被 `捕获` 接住的回归防护。
- `tests/test_jikuai.py`：`test_ac36_version_is_beta` → `test_ac36_version_consistency`
  （清理遗留 `beta` 命名，断言内容不变，仍校验三处版本一致）。
- 全量 `python -m pytest -q`：**258 passed**（v0.4.0 基线 252 + 新增 6），零回归。
- examples/scenarios/管道数据清洗.jk 的 赵可转 简化为直接 尝试/捕获（D-13 修复后已可行）。

## v0.4.0（2026-08-08）

极快语言首个对外发布版（GA）。历经 M1 / M2 / M3 三个里程碑，在 v0.3.2（156 项测试）
基线上累计新增至 250+ 项测试，全绿零回归；17 个示例（11 存量 + 6 管道 + 3 场景，
按目录计）退出码全部为 0。

### 概述（M1 / M2 / M3 主要变化）

- **M1 · ADR-06 副作用根治（ADR-09）**：把「用户定义名白名单」从「同次分词全域生效」
  收敛为**类作用域**。类内成员（`方法 长度` / 字段 `自身.求和`）不再污染类外顶层
  与其他类的内建动词语义；顶层定义仍全局可见。新增 `JIKUAI_LEGACY_ADR06=1`
  回退开关。配套 `docs/元数解析规范.md`、`docs/ADR-09-类作用域白名单.md`。
- **M2 · Python 双向互操作（ADR-10/11/12）**：
  - 独立 `src/jikuai/pybridge.py`：Python 桥核心与类型编组（列表/字典/人民币/日期）。
  - out-bound：`导入 蟒:math。` 后 `math.sqrt(16)` 括号调用（ADR-11：Python 桥函数
    **不进** `VERB_ARITY` 元数体系，免括号写法对桥无效，缺括号抛 SYNTAX 中文诊断）。
  - in-bound：`import jikuai; jikuai.load(...)` / `run_source(...)`，异常保留中文文案
    与 `ErrorInfo`。
  - 安全：默认**拒绝清单** `DENY_LIST`（os.system / subprocess.Popen / eval / exec）+
    显式 `蟒:` 前缀 + `load` 拒绝绝对路径与 `..` 穿越。⚠️ 这是黑名单而非完整沙箱
    （见「已知限制」）。
- **M3 · 示例成体系 + 发布**：
  - README「语言特色」重排为 **管道式数据流 → 元数驱动解析 → 无空格书写 →
    百家姓标识符 → 中国国情内置**（AC-106）。
  - 修正 README 管道示例注释（AC-107）：`列1 2 3 4 5，皆乘2，只大6，归加0。`
    实机结果为 **`30`**（原注释 `24` 有误）。根因：`大` 非内建比较动词
    （内建为 `大于`），副词 `只` 内部遇未知动词按原值透传、不产生过滤，故
    `[2,4,6,8,10]` 原样归约求和为 30。补充说明正确过滤写法 `只大于6` → `18`。
  - 新增 `examples/pipelines/` 6 个管道示例（AC-108/109/110）、`examples/scenarios/`
    3 个场景脚本（AC-113/114/115），全部实机跑通、退出码 0。

### 版本号

- 三处升到 `0.4.0`：`pyproject.toml::version`、`src/jikuai/__init__.py::__version__`、
  `src/jikuai/main.py::VERSION`；`test_ac36_version_is_beta` 与 `test_d11_module_*`
  同步；新增 `test_v040_version_consistency` 守护。

### 新增示例

- **管道范式**（`examples/pipelines/`）：
  | 文件 | 教学目标 | 实机输出摘要 |
  |------|----------|--------------|
  | `01_多级过滤映射聚合.jk` | 过滤→映射→聚合多级管道 | 一条龙 `只大于4，皆乘3，归加0` = 135 |
  | `02_条件分支管道.jk` | 管道结果结合 如果/否则 分支 | 平均 81.71 → 及格；逐元素分类 |
  | `03_字典结构化数据.jk` | 字典键值访问 / `皆取值"键"` 投影 | 80 分以上绩效总和 177 |
  | `04_异常在管道中传播.jk` | 尝试/捕获/最终 拦截管道异常 | 捕获除零/类型错误/业务异常，正常收尾 |
  | `05_副词组合.jk` | 皆/只/归 单用与组合 | `皆乘2→只大于6→归加0` = 60 |
  | `06_中国特色管道.jk` | 人民币/农历/干支/生肖进管道 | 报销合计 ￥111.10；近五年生肖干支 |
- **场景脚本**（`examples/scenarios/`）：
  | 文件 | 场景 | 实机输出摘要 |
  |------|------|--------------|
  | `财务计算.jk` | 报销单：￥字面量/税费/大写金额/汇总 | 小计 ￥421.50，含税 ￥446.79 |
  | `农历工具.jk` | 公历→农历/干支/生肖/甲子循环 | 2026 = 丙午(马)年；1984 与 2044 同为甲子 |
  | `管道数据清洗.jk` | 脏数据→多级管道(≥3 段)→结果 | 清洗后正数求和 197；一条龙 = 394 |

### 新增测试（`tests/test_v0_4_0_examples.py`）

- AC-112：6 个管道示例逐文件 `python -m jikuai` 退出码 0（+ 目录计数守卫）。
- AC-118：3 个场景脚本逐文件退出码 0（+ 目录计数守卫）。
- AC-107：README 管道示例 stdout 断言（值 == 30、`打印` 首行 == `30`；
  并以 `只大于6` → 18 作对照佐证）。
- 版本号一致性（0.4.0）三处对齐。

### AC 完成状态（AC-66 ~ AC-123）

- **AC-66 ~ AC-70（M1 · ADR-09）**：✅ 类作用域白名单，类内成员不污染类外；
  实例成员仍走类内方法；字段名同规则；REPL 跨输入生效；回退开关有守护。
- **AC-71 ~ AC-96（M2 · Python 桥 out-bound / 编组 / 安全）**：✅ `蟒:` 前缀、
  括号调用、类型往返、拒绝清单、路径穿越防护、缺括号 SYNTAX 诊断（AC-94）。
- **AC-97 ~ AC-104（M2 · in-bound 嵌入）**：✅ load / run_file / run_source 三入口，
  函数/变量/类/异常翻译，`import jikuai` 不触发 load、无全局可变状态（AC-104）。
- **AC-105**：✅ 桥可禁用 / 拒绝清单命中抛 RUNTIME 诊断（G8）。
- **AC-106**：✅ README「语言特色」重排。
- **AC-107**：✅ README 管道示例注释修正为实机结果 30，补测试断言 stdout。
- **AC-108 / 109 / 110**：✅ 6 个复杂管道示例，每文件 15~60 行、含中文教学注释、退出码 0。
- **AC-111**：✅ 示例主题一一对应（多级过滤映射聚合 / 条件分支 / 字典结构化 /
  异常传播 / 副词组合 / 中国特色）。
- **AC-112**：✅ 6 个管道进入 exit=0 遍历测试。
- **AC-113 / 114 / 115**：✅ 财务计算 / 农历工具 / 管道数据清洗 三场景脚本（25~120 行）。
- **AC-116**：✅ 场景脚本覆盖 ￥字面量/大写金额/税费、公历→农历/干支/生肖、
  脏数据多级管道（≥3 段）。
- **AC-117**：✅ README「示例与场景」小节引用全部管道与场景脚本。
- **AC-118**：✅ 3 个场景进入 exit=0 遍历测试。
- **AC-119 ~ AC-123**：✅ 版本号三处 → 0.4.0 并测试守护；CHANGELOG v0.4.0 段；
  路线图-v0.4.0 标记已完成；遗留登记见下。

### ADR 决议（ADR-09 / 10 / 11 / 12）

- **ADR-09（类作用域白名单）**：X1（类作用域 ScopeMap，本期采用）/ X2（parser 权威
  区间，权威但改动大，**延期未来版本**）/ X3（运行期回退，弃用）。选定 X1。
- **ADR-10（Python 桥核心）**：独立 `pybridge.py`，`蟒:` 前缀路由，类型编组，
  黑名单 `DENY_LIST` + 路径穿越防护。
- **ADR-11（括号调用守恒）**：Python 桥函数不污染中文动词元数体系，必须括号调用，
  缺括号抛 SYNTAX 中文诊断（AC-94），杜绝静默 fallthrough。
- **ADR-12（元数守卫前移）**：内建动词实参数量守卫（`_check_verb_arity`），
  变参/副词跳过，错误消息不泄漏 Python 实现细节。

### 已知限制与假设（发布保留项）

- **ADR-06 X2（parser 权威区间）延期**：`_class_regions` 仍为行文本启发式，极端
  缩进/嵌套下可能收窄区间（安全侧倾：只漏登记字段，不误切、不污染类外）。
  升级为 parser 权威定位延期至未来版本。
- **拒绝清单非完整沙箱**：`DENY_LIST` 是黑名单，未在清单内的危险调用（如
  `importlib` 间接导入）仍可能绕过。生产环境嵌入不可信 `.jk` 时须叠加进程级隔离。
  详见 `docs/互操作.md`「安全边界」。
- **副词内部仅识别内建动词**：`皆/只/归` 的内部动词必须是内建动词；写
  `皆某用户函数` 不报错但按原值透传（`04_异常在管道中传播.jk` 已注明，
  逐元素跑自定义逻辑请用 遍历 循环）。
- **`尝试` 块内的 `返回` 会被兜底 except 吞掉**：`_eval_Try` 的 `except Exception`
  捕获了控制流信号 `ReturnSignal`，导致 `尝试` 体内直接 `返回` 不生效。规避：用
  标记变量在 `尝试` 块外 `返回`（`管道数据清洗.jk::赵可转` 已示范）。此为存量
  evaluator 行为，本轮示例侧规避、未改框架，登记待架构层评估。

---

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
