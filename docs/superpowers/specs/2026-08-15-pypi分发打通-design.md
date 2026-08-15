# 极快 JiKuai · PyPI 分发打通设计（v0.24.0）

> 状态：设计已定稿（2026-08-15）。承接 brainstorming 结论，落 BACKLOG §10。
> 主题：**让 `pip install jikuai` 装出来的东西真的能用**——把 stdlib 收进包、三包同步发 PyPI、处置线上坏版。

---

## 一、背景与「初心」偏离

README 第一句：「一门为中国开发者量身定制的中文编程语言」。而当前真按官方姿势装它的人，得到的是坏的。

**实测证据（2026-08-15）**：

- PyPI `jikuai` 名字已被本项目作者（skywalk163）占用，最新发布版停在 **0.4.1**（2021 年那批的延续），仓库当前版本已是 0.23.0。
- 0.4.1 的 wheel 里 **19 个文件、0 个 stdlib**。干净 venv 装上后跑 `导入 数学。` → `运行错误：找不到模块：数学`，退出码 1。
- 结论：BACKLOG §10 不是「将来会触发的待办」，是**线上已存在的 regression**——任何人现在 `pip install jikuai` 拿到的是连标准库都导不进的半成品，且冠着本项目的名字。

**根因（BACKLOG §10 已查实）**：整个 `stdlib/`（1.07 MB / 381 文件，含 `分词词典.txt` 534 KB、`向量索引.bin` 169 KB、224 个块 `.jk`、112 个块 `.json`）既不在 wheel 也不在 sdist。`pyproject.toml` 只有 `packages.find where=["src"]`，无 `package-data`。运行时全靠 7 处 `__file__` 相对回溯，只在 `pip install -e .` 下成立。ADR-16 §3.4 的 data-files 原方案**本身不成立**（`data_files` 装到 `sys.prefix`，与代码从 `site-packages/jikuai/` 上溯 2–3 级对不上），这是它三个版本没落地的真实原因。

---

## 二、目标与边界

**一句话目标**：让 `pip install jikuai` 装出来的东西，在**干净 venv、非 editable wheel** 下，`jk hello.jk` / `导入 分词` 后真分词 / `jk 块 选` 三条都真的跑通；且 lsp / dap 同步可装、门面文档正确、线上坏版被处置。

### 范围内

1. stdlib 全目录随包发行（物理搬进 `src/jikuai/stdlib/`，方案 A）。
2. 7 处 `__file__` 回溯收敛成 `importlib.resources` 单一定位入口 + `JIKUAI_STDLIB` 环境变量覆盖。
3. 构建后门禁 G20：断言 wheel 内含 `分词词典.txt`、`向量索引.bin`、≥112 个块 `.json`。
4. 三包（jikuai / jikuai-lsp / jikuai-dap）版号对齐 0.24.0，lsp/dap 接单一真源，依赖钉 `jikuai>=0.24.0`。
5. TestPyPI 全链路预演 → 正式 PyPI 发布。
6. README 安装段去掉 `cd G:\jikuai`；PyPI 长描述核对。
7. yank PyPI 上 0.4.1 及更早所有版本。

### 范围外（明确不做，各有硬约束）

- **`tools/` 不入包**。`tools/web/server.py` 无鉴权且 `/api/跑` 在本机进程内执行提交的代码（经 `蟒:` 桥等于任意代码执行）——入包等于给每个 pip 用户默认装 RCE 服务端，安全底线。`tools/ai-bridge` sidecar 故意隔离（torch / sentence-transformers 在子进程另一侧，主包依赖面积零变化），且神经收益在 v0.16 链式任务被 TF-IDF 反超 38pp，未证实。`embed_client.py:57` 已按「pip 场景 tools/ 不存在 → 降级到启发式」设计，不动。
- **`scripts/` 的 `__file__` 回溯不改**。只用于把 `src/` 塞进 `sys.path` 或定位 `docs/`，只在源码仓跑，不随 wheel。
- **检索 / L3 / 语言特性一律不碰**。首发 PyPI 的这版必须是「除打包外什么都没动」，这样装完出问题归因一定落在打包上，不与功能 bug 混淆。发布工程纪律，同构于本项目「守卫绿≠守卫在守」教训。

---

## 三、stdlib 进包方案（方案 A，已否决 B/C）

**决策：方案 A · 物理搬进 `src/jikuai/stdlib/`** + `[tool.setuptools.package-data]`。

否决理由：

- **方案 B（`package-dir` 映射，物理不搬）** 引入「开发态一个位置、安装态另一个位置」的永久双态——正是让门禁只守住一半的经典成因（本机 editable 全绿、wheel 里坏没人知道，跟今天 0.4.1 事故同构）。且依赖未实测的 setuptools 混合布局行为，把未验证构建魔法放在首发关键路径上，风险不对称。
- **方案 C（拆独立包 `jikuai-stdlib`）** 多一个包 + 版本协同，单人维护是纯负担。

A 的代价（机械但量大）：约 50 个 `.py` 文件提到 `stdlib`（`test_v0_5_0_stdlib_contract.py` 24 处、`evaluator.py` 21 处、`stdlib_contract.py` 20 处），docs 更多；推翻 ADR-16 §3.4「固定仓库根不可移动」+ D-05，须另立 ADR。

### A 成立的两条前提（不是可选项）

1. **单一定位入口**：新增 `src/jikuai/resources.py`，7 处 `__file__` 回溯（`module_loader.py:147`、`stdlib_contract.py:21`、`evaluator.py:1644`、`pkg/blocks.py:307`、`pkg/blocks_cli.py:186`、`ai/retrieval.py:133`、`ai/retrieval.py:675`）全部改调它，并支持 `JIKUAI_STDLIB` 覆盖。**留任何一处旧回溯，A 就退化成双态。**
2. **G20 构建后门禁**：build wheel → 解包断言含 `分词词典.txt`、`向量索引.bin`、≥112 个块 `.json`。进 G10–G19 体系（这条守的正是今天已发生的事故；覆盖率那种趋势指标不进门禁，但这条是契约）。

### 新 ADR 裁决

新立 ADR（拟 ADR-39）推翻 ADR-16 §3.4：stdlib 是**包内资源**，定位唯一入口是 `importlib.resources`（或等价的包内路径解析），不再是「仓库根不可移动 + data-files」。

---

## 四、三包版本 / 依赖 / 旧版处置

**现状（已查实）**：`lsp/pyproject.toml` 写死 `version = "0.15.0"`、`dap/` 写死 `0.7.0`（W25 版本单一真源只覆盖主包）；两者 `dependencies = ["jikuai"]` **无下界**——照发可能给用户解析到坏掉的 0.4.1。`jikuai-lsp` / `jikuai-dap` 名字在 PyPI 均未占用（404）。

**四条决策（一个整体，缺一不可）**：

1. **三包版本对齐 0.24.0**，lsp/dap 也接上版本单一真源（不再手写字面量）。
2. **依赖钉下界** `jikuai>=0.24.0`——防 0.4.1 回流的关键，与 yank 双保险。
3. **首发版号 0.24.0**：延续仓库真实版本线（不倒退），远大于 0.4.1（解析器天然优先）。不跳 1.0——1.0 是语义承诺，与「修打包」性质不符。
4. **yank 0.4.1 及更早所有版本**：yank 不删包（已 pin 者不受影响），只让新装不再解析到；纯收益、可逆（能 unyank）。

四条互补：只发新版不 yank，旧坏版仍可能被依赖树选中；只 yank 不钉下界，sidecar 仍可能拽回旧版。

**不可逆边界**：PyPI 文件名一旦用过永久不可复用——`0.24.0` 一旦上传，删了也不能再传同名文件。故必须 TestPyPI 预演 + 干净 venv 三条命令绿之后才碰正式 PyPI。

---

## 五、实施阶段

- **M30 打包重构**：`stdlib` 搬进 `src/jikuai/stdlib/`；新增 `resources.py` 单一入口 + `JIKUAI_STDLIB`；7 处回溯改调它；`package-data` 声明；新增 G20；立 ADR-39。
- **M31 三包发布链路**：三包版号对齐 0.24.0 + lsp/dap 接单一真源 + 依赖钉 `jikuai>=0.24.0`；TestPyPI 全链路预演。
- **M32 门面与旧版**：README 安装段去 `cd G:\jikuai`；PyPI 长描述核对；yank 0.4.1 及更早；正式发布 + 全新 venv 从正式 PyPI 复验。

---

## 六、验收硬闸（顺序不可颠倒，前一道不绿不进下一道）

1. 全量回归绿（用例数不低于 v0.23.0 发布时基线）+ G10–G19 全绿 + 新增 G20 绿。
2. **干净 venv 装本地构建的主包非 editable wheel**，四条都过：`jk hello.jk`；`导入 分词` 后真分词；`jk 块 选 "月薪两万个税多少" --json`；`jk 包 列表`。
3. 干净 venv 装本地构建的 `jikuai-lsp` wheel，`python -m jikuai_lsp` 能应答 `initialize`。
4. **从 TestPyPI 装三个包**，重跑 2 与 3。
5. 才碰正式 PyPI；发完再开全新 venv 从正式 PyPI 复验一遍。

---

## 七、风险（按翻车概率排序）

1. **wheel（zip）里的中文文件名编码** —— 最可能出事。stdlib 从上到下全中文名（`分词词典.txt`、`blocks/财务/个税/*.jk`），而 0.4.1 wheel 里一个非 ASCII 文件都没有，**这条风险从未被暴露过**。zip 非 ASCII 名依赖 UTF-8 flag，不同 pip / 解包路径行为未实测。**M30 第一件事就是拿 10 个中文文件试跑 build+install，成了再动那 381 个**——不是留到最后验证。
2. **`stdlib/__pycache__` 与 `.gitkeep`** —— 目录里存在这两样。`__pycache__` 必须排除出 wheel；`.gitkeep` 意味有空目录，而 zip 不保留空目录，须确认无代码依赖某空目录存在。
3. **漏改路径** —— A 的主要风险。靠回归 + G20 + 干净 venv 三层网兜，其中只有第三层能抓「本机 editable 恰好还能回溯到旧位置」的假绿。
4. **`向量索引.bin` 跨平台**（待核实）—— 须确认它是平台无关的定长浮点数组。
5. **wheel 体积** 61 KB → 约 1.1 MB+。非问题，CHANGELOG 说明即可。

---

## 八、相关引用

- BACKLOG §10（唯一真源）：`docs/BACKLOG.md:209-271`——7 处定位点、修法与验收全在此。
- ADR-16 §3.4（将被 ADR-39 推翻）：`docs/ADR-16-标准库契约.md`。
- v0.23.0 挂账段：`docs/路线图-v0.23.md:161-170`。
- sidecar 隔离约束：`src/jikuai/ai/embed_client.py:10, 50-59`。
- 主包版本单一真源：`pyproject.toml:28-29`（`jikuai._version.__version__`）。
