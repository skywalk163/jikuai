# -*- coding: utf-8 -*-
"""v0.26.0 W138 · 制造域语义层（中文业务词 → 真实 表.字段）的门禁测试。

语义层落点：`src/jikuai/stdlib/blocks/制造/语义层.json`。它**不是块**——没有
`块.json`，`find_block_files` 只认 `块.json` / `*.块.json` 两种名字，所以它既不
进块索引（G11 实测仍 124 个块、索引零变化），也不会被 `jk 块 选` 返回。

正例：真实数据集（`赛题/chatbi/数据集/`）逐条核对表头。
反例：仓库纪律「只测正例等于没有门禁」（v0.19.0 W62 起）——校验逻辑抽成
`校验语义层()` 函数，再用**篡改后的内存副本**逐一证明每类错都被抓到：
表不存在 / 列不存在 / 业务词重复 / 同义词撞业务词 / 现成比率列漏标或误标 /
派生条目的分母指向不存在的列。

数据集不在时整份 skip（同 W130 测试的处置）。
"""

import copy
import csv
import importlib.util
import json
import pathlib

import pytest

# --- 定位 -------------------------------------------------------------------
# 语义层是包内 stdlib 资源，走 `jikuai` 包定位（find_spec 不执行模块），
# 这样 wheel 装法下也指得对。
_规格 = importlib.util.find_spec("jikuai")
包目录 = pathlib.Path(_规格.origin).resolve().parent
语义层路径 = 包目录 / "stdlib" / "blocks" / "制造" / "语义层.json"

# 赛题数据集**不进 wheel**（MANIFEST.in 只收 src/jikuai/stdlib），只能按仓库树找。
仓库根 = pathlib.Path(__file__).resolve().parent.parent
数据集 = 仓库根 / "赛题" / "chatbi" / "数据集"

# W138 任务书点名必须覆盖的 20 个业务词
必覆盖 = [
    "产量", "计划产量", "达成率", "缺陷数", "缺陷率",
    "电耗", "单车电耗", "水耗", "气耗",
    "停线时长", "工作时长", "延期天数", "订单量", "交付量",
    "车型", "产线", "车间", "班次", "客户", "区域",
]

# 实测（2026-08-18，读真实表头）：全数据集只有这两列是**已经算好的比率**。
# fact_quality_defects 没有 defect_rate —— 详见 test_现成比率列实测只有两个。
现成比率列实况 = {
    ("fact_production_actual", "achievement_rate"),
    ("fact_energy_usage", "energy_per_vehicle"),
}

pytestmark = pytest.mark.skipif(
    not 数据集.is_dir(), reason="赛题数据集不在（不随包发行），跳过")


# --- 工具 -------------------------------------------------------------------

def 读表头(表名: str):
    """读一张表的真实表头。`utf-8-sig` 是必需的：8 张表都带 BOM，用 utf-8 读
    会让第一个列名变成 `\\ufeff…`，列存在性检查就会假红（W130 已钉过这一点）。"""
    路径 = 数据集 / (表名 + ".csv")
    if not 路径.is_file():
        return None
    with 路径.open("r", encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f))


def 载入语义层() -> dict:
    with 语义层路径.open("r", encoding="utf-8") as f:
        return json.load(f)


def 校验语义层(数据: dict):
    """返回问题描述列表（空 = 全过）。反例测试靠篡改副本再调它来证明抓得到。"""
    问题 = []
    条目 = 数据.get("条目")
    if not isinstance(条目, list) or not 条目:
        return ["顶层缺少非空的「条目」列表"]

    表头缓存 = {}

    def 取表头(表名):
        if 表名 not in 表头缓存:
            表头缓存[表名] = 读表头(表名)
        return 表头缓存[表名]

    业务词集 = set()
    for i, 条 in enumerate(条目):
        词 = 条.get("业务词")
        位置 = "第 %d 条（%s）" % (i + 1, 词)
        for 必填 in ("业务词", "表", "字段", "口径备注", "现成比率列", "派生",
                     "时间锚点"):
            if 必填 not in 条:
                问题.append("%s 缺字段「%s」" % (位置, 必填))
        if not isinstance(词, str) or not 词.strip():
            问题.append("%s 业务词必须是非空字符串" % 位置)
            continue
        if 词 in 业务词集:
            问题.append("%s 业务词重复" % 位置)
        业务词集.add(词)

        # 单位约定：有量纲写非空串，无量纲写 JSON null。不许空串。
        单位 = 条.get("单位", "缺")
        if 单位 is not None and (not isinstance(单位, str) or not 单位.strip()):
            问题.append("%s 单位既不是 null 也不是非空字符串：%r" % (位置, 单位))

        表名, 列名 = 条.get("表"), 条.get("字段")
        表头 = 取表头(表名) if isinstance(表名, str) else None
        if 表头 is None:
            问题.append("%s 表不存在：%s" % (位置, 表名))
        elif 列名 not in 表头:
            问题.append("%s 列不存在：%s.%s" % (位置, 表名, 列名))

        比率 = 条.get("现成比率列")
        派生 = 条.get("派生")
        if not isinstance(比率, bool):
            问题.append("%s 现成比率列必须是布尔" % 位置)
        if not isinstance(派生, bool):
            问题.append("%s 派生必须是布尔" % 位置)
        if 比率 is True and (表名, 列名) not in 现成比率列实况:
            问题.append("%s 误标现成比率列：%s.%s 在真实表里不是算好的比率"
                        % (位置, 表名, 列名))
        if 比率 is False and (表名, 列名) in 现成比率列实况 and 派生 is False:
            问题.append("%s 漏标现成比率列：%s.%s 是算好的比率"
                        % (位置, 表名, 列名))
        if 派生 is True and 比率 is True:
            问题.append("%s 派生条目不该同时是现成比率列" % 位置)

        分母 = 条.get("分母")
        if 派生 is True:
            if not isinstance(分母, dict):
                问题.append("%s 派生条目必须给「分母」对象" % 位置)
            else:
                分母表, 分母列 = 分母.get("表"), 分母.get("字段")
                分母表头 = 取表头(分母表) if isinstance(分母表, str) else None
                if 分母表头 is None:
                    问题.append("%s 分母表不存在：%s" % (位置, 分母表))
                elif 分母列 not in 分母表头:
                    问题.append("%s 分母列不存在：%s.%s" % (位置, 分母表, 分母列))
        elif 分母 is not None:
            问题.append("%s 非派生条目的分母必须是 null" % 位置)

    # 同义词：不得与任何业务词字面相同，也不得跨条重复
    见过同义词 = {}
    for 条 in 条目:
        for 同 in 条.get("同义词") or []:
            if 同 in 业务词集:
                问题.append("同义词「%s」（属于 %s）与业务词字面冲突"
                            % (同, 条.get("业务词")))
            if 同 in 见过同义词:
                问题.append("同义词「%s」在 %s 与 %s 两条里重复"
                            % (同, 见过同义词[同], 条.get("业务词")))
            见过同义词[同] = 条.get("业务词")
    return 问题


# --- 正例 -------------------------------------------------------------------

def test_a_语义层文件可解析():
    assert 语义层路径.is_file(), "语义层文件不存在：%s" % 语义层路径
    数据 = 载入语义层()
    assert isinstance(数据, dict)
    assert 数据["领域"] == "制造"
    assert isinstance(数据["条目"], list)


def test_b_业务词不少于20条():
    条目 = 载入语义层()["条目"]
    assert len(条目) >= 20, "只有 %d 条，DoD 要求 ≥20" % len(条目)


def test_b2_任务书点名的20个业务词全覆盖():
    词集 = {条["业务词"] for 条 in 载入语义层()["条目"]}
    缺 = [w for w in 必覆盖 if w not in 词集]
    assert not 缺, "漏收业务词：%s" % 缺


def test_c_每条的表字段都在真实CSV表头里():
    数据 = 载入语义层()
    问题 = [p for p in 校验语义层(数据) if "不存在" in p]
    assert not 问题, "语义层指向了不存在的表或列：\n" + "\n".join(问题)


def test_d_业务词全局唯一且同义词不与业务词冲突():
    条目 = 载入语义层()["条目"]
    词表 = [条["业务词"] for 条 in 条目]
    assert len(词表) == len(set(词表)), "业务词有重复：%s" % [
        w for w in set(词表) if 词表.count(w) > 1]
    问题 = [p for p in 校验语义层(载入语义层()) if "冲突" in p or "重复" in p]
    assert not 问题, "\n".join(问题)


def test_e_现成比率列被正确打标():
    条目 = 载入语义层()["条目"]
    打标 = {(条["表"], 条["字段"]) for 条 in 条目 if 条["现成比率列"]}
    assert 打标 == 现成比率列实况, "打标集合与实况不符：%s" % 打标
    # 反向：其余条目一个都不许标 true（已由集合相等保证），且这两条各自的
    # 口径备注要写明「现成」，否则 W140 的口径块读不到警示
    for 条 in 条目:
        if 条["现成比率列"]:
            assert "现成" in 条["口径备注"], 条["业务词"]


def test_e2_现成比率列实测只有两个_没有defect_rate():
    """WBS W138 与 ADR-40 §5 导语都说「三个现成比率列」，实况只有两个。

    真实表头是唯一判据：fact_quality_defects 没有 defect_rate，也没有任何
    以 _rate 结尾或 per_ 形态的列。ADR-40 §5.3 自己写的是「无现成列」，
    所以 §5 那句「三个」是导语计数错，不是数据集换过。
    """
    全部列 = {}
    for f in sorted(数据集.glob("*.csv")):
        全部列[f.stem] = 读表头(f.stem)

    assert "defect_rate" not in 全部列["fact_quality_defects"], \
        "fact_quality_defects 表头：%s" % 全部列["fact_quality_defects"]

    疑似比率列 = {(表, 列) for 表, 列组 in 全部列.items() for 列 in 列组
                  if 列.endswith("_rate") or "_per_" in 列}
    assert 疑似比率列 == 现成比率列实况, "疑似比率列集合：%s" % 疑似比率列


def test_f_派生条目的约定自洽():
    """约定：派生条目 `字段` = 分子列，`分母` = {表, 字段, 说明}，现成比率列恒 false。"""
    条目 = 载入语义层()["条目"]
    派生条 = [条 for 条 in 条目 if 条["派生"]]
    assert 派生条, "至少「缺陷率」应当是派生条目"
    assert {条["业务词"] for 条 in 派生条} >= {"缺陷率"}
    for 条 in 派生条:
        assert 条["现成比率列"] is False, 条["业务词"]
        assert isinstance(条["分母"], dict), 条["业务词"]
        assert 条["分母"]["表"] and 条["分母"]["字段"]
    for 条 in 条目:
        if not 条["派生"]:
            assert 条["分母"] is None, 条["业务词"]


def test_g_整份语义层零问题():
    问题 = 校验语义层(载入语义层())
    assert 问题 == [], "语义层校验不通过：\n" + "\n".join(问题)


# --- 反例：证明校验真的在守 -------------------------------------------------

def test_反例_列不存在被抓():
    数据 = copy.deepcopy(载入语义层())
    数据["条目"][0]["字段"] = "根本没有这一列"
    assert any("列不存在" in p for p in 校验语义层(数据))


def test_反例_表不存在被抓():
    数据 = copy.deepcopy(载入语义层())
    数据["条目"][0]["表"] = "fact_不存在的表"
    assert any("表不存在" in p for p in 校验语义层(数据))


def test_反例_业务词重复被抓():
    数据 = copy.deepcopy(载入语义层())
    数据["条目"].append(copy.deepcopy(数据["条目"][0]))
    assert any("业务词重复" in p for p in 校验语义层(数据))


def test_反例_同义词撞业务词被抓():
    数据 = copy.deepcopy(载入语义层())
    数据["条目"][1].setdefault("同义词", []).append("产量")
    assert any("与业务词字面冲突" in p for p in 校验语义层(数据))


def test_反例_漏标现成比率列被抓():
    数据 = copy.deepcopy(载入语义层())
    for 条 in 数据["条目"]:
        if 条["业务词"] == "达成率":
            条["现成比率列"] = False
    assert any("漏标现成比率列" in p for p in 校验语义层(数据))


def test_反例_误标现成比率列被抓():
    数据 = copy.deepcopy(载入语义层())
    for 条 in 数据["条目"]:
        if 条["业务词"] == "产量":
            条["现成比率列"] = True
    assert any("误标现成比率列" in p for p in 校验语义层(数据))


def test_反例_派生条目分母指向不存在的列被抓():
    数据 = copy.deepcopy(载入语义层())
    for 条 in 数据["条目"]:
        if 条["派生"]:
            条["分母"]["字段"] = "没有这列"
    assert any("分母列不存在" in p for p in 校验语义层(数据))


def test_反例_单位写成空串被抓():
    数据 = copy.deepcopy(载入语义层())
    数据["条目"][0]["单位"] = ""
    assert any("单位既不是 null" in p for p in 校验语义层(数据))


def test_反例_非派生条目挂了分母被抓():
    数据 = copy.deepcopy(载入语义层())
    数据["条目"][0]["分母"] = {"表": "fact_production_plan", "字段": "planned_quantity"}
    assert any("非派生条目的分母必须是 null" in p for p in 校验语义层(数据))


# --- 语义层不是块：不进块索引 -----------------------------------------------

def test_语义层不被当成块扫进索引():
    """G11 契约：`find_block_files` 只认 `块.json` / `*.块.json`，
    所以把 `语义层.json` 放在 `blocks/制造/` 下不会派出一个假块。"""
    from jikuai.pkg.blocks import find_block_files
    命中 = [p for p in find_block_files() if "语义层" in p]
    assert 命中 == [], 命中
