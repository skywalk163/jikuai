# ADR-39 · stdlib 是包内资源

- 状态：**已采纳**（v0.24.0 · 2026-08-15）
- 相关：**推翻 [ADR-16 §3.4](ADR-16-标准库契约.md) 的「`stdlib/` 固定仓库根不可移动 + data-files 分发」裁决与其引用的 D-05**
- 结账：[BACKLOG.md](BACKLOG.md) §10
- WBS：W113–W117（`docs/路线图-v0.24.md`）

---

## 一、背景：这不是风险，是已经发生的事故

`pip install jikuai` 在 v0.24.0 之前**是坏的**，不是「将来可能坏」。实测证据：

- PyPI 上的 `jikuai 0.4.1` 的 wheel 只有 **19 个条目，零个 stdlib 文件**。
- 干净 venv 装上它，跑 `导入 数学。` 报「找不到模块：数学」，**退出码 1**。
- 而本机 `pip install -e .` 下一切正常——源码树在原地，`__file__` 相对回溯自然命中。

这是典型的**双态假绿**：开发态（editable，源码树在原地）与安装态（wheel 装进
`site-packages/jikuai/`，回溯目标不存在）走的是两条不同的路径，而仓库里所有测试、
所有门禁跑的都是开发态。这条缺口在 BACKLOG 里挂了三个版本没人碰，不是因为难，
是因为**没有任何自动化能看见它**。

`pyproject.toml` 当时只有 `packages.find where=["src"]`，无 `package-data`、
无 `include-package-data`、无 `data-files`，仓库无 `MANIFEST.in`；
`egg-info/SOURCES.txt` 实证零个 `stdlib/` 文件。

## 二、裁决：stdlib 物理搬进包内，定位收敛到单一入口

### 2.1 物理位置

`stdlib/` 从**仓库根**搬到 **`src/jikuai/stdlib/`**，成为 `jikuai` 包的包内资源，
随 wheel 与 sdist 发行。

「什么随包发」的**单一真源是 `MANIFEST.in`**，不是 `[tool.setuptools.package-data]`。
理由见 §5.2——这一条是执行期实测推翻计划的地方。

### 2.2 推翻 ADR-16 §3.4 的原方案

ADR-16 §3.4 的裁决是「`stdlib/` 固定在仓库根不可移动，分发时以 setuptools 的
`data_files` 声明，安装时复制到 site-packages 对应位置」。

**这个方案不成立，也从来没成立过**——它三个版本没落地的真实原因不是「忘了加一行声明」：

> setuptools 的 `data_files` 在 wheel 里装到 **`sys.prefix` 相对位置**
> （如 `<venv>/stdlib/...`），而代码是从 `site-packages/jikuai/` 往上回溯 2–3 级
> （`<venv>/Lib/site-packages/jikuai/../../stdlib` → `<venv>/Lib/stdlib`）。
> 两者在**任何平台上都对不上**。

所以本 ADR 推翻「不可移动」这条约束本身。ADR-16 §3.4 的原文保留不删（决策史要留痕），
只在节首加了推翻标记。

## 三、为什么不用 `importlib.resources`

这是「按现代 Python 打包最佳实践应该这么做」的默认答案，但对本项目**不适用**。

所有消费方要的都是**真实文件系统目录路径**，不是「可读的字节流」：

- `module_loader._search_paths()` 要把 stdlib 根**追加进搜索路径列表**
- `evaluator._load_stdlib_module()` 要 `spec_from_file_location(name, 路径)`
- `pkg/blocks.py` 要 `os.walk` 遍历 `blocks/` 下的领域目录树
- `ai/retrieval.py` 要 `open()` 读 `.bin` 与 `.json`

`importlib.resources` 的 `files()` 返回 `Traversable`；要拿到真实路径必须走
`as_file()` 上下文管理器，而它在 zip 场景下会把资源**解包到临时目录**，出了上下文就删。
把上面四处全改成上下文管理器，等于为了一个本项目用不上的能力（zip-safe）重写四个子系统。

**本项目本来就不可能 zip-safe**：`.jk` 模块加载、块目录树遍历、块自测都依赖真实路径。
承认这一点，用 `os.path.dirname(os.path.abspath(__file__))` 直接算目录，是**更诚实的方案**，
不是走捷径。

## 四、单一入口纪律

新增 `src/jikuai/resources.py`，是 stdlib 的**唯一**定位入口：

```python
ENV_STDLIB = 'JIKUAI_STDLIB'

def stdlib_dir() -> str: ...      # stdlib 根
def blocks_dir() -> str: ...      # stdlib/blocks/
def stdlib_path(*parts) -> str: ...  # 拼 stdlib 下的任意资源路径
```

纪律三条：

1. **`resources.py` 是唯一定位口。** 代码里再出现 `__file__` 相对回溯去找 stdlib，
   视为违约。此前有 **7 处**各写各的回溯（名单与订正见 §6.2），任何一处漏改都是
   运行期炸点。
2. **`JIKUAI_STDLIB` 是唯一覆盖口。** 值必须是已存在的目录，否则**忽略并回落包内默认值**
   ——刻意不抛错：一个打错的环境变量不该让整个解释器起不来。
   注意它与既有的 `JIKUAI_PATH`（`module_loader`）、`JIKUAI_PKG_ROOTS`（`pkg/blocks`）
   语义不同：那两个是**追加**额外搜索路径，`JIKUAI_STDLIB` 是**替换**内置根。
3. **`resources.py` 只 import 标准库。** 它被 4 个子系统调用，任何非标准库依赖都可能
   造成 import 环。这一条有静态测试把关（`tests/test_resources.py::test_模块只依赖标准库`）。

## 五、门禁 G20：wheel 内容断言

### 5.1 定义

`scripts/check_wheel_contents.py`。**真去构建 wheel 再解包看条目名**——
「pyproject 里声明了」和「文件真在包里」是两件事，只查声明的门禁守不住本节 §1 那次事故。

断言五条：

- 具名资源在：`分词词典.txt`、`blocks/向量索引.bin`、`blocks/索引.json`、
  `blocks/财务/保留分/保留分.py`
- 无 `.pyc` / `__pycache__` 泄漏
- 无 `临时_测试*` 测试产物随包发（W114 实测真发生过 9 个）
- 块元数据 json 数在 **[112, 500]**：下界 112 是 v0.23.0 的块数；
  **上界哨 500 是刻意加的**——只设下界会重演「守卫绿≠守卫在守」，
  库炸成 5000 个也照样绿。库真长这么多了就上调这个数，别删这条哨。
- 块背衬 `.py` **精确 14 个**（不是下界，是等值）

### 5.2 为什么「块背衬 .py 精确 14 个」是这条门禁存在的核心理由

ADR-16 §3.3 的混合模块里，有 14 个块的实现落在 `.py` 背衬文件上。
W114 执行期发现：如果这 14 个文件全没进 wheel，**原本设想的验收线（3 个具名文件 +
块 json 计数 + 无 pyc）会全绿**。而 `财务/保留分/保留分.py` 导出的 `圆分` 被
**13 个财务块引用**（含 `个税` / `增值税` 两个旗舰块）——漏了它们，装完的包在
选块阶段一切正常，只在真跑财务块时炸。

用等值而非下界，是要求「块背衬数变了就得有人来改这个常量并解释一句」。

### 5.3 G20 刻意不进静态门禁主流程

`scripts/check_stdlib_contract.py` 里的 G10–G19 全是秒级静态检查；G20 要跑
`python -m build`，量级差两个数量级，还多一个 `build` 包依赖。塞进去会拖垮常规 CI
的门禁步骤。

折中：G20 独立成脚本，`check_stdlib_contract.py` 在输出末尾打一行提示指向它
（`--json` 模式下不打，那个模式的 stdout 必须是纯 JSON）。
`tests/test_wheel_contents.py::test_G20已在静态门禁里留痕` 守住这行提示不被删——
否则 G20 就成了没人跑的死门禁。

### 5.4 真机验收才是终局判据

`scripts/verify_wheel_e2e.ps1`：全新 venv、`--no-deps` 装本地 wheel，跑通
hello / 分词 / 选块 / 包管理，并断言 stdlib 落在 `site-packages` 而**非回落源码树**、
`JIKUAI_STDLIB` 覆盖生效。

这是唯一能抓住「本机 editable 恰好还能回溯到旧位置」这种假绿的一步。
ADR-16 与 v0.23.0 W112 一直缺的就是这一步。

分词那条断言的判据是「结果里有没有把 `个人所得税` 切成一个词」而不是「退出码为 0」——
词典没进包时 `分词` 会退化成逐字切，退出码照样 0。

## 六、不做的事（划边界）

### 6.1 `tools/` 不入包

- **`tools/web/server.py` 有安全问题**：无任何鉴权，且 `/api/跑` 在本机进程内执行
  调用方提交的极快代码（极快能通过 `蟒:` 桥调 Python = 任意代码执行）。
  入包等于给每个 `pip install jikuai` 的用户默认装一个 RCE 面。
- **`tools/ai-bridge/` 是 sidecar**：依赖 torch / sentence-transformers，
  刻意与主包隔离；且神经检索的收益在 v0.16.0 的链式任务上被 TF-IDF 反超 38pp，
  「神经比词面强」这个前提本身未被证实。
- `src/jikuai/ai/embed_client.py:57` 回溯 `tools/ai-bridge/embed_query.py` 是
  **按缺口设计**的：注释自述「pip 安装场景 `tools/` 不随包发布，此文件不存在，
  那正是降级到启发式该覆盖的情况」。不要顺手一起搬。

### 6.2 `scripts/` 的回溯不改，且 BACKLOG 那份「7 处」名单有一个成员是错的

`scripts/` 下的 `__file__` 回溯只用于把 `src/` 塞进 `sys.path` 或定位 `docs/`，
只在源码仓库跑，不随 wheel 走，不用改。

**BACKLOG §10 原文列的 7 处名单里，`src/jikuai/pkg/blocks_cli.py:186 _repo_root()`
是误归类。** 它只在 `:202` 被用来定位 `tools/ai-bridge/glue.py`——定位的是 `tools/`，
不是 stdlib，按 §6.1 的边界就不该改。

**真实的 7 处**（判据是「它定位的是什么」，不是「它在不在 `tools/` 目录下」）：

- `src/jikuai/stdlib_contract.py:16-22`（`default_stdlib_dir`）
- `src/jikuai/module_loader.py:147-149`（`.jk` 兜底搜索路径）
- `src/jikuai/evaluator.py:1644-1646`（stdlib `.py` 实现加载）
- `src/jikuai/pkg/blocks.py:301-309`（`blocks_root`）
- `src/jikuai/ai/retrieval.py:131-135`（`vector_index_path`）
- `src/jikuai/ai/retrieval.py:673-677`（`_load_builtin_blocks`）
- `tools/ai-bridge/bench_compress.py:49` ← **BACKLOG 完全没记这一处**

最后那处是 W115 执行期才发现的：它在 `tools/` 下，但**定位的是 stdlib 块根**。
搬走后它悄悄失效，让依赖闭包全落空，示例压缩比从 ~10x 掉到 1.5x，
连带两条 `test_v0_14_0_demos.py` 门禁变红。这正是「按目录归类」这个判据的反例。

### 6.3 不改 `BLOCK_INDEX_VERSION`

索引格式版本与语言版本解耦，是 v0.12.0 起的既定设计，本轮不动。

## 七、已知残留与本轮处理

### 7.1 `向量索引.bin` 的字节序（W119 已修）

核查结论：文件头（MAGIC / 版本 / 维度 / 数量 / qmin / qmax / 名长）全部用**显式小端**
`struct` 格式串（`'<HH'` / `'<I'` / `'<ff'` / `'<H'`），但**向量载荷用的是原生字节序**
——写侧 numpy `int16.tobytes()`、读侧 `array.array('h').frombytes()` 都跟随机器字节序。

这是**真实缺陷**，而且是最坏的那类：仓库里提交的 `.bin` 生成于 x86（小端），
在大端平台（如 s390x）上读会把 int16 逐个字节翻转，**不抛异常，只是余弦相似度打分全错**。
而 `jikuai` 发的是 `py3-none-any` wheel（平台无关），真的可能装到大端机器上。

裁决：**文件格式口径定为「全小端」**（头部已经是，载荷跟上，保持自洽），
读写两侧都改成显式小端。W119 已落地，不留作残留。

### 7.2 块自测污染源码树（W115 已根治）

112 个块自测里有几个会往当前工作目录写文件，其中 9 个 `临时_测试*.txt` 真的进过 wheel。
根治方式是在 `tests/test_blocks_smoke.py` 里 `monkeypatch.chdir(tmp_path)`——
模块解析不受影响，因为它由 `file=test_path` 驱动而不是 cwd。
G20 另有一条独立的负向断言守这个洞（§5.1 第三条），因为「根因修了」不等于
「以后不会有别的东西漏进来」。

---

## 参考

- [ADR-16 · 标准库契约](ADR-16-标准库契约.md)（§3.3 混合模块 / §3.4 被本 ADR 推翻）
- [ADR-25 · M3 语义选块架构](ADR-25-M3语义选块架构.md)（向量索引格式）
- [BACKLOG.md](BACKLOG.md) §10（本缺口的原始记录与结账）
- [路线图-v0.24](路线图-v0.24.md)
