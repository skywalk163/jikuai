# ADR-25 · M3 语义选块的 A+B 混合双模式架构（v0.13.0 W6-W12）

- 状态：Accepted
- 日期：2026-08-09
- 决策者：用户裁决（2026-08-09 · "B+A" 收敛为混合双模式）
- 落地环节：v0.13.0 M3（W6-W12）语义选块
- 相关：ADR-15（块生态架构 §3.4 AI 桥接协议）、路线图-v0.13.0 §四 风险表

---

## 1. 背景

v0.12.0 的 AI 桥接用关键词匹配做块检索，20 条口语化需求 top-3 命中率约 40%。
v0.13.0 M3 目标是提升到 ≥80%，主流做法是 embedding + 余弦相似度。

问题：主流中文 embedding 模型（`text2vec-base-chinese` 级别）依赖 torch 或
onnxruntime，几百 MB 级别的库，与极快「零运行时依赖」核心卖点直接冲突。
路线图 §四风险表把这条列为**高严重度**。

## 2. 目标

- **不破坏零运行时依赖**：`src/jikuai/` 主发布包不引入 torch/onnxruntime
- **命中率达到 ≥80%**：M3 §五目标
- **离线可用**：无网络时不退化到不可用
- **首次 checkout / dev 环境无索引**：不崩，降级到可用状态
- **贡献者不被迫装几百 MB 才能改代码**

## 3. 决议：A+B 混合双模式

三层结构，责任严格分离：

| 层 | 位置 | 依赖 | 面向 |
|----|------|------|------|
| **运行时检索** | `src/jikuai/ai/retrieval.py`（新建） | 纯 Python 标准库 | 所有用户 |
| **向量索引生成** | `tools/ai-bridge/` | torch/onnx（可选） | 维护者/CI 打标节点 |
| **外部 API 扩展** | `tools/ai-bridge/cloud/` | 云端 embedding SDK（可选） | 需要私有索引的高级用户 |

### 3.1 运行时检索层（src，零依赖）

- 读取包内随发布的 `stdlib/blocks/向量索引.bin` —— 二进制格式（int16
  量化后的 float 向量），纯 Python 用 `struct` / `array.array` 读取
- 用纯 Python 做余弦相似度计算（NumPy 都不引入——极快没依赖 NumPy）
- **查询向量由调用方提供**：运行时不做模型推理（那会引入 torch，破坏零依赖）。
  `retrieve(query, top, query_vector=None)` 接受可选的查询 embedding；
  有向量走神经路径，无向量自动降级启发式。调用方责任链：
  1. `tools/ai-bridge/` CLI 工具本地推理后传入
  2. 云端 API（§3.4）推理后传入
  3. 未来 LSP 可预计算常见查询的向量缓存表（W8-W9 扩展点）
- **分层兜底**：
  1. 有 `向量索引.bin` + 有查询向量 → 神经检索路径，命中率 ≥80%
  2. 无索引 or 无查询向量 or 索引不兼容 → 自动 fallback 到 TF-IDF + 同义词表 + 领域先验，
     命中率 60-70% 保底可用（实测 52 块评测集 Recall@3=90%）
- **诊断透明**：CLI/桥接接口输出结果时标注检索路径（`[神经]` / `[启发式]`），
  帮 dev 判断为什么某个查询没命中

### 3.2 索引生成层（tools/ai-bridge，可选依赖）

- 独立 `requirements-ai.txt`，仅在需要重新生成索引时安装
- 单一入口脚本 `tools/ai-bridge/generate_embeddings.py`：
  - 读取 `stdlib/blocks/索引.json` 拿全部块
  - 对每个块的 `名称，领域，描述` 生成 embedding
  - 量化为 int16 落盘为 `stdlib/blocks/向量索引.bin`
  - 生成 sidecar `向量索引.元信息.json`（模型名/版本/维度/量化参数/生成时间）
- 索引文件进 git（不用 LFS —— 102 个块 × 768 维 × 2 字节 ≈ 154 KB，远低于阈值）

> **嵌入文本组成的修订（v0.13.0 P2 实测）。** 本节原定 `描述 + 示例`，留出集
> 横评（另写 25 条全新口语查询，不看主评测集调参）证否：`示例` 里的
> `从 blocks.X.Y 导入` 是每块一样的样板，在嵌入空间里把全部 102 块互相拉近，
> Recall@3 掉 16 pp；拟古导出名（缴税/圆分/聚簇）不在中文语义模型训练分布内，
> 同样是噪声，掉 4 pp。改成 `名称，领域，描述` 后留出集 Recall@3 由 72% 升到
> 84%，首次超过 TF-IDF 启发式的 80%。块名是最强信号，原方案恰好漏掉它、又
> 收进了两个噪声源。实测模型 `shibing624/text2vec-base-chinese` 维度为 768
> （非本文早前假设的 384），索引因此约 154 KB。

### 3.3 CI 双模式

- **常规 job**（`test-linux`）：不装 torch，跑全量测试 + G10/G11 + 新加的
  「TF-IDF fallback 命中率不劣化」门禁。**默认模式**，PR/push 都走这条
- **索引重生成 job**：`workflow_dispatch` 触发 or 打标签 `regen-index` 触发。
  装 `requirements-ai.txt` → 跑 `generate_embeddings.py` → 若索引变化则
  提交 PR。**只在真正需要更新索引时手动触发**，日常 CI 不跑
- **G12 新门禁**（v0.13.0 已上线）：`向量索引.元信息.json` 的 `块数` 与 `块哈希`
  必须与 `索引.json` 同源。实现在 `jikuai.pkg.blocks.check_vector_index()`，由
  `scripts/check_stdlib_contract.py` 串在 G10/G11 之后。纯标准库，常规 CI 就能跑，
  不需要 torch。**向量索引不存在时判为「缺失」并放行**——ADR-25 §3.1 允许无索引
  降级启发式，不该迫使每个贡献者装 torch 重生成索引；只有「索引在但与块列表不符」
  才失败，兜住「改了块忘了重跑 generate_embeddings.py」这条。
  哈希算法由 `blocks.blocks_content_hash()` 单点提供，生成端与校验端共用，
  避免两处实现漂移导致假报警。

### 3.4 外部 API 扩展（可选）

- `tools/ai-bridge/cloud/`：给需要私有索引的团队一个入口
- 通过环境变量配置 API key（`JIKUAI_EMBEDDING_API_URL` /
  `JIKUAI_EMBEDDING_API_KEY`）
- 输出格式与 `generate_embeddings.py` 完全一致（用户可以选择用本地模型还是云端）
- **不进主分支的 CI**——涉及外部依赖 + 密钥，责任在使用方

## 4. 数据格式契约

`向量索引.bin` 格式（W6 详设，此处只锁头部）：

```
魔数(4B) = 'JKBV'
版本(2B) = 1
维度(2B) = 384
块数(4B)
量化参数：min(4B float) max(4B float)
每块：块名长度(2B) + 块名(UTF-8) + 量化向量(维度 × int16)
```

`元信息.json`：

```json
{
  "格式版本": 1,
  "模型": "shibing624/text2vec-base-chinese",
  "模型版本": "1.0.0",
  "维度": 384,
  "量化": "int16-symmetric",
  "块数": 100,
  "块哈希": "sha256:...",
  "生成时间": "2026-09-10T..."
}
```

## 5. 里程碑映射

- **W6-W7**：`src/jikuai/ai/retrieval.py` 骨架 + TF-IDF fallback 独立可用
- **W7-W8**：`tools/ai-bridge/generate_embeddings.py` 骨架 + 首次生成索引 提交
- **W8-W9**：语义检索接入 CLI/桥接，20 条口语化需求基准测试
- **W10-W11**：类型对齐粘合器（独立于本 ADR）
- **W11**：G12 门禁上线（索引一致性）
- **W12**：端到端 10 demo + 压缩比报告 + 发布

## 6. 已知限制

- **索引可能过期**：块内容改了但没重新跑生成脚本，此时 fallback 会走神经索引
  的旧向量（相似度略偏），G12 门禁通过 `块哈希` 兜住这条
- **量化精度损失**：int16 对称量化 vs float32 会损失 <1% 检索质量，为省 6x
  存储值得
- **首次冷启**：无索引场景走 TF-IDF，命中率 60-70%，达不到 80% 但可用
- **不覆盖多语言**：`text2vec-base-chinese` 优化中文，用户块名/描述用英文时
  质量下降。可用性不受影响，命中率打折

## 7. 与 ADR-15 §3.4 的关系

ADR-15 §3.4 定义"AI 桥接协议"接一次读源为 `索引.json`。本 ADR 在其上叠加
`向量索引.bin`：桥接 API 优先加载向量索引，失败降级到关键词（当前 v0.12.0
行为）。ADR-15 §3.4 契约不破坏。

## 8. 回退

`JIKUAI_AI_RETRIEVAL=heuristic` —— 强制走 TF-IDF 路径，忽略 `向量索引.bin`。
供 debug 神经索引质量问题时对照。
