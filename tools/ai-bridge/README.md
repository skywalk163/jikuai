# 极快 AI 桥接（v0）

## 定位

**不替代 AI，而是重新分工。**

- **传统流程**：用户描述需求 → AI 从零生成几百行代码 → 用户 review、调试、修参数、再运行。
- **本桥接（v0）**：用户描述需求 → 本地关键词匹配从 `stdlib/blocks/索引.json` 里挑几个块 → 生成极短粘合代码 → 直接跑。

在这个流程里，AI（或本地匹配器）的**唯一职责**是：**从索引里选块 + 提供参数**。

不再是「代码生成器」，而是「块选择器」。极快语言承担粘合、类型对齐、错误处理。

## 现实检查（诚实评估）

v0 只对**已知模式**有效：

- 需求能被拆成能命中块「名称 / 描述 / 领域」的字符
- 待办能用 ≤ 5 个块拼装完成
- 参数是常量或前一步结果

**不适用**：

- 需求语义抽象（例如「帮我做个报表工具」——需要澄清）
- 待办需要新算法（本方案只能重用现有 52 个块）
- 复杂控制流（v0 的 glue 只生成顺序调用）

v0 是**协议原型**。语义匹配、真实大模型接入、复杂粘合逻辑都留 v0.13.0+。

## 文件清单

| 文件 | 作用 |
|------|------|
| `协议.md` | 《块选择协议 v0》的 JSON 结构定义与示例 |
| `select.py` | 本地关键词匹配选块器（可作库或 CLI） |
| `glue.py` | 把选块方案合成为可运行极快源码（可作库或 CLI） |
| `demos/demo1_数值统计.py` | 端到端 demo：求和 + 均值 / 批量统计 |
| `demos/demo2_文本清洗.py` | 端到端 demo：切分 → 去重 → 升序 → 合成 / 文本清洗 |
| `demos/demo3_中文报表.py` | 端到端 demo：金额雅写 / 金额报表 |
| `demos/_公用.py` | 三个 demo 的公共脚手架（模块载入、执行、压缩比、报告） |
| `demos/README.md` | 三个 demo 的输出汇总与压缩比（含估算方法说明） |
| `test_bridge.py` | 单元与端到端测试（14 条） |
| `conftest.py` | pytest 前置钩子，防止 `select.py` 遮蔽标准库 `select` |

### 关于 `select.py` 这个文件名

`select` 是 Python 标准库里的 I/O 多路复用模块。把 `tools/ai-bridge/` 放进
`sys.path` 首位会让后续 `import select` 命中我们的文件——POSIX 上
`selectors` / `subprocess` 都依赖真正的 `select`，一旦被遮蔽会以完全无关的
形式炸掉。

对策有两处：`conftest.py` 在收集用例前把标准库 `select` 钉进 `sys.modules`；
`demos/_公用.py` 与 `test_bridge.py` 一律用 `importlib` 按**绝对文件路径**
载入 `select.py`（挂成「块选择器」这个不冲突的名字），不走模块名解析。


## 快速上手

```
# 1) 选块（本地关键词匹配，不外接大模型）
python tools/ai-bridge/select.py "对一组数字求和"

# 2) 跑三个 demo（会打印生成的极快代码、实际输出、压缩比）
python tools/ai-bridge/demos/demo1_数值统计.py
python tools/ai-bridge/demos/demo2_文本清洗.py
python tools/ai-bridge/demos/demo3_中文报表.py

# 3) 跑测试
python -m pytest tools/ai-bridge/test_bridge.py -q
```

## 与 ADR-15 §3.7 的对齐

生成的粘合代码遵守块生态调用协议：

```jikuai
从 blocks.数据.求和 导入 汇总。   -- 目录名 求和，导出名 汇总
打印 汇总(列 1 2 3 4 5)。
```

导出名从每个块目录的 `.jk` 文件里用 `jikuai.pkg.blocks.extract_exports` 提取（索引本身不含此字段——避免 W3 索引结构改动的耦合）。

## 约束

- v0 **不外接任何大模型 API**（本地关键词匹配足以验证协议）
- 只在 `tools/ai-bridge/` 下写文件
- 不修改 `stdlib/`、`tests/`、`src/`、`scripts/`
- 中文注释与文档
