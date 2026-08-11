# 集成反馈：quye 浏览器工作台

> 来源：quye.com M2「浏览器工作台」集成方
> 日期：2026-08-11
> 场景：把 `src/jikuai/` + `stdlib/` 打进 Pyodide，在浏览器里跑
> 「中文需求 → 选块 → 组码 → 当场运行」的完整链路。
> 性质：外部集成方反馈，非 ADR。落地与否由维护者裁定。

---

## 先说结论

块元数据设计得很好——**105 个块的 `块.json` 里 100% 都写了 `示例` 字段**，
且都是逐字可跑的调用（例如 `增值税` 的 `打印 增税(113, 0.13)。`）。

问题在于这份**块作者亲手写的正确示例，在 选块 → 组码 这条 AI 链路上被完整地丢弃了**。
下游拿到的是 `增税(?)`，一个既不知道要几个参数、也不知道填什么的骨架。

---

## 一、`synthesize` 建议优先复用块的 `示例`（主建议）

### 现象

`tools/ai-bridge/glue.py` 的 `synthesize` 对任意块都只生成一个 `?` 占位：

```
-- 需人工填参：增税 的入参未指定（下一行的 ? 占位）
定义赵果1=增税(?)。
```

但 `stdlib/blocks/财务/增值税/块.json` 里 `输入` 是**两个**参数：

```json
"输入": [{"名": "含税", "类型": "数"}, {"名": "税率", "类型": "数"}]
```

于是一个不知情的使用者（人或 AI）自然会写 `增税(17202)`，然后崩在块内部：

```
File "jikuai/evaluator.py", line 431, in <lambda>
    '加': lambda a, b: a + b,
TypeError: unsupported operand type(s) for +: 'int' and 'NoneType'
```

报错点在 `evaluator` 的 `加`，离真正的错因（少传一个参数）隔了好几层，
排查体验很差。

### 建议

`synthesize` 生成调用行时，按优先级取参数：

1. 方案里显式给了 `参数` → 用它（现有行为）
2. 否则，若块 `块.json` 有 `示例` → **从示例里提取实参**，生成可跑的调用
3. 否则 → 按 `输入` 的**元数**生成对应个数的占位，如 `增税(?, ?)`，
   并在注释里点明参数名：`-- 需人工填参：含税, 税率`

第 3 条即便不做第 2 条也值得单独做——**至少让占位个数与真实元数一致**，
这是纯粹的正确性问题，跟示例无关。

### 收益

- AI Agent 拿到的组码结果**默认可跑**，不需要额外一轮"去读 `块.json` 补参数"
- 参数示例值来自块作者（权威），不是调用方猜的
- 多参块不再出现"参数个数对不上"这类隔层报错

---

## 二、`retrieval.Hit` 建议带上 `示例`（次建议）

`src/jikuai/ai/retrieval.py` 的 `Hit` 目前只有
`score/name/domain/description/path`，`as_dict()` 也一样。
选块之后要用示例，只能拿 `名称` 回去逐个块读 `块.json`——在浏览器等
"文件系统很贵"的环境里，这一步是 N 次额外 I/O。

建议 `Hit` 增加可选字段 `example: str = ''`，由 `Retriever` 构造时填入。

**注意**：这一条与 `索引.json` 的设计原则有冲突，见下条。

---

## 三、关于 `索引.json` 不含 `示例`——赞同现状，但建议留个开关

`src/jikuai/pkg/blocks.py` 里的设计说明写得很清楚，也很有道理：

```python
# 索引越窄，一次性读入的 token 成本越低，这正是块生态压缩 token 的关键。
_INDEX_ENTRY_KEYS = ('名称', '领域', '层级', '描述', '输入', '输出', '导出',
                     '稳定性', '命名空间')
```

**这个取舍是对的，不建议改默认行为。**

但建议给 `generate_index()` 加一个可选参数（如 `含示例=False`）
或 `scripts/generate_block_index.py` 加一个 `--with-examples` flag，
让"不在乎 token、在乎 I/O 次数"的下游（浏览器 / 单文件分发 / 离线包）
能生成一份带示例的胖索引。

105 个块的示例大约多 8-10 KB，对本地文件是零成本，对 token 敏感场景仍可关掉。

---

## 四、两处路径假设建议解耦（嵌入式场景）

这两条不影响命令行使用，只在把 `jikuai` 包搬到非仓库布局时才暴露。
quye 是通过打包 `src/jikuai/` → `/极快/jikuai/` 的方式在 Pyodide 里跑的
（`stdlib/` 平级放在 `/极快/stdlib/`）。

### 4.1 `ai/retrieval.py:_load_blocks()` 用 `__file__` 往上 3 级

```python
here = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.normpath(os.path.join(here, '..', '..', '..'))
idx_path = os.path.join(repo_root, 'stdlib', 'blocks', '索引.json')
```

这假设 `retrieval.py` 永远在 `<repo>/src/jikuai/ai/`（上 3 级刚好是仓库根）。
包被搬到 `<任意>/jikuai/ai/` 时，上 3 级就走出了包根，
`_load_blocks()` 静默返回 `[]`，`retrieve()` 恒返回空列表——**没有任何报错**。
集成时这一点消耗了最多的排查时间：现象是"选块永远 0 个候选"，
很容易误判成检索算法不命中，而不是索引根本没加载。

建议（任选，第 3 条最低成本）：
1. `retrieve()` / `_get_retriever()` 支持显式传入索引路径或 blocks 列表
2. 或读一个环境变量（类似 `module_loader` 已有的 `JIKUAI_PATH`）
3. **至少**：`_load_blocks()` 找不到索引时打个 warning，别静默返回空

对比参考：`module_loader._search_paths()` 用的是上 2 级 + 支持 `JIKUAI_PATH`，
这个设计明显更健壮，建议 `retrieval` 对齐。

### 4.2 `pkg/sources.py` 让"只想跑块"的场景被迫链接包管理器

`pkg/sources.py` 依赖 git subprocess，浏览器里不可用。但它不能简单删掉——
`pkg/__init__.py`、`pkg/resolver.py`、`pkg/installer.py`、`pkg/registry.py`
都在**模块级**从它 import：

```python
# pkg/resolver.py:21
from .sources import FetchedSource, SourceError, resolve_source, compute_checksum
```

而 `frontend.py:_collect_import_whitelist()` 在**编译期**就会
`from .pkg.blocks import block_exports`，于是只要源码里有一句
`从 blocks.财务.个税 导入 缴税。`，整条 `pkg` → `resolver` → `sources`
的 import 链就被拉起来。

结果：想在浏览器里跑一个块，必须先让包管理器的 import 链能通。
quye 的解法是写一个"保留 `__all__` 全部公开签名、调用时才抛错"的
`sources.py` 替身（`SourceError` / `FetchedSource` / `resolve_source` /
`compute_checksum` 四个名字都得留着，否则模块级 import 就炸）。

建议把 `pkg` 的**元数据读取层**（`blocks.py` 的 `block_exports` /
`scan_blocks` / `BlockMetadata`）与**安装执行层**（`sources` / `resolver` /
`installer` / `registry`）在 import 依赖上切开——前者是编译期刚需，
后者只在 `jk 装` 时需要。改成函数内 import 即可，不必动架构。

---

## 五、附：quye 侧现在的临时处置（供参考，不建议长期这样）

在极快侧修好之前，quye 的前端自己维护了一张示例值表：

```javascript
const _示例值表 = {
  月收: 20000, 含税: 17202, 税率: 0.13,
  本金: 100000, 年利率: 0.05, 期数: 12, ...
};
```

按 `输入` 的 `名` 命中就用领域常识值，没命中按 `类型` 兜底。
**这是猜的**——生成的代码保证能跑，但数值不一定符合语义。

如果第一条建议（`synthesize` 复用 `示例`）落地，这张表就可以整个删掉，
示例值改由块作者提供，这才是正确的归属。

---

## 优先级建议

- **P0**：`synthesize` 至少让 `?` 占位个数与 `输入` 元数一致（纯正确性 bug）
- **P1**：`synthesize` 优先从块 `示例` 提取实参，产出可跑代码
- **P1**：`_load_blocks()` 找不到索引时别静默返回空
- **P2**：`Hit` 带 `示例` / `generate_index` 加 `--with-examples`
- **P2**：`pkg` 元数据层与安装层的 import 解耦
