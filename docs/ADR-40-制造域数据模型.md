# ADR-40 · 制造域数据模型与两层架构（v0.26.0 W130-W131）

- 状态：Accepted
- 日期：2026-08-18
- 决策者：项目维护者
- 落地环节：v0.26.0 M27（W130 schema 冻结 / W131 领域注册 / W134-136 引擎层 / W140-142 口径层）
- 相关：ADR-15（块生态架构，领域白名单出处）、ADR-26（类型词表）、ADR-28（L3 聚合块规范）
- 缘起：长株潭 Agent 大赛命题赛道第三题「制造业运营态势 Chat BI Agent」

---

## 1. 背景

赛题要求基于官方模拟制造业数据集（8 张表）构建 Chat BI Agent：自然语言问数、
多表关联、图表生成、运营日报、异常解释、**溯源说明**。

现状核实（v0.25.0，块库 112）：

- `数据` 域 19 个块**全是单列标量统计**。无 group-by、无连接、无透视、无时间序列、
  无异常检测、无 CSV 解析。
- `数据/分组` 块的导出名是 `聚簇`，实际语义是「把列表按固定大小切成定长若干组」，
  **不是 group-by**。这正是 AGENTS.md 第四节所指的「兄弟能力缺位」——照着用会跑通、
  结果是错的。
- CSV 解析被项目自己写进 `tools/ai-bridge/评测集-无覆盖.json`，当作「明确无覆盖」负例。
- 全仓零图表能力。

所以这不是「挑几个现成块拼一下」，是新建一个领域。

## 2. 为什么这个赛题值得做

赛题的**必选功能**里有「溯源说明：回答中应说明使用了哪些表、字段或查询逻辑」；
**评审重点**六维里又单列「可解释性：是否说明数据来源、字段、查询逻辑和结论依据」；
**加分项**里还有「数据质量检查，如缺失值、异常值、**口径不一致提示**」。

这三条是极快的结构性产出而非外挂：生成的 `.jk` 源码**就是**查询逻辑，块元数据
**就是**字段与口径声明，`块.json` 的 `版本` 字段**就是**口径的版本。

**主张**：不让大模型算数，只让它选口径。LLM 只输出 `方案.json`（选哪个口径块、
按什么维度、什么时间窗口），一行执行代码都不写；所有算术在版本化、自报口径、
带单测的块里。

## 3. 决议：两层架构

### 3.1 引擎层（关系算子）

**表的运行时表示**：`列表<字典<字符串,任意>>`，即「行的列表，每行是列名→值的字典」。

类型标注写法（ADR-26 §3.2 递归嵌套）：

```json
{"类型": "列表", "元素类型": {"类型": "字典", "键类型": "字符串", "值类型": "任意"}}
```

算子清单：`选取`（过滤）、`投影`（取列）、`连接`（1:N 与 1:1）、`分组聚合`、
`排序`、`取前N`、`窗口`（按日期区间切片）。

**必须写清的代价**：`值类型: 任意` 在 `glue.py` 的 `type_feeds`
（`@LINE[237..238]`）走 `任意` 双向放行，等于**关掉类型检查**。也就是说
**类型图在引擎层不起作用**，引擎层的正确性完全由单测保证，不由粘合器保证。

这一点不许含糊表述。引擎层就是「用极快写的一个查询引擎」，它不享有极快
「确定性推链」的红利；写文档和白皮书时不得把它包装成推链成果。

### 3.2 口径层（指标块）

领域 `制造`，块的输入是引擎层产出的表 + 具体参数，输出**精确元组**。类型图在这一层
生效，`jk 块 选` 检索的也是这一层。

每个口径块的 `描述` 字段**必须自报口径**，至少覆盖：分母是什么、含不含哪些行、
用现成列还是重算、聚合方式。这不是文档风格要求，是 G22 的校验对象（见 §6）。

### 3.3 为什么分两层

| 备选 | 判决 | 理由 |
|------|------|------|
| 全部用具体元组（每张表一个固定形状） | 否 | 连接算子会组合爆炸；且隐藏题与现场追问覆盖不到，一个没预料的问法就现场翻车 |
| 语言级新增「表」一等类型 | 否 | 要动 `pkg/blocks.py` 的 `CONTAINER_TYPE_NAMES`、`glue.py` 的 `normalize_type`/`type_feeds`、G14、并修订 ADR-26。赛前动协议层风险大 |
| 全程 pandas opaque（`蟒:` 桥封 DataFrame） | 否 | 类型图完全失效、块不可推导，「确定性合成层」这个卖点大幅缩水 |
| **两层：引擎层通用 + 口径层精确** | **是** | 差异化留在口径层，通用性放在引擎层。代价（引擎层无类型保护）已在 §3.1 写明 |

语言级「表」类型留到 v0.29.0 之后另立 ADR，本轮**明确不做**。

## 4. Schema 冻结（2026-08-18 实测）

数据落点：`赛题/chatbi/数据集/`。时间范围 2026-01-01～2026-06-30。
**本节是引擎层与口径层的唯一 schema 真源**，改数据集必须先改本节。

### 4.1 维表

- **dim_model**（8 行）：`model_id` 主键, `model_name`, `product_series`,
  `vehicle_type`, `standard_cycle_minutes`, `standard_energy_kwh`, `launch_year`
- **dim_workshop_line**（8 行）：`line_id` 主键, `line_name`, `workshop`,
  `shift_type`, `designed_daily_capacity`, `main_model_series`
- **dim_customer**（60 行）：`customer_id` 主键, `customer_name`, `customer_type`,
  `region`, `priority_level`

### 4.2 事实表

- **fact_orders**（1850 行）：`order_id` 主键, `customer_id`, `model_id`,
  `order_date`, `planned_delivery_date`, `actual_delivery_date`, `order_quantity`,
  `delivered_quantity`, `order_status`, `delay_days`
- **fact_production_plan**（2896 行）：`plan_id` 主键, `production_date`, `line_id`,
  `model_id`, `shift`, `planned_quantity`
- **fact_production_actual**（2896 行）：`actual_id` 主键, `plan_id`,
  `production_date`, `line_id`, `model_id`, `shift`, `actual_quantity`,
  `working_hours`, `downtime_minutes`, `achievement_rate`
- **fact_quality_defects**（2992 行）：`defect_id` 主键, `defect_date`, `line_id`,
  `model_id`, `process`, `defect_type`, `defect_count`, `severity`, `rework_status`
- **fact_energy_usage**（2896 行）：`energy_id` 主键, `usage_date`, `workshop`,
  `line_id`, `model_id`, `shift`, `electricity_kwh`, `water_ton`, `gas_m3`,
  `energy_per_vehicle`

`fact_production_plan` 与 `fact_production_actual` 同为 2896 行，**1:1 关系实测成立**。

### 4.3 空值

全量扫过，**只有 `fact_orders` 两列有空值**：`actual_delivery_date`、`delay_days`
（未交付时为空）。其余七张表零空值。

引擎层 CSV 载入块的空值策略：空串载为极快的 `空`，**不填 0**。把「未交付」当 0 天
延期会直接污染延期统计——这是赛题加分项「缺失值提示」要抓的东西，不能自己先犯。

### 4.4 规模

最大单文件 `fact_quality_defects.csv` 203KB，全部远小于内建 `读取` 的 10 MiB 上限
（`docs/语法参考.md` §JK-E4003）。**结论：不需要 pandas 兜数据量**，`蟒:` 桥只在
CSV 解析这一步当 sidecar 用（Python 标准库 `csv`）。

### 4.5 外键完整性实测（2026-08-18 · W139）

`schema_relationships.csv` 共 14 条，其中 **11 条是真外键**（`1:N` / `1:1`），
另 3 条是 `业务关联`（复合键，写法形如 `line_id+model_id+production_date`），
**不是外键，不参与本节实测**——它们是 `邻期关联` / `连接` 的口径依据，见 §5.2。

**方向约定（读 CSV 时最容易搞反的一处）**：`source_*` 是「1」那一侧（维表/父表），
`target_*` 是「N」那一侧（事实表/子表），**外键列长在 `target` 上**。所以孤儿行
是事实表里的行，判据是「该行的外键值在维表对应列的取值集合里找不到」。

口径（与 `制造.质量体检` 块一致）：**外键列为 `空` 的行不算孤儿**（那是缺失值，
归 §4.3 管）；比对是**值相等且不做类型转换**。

| # | 子表.外键列 | → 父表.主键列 | 子表行数 | 孤儿行数 |
| --- | --- | --- | --- | --- |
| 1 | `fact_orders.customer_id` | `dim_customer.customer_id` | 1850 | **0** |
| 2 | `fact_orders.model_id` | `dim_model.model_id` | 1850 | **0** |
| 3 | `fact_production_plan.model_id` | `dim_model.model_id` | 2896 | **0** |
| 4 | `fact_production_plan.line_id` | `dim_workshop_line.line_id` | 2896 | **0** |
| 5 | `fact_production_actual.plan_id` | `fact_production_plan.plan_id` | 2896 | **0** |
| 6 | `fact_production_actual.model_id` | `dim_model.model_id` | 2896 | **0** |
| 7 | `fact_production_actual.line_id` | `dim_workshop_line.line_id` | 2896 | **0** |
| 8 | `fact_quality_defects.model_id` | `dim_model.model_id` | 2992 | **0** |
| 9 | `fact_quality_defects.line_id` | `dim_workshop_line.line_id` | 2992 | **0** |
| 10 | `fact_energy_usage.model_id` | `dim_model.model_id` | 2896 | **0** |
| 11 | `fact_energy_usage.line_id` | `dim_workshop_line.line_id` | 2896 | **0** |

**结论：11 条全部零孤儿，且 11 条的外键列零空值**（所以「空不算孤儿」这条口径在
本数据集上不影响任何数字，但仍是块的正式口径）。W139 DoD 那句「若不为 0 要写进
ADR-40 §4 而不是悄悄兜掉」这次没有需要写的例外。

顺带一并实测、同样零问题的两项：

- **主键唯一性**：§4.1/§4.2 点名的 8 个主键，重复数全为 **0**
  （口径：行数 − 不同主键组合数）。
- **空值**：与 §4.3 完全一致——只有 `fact_orders.actual_delivery_date` 与
  `delay_days` 各 **57** 个空值（合计 114 个单元格），其余七张表零空值。
  注意 `delay_days` 同一列里另有 **977** 行值为 `0`（按期交付），这 977 行**不是**
  空值：把它们混为一谈正是 §4.3 禁止的那种污染。

**复现方式**（本节不是手抄，是块跑出来的）：`制造.质量体检` 块（导出 `体检`），
回归钉在 `tests/test_v0_26_0_w139_质量体检.py`——它从 `schema_relationships.csv`
**现读**这 11 条关系而不在测试里手抄，数据集换了会当场红。块自测另见
`src/jikuai/stdlib/blocks/制造/质量体检/测试.jk`。

## 5. 口径分歧点（本 ADR 最要紧的一节）

数据集里有**两个**现成比率列（`fact_production_actual.achievement_rate`、
`fact_energy_usage.energy_per_vehicle`），直接汇总会算出错数；再加一个**无现成列、
必须跨表算分子分母**的缺陷率，共三处口径分歧。每一处都必须由口径块显式声明，
不许让粘合器或 LLM 猜。

> **2026-08-18 修正（W138）**：本节初稿写「三个现成比率列」，实测只有两个。
> `fact_quality_defects` 表头是 `defect_id, defect_date, line_id, model_id, process,
> defect_type, defect_count, severity, rework_status`，**没有 `defect_rate`**，
> 全数据集也没有第三个 `_rate`/`_per_` 形态的列——与 §5.3 自己写的「无现成列」一致，
> 是初稿导语与 §5.3 打架。**分歧点仍是三处**（达成率、单车电耗、缺陷率），
> G22 第 3 条不受影响。

### 5.1 平均达成率

`fact_production_actual.achievement_rate` 是行级现成列（= `actual_quantity` /
`planned_quantity`）。「L003 白班/夜班平均达成率」（Q_PUB_005）有两种口径：

- **行级算术平均**：`mean(achievement_rate)` —— 每个班次记录等权
- **加权口径**：`sum(actual_quantity) / sum(planned_quantity)` —— 按产量加权

**两个数不相等**。决议：默认口径为**加权**（产量加权更符合「产线效率」的业务含义），
另出一个行级平均的块，两块的 `描述` 各自写明。**禁止只做一个块然后在描述里含糊**。

**落地记录（W140）**：加权块目录名取 **`制造/达成率权重`**（导出 `达权`），不是本节
初稿写的 `达成率加权`——`加` 是内建动词（VERB），`达成率加权` 作点分路径段被切成
`达成率`+`加`+`权` 三个 token，`从 blocks.制造.达成率加权 导入` 直接 ParseError。
均值块 `制造/达成率均值`（导出 `达均`）。L003 实测四个数：白班加权 **0.969950** /
白班均值 **0.970209** / 夜班加权 **0.849740** / 夜班均值 **0.850028**——两口径确实
不等值。本数据集上差只有万分之几（L003 白夜班各 181 行、班内产量分布均匀），
但结论方向不受口径影响（夜班低约 0.12）；口径分歧的真实量级在块自测的最小例里
（5/10 与 100/100 两行）达到 0.2 以上。

### 5.2 单车电耗

`fact_energy_usage.energy_per_vehicle` 是现成列，但 `fact_energy_usage` 表里
**没有 `actual_quantity`**——该列是数据生成时算好的，其分母来自 `fact_production_actual`。
两种口径：

- 直接用现成列
- 按 `line_id + model_id + 日期 + shift` 关联 `fact_production_actual` 重算

决议：默认用现成列（数据字典即如此定义），**另出重算块供交叉校验**。
Q_HID_003 问「能耗异常是否与产量增加完全一致」，正需要同时看总电耗与单车电耗
两条线——两个口径都要在。

**2026-08-18 实测（W138）：这两个口径在本数据集上数值等值。** 逐行核对
2896/2896 行，`energy_per_vehicle` 恒等于 `electricity_kwh` / 同键
`fact_production_actual.actual_quantity`（按 `usage_date + line_id + model_id + shift`
四键关联，零漏配，容差 0.005）。`achievement_rate` 同样逐行等于
`actual_quantity / planned_quantity`（容差 1e-4），值域 **0.7619 ~ 1.0385**——
是 0-1 小数、**可以大于 1**、不是百分数。

所以重算块的价值**不是**在本数据集上算出不同的数，而是：(a) 证明现成列口径为何；
(b) 换一份真实工厂数据时这两个数会分叉，届时块已经在。W141 的 DoD「若完全相同要查清
并记档」在此结账——原因是数据生成时口径一致，不是重算块写错。

### 5.3 缺陷率

无现成列。口径 = `sum(defect_count)` / `sum(actual_quantity)`，两个分子分母来自
**不同表**，必须先按 `model_id`（或 `+line_id`）+ 时间窗口对齐再相除。
**先算比率再平均是错的**，块描述要写明是先汇总后相除。

## 6. 门禁 G22

新增 `scripts/check_manufacturing_contract.py`，串进 `check_stdlib_contract.py`。
断言三条：

1. **预置异常必须恰好命中 5 条**，不多不少。数据集 README 与 `业务关系说明.md` 给了
   全部答案：M003@L002 2026-06 焊装-焊点虚焊；L002 2026-06-10～06-24 单车电耗升高；
   C005 的 M003 订单 2026-06 集中延期；L003 夜班达成率持续低于白班；
   L005 2026-04-08～04-18 停线时长偏高。
2. **`制造` 域每个块的 `描述` 必须含口径声明关键词**（分母 / 汇总 / 加权 / 现成列 /
   重算 / 窗口 至少命中一项）。这是把 §3.2 的要求变成可执行检查，否则它只是一句愿望。
3. **§5 三处分歧点各自都有块，且缺一个就红**——但三处的判法**不一样**，
   这是 W144 落地时改的（初稿一律写「各有两个块」，做下去发现第三处不该那样判）：
   - **达成率**、**单车电耗**：两侧口径**都对**，所以按「双块」判——两侧块都在
     （`达成率权重`+`达成率均值`、`单车电耗现成`+`单车电耗重算`）、各自描述含口径自述、
     且**互相点名对方**。
   - **缺陷率**：另一侧（先算行级比率再平均）**本身是错的**——两表粒度不同（缺陷表
     2992 行带工序/缺陷类型/严重度三维，产量表 2896 行），行级比率对不上；实测
     2026-06 先汇总后相除 **0.050550** vs 行级比率平均 **0.032218**，差 **57%**。
     为一个已知错误的口径造块，等于把错口径升格成「可选口径」，正是 AGENTS.md 第四节
     要防的「兄弟能力缺位」。所以按「**单块 + 描述显式否决另一侧 + 分子侧姊妹块在位**」
     判：`缺陷率` 在位、描述含「先汇总后相除」（采纳口径）+「行级比率」（点名另一侧）
     + 一句明确否决，且 `缺陷汇总` 在位并被点名。反例测试证明抹掉否决句就红。

   顺带说明为什么 §5 的标题仍叫「三处分歧点」而不是「三处双块」：分歧是业务事实，
   有几处就是几处；**怎么落地**（双块 / 单块+否决）取决于另一侧是不是也对。

按 v0.18.0 W55 起的规矩：G22 上线必用 `tmp_path` 造反例逐一证明抓得到，只测正例
等于没有门禁。

> **编号说明**：`scripts/check_dist_metadata.py` 自 v0.25.0 W127 起已占用 **G21**
> （三包发行元数据门禁）。制造域口径契约因此排到 **G22**，避免与之冲突。

## 7. 影响

- `pkg/blocks.py` 的 `ALLOWED_DOMAINS` 从六域扩到七域（加 `制造`）——牵动 ADR-15。
- AGENTS.md 第一节「块库覆盖的域只有六个」文案要改；第四节若涉及新的口径陷阱表述，
  按 AGENTS.md §六规矩要重跑 `agents-md-ab/` 三臂对照实验。
- 新增块必过 G11（重跑 `generate_block_index.py`）与 G12（重跑 embeddings，
  本机离线设 `HF_HUB_OFFLINE=1` `TRANSFORMERS_OFFLINE=1`）。
- 导出名要过 G13 全局唯一——`汇总` 已被 `数据/求和` 占用，制造域命名需先 grep 查重。
- `赛题/` 是新的仓库顶层目录。`MANIFEST.in` 只 `recursive-include src/jikuai/stdlib`，
  故**不会进 wheel 与 sdist**，G20 不受影响。**2026-08-18 已实证**（W130；此前这条
  只是照 `MANIFEST.in` 推理，没跑过命令）：

  ```bash
  python -m build --outdir "$env:TEMP\w130-证据-dist" .   # build 1.5.0
  # 再用 zipfile.namelist() / tarfile.getnames() 在两个产物里 grep 「赛题」
  python scripts/check_wheel_contents.py "$env:TEMP\w130-证据-dist\jikuai-0.25.0-py3-none-any.whl"
  ```

  ```
  wheel 条目 431  含 赛题 = 0
  sdist 条目 667  含 赛题 = 0
  wheel 顶层 = ['jikuai', 'jikuai-0.25.0.dist-info']
  sdist 顶层 = ['<根>', 'MANIFEST.in', 'PKG-INFO', 'README.md', 'pyproject.toml',
                'setup.cfg', 'src', 'tests']
  G20 wheel 内容门禁：通过（431 个条目）
  ```

  推理与实测一致：`MANIFEST.in` 与 `.gitignore` **都不用动**。

  **顺带实测出的一条附带事实**：sdist 收了 `tests/`（100 个条目），但没收 `docs/`（0）
  也没收 `scripts/`（0）。所以引用 `赛题/chatbi/数据集/` 的测试文件会随 sdist 发行、
  而它要读的数据集不会——这类测试必须自带「数据集目录不存在就 skip」的守卫，否则从
  sdist 装出来的环境里会假红。`tests/test_v0_26_0_w130_chatbi_schema.py` 已按此写。


## 8. 遗留

- ~~**`数据/均值` 块的导出名是 `中位`**（`数据/中位数` 导出 `中值`）。在 BI 场景下
  均值/中位数混淆是致命的，W132 先改名清账。~~
  **已清（W132）**：`数据/均值` 导出名 `中位` → **`均数`**。选名依据：`tokenize('均数。')`
  单 IDENT（ADR-15 §3.7 词法原子），且不与既有 112 个块的任何导出名撞车（G13）。
  `数据/中位数`（导出 `中值`）一字未动。索引（G11）与 embeddings（G12）由本轮
  三个并行任务收口时统一重跑一次。
- `question_hidden.csv` 出现在参赛者包里（赛题说明写「建议由赛事方保留」）。
  本项目将其**定为留出集**：只当裁判，不看其 miss 明细调参——沿用 AGENTS.md §六
  对 `评测集-留出.json` 的同一条纪律。
