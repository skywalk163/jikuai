# -*- coding: utf-8 -*-
"""v0.26.0 W140 · 制造域口径块第一批（产量与达成率）的回归测试。

被测三块（都在 `src/jikuai/stdlib/blocks/制造/`）：

| 目录 | 导出 | 口径 |
| --- | --- | --- |
| 产量汇总 | 计产 | 按维度求和 `actual_quantity`，产量降序 |
| 达成率权重 | 达权 | `sum(actual_quantity)/sum(planned_quantity)`，产量加权（ADR-40 §5.1 默认） |
| 达成率均值 | 达均 | `mean(achievement_rate)`，行级算术平均、每条记录等权 |

**目录名偏离记录（必读）**：WBS/ADR-40 §5.1 把加权块写作 `制造/达成率加权`，但
`加权` 含动词 `加`，`tokenize('达成率加权')` 切成 达成率(IDENT)+加(VERB)+权(IDENT)
三段——它**不是词法原子的模块路径段**（ADR-15 §3.7），`从 blocks.制造.达成率加权
导入 达权。` 直接 ParseError（实测报「语法错误：从...期望 导入」，列号正落在 `加`
上）。与 `归组`/`择列` 那五个死名同一个坑，只是这次死在**目录名**而不是导出名上。
故目录名与 `块.json`「名称」取等义原子名 **达成率权重**；**导出名 `达权` 未变**，
调用方无感。`test_目录名词法原子性` 把这条钉成回归，别再改回去。

本文件的两条 DoD 主断言：

* **Q_PUB_001**：2026-06 各车型总产量降序，8 个车型的数字逐一钉死，并与本文件内
  **独立用标准库 `csv` 重算**的结果对齐——一路走块、一路走裸 Python，不是自我印证。
* **Q_PUB_005**：L003 白班/夜班平均达成率，**两个口径各出一个数且数值确实不同**。
  四个数与两个差值全部写实测真值（浮点钉到小数第 6 位的 `round`，整数分子分母
  精确相等），不用松容差的 `approx`。这条就是「口径可审计」的实证。

反例（口径块的价值在于该报错时报错，不只是算得对）：拒绝汇总现成比率列、产量列
空值、达权分母为 0、达均空表、达均疑似百分数、列名不存在——共 6 条。

数据集缺失时（从 wheel/sdist 装出来的环境里 `赛题/` 不存在，ADR-40 §7）真实数据
用例整体 skip，自足用例照跑——不假红也不假绿。
"""

import csv
import importlib.util
import json
import pathlib

import pytest

# --------------------------------------------------------------------------
# 定位：块根走包内资源（editable / wheel 都对），仓库根只用来找 赛题/
# --------------------------------------------------------------------------

_包入口 = pathlib.Path(importlib.util.find_spec("jikuai").origin).resolve()
块根 = _包入口.parent / "stdlib" / "blocks"
制造根 = 块根 / "制造"
仓库根 = _包入口.parent.parent.parent          # src/jikuai/__init__.py → 仓库根
数据集 = 仓库根 / "赛题" / "chatbi" / "数据集"

需数据集 = pytest.mark.skipif(
    not 数据集.is_dir(),
    reason="真实数据集 %s 不存在（赛题/ 不进 wheel 与 sdist，ADR-40 §7）" % 数据集)


def _载背衬(块名):
    """加载一个块的 Python 背衬（ADR-16 §3.3）。

    与 `module_loader._load_python_backing` 同样用 `spec_from_file_location`
    隔离加载，不污染 sys.path。直接拿背衬函数做数值断言，是为了让失败信息落在
    口径逻辑上而不是极快求值器里；`.jk` 门面 + 导入路径另有
    `test_块导入路径真能解析` 与各块自己的 `测试.jk` 覆盖。
    """
    路径 = 制造根 / 块名 / (块名 + ".py")
    规格 = importlib.util.spec_from_file_location("w140_背衬_" + 块名, str(路径))
    模块 = importlib.util.module_from_spec(规格)
    规格.loader.exec_module(模块)
    return 模块


计产 = _载背衬("产量汇总").计产
达权 = _载背衬("达成率权重").达权
达均 = _载背衬("达成率均值").达均
读表 = _载背衬("表载入").读表            # 引擎层 W133：CSV → 行的列表
联表 = _载背衬("连接").联表              # 引擎层 W135：等值内连接


def _元数据(块名):
    with (制造根 / 块名 / "块.json").open("r", encoding="utf-8") as f:
        return json.load(f)


# ===========================================================================
# 一、命名预检（不依赖数据集）
# ===========================================================================

def test_导出名词法原子且是单IDENT():
    """三个导出名必须是单 IDENT，否则调用方一侧会被切碎（ADR-15 §3.7）。"""
    from jikuai.pkg.blocks import check_export_atomicity
    for 名 in ("计产", "达权", "达均"):
        原子, 切分 = check_export_atomicity(名)
        assert 原子, "导出名 %s 非词法原子：%s" % (名, 切分)


def test_目录名词法原子性():
    """目录名 = 点分路径段，必须切成恰好 1 个 token。

    反面钉死 `达成率加权`：它切成三段，是死名——这就是本轮把目录名改成
    `达成率权重` 的全部理由。
    """
    from jikuai.pkg.blocks import check_module_segment_atomicity
    for 名 in ("产量汇总", "达成率权重", "达成率均值"):
        原子, 切分 = check_module_segment_atomicity(名)
        assert 原子, "目录名 %s 非词法原子，无法作点分路径段导入：%s" % (名, 切分)

    死名原子, 死名切分 = check_module_segment_atomicity("达成率加权")
    assert not 死名原子, "「达成率加权」竟然成了原子名？那本轮的改名理由要重新评估"
    assert [t for t, _ in 死名切分] == ["IDENT", "VERB", "IDENT"], 死名切分


def test_块导入路径真能解析():
    """三块都要能被 `从 blocks.制造.X 导入 Y。` 导入并调用。

    这是上一条的运行期版本：词法预检过了不等于 module_loader 找得到文件，
    也不等于 ADR-16 §3.3 的背衬注入生效。
    """
    import jikuai
    源码 = (
        "从 blocks.制造.产量汇总 导入 计产。\n"
        "从 blocks.制造.达成率权重 导入 达权。\n"
        "从 blocks.制造.达成率均值 导入 达均。\n"
        '定义 赵表 = 【{"actual_quantity": 90, "planned_quantity": 100,'
        ' "achievement_rate": 0.9, "model_id": "M001"}】。\n'
        '定义 赵维 = 【"model_id"】。\n'
        '定义 赵产 = 计产(赵表, 赵维, "actual_quantity")。\n'
        '定义 赵权 = 达权(赵表, "actual_quantity", "planned_quantity")。\n'
        '定义 赵均 = 达均(赵表, "achievement_rate")。\n'
        "打印 赵产。\n打印 赵权。\n打印 赵均。\n"
    )
    jikuai.run_source(源码)


# ===========================================================================
# 二、口径可审计：两块描述互相点名（W140 DoD 第三条）
# ===========================================================================

def test_两个达成率块的描述互相点名():
    """选块的人在候选列表里就该看见「还有另一个口径」。

    这是 ADR-40 §5.1「禁止只做一个块然后在描述里含糊」的可执行版本，也是
    AGENTS.md 第四节「兄弟能力缺位」的主动解法。
    """
    加权 = _元数据("达成率权重")
    均值 = _元数据("达成率均值")

    assert "达均" in 加权["描述"], "达成率权重 的描述没点名兄弟块导出名 达均"
    assert "达成率均值" in 加权["描述"], "达成率权重 的描述没点名兄弟块 达成率均值"
    assert "达权" in 均值["描述"], "达成率均值 的描述没点名兄弟块导出名 达权"
    assert "达成率权重" in 均值["描述"], "达成率均值 的描述没点名兄弟块 达成率权重"

    # 不只是提一嘴名字，还要说清「两者不等值」
    assert "不等值" in 加权["描述"] and "不等值" in 均值["描述"]
    # 各自自报口径
    assert "加权" in 加权["描述"]
    assert "行级算术平均" in 均值["描述"] and "每条记录等权" in 均值["描述"]


def test_两个达成率块都声明返回小数不是百分数():
    """量纲是这类块最容易被误用的一处：0-1 小数、可以大于 1、不乘 100。"""
    for 块名 in ("达成率权重", "达成率均值"):
        描述 = _元数据(块名)["描述"]
        assert "小数" in 描述 and "百分数" in 描述, 块名
        assert "不乘 100" in 描述, 块名
        assert "0.7619" in 描述 and "1.0385" in 描述, "%s 没写实测值域" % 块名


def test_两个达成率块都声明分母为0报错():
    """分母为 0 的行为必须写进描述，不许只写在实现里。"""
    for 块名 in ("达成率权重", "达成率均值"):
        描述 = _元数据(块名)["描述"]
        assert "分母为 0" in 描述, 块名
        assert "报中文错误" in 描述 or "报错" in 描述, 块名
        assert "绝不静默返回 0" in 描述, 块名


def test_三块描述都自报口径关键词():
    """G22 第 2 条（ADR-40 §6）预演：制造域每块描述至少命中一个口径关键词。"""
    关键词 = ("分母", "汇总", "加权", "现成列", "重算", "窗口")
    for 块名 in ("产量汇总", "达成率权重", "达成率均值"):
        描述 = _元数据(块名)["描述"]
        命中 = [k for k in 关键词 if k in 描述]
        assert 命中, "%s 的描述没有任何口径声明关键词（%s）" % (块名, "/".join(关键词))


def test_三块契约字段齐备且类型标注嵌套写全():
    """G11 名称↔目录一致 + G14 类型标注精度（ADR-26 §4.3，不许停在裸容器）。"""
    from jikuai.pkg.blocks import BlockMetadata, check_type_annotation
    for 块名 in ("产量汇总", "达成率权重", "达成率均值"):
        元 = _元数据(块名)
        assert 元["名称"] == 块名
        assert 元["领域"] == ["制造"]
        assert 元["层级"] == 0
        assert 元["依赖块"] == []
        assert 元["稳定性"] == "experimental"
        问题 = check_type_annotation(
            BlockMetadata(元, str(制造根 / 块名 / "块.json")))
        assert 问题 == [], "%s 类型标注精度不足：%s" % (块名, 问题)


def test_导出名与块json声明一致():
    from jikuai.pkg.blocks import extract_exports
    for 块名, 导出名 in (("产量汇总", "计产"), ("达成率权重", "达权"),
                       ("达成率均值", "达均")):
        assert _元数据(块名)["导出"] == [导出名]
        assert extract_exports(str(制造根 / 块名 / (块名 + ".jk"))) == {导出名}


# ===========================================================================
# 三、反例：口径块该报错时必须报错（不静默给错数）
# ===========================================================================

def test_反例1_拒绝汇总现成比率列():
    """对 achievement_rate / energy_per_vehicle 求和没有业务含义。

    这是本批块相对引擎层 `维聚` 的核心增量：`维聚` 会老老实实把比率列加起来，
    给出一个跑得通、但业务上毫无意义的数。
    """
    表 = [{"model_id": "M001", "achievement_rate": 0.9},
          {"model_id": "M001", "achievement_rate": 1.0}]
    with pytest.raises(ValueError, match="现成比率列"):
        计产(表, ["model_id"], "achievement_rate")

    表二 = [{"line_id": "L002", "energy_per_vehicle": 12.5}]
    with pytest.raises(ValueError, match="现成比率列"):
        计产(表二, ["line_id"], "energy_per_vehicle")


def test_反例2_产量列空值报错而不是跳过或填0():
    表 = [{"model_id": "M001", "actual_quantity": 10},
          {"model_id": "M001", "actual_quantity": None}]
    with pytest.raises(ValueError, match="空值"):
        计产(表, ["model_id"], "actual_quantity")


def test_反例3_达权分母为0报错而不是返回0():
    """计划产量合计为 0：比率无定义。返回 0 会被读成「一台没产出」。"""
    表 = [{"actual_quantity": 5, "planned_quantity": 0},
          {"actual_quantity": 3, "planned_quantity": 0}]
    with pytest.raises(ValueError, match="分母为 0"):
        达权(表, "actual_quantity", "planned_quantity")
    with pytest.raises(ValueError, match="达成率无定义"):
        达权([], "actual_quantity", "planned_quantity")


def test_反例4_达均空表报错而不是返回0():
    """参与行数为 0 → 平均值无定义。与 达权 同一条规则，口径对比才干净。"""
    with pytest.raises(ValueError, match="参与行数为 0"):
        达均([], "achievement_rate")


def test_反例5_达均拒绝疑似百分数():
    """95.5 这种值混进来会把结果抬高两个数量级，且不会报任何错。"""
    with pytest.raises(ValueError, match="疑似把百分数当小数"):
        达均([{"achievement_rate": 95.5}], "achievement_rate")
    with pytest.raises(ValueError, match="不可能为负"):
        达均([{"achievement_rate": -0.5}], "achievement_rate")


def test_反例6_列名不存在报错而不是静默返回空或0():
    表 = [{"model_id": "M001", "actual_quantity": 10}]
    with pytest.raises(ValueError, match="不存在"):
        计产(表, ["model_id"], "没这列")
    with pytest.raises(ValueError, match="不存在"):
        计产(表, ["没这列"], "actual_quantity")
    with pytest.raises(ValueError, match="不存在"):
        达权(表, "actual_quantity", "planned_quantity")
    with pytest.raises(ValueError, match="不存在"):
        达均(表, "achievement_rate")


def test_两个口径在同一份小数据上确实给出不同的数():
    """口径分歧的最小复现（不依赖数据集）。

    两行：产 5 台/计划 10 台（率 0.5）、产 100 台/计划 100 台（率 1.0）。
    加权 = 105/110 ≈ 0.954545；行级平均 = 0.75。差 0.2 以上。
    """
    表 = [{"actual_quantity": 5, "planned_quantity": 10, "achievement_rate": 0.5},
          {"actual_quantity": 100, "planned_quantity": 100, "achievement_rate": 1.0}]
    加权 = 达权(表, "actual_quantity", "planned_quantity")
    均值 = 达均(表, "achievement_rate")
    assert 加权[1:] == (105, 110, 2)
    assert 加权[0] == 105 / 110
    assert 均值 == (0.75, 1.5, 2)
    assert 加权[0] != 均值[0]
    assert round(加权[0] - 均值[0], 6) == 0.204545


# ===========================================================================
# 四、真实数据集：独立参照实现（走裸标准库，一行块代码都不碰）
# ===========================================================================

def _裸读(表名):
    with (数据集 / (表名 + ".csv")).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _裸六月车型产量():
    产 = {}
    for 行 in _裸读("fact_production_actual"):
        if 行["production_date"][:7] == "2026-06":
            产[行["model_id"]] = 产.get(行["model_id"], 0) + int(行["actual_quantity"])
    return sorted(产.items(), key=lambda kv: (-kv[1], kv[0]))


def _裸L003两口径(班次):
    计划量 = {行["plan_id"]: int(行["planned_quantity"])
             for 行 in _裸读("fact_production_plan")}
    行集 = [行 for 行 in _裸读("fact_production_actual")
           if 行["line_id"] == "L003" and 行["shift"] == 班次]
    实际合计 = sum(int(行["actual_quantity"]) for 行 in 行集)
    计划合计 = sum(计划量[行["plan_id"]] for 行 in 行集)
    # 达成率合计用**朴素逐项累加**，与 达均 背衬的 `合计 += 值` 完全一致。
    # 不用内置 sum()：Python 3.12+ 的 sum() 对 float 走 Neumaier 补偿求和，
    # 与朴素累加会差 1 ULP（`...054` vs `...055`）——那是浮点结合律的产物，
    # 不是口径分歧。参照实现要跟被测块同一种累加，交叉验证才是真的等价。
    率合计 = 0.0
    for 行 in 行集:
        率合计 += float(行["achievement_rate"])
    return (实际合计 / 计划合计, 率合计 / len(行集),
            实际合计, 计划合计, len(行集))


def _块载六月产量表():
    """走块链路：表载入 → 只留 2026-06 的行。

    这里的月份筛选故意用 Python 而不是 制造/窗口(截期)：本文件测的是 W140 三块，
    多串一个别的块进来，失败时得先排查那个块。窗口块自己有 W137 的单测。
    """
    实绩 = 读表(str(数据集 / "fact_production_actual.csv"))
    return [行 for 行 in 实绩 if 行["production_date"][:7] == "2026-06"]


# ---------------------------------------------------------------------------
# Q_PUB_001：2026 年 6 月各车型总产量，按产量从高到低
# ---------------------------------------------------------------------------

#: 实测真值（2026-08-18 · fact_production_actual 2896 行 / 6 月 1120 组记录）。
Q_PUB_001_真值 = [
    ("M003", 6376),
    ("M001", 5380),
    ("M002", 4073),
    ("M005", 3714),
    ("M006", 3618),
    ("M004", 2876),
    ("M007", 2459),
    ("M008", 1435),
]

#: dim_model 的中文车型名，只为报告可读；断言一律以 model_id 为准。
Q_PUB_001_车型名 = {
    "M001": "轻卡A型", "M002": "轻卡B型", "M003": "中卡C型", "M004": "中卡D型",
    "M005": "重卡E型", "M006": "重卡F型", "M007": "专用车G型", "M008": "专用车H型",
}


@需数据集
def test_Q_PUB_001_六月各车型总产量降序():
    六月 = _块载六月产量表()
    结果 = 计产(六月, ["model_id"], "actual_quantity")

    实得 = [(行["model_id"], 行["产量"]) for 行 in 结果]
    assert 实得 == Q_PUB_001_真值, "6 月各车型产量表与实测真值不符：%s" % 实得

    # 降序性质单独断言一次（真值表本身已按降序写，这条防的是真值表被人改乱）
    产量序 = [q for _, q in 实得]
    assert 产量序 == sorted(产量序, reverse=True)

    # 8 个车型全在，一个不多一个不少
    assert len(结果) == 8
    assert {行["model_id"] for 行 in 结果} == set(Q_PUB_001_车型名)

    # 记录数守恒：各组记录数之和 = 6 月行数
    assert sum(行["记录数"] for 行 in 结果) == len(六月)
    # 产量守恒：各组产量之和 = 6 月总产量
    assert (sum(行["产量"] for 行 in 结果)
            == sum(行["actual_quantity"] for 行 in 六月))


@需数据集
def test_Q_PUB_001_与裸标准库独立重算一致():
    """独立路径交叉验证：块链路 vs 裸 csv，两条路必须给同一张表。"""
    结果 = 计产(_块载六月产量表(), ["model_id"], "actual_quantity")
    assert [(行["model_id"], 行["产量"]) for 行 in 结果] == _裸六月车型产量()


@需数据集
def test_Q_PUB_001_按车型加班次的复合维度也守恒():
    """顺手压一遍复合维度：车型 × 班次，组数 = 8 车型 × 2 班次。"""
    六月 = _块载六月产量表()
    结果 = 计产(六月, ["model_id", "shift"], "actual_quantity")
    assert len(结果) == 16
    assert {行["shift"] for 行 in 结果} == {"白班", "夜班"}
    assert (sum(行["产量"] for 行 in 结果)
            == sum(行["actual_quantity"] for 行 in 六月))


# ---------------------------------------------------------------------------
# Q_PUB_005：L003 白班 / 夜班平均达成率 —— 两个口径，两个不同的数
# ---------------------------------------------------------------------------

#: 实测真值（2026-08-18）。每班 181 行。
#: 键：班次 → (加权 round6, 均值 round6, 差 round6, 实际合计, 计划合计, 行数)
Q_PUB_005_真值 = {
    "白班": (0.96995, 0.970209, -0.000259, 11717, 12080, 181),
    "夜班": (0.84974, 0.850028, -0.000288, 10462, 12312, 181),
}


def _块载L003(班次):
    """走块链路：表载入 ×2 → 连接(联表) 按 plan_id 1:1 → 筛 L003 + 班次。

    实绩当左表、计划当右表并给前缀 `计划_`：左表列名原样保留，右表五个同名列
    （plan_id/production_date/line_id/model_id/shift）加前缀，`planned_quantity`
    无冲突照原名进来。这样后续筛选与取值都读实绩侧的原始列名。
    """
    实绩 = 读表(str(数据集 / "fact_production_actual.csv"))
    计划 = 读表(str(数据集 / "fact_production_plan.csv"))
    联 = 联表(实绩, 计划, ["plan_id"], ["plan_id"], "计划_")
    # 1:1 关系：连出来的行数必须还是 2896（ADR-40 §4.2）
    assert len(联) == 2896, "plan ⋈ actual 应为 1:1 的 2896 行，实得 %d" % len(联)
    return [行 for 行 in 联
            if 行["line_id"] == "L003" and 行["shift"] == 班次]


@需数据集
@pytest.mark.parametrize("班次", ["白班", "夜班"])
def test_Q_PUB_005_两个口径各出一个数且数值不同(班次):
    真加权, 真均值, 真差, 真实际, 真计划, 真行数 = Q_PUB_005_真值[班次]
    行集 = _块载L003(班次)
    assert len(行集) == 真行数

    加权率, 实际合计, 计划合计, 加权行数 = 达权(
        行集, "actual_quantity", "planned_quantity")
    均值率, 率合计, 均值行数 = 达均(行集, "achievement_rate")

    # 审计痕迹：分子分母是整数，精确相等
    assert (实际合计, 计划合计, 加权行数) == (真实际, 真计划, 真行数)
    assert 均值行数 == 真行数
    # 加权率就是两个整数的商，可以精确断言
    assert 加权率 == 真实际 / 真计划

    # 四个数钉到小数第 6 位
    assert round(加权率, 6) == 真加权
    assert round(均值率, 6) == 真均值

    # 本条测试的全部意义：两个口径不是同一个数
    assert 加权率 != 均值率, (
        "%s 两个口径算出了相同的数——说明有一个块写错了，去查" % 班次)
    assert round(加权率 - 均值率, 6) == 真差
    # 行级平均在本数据集上偏高（小产量班次被抬权）
    assert 均值率 > 加权率
    # 但差异只有万分之几：口径分歧的量级也要钉住，防止哪天实现漂了却还"不相等"
    assert 0 < 均值率 - 加权率 < 0.001

    # 均值口径的分子（达成率合计）与行数相除即为均值，自洽
    assert 均值率 == 率合计 / 真行数


@需数据集
def test_Q_PUB_005_与裸标准库独立重算一致():
    for 班次 in ("白班", "夜班"):
        裸加权, 裸均值, 裸实际, 裸计划, 裸行数 = _裸L003两口径(班次)
        行集 = _块载L003(班次)
        块加权 = 达权(行集, "actual_quantity", "planned_quantity")
        块均值 = 达均(行集, "achievement_rate")
        assert 块加权 == (裸加权, 裸实际, 裸计划, 裸行数), 班次
        assert 块均值[0] == 裸均值, 班次
        assert 块均值[2] == 裸行数, 班次


@需数据集
def test_Q_PUB_005_夜班明显低于白班():
    """赛题 reference_answer：「夜班应明显低于白班」（预置异常之一）。

    两个口径都要给出同一个结论——口径分歧改的是小数第 4 位，不该改变结论方向。
    """
    白加权 = 达权(_块载L003("白班"), "actual_quantity", "planned_quantity")[0]
    夜加权 = 达权(_块载L003("夜班"), "actual_quantity", "planned_quantity")[0]
    白均值 = 达均(_块载L003("白班"), "achievement_rate")[0]
    夜均值 = 达均(_块载L003("夜班"), "achievement_rate")[0]
    assert 夜加权 < 白加权
    assert 夜均值 < 白均值
    assert round(白加权 - 夜加权, 6) == 0.12021
    assert round(白均值 - 夜均值, 6) == 0.120181


# ---------------------------------------------------------------------------
# 复核 W138 给的实测事实（本轮口径的前提，不能只当二手结论采信）
# ---------------------------------------------------------------------------

@需数据集
def test_复核_achievement_rate逐行等于实际除计划且值域是0到1小数():
    """W138 说 `achievement_rate` 逐行等于 actual/planned（容差 1e-4），
    值域 0.7619~1.0385——是 0-1 小数、可以大于 1、不是百分数。这里自己再验一遍。

    这条是 `达均`「用现成列不重算」口径成立的前提：如果现成列跟原始量算出来的
    不一致，两个口径的差就不只是加权与否，那 Q_PUB_005 的解读要全部重写。
    """
    计划量 = {行["plan_id"]: 行["planned_quantity"]
             for 行 in 读表(str(数据集 / "fact_production_plan.csv"))}
    实绩 = 读表(str(数据集 / "fact_production_actual.csv"))
    assert len(实绩) == 2896
    assert len(计划量) == 2896

    超差 = []
    最小 = 最大 = None
    for 行 in 实绩:
        率 = 行["achievement_rate"]
        重算 = 行["actual_quantity"] / 计划量[行["plan_id"]]
        if abs(率 - 重算) > 1e-4:
            超差.append((行["actual_id"], 率, 重算))
        最小 = 率 if 最小 is None else min(最小, 率)
        最大 = 率 if 最大 is None else max(最大, 率)

    assert 超差 == [], "现成列与重算不一致的行：%s" % 超差[:5]
    assert round(最小, 4) == 0.7619
    assert round(最大, 4) == 1.0385
    assert 最大 > 1, "值域上界应大于 1（超产），否则「可以大于 1」这条口径要改"
    assert 最小 > 0


@需数据集
def test_复核_产量表零空值且空值只在fact_orders两列():
    """ADR-40 §4.3。本批三块的「空值一律报错」口径建立在这条之上——若产量表
    真有空值，那口径就得改成显式跳过 + 报出跳过行数。"""
    for 表名 in ("fact_production_actual", "fact_production_plan"):
        表 = 读表(str(数据集 / (表名 + ".csv")))
        空格子 = [(序, 列) for 序, 行 in enumerate(表, 1)
                for 列, 值 in 行.items() if 值 is None]
        assert 空格子 == [], "%s 出现空值：%s" % (表名, 空格子[:5])

    订单 = 读表(str(数据集 / "fact_orders.csv"))
    空列 = {}
    for 行 in 订单:
        for 列, 值 in 行.items():
            if 值 is None:
                空列[列] = 空列.get(列, 0) + 1
    assert 空列 == {"actual_delivery_date": 57, "delay_days": 57}, 空列
