# examples/块包/ — 块包 fixture 目录（ADR-32 W64 起）

这里的每个子目录是一个**可发布的块包 fixture**——用来验证 ADR-32 定的
「一个包携带一批块」桥接机制。

## 子目录

- `示范块集/` — v0.19.0 W64 桩验证的最小块包：一个包 + 一个 L0 块（翻倍/倍增）。

## 布局约定（对照 ADR-32 §2）

每个块包必须有 `包.json`，其中：

- `块` 字段（可选顶层字段）声明包内块根的相对路径列表；默认不携带块。
- 每条路径直接指向 `blocks/` 那一级（语义与 `JIKUAI_PKG_ROOTS` 每条路径一致）。
- 块根下推荐套一层命名空间目录（约定命名空间 = 包名），避免与内置块争同名域。

```
<包名>/
├── 包.json              # 声明 名称/版本/块=["blocks"]
└── blocks/              # 块根（对应 包.json 的 块 字段）
    └── <命名空间>/        # 推荐 = 包名
        └── <领域>/
            └── <块名>/
                ├── <块名>.jk
                ├── 块.json
                └── 测试.jk
```

## 手工桩验证（W64 用）

块生态有**两套独立的根系统**，桩验证必须两条都走通（这是 W64 的头号发现，
见 ADR-32 §2.3）：

**1. 发现侧** —— `JIKUAI_PKG_ROOTS` 指向「`blocks/` 那一级」：

```powershell
$env:JIKUAI_PKG_ROOTS = "g:\jikuai\examples\块包\示范块集\blocks"
py -3.13 -c "import sys; sys.path.insert(0,'src'); from jikuai.pkg import blocks; bs = blocks.scan_blocks(); print([b.qualified_name for b in bs if b.namespace])"
```

期望：`['示范块集.数据.翻倍']`

**2. 执行侧** —— `JIKUAI_PATH` 指向「`blocks/` 的**父目录**」（即包根）：

```powershell
$env:JIKUAI_PATH = "g:\jikuai\examples\块包\示范块集"
py -3.13 -c "import sys; sys.path.insert(0,'src'); from jikuai import run_source; run_source('从 blocks.示范块集.数据.翻倍 导入 倍增。\n打印 倍增(21)。\n')"
```

期望：`42`

> **注意两个环境变量的层级差一级。** 只挂发现侧，块能被 `jk 块 选` 检索到但
> `导入` 时报 `JK-E5001 找不到模块`；只挂执行侧，块能跑但检索排序里没有它。
> 安装器接线（W65-W66）要**两侧都写**：`extra_roots()` 读索引里的路径本身，
> `module_loader._search_paths()` 读同一份索引取其 `dirname`。

