# 极快 JiKuai · 待办真源（BACKLOG）

本文件是**唯一待办真源**（v0.16.0 W34 建立）。此前散落在复盘、各 README、
包管理与语法参考里的待办，全部收敛到这里；原处只保留一句摘要 + 指回本文的链接。

**改动纪律**：任何"暂不做 / 未实现 / 推迟"的结论，先写进本表，再在源文件里留一句
摘要指回。不要在多处各写各的，避免描述漂移。

**列含义**
- **条目**：具体待办或已知边界。
- **来源**：最初记录该条的文件（回溯用）。
- **优先级**：`高` / `中` / `低`；`设计边界` 表示这是**有意的取舍，不是 bug**，
  除非另立 ADR 否则不做。
- **目标版本**：预计落地版本；`待定` 表示尚无排期；`不做` 表示明确不做。

---

## 1. LSP 缺口

来源：`lsp/README.md`「已知缺口」表（W32 已从该表移出 `documentSymbol` /
`signatureHelp`）。

| 条目 | 来源 | 优先级 | 目标版本 |
|------|------|--------|----------|
| ~~`textDocument/rename`~~ —— **v0.17.0 W41 已上线**（跨文件 `WorkspaceEdit`；非原子新名被拒；块导出名被拒且给中文理由） | `lsp/README.md` | — | v0.17.0 已完成 |
| ~~`textDocument/references`~~ —— **v0.17.0 W40 已上线**（跨文件，走 `service/symbol_index.py` 反向引用图） | `lsp/README.md` | — | v0.17.0 已完成 |
| `textDocument/codeAction` —— v0.15.0/v0.16.0/v0.17.0/v0.18.0 四轮复审后明确关闭。理由见 `docs/ADR-31-不做codeAction.md`：14 个诊断码无一满足唯一机械修复、唯一候选用例已被 `极快.选块` 覆盖、四轮零社区诉求。重开条件见 ADR-31 §5 | `lsp/README.md` | 设计边界 | 不做（除非另立 ADR 推翻 ADR-31） |
| `foldingRange` —— 未实现 | `lsp/README.md` | 低 | 待定 |
| 增量诊断 —— `didChange` 已走增量同步，但诊断仍是整篇重编译 | `lsp/README.md` | 低 | 待定 |
| ~~多根 workspace~~ —— **v0.18.0 W54 已补齐**。v0.17.0 W38 只做到 `initialize` 解析并记录 `workspaceFolders`；W54 把 `definition` 的块路径解析扩到多根（此前只查 `blocks_root()` 与文档自身目录） | `lsp/README.md` | — | v0.18.0 已完成 |
| ~~`_token_at` 切不开 `定义X` / `赋值X`~~ —— **v0.18.0 W56 已修**。改成优先走 JiKuai lexer 分词（`定义` KEYWORD + `赵共享` IDENT 是两截），lexer 抛异常时回落字符边界扫描。此前从 `定义赵共享` 起发起 rename 拿不到符号 | `lsp/README.md` | — | v0.18.0 已完成 |
| 启动时全量扫工作区 —— 未做。只在 `didOpen` / `didChange` 时增量索引；未打开文件里的引用查不到 | `lsp/README.md` | 低 | 待定 |
| pull-based 诊断 —— `diagnosticProvider: false`，诊断只走服务端 push | `lsp/README.md` | 低 | 待定 |

> `rename` / `references` 在 v0.17.0 W40-W41 落地；`codeAction` 在 v0.18.0 W53 以
> ADR-31 **明确关闭**（不再逐轮复审）；多根 `definition`（W54）与 `_token_at`（W56）
> 在 v0.18.0 清账。

## 2. AOT 子集边界（**设计边界，不是 bug**）

来源：`tools/aot/jikuai_aot/subset_gate.py` 的 `UNSUPPORTED_NODE_TYPES` +
`_NODE_FEATURE_NOTES`（对外表述与 `docs/AOT.md` / `docs/语法参考.md` §13.3 对齐）。
超出子集时报 `JK-E7001` 并**拒绝产出任何文件**。这些不是缺陷，是 AOT 后端刻意
不引入的运行时依赖。

| 条目 | 来源 | 优先级 | 目标版本 |
|------|------|--------|----------|
| 成员访问（`对象.字段` / `对象.方法`）—— 依赖运行时对象模型 | `tools/aot/jikuai_aot/subset_gate.py` | 设计边界 | 不做（除非另立 ADR） |
| Try / Throw（异常）—— 需要栈展开机制 | `tools/aot/jikuai_aot/subset_gate.py` | 设计边界 | 不做（除非另立 ADR） |
| Lambda（闭包）—— 需要环境捕获（逃逸分析或显式 env 结构体）；改用顶层 `函数` 定义 | `tools/aot/jikuai_aot/subset_gate.py` | 设计边界 | 不做（除非另立 ADR） |
| ListLit / DictLit / Index（列表/字典字面量与索引）—— 需要堆分配 + 运行时容器（哈希表） | `tools/aot/jikuai_aot/subset_gate.py` | 设计边界 | 不做（除非另立 ADR） |
| 类 / 继承 / 管道 / 副词 / 模块导入 —— 各有明确运行时依赖，同属子集外 | `tools/aot/jikuai_aot/subset_gate.py` | 设计边界 | 不做（除非另立 ADR） |

> 已在子集内（历史扩容）：算术/比较/逻辑/打印动词、变量、人民币字面量、
> 条件分支、`当`/`重复 N 次` 循环、`跳出`/`跳过`、顶层无捕获用户函数、
> `遍历 于 范围(...)` 与 `遍历 于 【字面量列表】`。

## 3. 包管理

来源：`docs/包管理.md`「尚未实现」章节 + `src/jikuai/pkg/`。

> **v0.19.0 W61 纠偏**：此前本节把「本地注册表」「`jk 包 发布`」记为「MVP 占位 / 未落地」——
> **均为错误认定，落后代码约七个版本**。实况：**本地文件系统注册表在 v0.11.0
> M11-1 就完整实现**（`registry.publish` / `lookup` / `list_packages` / `search` /
> `unpublish` 全可跑，`sources._fetch_registry` 已接 `registry.lookup()`，
> `tests/test_v0_11_0_registry.py` 端到端绿）；`jk 包 发布 [--确认] [--分类]
> [--允许覆盖]` 早已实现（默认演练）。真正的缺口是下表三项。

| 条目 | 来源 | 优先级 | 目标版本 |
|------|------|--------|----------|
| ~~中央注册表 = MVP 占位~~ / ~~`jk 包 发布` 未落地~~ —— **认定错误，已于 v0.19.0 W61 纠偏**。本地注册表 + 发布 早在 v0.11.0 落地 | `src/jikuai/pkg/registry.py`、`cli.py` | — | v0.11.0 已完成（此前误记） |
| ~~HTTP 分发（远程注册表）~~ —— **v0.20.0 M20（W77-W80）已完成**。ADR-34 已实施：`backend.py` 的 `RegistryBackend` 协议（Local/Http 两实现）+ urllib.request 零依赖 + tar.gz 快照传输与安全解压 + 两层多注册表（`JIKUAI_REGISTRY` 全局默认 + per-dependency `{"注册表": url}` override）+ Bearer token 鉴权（`JIKUAI_REGISTRY_TOKEN` / `~/.jikuai/凭证.json`）。**远程发布（POST）仍未做**，`HttpBackend` 写操作抛 `UnsupportedOperation`，接口已按 ADR-34 §2.6 预留 | `src/jikuai/pkg/{backend,registry,sources,manifest}.py`、`docs/ADR-34-远程HTTP注册表.md` | — | v0.20.0 M20 已完成 |
| ~~远程**发布**~~（`POST /publish` + 服务端进程 + 写路径鉴权 + 审计）—— **v0.21.0 M23（W89-W93）已完成**。ADR-35 已实施：`tools/registry-server/` 服务端（零依赖 `http.server`；token 只存 sha256 + `hmac.compare_digest`；token↔签名者↔包名白名单三重绑定；频次/单包/请求体三层配额；append-only 审计且拒绝也记、不记 token）+ 客户端 `HttpBackend.publish_package()` + `registry.publish()` 远程分支（强制签名、一律拒覆盖）+ G18 扩展到服务端环境变量。测试：auth 13 例、客户端 20 例、跨进程端到端 14 例（含越权/覆盖/未签名/伪造签名/协议版本/错误不泄露路径） | `tools/registry-server/`、`src/jikuai/pkg/{backend,registry,cli}.py`、`docs/ADR-35-远程发布协议.md` | — | v0.21.0 M23 已完成 |
| ~~校验和格式不一致~~ —— **v0.20.0 W73 已清**。`registry.publish` 曾存裸 hex、`installer` 往 `包.锁` 存 `sha256:<hex>`；统一为带前缀。动因不是好看：ADR-33 的签名对象是校验和字符串，两端格式不同会签出不同签名导致验签必失败 | `docs/ADR-33-包签名.md` | — | v0.20.0 W73 已完成 |
| 包签名（非对称）—— **v0.20.0 W73-W76 已完成**。ADR-33 已实施：`_ed25519.py`（RFC 8032 纯标准库）+ `keys.py` 密钥管理 + `trust.py` TOFU 信任库 + `registry.publish(signer=)` 签校验和 + `installer._verify_registry_signature` 三道检查（完整性/签名/未签名过渡告警）+ CLI `密钥` 子命令族与 `发布 --签名` + G17/G18 门禁。`test_pkg_signing.py` 28 用例 | `docs/ADR-33-包签名.md`、`src/jikuai/pkg/{_ed25519,keys,trust,registry,installer,cli}.py` | — | v0.20.0 M19（W73-W76 已完成） |
| 全局缓存共享 —— 当前每个项目独立 `极快_包/` | `docs/包管理.md` | 低 | 待定 |
| git 依赖的 commit 级锁定 —— 现在只锁到标签 | `docs/包管理.md` | 低 | 待定 |
| `sources._iter_source_files` 死代码 —— `if fn.endswith('.tmp'): continue` 不可达：上一行已限定 `fn` 以 `.jk`/`.py`/`.json` 结尾，不存在同时以 `.tmp` 结尾的文件名。v0.22.0 W99 补覆盖时发现，当时未改（不在范围内） | `src/jikuai/pkg/sources.py` | 低 | 待清理 |
| G17 只锁顶层子命令 —— `密钥` 的子子命令（`生成`/`列表`/`导出`/`信任`/`撤信` 及英文写法）不在 `_ALIASES` 里，G17 看不见，靠人工同步。v0.22.0 W101 加 `信任`/`撤信` 时暴露：为过门禁只能把它们的英文写法移出 `英文别名：` 段落。**这正是 G17 当初要防的那类漂移，只是深了一层**。修法：G17 再解析 `_cmd_key` 里 `sub in (...)` 的分支集，与文档 `jk 包 密钥 <子命令>` bullet 双向 diff | `scripts/check_pkg_doc.py`、`src/jikuai/pkg/cli.py` | 中 | v0.23.0 |
| 块 ↔ 包 桥接 —— 块靠 `JIKUAI_PKG_ROOTS` 手配环境变量发现，包装到 `极快_包/`，两套体系互不相通。ADR-27 §4 末尾承认此桥接未做。**v0.19.0 W63 已定 `docs/ADR-32-块包格式.md`**（`包.json` 加可选 `块` 字段声明块根 + 安装器维护 `极快_包/.块根.json` 索引让 `extra_roots()` 合并读取），W65-W66 实现 | `docs/ADR-32-块包格式.md`、`src/jikuai/pkg/blocks.py` | 中 | v0.19.0（设计已定，待实现） |

## 4. 块生态

来源：`docs/ADR-28-L3聚合块规范.md` §3.3、§5「已知欠账」+
`docs/v0.16.0-WBS.md` W30（粘合器对 L3 覆盖率实测）+ `docs/v0.14-v0.15-复盘.md` §四。

| 条目 | 来源 | 优先级 | 目标版本 |
|------|------|--------|----------|
| L4+ 层级 —— ADR-28 §3.3 已定**本轮只开到 L3**（`MAX_BLOCK_LEVEL = 3`）；要开 L4 必须**另立 ADR**，并拿出 ≥3 个 L3 块的实测链式覆盖率 + 一个"L3 表达不了、必须 L4"的真实场景 | `docs/ADR-28-L3聚合块规范.md` §3.3 | 设计边界 | 不做（除非另立 ADR） |
| ~~粘合器对 L3 自动链式覆盖率偏低~~ —— **v0.17.0 W42-W43 已改善**。W42 逐槽归因（`tools/ai-bridge/bench_glue_l3.py`）：20 槽里 A 类（同型不同义）14 个占失配 100%，B/C/D 各 0 —— 证伪「扩类型词表能解」（生年/今年 即使细分子类型仍同为「年」）。W43 按路线 2 落地 ADR-30 槽名字面匹配 + 同型槽不复用：场景甲（共享按槽名命名）20/20 = 100%，场景乙（无字面线索）硬塞 0 次 | `docs/ADR-30-槽绑定歧义消解.md`、`docs/v0.17.0-WBS.md` W42-W43 | — | v0.17.0 已完成 |
| ~~存量 stable L2 依赖 experimental L1 未追溯~~ —— **v0.17.0 W44 已清零**。`check_stability_propagation` 放开到全量强度（依赖方 L2+、被依赖方任意层级）；三处违规逐个裁决为「把 `税单`/`姓名拆分`/`地址剖解` 提为 stable」而非降 L2（依据：`稳定性` 承诺接口兼容而非解析准确率）。门禁实测 0 违规 | `docs/ADR-28-L3聚合块规范.md` §3.2/§5/§6 | — | v0.17.0 已完成 |
| ~~L3 样本量仍是 3 个~~ —— **v0.18.0 W50-W51 已扩到 7 个**（新增 `员工薪历` / `档案贺卡` / `贷户档案` / `贷款简报`；加上原有 `客户对账` / `工资册` / `报销单` 共 7 块，跨财务 + 中文 + 数据三域）。L3 粘合率评测基数从 20 槽提升到约 50 槽，统计意义改善 | `docs/v0.18.0-WBS.md` W50-W51 | — | v0.18.0 已完成 |
| L0/L1/L2 无层级判定 —— 本轮只机检 L3；存量 112 块在无判定规则的年代写就，追溯等于大规模元数据返工，留待后续 ADR | `docs/ADR-28-L3聚合块规范.md` §5 | 低 | 待定 |
| 跨命名空间同名块会被并成一个依赖图节点 —— 依赖图按叶名建，第三方命名空间出现同名 L3 时理论上可能报假环；等第三方 L3 真出现再上"命名空间 + 叶名"两级解析 | `docs/ADR-28-L3聚合块规范.md` §5 | 低 | 待定 |

## 5. Web

来源：`tools/web/README.md`（W31 可写化评估 + 端点说明）+
`docs/v0.14-v0.15-复盘.md` §5。W31 只做了"另存为"式的方案持久化（存到
`~/.jikuai/web-方案/<id>.json`）。

| 条目 | 来源 | 优先级 | 目标版本 |
|------|------|--------|----------|
| ~~方案"原地更新"语义~~ —— **v0.17.0 W46 已上线**。`PUT /api/方案/<id>` 覆盖式更新；乐观锁用 `sha256(存档字节)[:16]` 做版本标记（不用秒级时间戳——同秒两次更新会误判无冲突），`期望版本` 必填缺则 400，版本不符回 409，不做静默覆盖 | `tools/web/README.md`、`docs/v0.17.0-WBS.md` W46 | — | v0.17.0 已完成 |
| 多标签页实时同步 —— W31/W46 两轮评估结论一致：**不做**。Web UI 是 loopback-only 的本地工具，同一台机器开两标签编辑同一份方案的场景极罕见；W46 的 409 乐观锁已兜住真出现时的数据安全。真要做需重新评估状态管理方案 | `tools/web/README.md` | 低 | 不做（除非有真实诉求） |

## 6. 语言特性（语法参考 §13.2 未实现）

来源：`docs/语法参考.md` §13.2（W28 已把内建动词 `写入`/`读取` 移出未实现清单）。

| 条目 | 来源 | 优先级 | 目标版本 |
|------|------|--------|----------|
| 抽象类 / 接口 / 属性装饰器 / 多重继承 / 显式 super 调用 / 类变量 | `docs/语法参考.md` §13.2 | 低 | 待定 |
| 中缀运算符 —— **设计上不提供** | `docs/语法参考.md` §13.2 | 设计边界 | 不做 |
| 泛型 / 类型注解 / 类型检查（语言级） | `docs/语法参考.md` §13.2 | 低 | 待定 |

---

## 7. 文档债

来源：v0.16.0 W34 文档 lint 实测。

| 条目 | 来源 | 优先级 | 目标版本 |
|------|------|--------|----------|
| ADR 编号有 14 个（01/02/03/06/07/08/10/11/12/17/19/20/22/23）只在正文里被引用，没有独立的 `docs/ADR-NN-*.md` 文件——决议散落在 `CHANGELOG.md` / 路线图 / 其它 ADR 正文里。**不是死链**（无人以文件链接方式引用它们），但编号实践不一致，回溯成本高 | W34 文档 lint（`docs/`、`CHANGELOG.md`、各 README） | 低 | 待定 |

---

## 8. v0.16.0 本轮刻意不做的（逐条 + 理由）

来源：`docs/v0.16.0-WBS.md`（W32 取舍、W34 动作）+ `docs/v0.14-v0.15-复盘.md` §5。

- **LSP `rename` / `references`**（→ v0.17.0）：需要跨文件符号表 + 引用图，是独立
  大工程；本轮做增量补丁式的半吊子实现只会留债。见 §1。
- **LSP `codeAction`**（→ v0.17.0）：v0.15.0 已判定可选，W32 复审无新证据说明
  必要性；跨文件符号表的坑填完再谈。见 §1。
- **AOT 子集缺口**（成员访问 / 异常 / Lambda / 列表-字典-索引）：设计边界，不是
  bug。各有明确运行时依赖，本轮不扩子集。见 §2。
- **`sources.py` 注册表源**（~~中央仓库未部署~~ v0.19.0 W61 纠偏：本地注册表早已落地，
  此处所指是**HTTP 远程注册表**未部署）：HTTP 分发留到 v0.20.0 接入 token 鉴权 + 包签名之后。见 §3。
- **L4+ 块**：ADR-28 §3.3 已定不开；解释成本、粘合器推导爆炸、零需求证据三条
  理由，要开必须另立 ADR。见 §4。
- **Web 引入交互框架**：W31 评估结论——原生 JS 足以胜任编辑/保存/列历史/删历史，
  引框架会击穿 gzip 上限并打破"零依赖、无 CDN"品牌主张。见 §5。

---

## 相关文档

- v0.14→v0.15 复盘：[`v0.14-v0.15-复盘.md`](v0.14-v0.15-复盘.md)
- L3 聚合块规范：[`ADR-28-L3聚合块规范.md`](ADR-28-L3聚合块规范.md)
- 块包格式（块↔包桥接）：[`ADR-32-块包格式.md`](ADR-32-块包格式.md)
- 包管理：[`包管理.md`](包管理.md)
- 语法参考：[`语法参考.md`](语法参考.md)
- AOT 边界：[`AOT.md`](AOT.md)
- LSP 已知缺口：[`../lsp/README.md`](../lsp/README.md)
- Web 说明：[`../tools/web/README.md`](../tools/web/README.md)
- v0.16.0 WBS：[`v0.16.0-WBS.md`](v0.16.0-WBS.md)
