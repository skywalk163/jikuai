# -*- coding: utf-8 -*-
"""v0.28.0 W170 · 语义层「默认时间锚点列」（BACKLOG §12.3 缺口 5）。

缺口原文：`fact_orders` 有 `order_date` / `planned_delivery_date` /
`actual_delivery_date` 三个候选，选哪个直接改变结论；本轮之前两个口径都算了、
结论没翻转，**但那是运气**。

落地：语义层每条业务词加 `时间锚点`，值形如 `表.列` 或 `null`。取值**机械可复算**：
等于本条 `表` 的规范时间列；只有 `fact_orders`（三列竞争）与 `dim_*`（无时间维）
给 null。**加字段不等于替业务拍板** —— 有唯一答案的固定下来，没有唯一答案的显式
标 null，并在 `口径备注` 里写清三个候选各是什么、为什么不给默认值。

本文件三段：
1. 正例 —— 42 条键全在、值形状合法、机械规则可复算、非 null 的列真实存在。
2. 反例 —— 按仓库纪律「只测正例等于没有门禁」，用篡改后的语义层副本证明 G22
   断言 4 的**每一类**新错都被抓到（缺键 / 形状错 / 跨表锚点 / 列不存在）。
3. DoD 记档 —— Q_PUB_006 在三个锚点下的实测数字，证明「结论没翻转是运气」。

数据集不在场时，需要读 CSV 的用例单独 skip（不整份 skip：前两段有一半用例
只读语义层 JSON，没有数据集也该跑）。
"""

import csv
import importlib.util
import json
import pathlib
import sys

import pytest

仓库根 = pathlib.Path(__file__).resolve().parent.parent
语义层路径 = (仓库根 / "src" / "jikuai" / "stdlib" / "blocks" / "制造"
              / "语义层.json")
数据集 = 仓库根 / "赛题" / "chatbi" / "数据集"
桥目录 = 仓库根 / "tools" / "ai-bridge"

需数据集 = pytest.mark.skipif(
    not 数据集.is_dir(), reason="赛题数据集不在（不随包发行），跳过")


def _载G22():
    规格 = importlib.util.spec_from_file_location(
        "check_manufacturing_contract",
        仓库根 / "scripts" / "check_manufacturing_contract.py")
    模块 = importlib.util.module_from_spec(规格)
    规格.loader.exec_module(模块)
    return 模块


G22 = _载G22()

if str(桥目录) not in sys.path:
    sys.path.insert(0, str(桥目录))
import planner  # noqa: E402  （要先把 tools/ai-bridge 挂上 sys.path）


#: 机械规则的真源：表 → 该表的规范时间列。`fact_orders` 与 `dim_*` 落 None。
#: 这张表**不是抄语义层抄来的**，是按 `赛题/chatbi/数据集/` 八张 CSV 里含 date
#: 的列实测出来的（见 test_锚点规则与真实表头的日期列对得上）。
规范时间列 = {
    "fact_production_actual": "production_date",
    "fact_production_plan": "production_date",
    "fact_energy_usage": "usage_date",
    "fact_quality_defects": "defect_date",
    "fact_orders": None,          # order_date / planned_ / actual_ 三选一
}


def _语义层():
    return json.loads(语义层路径.read_text(encoding="utf-8"))


def _条目():
    return _语义层()["条目"]


def _读表头(表名):
    with (数据集 / (表名 + ".csv")).open(
            "r", encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f))


def _取条(业务词):
    for 条 in _条目():
        if 条.get("业务词") == 业务词:
            return 条
    raise AssertionError("语义层里没有业务词 %r" % 业务词)


# ---------------------------------------------------------------------------
# 一、正例
# ---------------------------------------------------------------------------

def test_每条都有时间锚点键():
    """键**必须**每条都在。缺键与 null 不等价：null 是表态，缺键是漏写。"""
    缺 = [条.get("业务词") for 条 in _条目() if "时间锚点" not in 条]
    assert 缺 == [], "这些条目缺 `时间锚点` 键：%s" % 缺


def test_时间锚点的值只能是表点列或null():
    for 条 in _条目():
        锚 = 条.get("时间锚点")
        if 锚 is None:
            continue
        assert isinstance(锚, str) and 锚.count(".") == 1, (
            "%s 的 时间锚点 形状不合法：%r" % (条.get("业务词"), 锚))


def test_锚点的表那一段必须与本条的表一致():
    """跨表锚点会把「按 6 月筛」筛到另一张表的日期上，而且一行不报错。"""
    for 条 in _条目():
        锚 = 条.get("时间锚点")
        if not 锚:
            continue
        assert 锚.split(".")[0] == 条["表"], (
            "%s 的锚点 %s 与本条 表 %s 不同表" % (条.get("业务词"), 锚, 条["表"]))


def test_取值可用机械规则逐条复算():
    """42 条一条不例外：锚点 == 规范时间列[本条表]。

    这条断言的价值是**防后人手改成"看着合理"的值**——一旦有人给 `订单量` 补个
    `fact_orders.order_date`，这里立刻红，而不是等到某次演示答错才发现。
    """
    for 条 in _条目():
        表 = 条["表"]
        列 = (规范时间列[表] if 表 in 规范时间列 else None)
        期望 = "%s.%s" % (表, 列) if 列 else None
        assert 条.get("时间锚点") == 期望, (
            "%s（表 %s）锚点应为 %r，实际 %r"
            % (条.get("业务词"), 表, 期望, 条.get("时间锚点")))


def test_订单侧七条全是null_且订单日期那条点名三个候选():
    """WBS 明确要求：`订单日期` 那条给 null，`口径备注` 保留「不替使用者选」。"""
    订单条 = [条 for 条 in _条目() if 条["表"] == "fact_orders"]
    assert len(订单条) == 7, [条["业务词"] for 条 in 订单条]
    assert all(条["时间锚点"] is None for 条 in 订单条), (
        "订单侧不该有默认锚点：%s"
        % [(条["业务词"], 条["时间锚点"]) for 条 in 订单条])

    备注 = _取条("订单日期")["口径备注"]
    for 候选列 in ("order_date", "planned_delivery_date",
                   "actual_delivery_date"):
        assert 候选列 in 备注, "口径备注没点名候选列 %s" % 候选列
    assert "不替使用者选" in 备注, "「语义层不替使用者选」这句不许丢"
    assert "Q_PUB_006" in 备注, "为什么不给默认值，要拿实测那道题说话"


def test_维表条目一律没有时间锚点():
    维条 = [条 for 条 in _条目() if 条["表"].startswith("dim_")]
    assert len(维条) == 16, [条["业务词"] for 条 in 维条]
    assert all(条["时间锚点"] is None for 条 in 维条)


def test_约定块解释了新键():
    约定 = _语义层()["约定"]
    assert "时间锚点" in 约定, "加了键就要在 `约定` 里加说明"
    说明 = 约定["时间锚点"]
    for 要点 in ("表.列", "null", "fact_orders", "不替业务拍板"):
        assert 要点 in 说明, "`约定.时间锚点` 少讲了 %s" % 要点


@需数据集
def test_锚点规则与真实表头的日期列对得上():
    """`规范时间列` 那张表不是拍脑袋写的——按真实表头复核一遍。

    判据：一张表恰好一个含 `date` 的列时，规范时间列就是它；多于一个（fact_orders
    三个）则 None；一个都没有（dim_*）也 None。
    """
    for 表, 期望 in 规范时间列.items():
        日期列 = [c for c in _读表头(表) if "date" in c.lower()]
        if 期望 is None:
            assert len(日期列) != 1, (
                "%s 只有一个日期列 %s，那它就该有默认锚点而不是 None"
                % (表, 日期列))
        else:
            assert 日期列 == [期望], (
                "%s 的日期列实测是 %s，与规则里的 %r 不符" % (表, 日期列, 期望))


@需数据集
def test_G22断言4在真仓库上通过且报数带锚点():
    问题, 通过, 跳过 = G22.校验语义层(语义层路径, 数据集)
    assert 问题 == [], 问题
    assert 跳过 is None
    assert "时间锚点" in 通过, "报数里要能看出锚点被查过：%r" % 通过
    assert "42 条键全在" in 通过, 通过
    assert "19 条非 null" in 通过, ("非 null 条数变了就该有人来改这个数：%r" % 通过)


# ---------------------------------------------------------------------------
# 二、反例：证明 G22 断言 4 真抓得到（「守卫绿≠守卫在守」，v0.22 教训）
# ---------------------------------------------------------------------------

def _造语义层(tmp_path, 改):
    """把真语义层拷进 tmp_path，用 `改(条目列表)` 定点破坏后落盘。"""
    数据 = _语义层()
    改(数据["条目"])
    路径 = tmp_path / "语义层.json"
    路径.write_text(json.dumps(数据, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return 路径


@需数据集
def test_反例_缺时间锚点键会红(tmp_path):
    def 改(条目):
        del 条目[0]["时间锚点"]

    问题, _, _ = G22.校验语义层(_造语义层(tmp_path, 改), 数据集)
    合 = "\n".join(问题)
    assert "缺 `时间锚点` 键" in 合, 问题
    assert "null 与缺键不等价" in 合, "错误文案要讲清为什么，不然改的人会随手填 null"


@需数据集
def test_反例_锚点形状不是表点列会红(tmp_path):
    def 改(条目):
        条目[0]["时间锚点"] = "production_date"      # 少了 `表.`

    问题, _, _ = G22.校验语义层(_造语义层(tmp_path, 改), 数据集)
    assert any("必须是 `表.列` 形状" in 条 for 条 in 问题), 问题


@需数据集
def test_反例_跨表锚点会红(tmp_path):
    """本条表是 fact_production_actual，锚点却指到能耗表——最阴的一类错。"""
    def 改(条目):
        条目[0]["时间锚点"] = "fact_energy_usage.usage_date"

    问题, _, _ = G22.校验语义层(_造语义层(tmp_path, 改), 数据集)
    合 = "\n".join(问题)
    assert "不同表" in 合, 问题
    assert "且不报错" in 合, "要讲明危害：它不会崩，只会算错"


@需数据集
def test_反例_锚点列在CSV里不存在会红(tmp_path):
    def 改(条目):
        条目[0]["时间锚点"] = "fact_production_actual.produce_date"   # 拼错

    问题, _, _ = G22.校验语义层(_造语义层(tmp_path, 改), 数据集)
    assert any("没有列" in 条 and "produce_date" in 条 for 条 in 问题), 问题


@需数据集
def test_反例_给订单侧硬塞默认锚点不会被G22拦_只有测试拦得住(tmp_path):
    """★ 如实记一处**门禁盲区**。

    给 `订单日期` 填 `fact_orders.order_date` 是「形状对、同表、列真实存在」的，
    G22 断言 4 **抓不到**——它校验的是「锚点是否指向真实列」，不是「该不该有锚点」。
    挡住这类改动的是上面 `test_取值可用机械规则逐条复算`。写下这条是为了别让人
    以为「G22 绿了就说明没人偷偷替业务拍板」。
    """
    def 改(条目):
        for 条 in 条目:
            if 条["业务词"] == "订单日期":
                条["时间锚点"] = "fact_orders.order_date"

    问题, 通过, _ = G22.校验语义层(_造语义层(tmp_path, 改), 数据集)
    assert 问题 == [], "这类改动 G22 确实拦不住，这条断言就是记录这个事实"
    assert 通过 and "20 条非 null" in 通过, (
        "至少非 null 条数会变，报数里看得见：%r" % 通过)


# ---------------------------------------------------------------------------
# 三、锚点进上下文包（DoD「上下文包里有明确默认值」）
# ---------------------------------------------------------------------------

def test_非null锚点会缀进口径说明():
    命中 = planner.语义命中("2026年6月各车型的总产量")
    产量 = [条 for 条 in 命中 if 条["业务词"] == "产量"]
    assert 产量, [条["业务词"] for 条 in 命中]
    说明 = 产量[0]["口径说明"]
    assert "【默认时间锚点：fact_production_actual.production_date】" in 说明
    assert "求和" in 说明, "原来的口径备注不能被挤掉，只是在末尾追加"


def test_null锚点不缀废话():
    """null 的那些靠 `口径备注` 讲「为什么不给默认值」，再缀一句「没有」是废话。"""
    命中 = planner.语义命中("6月的下单日期分布")
    订单日期 = [条 for 条 in 命中 if 条["业务词"] == "订单日期"]
    assert 订单日期, [条["业务词"] for 条 in 命中]
    说明 = 订单日期[0]["口径说明"]
    assert "默认时间锚点" not in 说明, 说明
    assert "不替使用者选" in 说明, "该讲的还是要在包里看得到"


def test_时间锚点没进协议字段集():
    """它刻意**不是**协议字段：ADR-41 §3 冻结集与 G23 断言 1a 一行未动。

    这条断言在这里，是为了让「以后想把它升成协议字段」的人先看见这个决定。
    """
    from jikuai.service import schema
    assert "时间锚点" not in schema.SEMANTIC_HIT_REQUIRED
    assert "时间锚点" not in schema.SEMANTIC_HIT_OPTIONAL
    assert schema.SEMANTIC_HIT_REQUIRED == ("业务词", "表", "字段", "口径说明")


# ---------------------------------------------------------------------------
# 四、DoD 记档：Q_PUB_006 三个锚点各是什么数（证明「没翻转是运气」）
# ---------------------------------------------------------------------------

@需数据集
def test_Q_PUB_006三个锚点下延期单数不同但结论都是M003():
    """实测（2026-08-20，客户 C005 共 149 单）：

    锚点                      6 月订单   延期单   延期车型分布
    order_date                  21        6      M003 6
    planned_delivery_date       30       10      M003 8 / M006 1 / M001 1
    actual_delivery_date        25        5      M003 3 / M006 1 / M001 1

    三个口径下「主要涉及 M003」都成立，所以 reference 答案没被推翻 —— **但优势从
    6:0 缩到 3:1:1**，换个客户就可能翻转。这正是「本轮两个口径都算了、结论没翻转，
    但那是运气」的实证，也是 `订单日期` 的 `时间锚点` 必须留 null 的理由。
    """
    行 = list(csv.DictReader(
        (数据集 / "fact_orders.csv").open(
            "r", encoding="utf-8-sig", newline="")))
    c5 = [r for r in 行 if r["customer_id"] == "C005"]
    assert len(c5) == 149

    期望 = {
        "order_date": (21, 6, {"M003": 6}),
        "planned_delivery_date": (30, 10, {"M003": 8, "M006": 1, "M001": 1}),
        "actual_delivery_date": (25, 5, {"M003": 3, "M006": 1, "M001": 1}),
    }
    for 列, (单数, 延数, 车型分布) in 期望.items():
        窗内 = [r for r in c5 if (r[列] or "").startswith("2026-06")]
        延 = [r for r in 窗内 if r["order_status"] == "延期交付"]
        实分布 = {}
        for r in 延:
            实分布[r["model_id"]] = 实分布.get(r["model_id"], 0) + 1
        assert (len(窗内), len(延), 实分布) == (单数, 延数, 车型分布), 列
        assert max(实分布, key=实分布.get) == "M003", (
            "%s 口径下榜首不再是 M003 —— 结论真翻转了，docstring 那段要重写" % 列)

    # 实交口径会整体漏掉未交付订单：该列在 C005 里有 9 个空值。
    空 = [r for r in c5 if not (r["actual_delivery_date"] or "").strip()]
    assert len(空) == 9
    assert all(r["order_status"] == "生产中" for r in 空), (
        "空 actual_delivery_date 应与 order_status=生产中 完全重合")
