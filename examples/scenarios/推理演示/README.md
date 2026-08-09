# 场景：Reasonix 推理引擎演示

## 用途

演示极快的**模块化面向对象 + Python 互操作能力**：
用 Chain-of-Thought 4 阶段推理框架对固定问题进行结构化分析。

覆盖语言特性：
- 类 / 构造 / 方法 / 自身；ADR-22 词法作用域下的跨模块方法调用
- 多模块导入/导出（`从 X 导入 Y`）
- 字典字面量 `{"键": 值}` 及 `.键` / `["键"]` 两种访问方式
- `蟒:` 桥 · ADR-23 同目录 sidecar：`导入 蟒:智言。` 加载同目录的 `智言.py`
- 遍历、当循环、条件判断、`如果 X 那么: … 否则: …`
- 内建动词：拼接 / 分割 / 长度 / 首个 / 追加 / 转字符串 / 去空白

## 输入

问题是**固定字面量**，内嵌在 `main.jk` 中，不读取系统时间：
- 问题 1：「从 1 加到 100 的和是多少？」（数学题）
- 问题 2：「为什么夜晚天空整体是黑的？」（常识题）

推理内容有两种来源：
- **AI 模式**：`.env` 中配置 `REASONIX_API_KEY` 后，`智言.py` 走 OpenAI 兼容
  Chat Completions（支持 DeepSeek/OpenAI/通义千问等）。每阶段用 `提示词` 模块
  生成的分阶段 prompt 询问模型，回复替换该阶段的模拟内容。
- **模拟模式**：`REASONIX_OFFLINE=1` 或未配 `.env` → 用 `提示词` 模块预设的
  固定内容，输出稳定可作快照。

## 预期输出

见同目录 `expected.txt`（**模拟模式**下的稳定快照）。要点：
- 2 个问题各经 4 阶段（理解问题→信息提取→逻辑推理→验证答案）
- 每阶段有 `┌ ... ┐ / │ ... / └ ... ┘` 框显
- 最终答案综合 4 阶段首行
- 历史记录汇总 2 条

AI 模式下每次输出会不同，因此 `expected.txt` 只覆盖离线模式。
场景快照测试 (`tests/test_v0_7_0_scenarios.py`) 用 `REASONIX_OFFLINE=1` 强制离线，
保证 CI 里不联网、不消耗 token。

## 前置依赖

- Python 3.10+，仓库根目录下运行
- 无外部 pip 依赖（`智言.py` 只用标准库 `urllib`）
- 极快 v0.7 的字典字面量语法、`去空白` 动词、ADR-22 词法作用域、ADR-23 同目录 sidecar

### 运行

模拟模式（默认，无网络）：
```bash
REASONIX_OFFLINE=1 PYTHONPATH=src python -m jikuai examples/scenarios/推理演示/main.jk
```

AI 模式（复制 `.env.example` 为 `.env`，填 `REASONIX_API_KEY`）：
```bash
PYTHONPATH=src python -m jikuai examples/scenarios/推理演示/main.jk
```

支持的 `.env` 键：`REASONIX_API_KEY` / `REASONIX_API_BASE_URL` / `REASONIX_MODEL` /
`REASONIX_MAX_TOKENS` / `REASONIX_TEMPERATURE`。`.env` 已在仓库根 `.gitignore` 中，
密钥不会入库。
