# -*- coding: utf-8 -*-
"""v0.28.0 W167 · 三个「表出口」姊妹块的 pytest 断言（BACKLOG §12.3 缺口 1 + 6）。

三个新块（都是纯 `.jk`、层级 1、只依赖一个已有 0 层块）：
  制造.停线汇总表 → 计停表（= 计停(...)【0】）
  制造.延期汇总表 → 计延表（= 计延(...)【0】）
  制造.延期排行表 → 延榜表（= 延榜(...)【0】）

**为什么要这三个块**：0 层块出的是**元组**（表 + 排除计数 + 计入计数），
`定序`/`摘前`/`择字段` 这些下游块吃的是**表**。元组接不进链，粘合器只能停在
第一步，agent 得手写 `【0】`——这正是 §12.3 缺口 1（元组出口接不上链）与缺口 6
（表→表 链式不通）是同一个结。姊妹块把 `【0】` 一次性做进块里，代价是丢掉
排除计数（要计数就仍调 0 层块）。**0 层块的行为一行没改。**

`测试.jk` 已在极快侧覆盖语义边界（含「忘了 【0】」「空 ≠ 0」「取前 N 边界」）。
这里做 `.jk` 里做不动的事：
1. 在**真实数据集**上钉住姊妹块 == 0 层块元组第 0 项（逐元素相等）。
2. Q_PUB_007 口径（按延期天数合计降序取前 3）的数值回归。
3. 元数据契约：层级 1 / 依赖块 / 无 `.py` 背衬（`块背衬PY数` 仍是 37，不是 40）。
4. 形参名词法原子性回归——W167 真被这个咬过一次（见 test_形参名 那条）。

数据集缺失时（`赛题/` 不进 wheel 与 sdist，ADR-40 §7）整个模块 skip。
"""

import importlib.util
import json
import pathlib
import sys

import pytest

_源 = importlib.util.find_spec("jikuai").origin
仓库根 = pathlib.Path(_源).resolve().parent.parent.parent
数据集 = 仓库根 / "赛题" / "chatbi" / "数据集"
块根 = 仓库根 / "src" / "jikuai" / "stdlib" / "blocks" / "制造"

pytestmark = pytest.mark.skipif(
    not 数据集.is_dir(),
    reason="赛题/chatbi/数据集/ 不存在（赛题/ 不进 wheel 与 sdist，ADR-40 §7）",
)


def _载背衬(块名):
    """0 层块的 Python 背衬（sidecar `.py`），直接按文件路径加载。"""
    路径 = 块根 / 块名 / (块名 + ".py")
    规格 = importlib.util.spec_from_file_location("w167_" + 块名, 路径)
    模块 = importlib.util.module_from_spec(规格)
    规格.loader.exec_module(模块)
    return 模块


def _载姊妹(块名, 导出名):
    """姊妹块是**纯 `.jk`**，没有 `.py` 可 import——只能过极快运行时。

    `jikuai.load` 只收相对路径（绝对路径与 `..` 一律拒），所以 base_dir 给块根。
    """
    import jikuai
    模块 = jikuai.load(f"{块名}/{块名}.jk", base_dir=str(块根))
    return getattr(模块, 导出名)


读表 = _载背衬("表载入").读表
计停 = _载背衬("停线汇总").计停
计延 = _载背衬("延期汇总").计延
延榜 = _载背衬("延期排行").延榜


@pytest.fixture(scope="module")
def 订单():
    return 读表(str(数据集 / "fact_orders.csv"))


@pytest.fixture(scope="module")
def 实绩():
    return 读表(str(数据集 / "fact_production_actual.csv"))


@pytest.fixture(scope="module")
def 计停表():
    return _载姊妹("停线汇总表", "计停表")


@pytest.fixture(scope="module")
def 计延表():
    return _载姊妹("延期汇总表", "计延表")


@pytest.fixture(scope="module")
def 延榜表():
    return _载姊妹("延期排行表", "延榜表")


# ===========================================================================
# 1. 姊妹块 == 0 层块元组第 0 项（真实数据集，逐元素）
# ===========================================================================

@pytest.mark.parametrize("维度", [[], ["line_id"], ["line_id", "shift"]])
def test_计停表_等于计停元组第0项(实绩, 计停表, 维度):
    期望, _排除 = 计停(实绩, 维度)
    实得 = 计停表(实绩, 维度)
    assert 实得 == 期望


@pytest.mark.parametrize("维度", [[], ["model_id"], ["customer_id", "model_id"]])
def test_计延表_等于计延元组第0项(订单, 计延表, 维度):
    期望, _排除, _计入 = 计延(订单, 维度)
    实得 = 计延表(订单, 维度)
    assert 实得 == 期望


@pytest.mark.parametrize("前几", [3, 10, 1793, 1850])
def test_延榜表_等于延榜元组第0项(订单, 延榜表, 前几):
    期望, _排除, _参与 = 延榜(订单, 前几)
    实得 = 延榜表(订单, 前几)
    assert 实得 == 期望


# ===========================================================================
# 2. 出口是**表**不是元组 —— 缺口 1 的验收点
# ===========================================================================

def test_三个姊妹块的返回值都是列表而非元组(订单, 实绩,
                                            计停表, 计延表, 延榜表):
    """0 层块出元组、姊妹块出列表：类型换了，下游 `定序`/`摘前` 才接得上。"""
    assert isinstance(计停(实绩, ["line_id"]), tuple)
    assert isinstance(计延(订单, ["model_id"]), tuple)
    assert isinstance(延榜(订单, 3), tuple)

    assert isinstance(计停表(实绩, ["line_id"]), list)
    assert isinstance(计延表(订单, ["model_id"]), list)
    assert isinstance(延榜表(订单, 3), list)


def test_长度不等于元组元数_忘了取0会当场露馅(实绩, 计停表):
    """按 4 个产线分组 → 表 4 行，而元组只有 2 项。

    这条专治「忘了 `【0】`」：若姊妹块误返回整个元组，长度会是 2 而不是 4。
    """
    四线 = [r for r in 实绩 if r["line_id"] in ("L001", "L002", "L003", "L005")]
    表 = 计停表(四线, ["line_id"])
    assert len(表) == 4
    assert len(表) != 2
    assert all(isinstance(行, dict) for 行 in 表)


# ===========================================================================
# 3. Q_PUB_007：按延期天数合计降序取前 3（DoD 的端到端口径）
# ===========================================================================

def test_Q_PUB_007_按延期天数合计降序取前3的车型(订单, 计延表):
    """姊妹块出表 → 直接按 `延期天数合计` 降序取前 3，无需手工解元组。

    实测：M003 合计最高，前 3 名合计单调不增，且三者之和占全表 2213 的多数。
    """
    表 = 计延表(订单, ["model_id"])
    排 = sorted(表, key=lambda 行: -行["延期天数合计"])[:3]
    assert len(排) == 3
    assert 排[0]["model_id"] == "M003"
    合计 = [行["延期天数合计"] for 行 in 排]
    assert 合计 == sorted(合计, reverse=True)
    # 与整表口径守恒：分组合计之和 == 整表合计 2213
    assert sum(行["延期天数合计"] for 行 in 表) == 2213
    assert sum(合计) > 2213 / 2


def test_延榜表_前3单与延榜一致且空值行不占名额(订单, 延榜表):
    """前 3 名与 0 层块逐字对齐；第 1 名沿用 W142 已钉死的 O001328(19 天)。"""
    表 = 延榜表(订单, 3)
    榜, _排除, _参与 = 延榜(订单, 3)
    assert len(表) == 3
    assert [行["order_id"] for 行 in 表] == [行["order_id"] for 行 in 榜]
    assert 表[0]["order_id"] == "O001328"
    assert 表[0]["delay_days"] == 19
    assert all(行["delay_days"] is not None for 行 in 表)


def test_延榜表_取满全量仍是1793行(订单, 延榜表):
    """1850 行里 57 行未交付被剔除；取 1850 只拿到 1793 行。"""
    assert len(延榜表(订单, 1850)) == 1793


# ===========================================================================
# 4. 元数据契约：层级 1 / 依赖块 / 纯 .jk（块背衬PY数 不变）
# ===========================================================================

@pytest.mark.parametrize("目录,导出,依赖", [
    ("停线汇总表", "计停表", "停线汇总"),
    ("延期汇总表", "计延表", "延期汇总"),
    ("延期排行表", "延榜表", "延期排行"),
])
def test_块元数据_层级1_依赖单块_口径关键词(目录, 导出, 依赖):
    with open(块根 / 目录 / "块.json", encoding="utf-8") as f:
        元 = json.load(f)
    assert 元["导出"] == [导出]
    assert 元["领域"] == ["制造"]
    assert 元["层级"] == 1, "只依赖 0 层块的组合块是 1 层"
    assert 元["依赖块"] == [依赖]
    # G22 第 2 条：描述必须自报口径
    assert [k for k in ("分母", "汇总", "加权", "现成列", "重算", "窗口")
            if k in 元["描述"]], "描述缺口径声明关键词（G22 第 2 条）"


@pytest.mark.parametrize("目录", ["停线汇总表", "延期汇总表", "延期排行表"])
def test_姊妹块是纯jk_没有py背衬(目录):
    """本轮刻意不给姊妹块配 `.py`：`块背衬PY数` 仍是 37，
    `scripts/check_wheel_contents.py` 一行不用改（WBS 里 37→40 的假设不成立）。
    """
    assert (块根 / 目录 / (目录 + ".jk")).is_file()
    assert not (块根 / 目录 / (目录 + ".py")).exists()
    assert (块根 / 目录 / "测试.jk").is_file()


# ===========================================================================
# 5. 形参名词法原子性 —— W167 被这个咬过一次
# ===========================================================================

def test_形参名_赵维度列不是词法原子而赵维度清单是():
    """回归 W167 实际踩的坑：形参名 `赵维度列` 里的 `列` 是内建动词（变长列表构造），
    分词切成 `IDENT 赵维度` + `VERB 列`，于是 `计停(赵表, 赵维度列)` 被编译成
    `计停(赵表, 赵维度, 列())` —— 报「takes 2 positional arguments but 3 were
    given」，看着像模块加载器的锅，其实是词法。

    `jk 块 新建` 有形参名预检（`pkg/blocks_cli.py`），**手写块文件绕过了它**。
    所以块目录名、导出名之外，**形参名也要过同一把尺**。
    """
    sys.path.insert(0, str(仓库根 / "src"))
    from jikuai.pkg.blocks import check_export_atomicity as 原子

    坏, 碎片 = 原子("赵维度列")
    assert 坏 is False
    assert len(碎片) >= 2
    好, _ = 原子("赵维度清单")
    assert 好 is True


@pytest.mark.parametrize("目录", ["停线汇总表", "延期汇总表", "延期排行表"])
def test_三个姊妹块的形参名全是词法原子(目录):
    """把上一条的判据真正应用到落地的三个块上（目录名 / 导出名 / 形参名三处）。"""
    sys.path.insert(0, str(仓库根 / "src"))
    from jikuai.pkg.blocks import (check_export_atomicity as 导出原子,
                                   check_module_segment_atomicity as 段原子)

    好, 碎片 = 段原子(目录)
    assert 好 is True, f"目录名不是词法原子：{碎片}"

    源 = (块根 / 目录 / (目录 + ".jk")).read_text(encoding="utf-8")
    形参行 = [行 for 行 in 源.splitlines() if 行.startswith("函数 ")]
    assert 形参行, "没找到 `函数 … 接收 …` 定义行"
    for 行 in 形参行:
        名字 = 行.rstrip("：:").split()
        # ['函数', '<导出名>', '接收', '<形参>', ...]
        assert 名字[2] == "接收"
        for 名 in [名字[1]] + 名字[3:]:
            通, 碎 = 导出原子(名)
            assert 通 is True, f"{目录}: `{名}` 不是词法原子，会被切成 {碎}"
