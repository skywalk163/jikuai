# -*- coding: utf-8 -*-
"""v0.28.0 W171 · 两件事的 pytest 断言。

**一、`邻期关联` 的去重标记列**（BACKLOG §12.3 缺口 4）
`邻联` 是多对多算子，输出行数会膨胀。原本块描述只写了「会膨胀」，下游 `缺陷汇总`
照样逐行求和，于是 Q_HID_002 步骤 17 得出 L002-焊装 **1599**，而真实缺陷数是
465+97=**562**。本轮给每行加两个标记列 `左行序` / `右行序`，让下游能把
「结构比较」与「绝对量求和」分开。

**偏离 WBS（要点）**：WBS 写的是「加去重标记列（**左行标识**）」，实测发现
**Q_HID_002 的膨胀在右侧**——`defect_count` 是缺陷表（= 右表）的列，一条缺陷被多张
订单命中而重复；只给左标记**盖不住这个 DoD**。所以落地成**两列**：度量列在左表按
`左行序` 去重、在右表按 `右行序` 去重。`test_只给左行序盖不住这道题` 把这个判断钉住。

**二、G25 形参名词法原子性门禁**（BACKLOG §12.4 记的账）
目录名与导出名早有原子性校验，形参名没有；W167 真踩过一次（`赵维度列` →
`计停(赵表, 赵维度, 列())`）。`jk 块 新建` 的预检**手写块文件绕得过**，所以把尺挪到
门禁上。`tests/test_v0_28_0_w167_姊妹出口.py` 只覆盖那三个块，这里覆盖全库 + 反例。

数据集缺失时（`赛题/` 不进 wheel 与 sdist，ADR-40 §7）只 skip 需要真实数据的那几条，
小表用例与 G25 用例照跑。
"""

import importlib.util
import json
import pathlib
import sys

import pytest

_源 = importlib.util.find_spec("jikuai").origin
仓库根 = pathlib.Path(_源).resolve().parent.parent.parent
数据集 = 仓库根 / "赛题" / "chatbi" / "数据集"
块库根 = 仓库根 / "src" / "jikuai" / "stdlib" / "blocks"
块根 = 块库根 / "制造"

sys.path.insert(0, str(仓库根 / "src"))

需数据集 = pytest.mark.skipif(
    not 数据集.is_dir(),
    reason="赛题/chatbi/数据集/ 不存在（赛题/ 不进 wheel 与 sdist，ADR-40 §7）",
)


def _载(块名, 导出名):
    """纯 `.jk` 块：过极快运行时按相对路径加载。"""
    import jikuai
    模块 = jikuai.load(f"{块名}/{块名}.jk", base_dir=str(块根))
    return getattr(模块, 导出名)


def _载背衬(块名, 导出名):
    """带 `.py` 背衬的块（`缺陷汇总`/`表载入`）：直接按文件路径加载背衬，
    不走 `.jk` 门面（门面靠 `蟒:` 桥转导，import 路径在测试环境里不稳）。"""
    路径 = 块根 / 块名 / (块名 + ".py")
    规格 = importlib.util.spec_from_file_location("w171_" + 块名, 路径)
    模块 = importlib.util.module_from_spec(规格)
    规格.loader.exec_module(模块)
    return getattr(模块, 导出名)


@pytest.fixture(scope="module")
def 邻联():
    return _载("邻期关联", "邻联")


@pytest.fixture(scope="module")
def 计陷():
    return _载背衬("缺陷汇总", "计陷")


@pytest.fixture(scope="module")
def 读表():
    return _载背衬("表载入", "读表")


def _元(块名):
    with open(块根 / 块名 / "块.json", encoding="utf-8") as f:
        return json.load(f)


# ===========================================================================
# 1. 标记列本身：两列都在、值可机械复算、重名报错
# ===========================================================================

左表 = [
    {"model_id": "M003", "planned_delivery_date": "2026-06-10", "order_id": "OA"},
    {"model_id": "M003", "planned_delivery_date": "2026-06-11", "order_id": "OB"},
]
右表 = [
    {"model_id": "M003", "defect_date": "2026-06-10", "defect_count": 5},
    {"model_id": "M003", "defect_date": "2026-06-30", "defect_count": 7},
]


def test_每行都带两个标记列(邻联):
    行们 = 邻联(左表, ["model_id"], "planned_delivery_date",
               右表, ["model_id"], "defect_date", 1, "缺陷_")
    assert 行们, "±1 天内应命中"
    for 行 in 行们:
        assert "左行序" in 行 and "右行序" in 行


def test_标记值就是源表行号_可逐行复算(邻联):
    """右表第 1 行（6-10）被左表两行（6-10 / 6-11）各命中一次；第 2 行（6-30）落窗外。"""
    行们 = 邻联(左表, ["model_id"], "planned_delivery_date",
               右表, ["model_id"], "defect_date", 1, "缺陷_")
    assert [(行["左行序"], 行["右行序"]) for 行 in 行们] == [(1, 1), (2, 1)]
    # 行号是「在源表里的位置」，所以能拿它反查源行
    for 行 in 行们:
        assert 行["order_id"] == 左表[行["左行序"] - 1]["order_id"]
        assert 行["defect_count"] == 右表[行["右行序"] - 1]["defect_count"]


def test_右表度量列_直接求和被乘一遍_按右行序去重才是真值(邻联):
    行们 = 邻联(左表, ["model_id"], "planned_delivery_date",
               右表, ["model_id"], "defect_date", 1, "缺陷_")
    毛 = sum(行["defect_count"] for 行 in 行们)
    净 = sum(行["defect_count"] for 行 in
            {行["右行序"]: 行 for 行 in 行们}.values())
    assert 毛 == 10, "同一条缺陷被两张单各算一次"
    assert 净 == 5, "按 右行序 去重后回到真值"


def test_左表度量列_要按左行序去重(邻联):
    """对称的一半：度量列在左表时，膨胀来自「一张单命中多条缺陷」。"""
    左 = [{"model_id": "M003", "planned_delivery_date": "2026-06-10",
          "order_quantity": 30}]
    右 = [{"model_id": "M003", "defect_date": "2026-06-10", "defect_count": 1},
         {"model_id": "M003", "defect_date": "2026-06-11", "defect_count": 2}]
    行们 = 邻联(左, ["model_id"], "planned_delivery_date",
               右, ["model_id"], "defect_date", 1, "缺陷_")
    assert len(行们) == 2
    assert sum(行["order_quantity"] for 行 in 行们) == 60
    assert sum(行["order_quantity"] for 行 in
               {行["左行序"]: 行 for 行 in 行们}.values()) == 30


@pytest.mark.parametrize("标记", ["左行序", "右行序"])
def test_标记列与数据列重名就报错_不静默覆盖(邻联, 标记):
    左 = [dict(左表[0], **{标记: 99})]
    with pytest.raises(Exception) as 捕:
        邻联(左, ["model_id"], "planned_delivery_date",
            右表, ["model_id"], "defect_date", 1, "缺陷_")
    assert "重名" in str(捕.value)


def test_行数与列集_除两个标记外一行未变(邻联):
    """标记是**加列**，不改行数、不动既有列——下游只读自己那几列的块不受影响。"""
    行们 = 邻联(左表, ["model_id"], "planned_delivery_date",
               右表, ["model_id"], "defect_date", 1, "缺陷_")
    assert len(行们) == 2
    列集 = set(行们[0])
    assert 列集 - {"左行序", "右行序"} == {
        "model_id", "planned_delivery_date", "order_id",
        "缺陷_model_id", "defect_date", "defect_count"}


# ===========================================================================
# 2. DoD：Q_HID_002 步骤 17 的 1599 vs 562（真实数据集）
# ===========================================================================

def _Q_HID_002_两表(读表):
    订单 = 读表(str(数据集 / "fact_orders.csv"))
    缺陷 = 读表(str(数据集 / "fact_quality_defects.csv"))
    左 = [r for r in 订单
         if r["customer_id"] == "C005"
         and "2026-06-01" <= r["planned_delivery_date"] <= "2026-06-30"]
    右 = [r for r in 缺陷
         if r["model_id"] == "M003"
         and "2026-06-01" <= r["defect_date"] <= "2026-06-30"]
    return 左, 右


@需数据集
def test_Q_HID_002_步骤17_膨胀1599与真值562都能算出来(邻联, 计陷, 读表):
    """**这条就是 W171 的 DoD**：1599 与 562 的差不再只能靠人看出来。

    `缺陷汇总` 的行为一行没改（照旧逐行求和 → 1599）；改变的是 `邻联` 输出里多了
    `右行序`，于是「先去重再汇总」成为可做的事，且两个数能同时摆出来对照。
    """
    左, 右 = _Q_HID_002_两表(读表)
    宽 = 邻联(左, ["model_id"], "planned_delivery_date",
             右, ["model_id"], "defect_date", 7, "缺陷_")

    膨胀 = 计陷(宽, ["line_id", "process"])
    焊装 = [行 for 行 in 膨胀 if 行["line_id"] == "L002"
           and 行["process"] == "焊装"][0]
    assert 焊装["缺陷数"] == 1599, "与 产出/留出/Q_HID_002-结果.txt 逐字一致"
    assert 焊装["记录条数"] == 185

    去重 = list({行["右行序"]: 行 for 行 in 宽}.values())
    真 = 计陷(去重, ["line_id", "process"])
    焊装真 = [行 for 行 in 真 if 行["line_id"] == "L002"
             and 行["process"] == "焊装"][0]
    assert 焊装真["缺陷数"] == 562, "465（焊点虚焊）+ 97（焊缝气孔）"
    assert 焊装真["记录条数"] == 65

    # 「结构比较仍有效」也一并钉住：膨胀前后 L002-焊装 都是榜首
    assert 膨胀[0]["line_id"] == "L002" and 膨胀[0]["process"] == "焊装"
    assert 真[0]["line_id"] == "L002" and 真[0]["process"] == "焊装"


@需数据集
def test_只给左行序盖不住这道题(邻联, 计陷, 读表):
    """★ 记档：WBS 写的「左行标识」在这道题上**不成立**，所以本轮落了两列。

    左表是 C005 的 6 月订单（8 张），右表是缺陷。按 `左行序` 去重后每张订单只剩一行，
    `defect_count` 只是它随机撞上的那一条的值——既不是 1599 也不是 562。
    """
    左, 右 = _Q_HID_002_两表(读表)
    宽 = 邻联(左, ["model_id"], "planned_delivery_date",
             右, ["model_id"], "defect_date", 7, "缺陷_")
    按左 = list({行["左行序"]: 行 for 行 in 宽}.values())
    assert len(按左) == len({行["左行序"] for 行 in 宽})
    assert len(按左) < 20, "左表只有个位数张订单，去重后行数与缺陷数量级无关"
    合 = sum(行["defect_count"] for 行 in 按左)
    assert 合 not in (1599, 562), (
        "按左行序去重既回不到真值也不是膨胀值，实测 %d——左标记对这道题无效" % 合)


@需数据集
def test_膨胀倍数就是右行被复制的次数(邻联, 读表):
    """把「为什么是 2.85 倍」变成可复算的事实，而不是一句形容。

    注意两个比值**不完全相等**：行数比 185/65 = 2.8462，缺陷数比 1599/562 = 2.8452。
    因为每条缺陷的 `defect_count` 不同，被复制次数多的那些行权重也不同——
    「倍数」是加权平均，不是行数比。这条差异本身就值得钉住，免得下次有人拿行数比
    去反推缺陷数。
    """
    左, 右 = _Q_HID_002_两表(读表)
    宽 = 邻联(左, ["model_id"], "planned_delivery_date",
             右, ["model_id"], "defect_date", 7, "缺陷_")
    焊装行 = [行 for 行 in 宽 if 行["line_id"] == "L002"
             and 行["process"] == "焊装"]
    独立右行 = {行["右行序"] for 行 in 焊装行}
    assert len(焊装行) == 185 and len(独立右行) == 65
    行数比 = len(焊装行) / len(独立右行)
    数量比 = 1599 / 562
    assert abs(行数比 - 数量比) < 0.01, "两个比值同量级但不相等"
    assert 行数比 != 数量比


# ===========================================================================
# 3. 两块描述互相点名（WBS DoD：「两块描述互相点名有测试断言」）
# ===========================================================================

def test_两块描述互相点名且都点了膨胀坑():
    邻 = _元("邻期关联")["描述"]
    陷 = _元("缺陷汇总")["描述"]
    assert "缺陷汇总" in 邻, "关联块要点名典型下游"
    assert "邻期关联" in 陷, "下游要点名膨胀的来源块"
    for 词 in ("左行序", "右行序", "去重"):
        assert 词 in 邻, f"邻期关联 描述缺「{词}」"
    for 词 in ("右行序", "去重", "1599", "562"):
        assert 词 in 陷, f"缺陷汇总 描述缺「{词}」"
    # 「不会替你去重」这句是这条口径的要害，别被改软
    assert "不会替你去重" in 陷


def test_邻期关联描述仍自报口径关键词_G22第2条():
    命中 = [k for k in ("分母", "汇总", "加权", "现成列", "重算", "窗口")
           if k in _元("邻期关联")["描述"]]
    assert 命中


def test_邻期关联版本已随输出契约变更上抬():
    assert _元("邻期关联")["版本"] == "0.2.0", "输出多了两列，不是纯文案改动"


# ===========================================================================
# 4. G25：形参名词法原子性（全库 + 反例）
# ===========================================================================

def test_全库形参名零违规_含测试jk():
    from jikuai.pkg.blocks import check_stdlib_param_atomicity
    问题 = check_stdlib_param_atomicity()
    assert 问题 == [], "形参名非原子：%s" % 问题
    assert check_stdlib_param_atomicity(含测试=False) == []


def test_扫描规模符合预期_门禁不是空转():
    """「守卫绿 ≠ 守卫在守」：绿之前先证明它真扫到了东西（v0.22 教训）。"""
    import os
    from jikuai.pkg.blocks import extract_func_params, blocks_root
    根 = blocks_root()
    定义数 = 0
    形参数 = 0
    for 目录, _子, 文件们 in os.walk(根):
        for 名 in 文件们:
            if 名.endswith(".jk"):
                for _函, 形参 in extract_func_params(os.path.join(目录, 名)):
                    定义数 += 1
                    形参数 += len(形参)
    assert 定义数 >= 100, "只扫到 %d 个函数定义，规模不对" % 定义数
    assert 形参数 >= 200, "只扫到 %d 个形参" % 形参数


def test_extract_func_params_取的是作者写下的词而不是parser的形参():
    """★ 本条是 G25 判据的根：**不能拿 parser 的 `FuncDef.params` 当输入**。

    `parser._parse_param_list` 只收 `TokenType.IDENT`，`赵维度列` 到它手上已经被切成
    `赵维度` 了——那正是 bug 本身。所以扫描必须回到源文本取整个词，再送 lexer 判原子。
    """
    from jikuai.lexer import tokenize
    from jikuai.parser import parse
    from jikuai.pkg.blocks import check_export_atomicity

    源 = "函数 计停 接收 赵表 赵维度列：\n  返回 赵表。\n。\n"
    树 = parse(tokenize(源))
    函数们 = [n for n in 树.body if type(n).__name__ == "FuncDef"]
    assert 函数们, "解析不出函数定义"
    assert 函数们[0].params == ["赵表", "赵维度"], (
        "parser 眼里第二个形参已经是 `赵维度`——它看不见 `列` 被切走了")
    # 而作者写的那个词过尺就是红的
    原子, 碎片 = check_export_atomicity("赵维度列")
    assert 原子 is False and len(碎片) >= 2


def test_G25反例_手写一个坏形参名会被抓到(tmp_path):
    from jikuai.pkg.blocks import check_stdlib_param_atomicity
    坏块 = tmp_path / "制造" / "坏形参"
    坏块.mkdir(parents=True)
    (坏块 / "坏形参.jk").write_text(
        "函数 计坏 接收 赵表 赵维度列：\n  返回 赵表。\n。\n\n导出 计坏。\n",
        encoding="utf-8")
    问题 = check_stdlib_param_atomicity(root=str(tmp_path))
    assert len(问题) == 1
    相对, 函数名, 形参名, 碎片 = 问题[0]
    assert 函数名 == "计坏" and 形参名 == "赵维度列"
    assert "坏形参.jk" in 相对.replace("\\", "/")
    assert [t for t, _v in 碎片] == ["IDENT", "VERB"]


def test_G25反例_测试jk里的坏形参也抓_豁免会给下次留门(tmp_path):
    """`含测试=False` 是可选项而不是默认：块自测里写坏形参照样是坏形参。"""
    from jikuai.pkg.blocks import check_stdlib_param_atomicity
    块 = tmp_path / "制造" / "某块"
    块.mkdir(parents=True)
    (块 / "某块.jk").write_text("函数 计好 接收 赵表：\n  返回 赵表。\n。\n",
                              encoding="utf-8")
    (块 / "测试.jk").write_text("函数 助手 接收 赵行数：\n  返回 赵行数。\n。\n",
                              encoding="utf-8")
    assert check_stdlib_param_atomicity(root=str(tmp_path)) == []
    (块 / "测试.jk").write_text("函数 助手 接收 赵维度列：\n  返回 赵维度列。\n。\n",
                              encoding="utf-8")
    assert len(check_stdlib_param_atomicity(root=str(tmp_path))) == 1
    assert check_stdlib_param_atomicity(root=str(tmp_path), 含测试=False) == []


@pytest.mark.parametrize("源,期望", [
    ("函数 甲：\n", ("甲", [])),
    ("函数 甲 接收 赵一：\n", ("甲", ["赵一"])),
    ("函数 甲 接收 赵一 赵二：\n", ("甲", ["赵一", "赵二"])),
    ("函数 甲 接收 赵一，赵二：\n", ("甲", ["赵一", "赵二"])),
    ("  函数 甲 接收 赵一：\n", ("甲", ["赵一"])),
])
def test_extract_func_params_认得空格与逗号两种写法(tmp_path, 源, 期望):
    from jikuai.pkg.blocks import extract_func_params
    f = tmp_path / "x.jk"
    f.write_text(源, encoding="utf-8")
    assert extract_func_params(str(f)) == [期望]


def test_extract_func_params_不把注释里的函数当定义(tmp_path):
    from jikuai.pkg.blocks import extract_func_params
    f = tmp_path / "x.jk"
    f.write_text("-- 函数 假 接收 赵维度列：\n函数 真 接收 赵表：\n",
                 encoding="utf-8")
    assert extract_func_params(str(f)) == [("真", ["赵表"])]


def test_G25串在主门禁里而不是只当库函数():
    """门禁不进主脚本就等于没有（v0.25.0 W129 / v0.26.0 W144 的教训）。"""
    主 = (仓库根 / "scripts" / "check_stdlib_contract.py").read_text(
        encoding="utf-8")
    assert "check_stdlib_param_atomicity" in 主
    assert "G25" in 主
