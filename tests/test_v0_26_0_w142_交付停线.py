# -*- coding: utf-8 -*-
"""v0.26.0 W142 · 制造域口径块第三批（订单交付与停线）的 pytest 断言。

四个块（导出名）：
  制造.延期汇总  → 计延（按 delay_days 统计，空值行显式排除并报出排除条数）
  制造.延期排行  → 延榜（按 delay_days 降序取前 N，空值行剔除并报出排除条数）
  制造.停线汇总  → 计停（按维度汇总 downtime_minutes）
  制造.班间对比  → 班比（同一表白班 vs 夜班的度量对比，两组数 + 差值）
                   ※ 目录名 班间对比：WBS 预定名「班次对比」的「次」是循环关键字、
                     非词法原子（班+次+对比 三 token），无法作点分路径段导入，故改名。

`测试.jk` 已在极快侧覆盖语义边界。这里做 `.jk` 里做不动 / 不宜硬编码的事：
1. 把 W142 DoD 的真实数据集数值钉成回归（Q_PUB_006 / 008 / 010）。
2. 钉死 ADR-40 §4.3 的 57 / 977 两个数字，并证明「空 ≠ 0」被如实分离。
3. **空值排除条数被如实报出** —— 每个涉空块的返回元组里都带排除计数。
4. 至少 3 条反例，含「掺一行空值，排除计数应 +1」这类证明它真在数的反例。

数据集缺失时（`赛题/` 不进 wheel 与 sdist，ADR-40 §7）整个模块 skip。
零第三方依赖：只用标准库 + pytest。
"""

import csv
import importlib.util
import pathlib

import pytest

# ---------------------------------------------------------------------------
# 定位仓库根与数据集（同 tests/test_v0_26_0_w139_质量体检.py 的口径）
# ---------------------------------------------------------------------------

_源 = importlib.util.find_spec("jikuai").origin
仓库根 = pathlib.Path(_源).resolve().parent.parent.parent
数据集 = 仓库根 / "赛题" / "chatbi" / "数据集"
块根 = 仓库根 / "src" / "jikuai" / "stdlib" / "blocks" / "制造"

pytestmark = pytest.mark.skipif(
    not 数据集.is_dir(),
    reason="赛题/chatbi/数据集/ 不存在（赛题/ 不进 wheel 与 sdist，ADR-40 §7）",
)


def _载背衬(块名):
    路径 = 块根 / 块名 / (块名 + ".py")
    规格 = importlib.util.spec_from_file_location("w142_" + 块名, 路径)
    模块 = importlib.util.module_from_spec(规格)
    规格.loader.exec_module(模块)
    return 模块


读表 = _载背衬("表载入").读表
计延 = _载背衬("延期汇总").计延
延榜 = _载背衬("延期排行").延榜
计停 = _载背衬("停线汇总").计停
班比 = _载背衬("班间对比").班比


@pytest.fixture(scope="module")
def 订单():
    return 读表(str(数据集 / "fact_orders.csv"))


@pytest.fixture(scope="module")
def 实绩():
    return 读表(str(数据集 / "fact_production_actual.csv"))


# ===========================================================================
# 0. 数据集底数：57 / 977 —— 空 ≠ 0（ADR-40 §4.3）
# ===========================================================================

def test_底数_57个空与977个0是同一列的两个含义(订单):
    """`delay_days` 一列：57 行为空（未交付），977 行为 0（按期交付）。

    这两个数是 W142 立项的地基。用最朴素的方式（不经块）直接数一遍钉住，
    数据集换了会当场红。
    """
    assert len(订单) == 1850
    空 = sum(1 for r in 订单 if r["delay_days"] is None)
    零 = sum(1 for r in 订单
             if r["delay_days"] == 0 and not isinstance(r["delay_days"], bool))
    正 = sum(1 for r in 订单
             if isinstance(r["delay_days"], int)
             and not isinstance(r["delay_days"], bool) and r["delay_days"] > 0)
    assert 空 == 57, "未交付（delay_days 为空）应为 57 行"
    assert 零 == 977, "按期交付（delay_days == 0）应为 977 行"
    assert 正 == 816, "延期（delay_days > 0）应为 816 行"
    assert 空 + 零 + 正 == 1850


def test_底数_57行未交付与生产中完全重合且交付量非零(订单):
    """57 行空值行 == order_status='生产中'，且 delivered_quantity 是**非零**部分交付。

    这条钉死「判交付不能看 delivered_quantity==0」：那 57 行的 delivered_quantity
    最小是 4，一行都不是 0。
    """
    空行 = [r for r in 订单 if r["delay_days"] is None]
    生产中 = [r for r in 订单 if r["order_status"] == "生产中"]
    assert len(空行) == 57
    assert len(生产中) == 57
    assert {r["order_id"] for r in 空行} == {r["order_id"] for r in 生产中}
    # actual_delivery_date 同步为空
    assert all(r["actual_delivery_date"] is None for r in 空行)
    交付量 = [r["delivered_quantity"] for r in 空行]
    assert all(isinstance(v, int) and v > 0 for v in 交付量), \
        "未交付订单的 delivered_quantity 是非零部分交付值"
    assert min(交付量) == 4, "实测最小部分交付量为 4（不是 0，也不是 10）"


# ===========================================================================
# 1. 计延（延期汇总）—— 空值排除且排除条数如实报出
# ===========================================================================

def test_计延_整表_排除57行_分母是1793(订单):
    """整表统计：排除的空值行数 = 57，计入 = 1793，均值分母是 1793 不是 1850。"""
    汇总, 排除, 计入 = 计延(订单, [])
    assert 排除 == 57
    assert 计入 == 1793
    行 = 汇总[0]
    assert 行["订单数"] == 1850
    assert 行["未交付单数"] == 57
    assert 行["按期单数"] == 977        # 977 个 0 落在按期，没被当未交付
    assert 行["延期单数"] == 816
    assert 行["延期天数合计"] == 2213
    assert 行["最长延期天数"] == 19
    # ★ 均值分母是非空行数 1793；填 0 口径会用 1850，两者不等
    assert 行["延期天数均值"] == pytest.approx(2213 / 1793)
    assert 行["延期天数均值"] != pytest.approx(2213 / 1850)


def test_计延_排除计数与未交付单数守恒(订单):
    """元组第 2 项（排除数）必须等于各组 未交付单数 之和，且 = 订单数 − 计入单数。"""
    汇总, 排除, 计入 = 计延(订单, ["model_id"])
    assert 排除 == sum(行["未交付单数"] for 行 in 汇总)
    assert 排除 + 计入 == sum(行["订单数"] for 行 in 汇总)
    assert 排除 == 57


def test_Q_PUB_006_C005六月延期涉及车型是M003(订单):
    """Q_PUB_006：客户 C005 在 6 月（按 order_date）的延期订单只涉及 M003。

    给出明细：窗口内 21 单全是 M003，其中 9 单延期（delay_days>0）、3 单按期、
    9 单未交付（被排除）。延期涉及的车型集合 = {M003}。
    """
    c5_6 = [r for r in 订单
            if r["customer_id"] == "C005"
            and r["order_date"] is not None
            and r["order_date"].startswith("2026-06")]
    assert len(c5_6) == 21
    汇总, 排除, 计入 = 计延(c5_6, ["model_id"])
    # 窗口内只有一个车型组
    assert len(汇总) == 1
    行 = 汇总[0]
    assert 行["model_id"] == "M003"
    assert 行["订单数"] == 21
    assert 行["延期单数"] == 9
    assert 行["按期单数"] == 3
    assert 行["未交付单数"] == 9
    assert 排除 == 9
    assert 行["最长延期天数"] == 19
    # 延期涉及的车型集合（延期单数 > 0 的组）恰是 {M003}
    延期车型 = {行["model_id"] for 行 in 汇总 if 行["延期单数"] > 0}
    assert 延期车型 == {"M003"}


def test_异常_C005的M003订单2026_06集中延期(订单):
    """预置异常（G22 第 1 条 5 个之一）：C005 的 M003 订单 2026-06 集中延期。

    数字证据：C005 全期 M003 延期 21 单、平均 4.06 天、最长 19 天，
    显著高于 C005 其它车型（次高 M006 均值 1.43 天）。
    """
    c5 = [r for r in 订单 if r["customer_id"] == "C005"]
    汇总, _排除, _计入 = 计延(c5, ["model_id"])
    按车型 = {行["model_id"]: 行 for 行 in 汇总}
    m3 = 按车型["M003"]
    assert m3["延期单数"] == 21
    assert m3["最长延期天数"] == 19
    assert m3["延期天数均值"] == pytest.approx(138 / 34)  # 4.0588...
    # M003 的平均延期天数是 C005 全部车型里最高的
    有延期均值 = {k: v["延期天数均值"] for k, v in 按车型.items()
               if v["延期天数均值"] is not None}
    assert max(有延期均值, key=有延期均值.get) == "M003"


def test_计延_反例_掺一行空值排除计数加一(订单):
    """反例（证明它真在数空值）：给整表掺一行 delay_days 为空的订单，排除计数 +1。"""
    _基汇总, 基排除, 基计入 = 计延(订单, [])
    掺 = list(订单) + [dict(订单[0], delay_days=None, order_id="O_FAKE")]
    _汇总, 排除, 计入 = 计延(掺, [])
    assert 排除 == 基排除 + 1        # 57 → 58
    assert 计入 == 基计入            # 计入不变


def test_计延_反例_掺一行0天不进未交付(订单):
    """反例：掺一行 delay_days=0（按期），排除计数不变、按期单数 +1。

    这条与上一条成对：证明块把「空」和「0」分到了不同的计数器。
    """
    _基, 基排除, _基计入 = 计延(订单, [])
    掺 = list(订单) + [dict(订单[0], delay_days=0, order_id="O_ONTIME")]
    汇总, 排除, _计入 = 计延(掺, [])
    assert 排除 == 基排除            # 0 不是空，排除数不变
    assert 汇总[0]["按期单数"] == 977 + 1


def test_计延_反例_空值行被填0会污染均值():
    """反例（对照实验）：手工构造一张表，证明「填 0」与「排除」给出不同的均值。

    3 单：延期 6 天 / 按期 0 天 / 未交付。
      正确（排除未交付）：分母 2，均值 3.0
      污染（未交付填 0）：分母 3，均值 2.0
    块必须给正确口径。
    """
    表 = [
        {"delay_days": 6},
        {"delay_days": 0},
        {"delay_days": None},
    ]
    汇总, 排除, 计入 = 计延(表, [])
    assert 排除 == 1
    assert 计入 == 2
    assert 汇总[0]["延期天数均值"] == 3.0          # 6 / 2，不是 6 / 3 = 2.0
    assert 汇总[0]["延期天数均值"] != 2.0


# ===========================================================================
# 2. 延榜（延期排行）—— Q_PUB_010 + 排除条数
# ===========================================================================

def test_Q_PUB_010_上半年延期前10单_且报出排除的空值行数(订单):
    """Q_PUB_010：上半年（数据集全量即上半年）延期天数前 10 单。

    ★ DoD 硬要求：必须报出被排除的空值行数 = 57。前 10 单降序、稳定次键为原表
    行序；实测第 1 名 O001328(19 天)，第 10 名 O000415(11 天)，10 单全为 M003。
    """
    榜, 排除, 参与 = 延榜(订单, 10)
    assert 排除 == 57          # ← 被排除的空值行数如实报出
    assert 参与 == 1793
    assert 排除 + 参与 == 1850
    assert len(榜) == 10
    assert 榜[0]["order_id"] == "O001328"
    assert 榜[0]["delay_days"] == 19
    assert 榜[9]["order_id"] == "O000415"
    assert 榜[9]["delay_days"] == 11
    # 降序：相邻不递增
    delays = [r["delay_days"] for r in 榜]
    assert delays == sorted(delays, reverse=True)
    # 前 10 单全是 M003（C005 集中延期异常的侧证）
    assert all(r["model_id"] == "M003" for r in 榜)


def test_延榜_稳定次键是原表行序(订单):
    """同延期天数的订单按原表行序排列（稳定排序），结果可复现。"""
    甲, _, _ = 延榜(订单, 30)
    乙, _, _ = 延榜(订单, 30)
    assert [r["order_id"] for r in 甲] == [r["order_id"] for r in 乙]
    # 手工核对 15 天档三单的原表相对顺序（O001126 < O001404 < O001467）
    序 = {r["order_id"]: i for i, r in enumerate(订单)}
    十五天 = [r["order_id"] for r in 甲 if r["delay_days"] == 15]
    assert 十五天 == sorted(十五天, key=lambda oid: 序[oid])


def test_延榜_反例_空值行不进榜也不占名额(订单):
    """反例：延榜取满 1793（参与排序全量）后再取 1850，长度仍是 1793。

    证明 57 个空值行被剔除、不是排到末尾——若是「排末尾」，取 1850 会拿到 1850 行。
    """
    榜, 排除, 参与 = 延榜(订单, 1850)
    assert 排除 == 57
    assert 参与 == 1793
    assert len(榜) == 1793          # 不是 1850
    assert all(r["delay_days"] is not None for r in 榜)


def test_延榜_反例_掺一行空值排除计数加一(订单):
    """反例：掺一行 delay_days 为空，排除计数 +1、参与数不变。"""
    _基榜, 基排除, 基参与 = 延榜(订单, 5)
    掺 = list(订单) + [dict(订单[0], delay_days=None, order_id="O_FAKE")]
    _榜, 排除, 参与 = 延榜(掺, 5)
    assert 排除 == 基排除 + 1
    assert 参与 == 基参与


def test_延榜_负数报错(订单):
    with pytest.raises(ValueError):
        延榜(订单, -1)


# ===========================================================================
# 3. 计停（停线汇总）—— Q_PUB_008 + 128 个零停线 + 0≠缺测
# ===========================================================================

def test_计停_整表_128个零停线_零缺测(实绩):
    """整表：downtime_minutes 有 128 行是 0（真没停线），零缺测（这一列无空值）。"""
    汇总, 排除 = 计停(实绩, [])
    assert 排除 == 0
    行 = 汇总[0]
    assert 行["记录数"] == 2896
    assert 行["计入记录数"] == 2896
    assert 行["缺测记录数"] == 0
    assert 行["零停线记录数"] == 128
    assert 行["停线合计分钟"] == 66951
    assert 行["最长停线分钟"] == 92


def test_Q_PUB_008_四月窗口停线偏高产线是L005(实绩):
    """Q_PUB_008：2026-04-08~04-18 停线时长偏高产线 = L005。

    给出各产线该窗口停线排行 + 窗口外对比。窗口内 L005 合计 1086 分钟居首
    （22 条记录，均值 49.36）；而窗口外 L005 均值仅 19.33 —— L005 的高停线是
    这个窗口内的异常，不是它一贯如此（一贯高的是 L003 ≈ 37）。
    """
    窗 = [r for r in 实绩
          if "2026-04-08" <= r["production_date"] <= "2026-04-18"]
    assert len(窗) == 176
    汇总, 排除 = 计停(窗, ["line_id"])
    assert 排除 == 0
    assert len(汇总) == 8
    按线 = {行["line_id"]: 行 for 行 in 汇总}
    # L005 窗口内合计最高
    最高线 = max(按线, key=lambda k: 按线[k]["停线合计分钟"])
    assert 最高线 == "L005"
    assert 按线["L005"]["停线合计分钟"] == 1086
    assert 按线["L005"]["记录数"] == 22
    assert 按线["L005"]["停线均值分钟"] == pytest.approx(1086 / 22)

    # 窗口外对比：L005 均值大幅回落，证明是窗口内异常
    窗外 = [r for r in 实绩
            if not ("2026-04-08" <= r["production_date"] <= "2026-04-18")]
    汇总外, _ = 计停(窗外, ["line_id"])
    按线外 = {行["line_id"]: 行 for 行 in 汇总外}
    assert 按线外["L005"]["停线均值分钟"] < 20      # 实测 19.33
    assert 按线["L005"]["停线均值分钟"] > 45         # 窗口内 49.36
    # 窗口外一贯最高的是 L003，不是 L005（口径可解释性的证据）
    窗外最高 = max(按线外, key=lambda k: 按线外[k]["停线均值分钟"])
    assert 窗外最高 == "L003"


def test_计停_反例_0与空分开计数():
    """反例：手工表 0 / 空 / 20 三条。0 进分母且计零停线；空排除、计缺测。"""
    表 = [
        {"line_id": "L", "downtime_minutes": 0},
        {"line_id": "L", "downtime_minutes": None},
        {"line_id": "L", "downtime_minutes": 20},
    ]
    汇总, 排除 = 计停(表, ["line_id"])
    行 = 汇总[0]
    assert 排除 == 1
    assert 行["记录数"] == 3
    assert 行["计入记录数"] == 2        # 0 与 20
    assert 行["缺测记录数"] == 1        # 只有 None
    assert 行["零停线记录数"] == 1      # 只有那个 0
    assert 行["停线合计分钟"] == 20
    assert 行["停线均值分钟"] == 10.0   # 20 / 2，分母是计入记录数不是记录数


def test_计停_反例_掺一行空值排除计数加一(实绩):
    """反例：给整表掺一行 downtime_minutes 为空，缺测计数从 0 变 1。"""
    _基, 基排除 = 计停(实绩, [])
    assert 基排除 == 0
    掺 = list(实绩) + [dict(实绩[0], downtime_minutes=None)]
    汇总, 排除 = 计停(掺, [])
    assert 排除 == 1
    assert 汇总[0]["缺测记录数"] == 1


def test_计停_反例_负分钟报错():
    with pytest.raises(ValueError):
        计停([{"line_id": "L", "downtime_minutes": -5}], ["line_id"])


# ===========================================================================
# 4. 班比（班间对比）—— L003 夜班达成率异常 + 空班次报错
# ===========================================================================

def test_异常_L003夜班达成率持续低于白班(实绩):
    """预置异常：L003 夜班达成率持续低于白班（行级均值口径）。

    度量列传 achievement_rate。白班均值 0.9702、夜班均值 0.8500、差值 +0.1202
    （白 − 夜 > 0 即夜班低）。两班各 181 行。
    """
    l3 = [r for r in 实绩 if r["line_id"] == "L003"]
    白, 夜, 差 = 班比(l3, "achievement_rate")
    assert 白["班次"] == "白班" and 夜["班次"] == "夜班"
    assert 白["行数"] == 181 and 夜["行数"] == 181
    assert 白["非空个数"] == 181 and 夜["非空个数"] == 181
    assert 白["均值"] == pytest.approx(0.9702088, abs=1e-6)
    assert 夜["均值"] == pytest.approx(0.8500276, abs=1e-6)
    assert 差 == pytest.approx(0.1201812, abs=1e-6)
    assert 差 > 0        # 白 − 夜 > 0 = 夜班持续低于白班


def test_班比_同块换度量列_停线夜班更长(实绩):
    """同一个通用块换度量列：L003 夜班停线分钟远高于白班（差值为负）。

    印证「夜班达成率低」与「夜班停线长」是同一现象的两面，且证明 班比 是通用块
    ——度量列由调用方传，块本身不认识指标。
    """
    l3 = [r for r in 实绩 if r["line_id"] == "L003"]
    白, 夜, 差 = 班比(l3, "downtime_minutes")
    assert 白["均值"] < 夜["均值"]
    assert 差 < 0        # 白 − 夜 < 0 = 夜班停线更长


def test_班比_跨产线传全表得到的是另一个问题的答案(实绩):
    """本块不筛产线：全表传进来得到的是跨产线两组数（行数 = 全表两班分布）。

    这条不是 bug，是提醒——它证明「同产线对比」必须调用方先 制造.选取 筛产线。
    """
    白, 夜, 差 = 班比(实绩, "achievement_rate")
    assert 白["行数"] + 夜["行数"] == 2896     # 全表，不是单产线的 362
    assert 白["行数"] == 1448 and 夜["行数"] == 1448


def test_班比_反例_某班次一行都没有报错():
    """反例（本块核心决议）：单班表报错，不返回空差值。"""
    with pytest.raises(ValueError, match="一行都没有"):
        班比([{"shift": "白班", "v": 1}, {"shift": "白班", "v": 2}], "v")


def test_班比_反例_有记录但度量全空返回空而不报错():
    """对照上一条：两班都有记录、白班度量全空 → 不报错，返回 非空个数=0/均值空/差值空。

    这正是「空班次报错」要与之区分的情形——两者绝不能并成同一个 空。
    """
    表 = [
        {"shift": "白班", "v": None},
        {"shift": "白班", "v": None},
        {"shift": "夜班", "v": 5},
    ]
    白, 夜, 差 = 班比(表, "v")
    assert 白["行数"] == 2
    assert 白["非空个数"] == 0
    assert 白["均值"] is None
    assert 夜["均值"] == 5.0
    assert 差 is None


def test_班比_反例_第三种班次值报错():
    with pytest.raises(ValueError):
        班比([{"shift": "白班", "v": 1},
              {"shift": "夜班", "v": 2},
              {"shift": "中班", "v": 3}], "v")


def test_班比_空表报错():
    with pytest.raises(ValueError):
        班比([], "v")


# ===========================================================================
# 5. 契约：块目录名 / 导出名 / 描述口径关键词
# ===========================================================================

@pytest.mark.parametrize("目录,导出,关键词", [
    ("延期汇总", "计延", "汇总"),
    ("延期排行", "延榜", "排"),
    ("停线汇总", "计停", "汇总"),
    ("班间对比", "班比", "对比"),
])
def test_块元数据_导出名与口径关键词(目录, 导出, 关键词):
    """每块 块.json：导出名正确，且描述含 G22 第 2 条要求的口径声明关键词。"""
    import json
    with open(块根 / 目录 / "块.json", encoding="utf-8") as f:
        元 = json.load(f)
    assert 元["导出"] == [导出]
    assert 元["领域"] == ["制造"]
    assert 元["层级"] == 0
    # G22 第 2 条：分母/汇总/加权/现成列/重算/窗口 至少一项
    命中 = [k for k in ("分母", "汇总", "加权", "现成列", "重算", "窗口")
           if k in 元["描述"]]
    assert 命中, "描述缺口径声明关键词（G22 第 2 条）"
    # 涉空块必须写明空值处理口径
    assert "空值" in 元["描述"] or "空" in 元["描述"]


def test_班间对比_目录名词法原子而班次对比不是():
    """回归：记录「班次对比」目录名不可用、改名「班间对比」这一偏离决策。

    直接调用契约校验器，钉死两件事：预定名会被门禁判死、改名后合法。
    """
    import sys
    sys.path.insert(0, str(仓库根 / "src"))
    from jikuai.pkg.blocks import check_module_segment_atomicity as 原子
    好, _ = 原子("班间对比")
    坏, 碎片 = 原子("班次对比")
    assert 好 is True
    assert 坏 is False      # 次 是 KEYWORD，被切成多 token
    assert len(碎片) >= 2
