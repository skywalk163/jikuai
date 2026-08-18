# -*- coding: utf-8 -*-
"""G22 · 制造域口径契约门禁反例测试（v0.26.0 W144 · ADR-40 §6）。

沿用 v0.18.0 W55 / v0.19.0 W62 的规矩：**只测正例等于没有门禁**。这里用
`tmp_path` 逐一造反例，证明四条断言每一条都真能抓到；并钉住真仓库状态下四条全过。

反例覆盖（WBS 要求 ≥3，本轮要 5 类）：

1. 少一个预置异常（删掉 A_03）——断言 1a 编号集合
2. 多一个预置异常（塞进 A_06）——断言 1a 编号集合
3. 某个制造块描述不含任何口径关键词——断言 2
4. 某处分歧点只做了一个块（删掉 达成率均值）——断言 3
5. 语义层指向不存在的列——断言 4

外加几条把「恰好」判法的其它层与「数据集缺失显式跳过」也钉住的用例，因为它们
同属本门禁的价值面（编号对了但内容被换 / 冒名 / 分歧点两块不互相点名 / 缺陷率
删掉否决表述 / skip 不当通过）。
"""

import importlib.util
import json
import pathlib
import shutil

import pytest

仓库根 = pathlib.Path(__file__).resolve().parent.parent
脚本路径 = 仓库根 / "scripts" / "check_manufacturing_contract.py"
真实块根 = 仓库根 / "src" / "jikuai" / "stdlib" / "blocks"
真实评测集 = 仓库根 / "tools" / "ai-bridge" / "评测集-chatbi.json"
真实语义层 = 真实块根 / "制造" / "语义层.json"
真实数据集 = 仓库根 / "赛题" / "chatbi" / "数据集"


def _载入脚本():
    规格 = importlib.util.spec_from_file_location(
        "check_manufacturing_contract", 脚本路径)
    模块 = importlib.util.module_from_spec(规格)
    规格.loader.exec_module(模块)
    return 模块


G22 = _载入脚本()


# ---------------------------------------------------------------------------
# 造材料：把真实制造块目录 / 评测集 / 语义层拷进 tmp_path 再定点破坏
# ---------------------------------------------------------------------------

def _拷块根(tmp_path) -> pathlib.Path:
    """把真实 制造 域目录拷成 <tmp>/blocks/制造，返回 <tmp>/blocks 作块根。"""
    块根 = tmp_path / "blocks"
    (块根).mkdir()
    shutil.copytree(真实块根 / "制造", 块根 / "制造")
    return 块根


def _读评测集():
    with 真实评测集.open("r", encoding="utf-8") as f:
        return json.load(f)


def _写评测集(tmp_path, 数据) -> pathlib.Path:
    p = tmp_path / "评测集-chatbi.json"
    with p.open("w", encoding="utf-8") as f:
        json.dump(数据, f, ensure_ascii=False, indent=2)
    return p


def _改块描述(块根: pathlib.Path, 块名: str, 新描述: str):
    元 = 块根 / "制造" / 块名 / "块.json"
    with 元.open("r", encoding="utf-8") as f:
        数据 = json.load(f)
    数据["描述"] = 新描述
    with 元.open("w", encoding="utf-8") as f:
        json.dump(数据, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 正例：真仓库状态四条全过
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过（第 4 条要它）")
def test_真仓库四条全过():
    问题, 通过, 跳过 = G22.校验(真实块根, 真实评测集, 真实语义层, 真实数据集)
    assert 问题 == [], "真仓库不该有任何违规：\n" + "\n".join(问题)
    assert 跳过 == [], "数据集在场时第 4 条不该跳过"
    assert len(通过) == 4, 通过


def test_main真仓库退出码为0():
    # main 默认路径就是真仓库；数据集在场则四条全绿，缺席则第 4 条跳过、其余三条绿。
    assert G22.main(["--quiet"]) == 0


# ---------------------------------------------------------------------------
# 反例 1：少一个预置异常
# ---------------------------------------------------------------------------

def test_反例_少一个预置异常(tmp_path):
    数据 = _读评测集()
    数据["用例"] = [c for c in 数据["用例"] if c.get("id") != "A_03"]
    路径 = _写评测集(tmp_path, 数据)

    问题, 过 = G22.校验预置异常(路径)
    assert 过 is None
    assert any("A_03" in p and "少" in p for p in 问题), 问题


# ---------------------------------------------------------------------------
# 反例 2：多一个预置异常
# ---------------------------------------------------------------------------

def test_反例_多一个预置异常(tmp_path):
    数据 = _读评测集()
    数据["用例"].append({
        "id": "A_06",
        "需求": "随便编的第六个异常",
        "期望": ["表载入"],
        "期望结果": "预置异常 6：这条不该存在。",
    })
    路径 = _写评测集(tmp_path, 数据)

    问题, 过 = G22.校验预置异常(路径)
    assert 过 is None
    assert any("A_06" in p and "多" in p for p in 问题), 问题


# ---------------------------------------------------------------------------
# 反例 3：某个制造块描述不含任何口径关键词
# ---------------------------------------------------------------------------

def test_反例_块描述不含口径关键词(tmp_path):
    块根 = _拷块根(tmp_path)
    # 挑一个引擎层块，把描述换成一段完全不含 分母/汇总/加权/现成列/重算/窗口 的话
    _改块描述(块根, "投影", "这是一个块，做一些处理，返回结果。别的什么都不说。")
    块表 = G22.收集块(块根)

    问题, 过 = G22.校验口径关键词(块表)
    assert 过 is None
    assert any("投影" in p and "口径声明关键词" in p for p in 问题), 问题


# ---------------------------------------------------------------------------
# 反例 4：某处分歧点只做了一个块
# ---------------------------------------------------------------------------

def test_反例_分歧点只做一个块(tmp_path):
    块根 = _拷块根(tmp_path)
    # 删掉达成率均值，只剩加权那一侧 —— 选块的人就看不见还有行级平均口径
    shutil.rmtree(块根 / "制造" / "达成率均值")
    块表 = G22.收集块(块根)

    问题, 过 = G22.校验分歧点(块表)
    assert 过 is None
    assert any("达成率均值" in p and "缺块" in p for p in 问题), 问题


# ---------------------------------------------------------------------------
# 反例 5：语义层指向不存在的列
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not 真实数据集.is_dir(), reason="真实数据集不在，跳过（这条要比对真实表头）")
def test_反例_语义层指向不存在的列(tmp_path):
    with 真实语义层.open("r", encoding="utf-8") as f:
        语义 = json.load(f)
    # 把第一条业务词的字段改成一个真实表头里没有的列名
    语义["条目"][0]["字段"] = "根本不存在的列名_zzz"
    坏语义层 = tmp_path / "语义层.json"
    with 坏语义层.open("w", encoding="utf-8") as f:
        json.dump(语义, f, ensure_ascii=False, indent=2)

    问题, 过, 跳 = G22.校验语义层(坏语义层, 真实数据集)
    assert 过 is None and 跳 is None
    assert any("根本不存在的列名_zzz" in p for p in 问题), 问题


# ---------------------------------------------------------------------------
# 「恰好」判法的其它层：编号对但内容被换 / 冒名 / 序号错位
# ---------------------------------------------------------------------------

def test_反例_编号对但关键实体被换掉(tmp_path):
    """1b：把 A_03（C005 的 M003 延期）内容换成别的客户，编号集合仍相等，
    但关键实体对不上 —— 只查编号查不出来。"""
    数据 = _读评测集()
    for c in 数据["用例"]:
        if c.get("id") == "A_03":
            c["需求"] = "客户张三的某车型在某月是不是延期了？"
            c["期望结果"] = "预置异常 3：换成了完全不同的客户和车型。"
    路径 = _写评测集(tmp_path, 数据)

    问题, 过 = G22.校验预置异常(路径)
    assert 过 is None
    assert any("A_03" in p and "关键实体" in p for p in 问题), 问题


def test_反例_公开题冒名预置异常(tmp_path):
    """1d：把第 6 个异常伪装成公开题（非 A_ 前缀但自称「预置异常」）。"""
    数据 = _读评测集()
    数据["用例"].append({
        "id": "Q_PUB_011",
        "需求": "伪装成公开题的异常",
        "期望": ["表载入"],
        "期望结果": "预置异常 6：混进来的第六条。",
    })
    路径 = _写评测集(tmp_path, 数据)

    问题, 过 = G22.校验预置异常(路径)
    assert 过 is None
    assert any("Q_PUB_011" in p and "自称" in p for p in 问题), 问题


def test_反例_序号自述错位(tmp_path):
    """1c：A_02 的 期望结果 里把「预置异常 2」写成别的序号 —— 内容错位被抓。"""
    数据 = _读评测集()
    for c in 数据["用例"]:
        if c.get("id") == "A_02":
            c["期望结果"] = c["期望结果"].replace("预置异常 2", "预置异常 4")
    路径 = _写评测集(tmp_path, 数据)

    问题, 过 = G22.校验预置异常(路径)
    assert 过 is None
    assert any("A_02" in p and "自述" in p for p in 问题), 问题


# ---------------------------------------------------------------------------
# 断言 3 其它面：双块不互相点名 / 缺陷率删掉否决表述
# ---------------------------------------------------------------------------

def test_反例_分歧点两块不互相点名(tmp_path):
    """ADR-40 §5.1 明写「禁止只做一个块然后在描述里含糊」，两块都在但互不点名
    等价于让人漏看另一个口径。把达成率均值描述里对权重块的点名抹掉。"""
    块根 = _拷块根(tmp_path)
    元 = 块根 / "制造" / "达成率均值" / "块.json"
    with 元.open("r", encoding="utf-8") as f:
        数据 = json.load(f)
    # 保留口径关键词（行级/平均），但删掉对「达成率权重」的点名
    数据["描述"] = 数据["描述"].replace("达成率权重", "另一个块")
    with 元.open("w", encoding="utf-8") as f:
        json.dump(数据, f, ensure_ascii=False, indent=2)
    块表 = G22.收集块(块根)

    问题, 过 = G22.校验分歧点(块表)
    assert 过 is None
    assert any("没点名另一侧" in p for p in 问题), 问题


def test_反例_缺陷率删掉否决表述(tmp_path):
    """缺陷率这一处刻意不造第二个块，靠描述显式否掉「行级比率平均」把关。
    把否决表述抹掉后（只提一句不否掉），本门禁必须红 —— 证明这条判法不放水。"""
    块根 = _拷块根(tmp_path)
    元 = 块根 / "制造" / "缺陷率" / "块.json"
    with 元.open("r", encoding="utf-8") as f:
        数据 = json.load(f)
    描述 = 数据["描述"]
    for 句 in ("非行级比率的平均", "不能先算行级比率", "先算行级比率再平均是错的"):
        描述 = 描述.replace(句, "")
    数据["描述"] = 描述
    with 元.open("w", encoding="utf-8") as f:
        json.dump(数据, f, ensure_ascii=False, indent=2)
    块表 = G22.收集块(块根)

    问题, 过 = G22.校验分歧点(块表)
    assert 过 is None
    assert any("缺陷率" in p and "否决表述" in p for p in 问题), 问题


# ---------------------------------------------------------------------------
# 断言 4：数据集缺失时显式 skip，绝不静默当通过
# ---------------------------------------------------------------------------

def test_数据集缺失时第4条显式跳过而非通过(tmp_path):
    缺 = tmp_path / "没有数据集"
    问题, 过, 跳 = G22.校验语义层(真实语义层, 缺)
    assert 问题 == []          # 不因缺数据集而报错
    assert 过 is None          # 但也不算「通过」
    assert 跳 is not None and "因数据集缺失跳过第 4 条" in 跳


def test_main数据集缺失整体仍0且打印跳过(tmp_path, capsys):
    码 = G22.main(["--数据集", str(tmp_path / "没有"), "--quiet"])
    assert 码 == 0
    err = capsys.readouterr().err
    assert "因数据集缺失跳过第 4 条" in err


# ---------------------------------------------------------------------------
# main 级集成：反例必须让整个门禁红（退出码 1），不只是子函数报问题
# ---------------------------------------------------------------------------

def test_反例_main级少一个预置异常退出码1(tmp_path, capsys):
    数据 = _读评测集()
    数据["用例"] = [c for c in 数据["用例"] if c.get("id") != "A_01"]
    路径 = _写评测集(tmp_path, 数据)

    assert G22.main(["--评测集", str(路径), "--quiet"]) == 1
    err = capsys.readouterr().err
    assert "断言1 预置异常" in err and "A_01" in err


def test_反例_main级块描述被抽空退出码1(tmp_path, capsys):
    块根 = _拷块根(tmp_path)
    _改块描述(块根, "取前N", "什么都不说的一句话。")

    assert G22.main(["--块根", str(块根),
                     "--语义层", str(真实语义层), "--quiet"]) == 1
    err = capsys.readouterr().err
    assert "断言2 口径关键词" in err and "取前N" in err


# ---------------------------------------------------------------------------
# 结构性守卫
# ---------------------------------------------------------------------------


def test_块根不存在退出码为2(tmp_path):
    assert G22.main(["--块根", str(tmp_path / "没有"), "--quiet"]) == 2


def test_评测集损坏时报错而非静默跳过(tmp_path):
    """新门禁解析不了就是它自己坏了（同 G16/G17）——不 try/except 静默跳过。"""
    坏 = tmp_path / "坏.json"
    坏.write_text("{ 不是合法 json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        G22.校验预置异常(坏)


def test_G22已串进check_stdlib_contract():
    """G22 必须被 check_stdlib_contract.py 调用，否则 CI 跑不到它。"""
    path = 仓库根 / "scripts" / "check_stdlib_contract.py"
    text = path.read_text(encoding="utf-8")
    assert "check_manufacturing_contract" in text
    assert "G22" in text
    # 不许 try/except 静默跳过（沿用 G16/G17 规矩）：串接处必须是裸 import + 判返回码
    片段 = text[text.index("import check_manufacturing_contract"):][:160]
    assert 'check_manufacturing_contract.main(["--quiet"])' in 片段, 片段
    assert "except" not in 片段, "G22 串接处不该有 except→跳过：" + 片段
