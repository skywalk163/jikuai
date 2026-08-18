# -*- coding: utf-8 -*-
"""v0.26.0 W130 · 赛题数据集 schema 冻结校验的门禁测试。

正例：真实数据集（`赛题/chatbi/数据集/`）全过。
反例：仓库纪律要求「只测正例等于没有门禁」（v0.19.0 W62 / 路线图-v0.25 W127），
所以用 `tmp_path` 造损坏副本，逐一证明每类损坏都被抓到并让脚本红。

被证明能抓的反例（至少 4 类，任务要求）：
1. 少一列 / 列序错
2. 行数不对
3. 多出一个有空值的列
4. 外键孤儿行
另外补了主键重复、1:1 破坏、超大文件、非外键行不误判几条，因为它们同属
「数据集被换掉/被误改」这一威胁面，顺手一起钉住。
"""

import csv
import importlib.util
import pathlib
import shutil

import pytest

仓库根 = pathlib.Path(__file__).resolve().parent.parent
真实数据集 = 仓库根 / "赛题" / "chatbi" / "数据集"
脚本路径 = 仓库根 / "scripts" / "check_chatbi_schema.py"


def _载入脚本():
    规格 = importlib.util.spec_from_file_location("check_chatbi_schema", 脚本路径)
    模块 = importlib.util.module_from_spec(规格)
    规格.loader.exec_module(模块)
    return 模块


校验 = _载入脚本()


# ---------------------------------------------------------------------------
# 工具：把真实数据集拷进 tmp_path，再按需破坏某一张表
# ---------------------------------------------------------------------------

def _拷贝数据集(tmp_path) -> pathlib.Path:
    目标 = tmp_path / "数据集"
    shutil.copytree(真实数据集, 目标)
    return 目标


def _读(目录: pathlib.Path, 表名: str):
    with (目录 / (表名 + ".csv")).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def _写(目录: pathlib.Path, 表名: str, 行):
    with (目录 / (表名 + ".csv")).open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(行)


# ---------------------------------------------------------------------------
# 正例
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_真实数据集全过():
    问题 = 校验.校验数据集(真实数据集)
    assert 问题 == [], "真实数据集不该有任何 schema 违规：\n" + "\n".join(问题)


@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_main_真实数据集退出码为0():
    assert 校验.main([str(真实数据集), "--quiet"]) == 0


def test_数据集目录不存在退出码为2(tmp_path):
    缺 = tmp_path / "不存在"
    assert 校验.main([str(缺)]) == 2


@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_BOM_混合是实测事实且两种都能吃():
    """`utf-8-sig` 不是防御性写法，是**必需**：真实数据集里两种都存在。

    实测（2026-08-18）：8 张表 + 2 份问题集都带 UTF-8 BOM，
    `schema_relationships.csv` **不带**。用 `utf-8` 读带 BOM 的文件，第一个列名
    会变成 `\\ufeffmodel_id`，表头检查就会假红。
    """
    带BOM = []
    无BOM = []
    for f in sorted(真实数据集.glob("*.csv")):
        (带BOM if f.read_bytes()[:3] == b"\xef\xbb\xbf" else 无BOM).append(f.name)
    assert "schema_relationships.csv" in 无BOM, 无BOM
    assert "dim_model.csv" in 带BOM, 带BOM
    # 两种混着也全过 —— 正例测试已覆盖，这里再钉一次前提
    assert 校验.校验数据集(真实数据集) == []


# ---------------------------------------------------------------------------
# 反例 1：少一列 / 列序错
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_反例_少一列被抓(tmp_path):
    目录 = _拷贝数据集(tmp_path)
    行 = _读(目录, "dim_model")
    # 删掉最后一列 launch_year
    行 = [r[:-1] for r in 行]
    _写(目录, "dim_model", 行)

    问题 = 校验.校验数据集(目录)
    assert any("dim_model" in p and "缺列" in p for p in 问题), 问题


@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_反例_列序错被抓(tmp_path):
    目录 = _拷贝数据集(tmp_path)
    行 = _读(目录, "dim_customer")
    # 交换前两列（列集合不变，只有顺序变）
    换 = [[r[1], r[0]] + r[2:] for r in 行]
    _写(目录, "dim_customer", 换)

    问题 = 校验.校验数据集(目录)
    assert any("dim_customer" in p and "列序" in p for p in 问题), 问题


# ---------------------------------------------------------------------------
# 反例 2：行数不对
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_反例_行数不对被抓(tmp_path):
    目录 = _拷贝数据集(tmp_path)
    行 = _读(目录, "dim_workshop_line")
    _写(目录, "dim_workshop_line", 行[:-1])          # 删一行

    问题 = 校验.校验数据集(目录)
    assert any("dim_workshop_line" in p and "行" in p and "冻结值" in p
               for p in 问题), 问题


# ---------------------------------------------------------------------------
# 反例 3：多出一个有空值的列
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_反例_已有列出现意外空值被抓(tmp_path):
    """在一张零空值表里，把某个非允许列的一格挖空 —— 应触发意外空值红。

    注意：这里改的是**已有列**的值而非加新列，这样表头仍与冻结一致、能走到
    空值检查那一步（加新列会先被列名检查拦下，反而测不到空值分支）。
    """
    目录 = _拷贝数据集(tmp_path)
    行 = _读(目录, "fact_production_plan")
    # 表头 + 数据行；把第一条数据行的 planned_quantity（最后一列）挖空
    行[1][-1] = ""
    _写(目录, "fact_production_plan", 行)

    问题 = 校验.校验数据集(目录)
    assert any("fact_production_plan" in p and "空值" in p for p in 问题), 问题


@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_反例_真的多出一个空值列被抓(tmp_path):
    """字面意义的「多出一个有空值的列」：给表尾加一列、全填空串。

    这一条会被**列名检查**先拦下（报「多列」），而不是走到空值检查 ——
    断言写成「红了」而不是「报的是空值」，免得测试把实现细节钉死。
    """
    目录 = _拷贝数据集(tmp_path)
    行 = _读(目录, "fact_energy_usage")
    行[0].append("steam_ton")                       # 新列名
    for r in 行[1:]:
        r.append("")                                # 全空
    _写(目录, "fact_energy_usage", 行)

    问题 = 校验.校验数据集(目录)
    assert any("fact_energy_usage" in p and "多列" in p for p in 问题), 问题
    assert 校验.main([str(目录), "--quiet"]) == 1


@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_反例_fact_orders已允许的空值列不误报(tmp_path):
    """fact_orders 的两列本就允许空 —— 正例里它们有空值不能算违规。"""
    目录 = _拷贝数据集(tmp_path)
    问题 = 校验.校验数据集(目录)
    assert not any("fact_orders" in p and "空值" in p for p in 问题), 问题


# ---------------------------------------------------------------------------
# 反例 4：外键孤儿行
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_反例_外键孤儿行被抓(tmp_path):
    目录 = _拷贝数据集(tmp_path)
    行 = _读(目录, "fact_orders")
    # customer_id 是第 2 列（索引 1），指向 dim_customer。改成一个不存在的客户。
    行[1][1] = "C_不存在_9999"
    _写(目录, "fact_orders", 行)

    问题 = 校验.校验数据集(目录)
    assert any("孤儿" in p and "fact_orders" in p for p in 问题), 问题


# ---------------------------------------------------------------------------
# 补充反例：主键重复 / 1:1 破坏 / 超大文件
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_反例_主键重复被抓(tmp_path):
    目录 = _拷贝数据集(tmp_path)
    行 = _读(目录, "dim_model")
    # 把第二条数据行的主键改成和第一条相同（保持行数不变，单独测主键）
    行[2][0] = 行[1][0]
    _写(目录, "dim_model", 行)

    问题 = 校验.校验数据集(目录)
    assert any("dim_model" in p and "主键" in p and "重复" in p for p in 问题), 问题


@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_反例_一对一被破坏被抓(tmp_path):
    目录 = _拷贝数据集(tmp_path)
    行 = _读(目录, "fact_production_actual")
    # actual 表 plan_id 是第 2 列。把一条改成一个 plan 表里没有的值，
    # 集合就不相等了（行数仍相同，专测 1:1 而非行数）。
    行[1][1] = "PLAN_不存在_X"
    _写(目录, "fact_production_actual", 行)

    问题 = 校验.校验数据集(目录)
    assert any("1:1 不成立" in p or "集合不相等" in p for p in 问题), 问题


@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过")
def test_反例_超大文件被抓(tmp_path, monkeypatch):
    目录 = _拷贝数据集(tmp_path)
    # 把上限临时压到 1KB，dim_model 就会超限 —— 证明大小断言真的在跑
    monkeypatch.setattr(校验, "单文件上限字节", 1024)
    问题 = 校验.校验数据集(目录)
    assert any("上限" in p for p in 问题), 问题
