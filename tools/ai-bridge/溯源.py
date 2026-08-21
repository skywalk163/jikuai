# -*- coding: utf-8 -*-
"""溯源面板 + 等价 SQL 导出（W173 · v0.28.0）。

两条子命令，**都是纯函数式导出**：读一份 `方案.json`，出文本。

- `面板`：每一步给出**四要素** —— 块名 / 版本 / 口径声明 / 生成的 `.jk` 源码行。
  「这个数是谁算的、按哪个口径算的、对应哪行代码」是赛题要求的可解释性下限。
- `SQL`：给每一步一段**等价 SQL**，并出一张「`.jk` 源码行 ↔ SQL 片段」的逐步对照表。

::

    python tools/ai-bridge/溯源.py 面板 赛题/chatbi/产出/W172-角色视角/R1-...-方案.json
    python tools/ai-bridge/溯源.py SQL  赛题/chatbi/产出/W172-角色视角/R1-...-方案.json --json
    python tools/ai-bridge/溯源.py SQL  <方案.json> --出 对照表.txt   # 存档用，见 --出

三条硬约束（本模块的存在意义就在这三条上，别在后续改动里把它们磨掉）
----------------------------------------------------------------

1. **等价 SQL 只作展示，本工具一行都不执行**。没有 `sqlite3`、没有任何 DB-API
   import，也不接受连接串。它输出的是给人看/给人贴到自己库里跑的文本，沿用
   `网络/请求组装` 的「只产出请求描述，不发起连接」先例。
2. **没登记等价 SQL 的块就如实说「未登记」，绝不猜**。一段看着像样但口径错了的
   SQL 比报错糟得多（AGENTS.md §四那条「会跑通、结果是错的」）。未登记的步骤在
   对照表里是一行注释 + 理由，`覆盖` 字段为假。
3. **`质量体检`（`体检`）是刻意不登记的**，理由不是「来不及写」：它的输出是四元组
   `[行数, 空值列与计数, 主键重复数, 外键孤儿行数]`，**不是一张表**。硬写成一条
   `SELECT` 会把「四项互不同构的计数」伪装成一行数据，那正是第 2 条要防的事。

为什么放 `tools/ai-bridge/` 而不做成块
--------------------------------------
它吃的是**方案 + 块元数据 + 合成源码**——三样都是合成层的东西，制造域的块只吃
「行的列表」。做成块就得让块反过来 import 编译它自己的工具链，层次是倒的；块库
也会为一个不产表的东西多一个成员（连带 G12/G20/G22 全要跟着动）。
"""

import argparse
import importlib.util
import json
import os
import re
import sys

#: 仓库根 = 本文件上两级。用 `__file__` 推而不用 `os.getcwd()`（CLI 与 pytest 的 cwd 不同）。
_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg.blocks import blocks_root, load_block_metadata  # noqa: E402
from jikuai.service.schema import (  # noqa: E402
    PLAN_OPTIONAL, PLAN_REQUIRED, STEP_OPTIONAL, STEP_REQUIRED, validate_plan,
)

#: 协议字段名一律从 schema 常量取，本文件不写裸字面量（W20 硬门槛）。
_F块, _F领域, _F导出名 = STEP_REQUIRED
_F参数, _F说明, _F命名空间 = STEP_OPTIONAL
_F步骤, = PLAN_REQUIRED
_F需求, _F共享, _F打印 = PLAN_OPTIONAL

#: 共享项的两个键。schema 里**没有**对应常量（`glue.py:604` 同样是裸字面量），
#: 这里跟着它走；哪天 schema 加了常量，两处一起换。
_共享名 = '名'
_共享值 = '值'

__all__ = ['读方案', '溯源面板', '等价SQL', '面板文本', '对照表文本', 'SQL登记表']

#: 声明本工具产出的 SQL 方言。**不是**某个具体数据库的方言校验过的产物。
方言 = 'ANSI SQL（只作展示，本工具从不执行，也不连接任何数据库）'

#: 粘合器给每步结果起的变量名形状（`glue.result_var`）。等价 SQL 直接拿它当 CTE 名，
#: 「一步一个 CTE」就是「逐步对应」这四个字的落地方式。
_结果变量形 = re.compile(r'^赵果\d+$')


def _glue():
    """按路径载入同目录的 `glue.py`（它不是包内模块，`import glue` 不可靠）。"""
    路径 = os.path.join(_HERE, 'glue.py')
    spec = importlib.util.spec_from_file_location('w173_glue', 路径)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# 读方案 / 取块元数据
# --------------------------------------------------------------------------

def 读方案(路径):
    """读一份 `方案.json` 并过协议校验器。不合法就抛 `ValueError`（带中文原因）。"""
    with open(路径, 'r', encoding='utf-8') as f:
        方案 = json.load(f)
    问题 = validate_plan(方案)
    if 问题:
        raise ValueError('方案不合协议：%s' % '；'.join(问题))
    return 方案


def _块元(步骤):
    """按 `(领域, 块)` 读 `块.json`。

    **必须走 `load_block_metadata`，不能从 `索引.json` 拿**——索引条目的字段集
    （`blocks.py:_INDEX_ENTRY_KEYS`）里**没有 `版本`**，而版本是四要素之一。
    """
    领域 = 步骤.get(_F领域) or ''
    块名 = 步骤.get(_F块) or ''
    目录 = os.path.join(blocks_root(), 领域, 块名)
    return load_block_metadata(目录)


def _源码分行(方案):
    """合成源码，并把「第 i 步对应哪一行」切出来。

    `glue.synthesize` 只回一个字符串、不回「步骤 → 行」的映射，所以这里按步骤行的
    固定前缀 `定义赵果N=` 定位。那个前缀是 `glue.py:648` 的 `'定义%s=%s(%s)。'`
    写死的（`定义` 与变量名之间无空格），拿它当锚点是确定的。
    """
    glue = _glue()
    源码 = glue.synthesize(方案, 自动链式=True)
    行表 = 源码.split('\n')
    步数 = len(方案.get(_F步骤) or [])
    映射 = {}
    for i in range(步数):
        变量 = glue.result_var(i)
        前缀 = '定义%s=' % 变量
        for 行 in 行表:
            if 行.startswith(前缀):
                映射[i] = 行
                break
    return 源码, 映射


# --------------------------------------------------------------------------
# 溯源面板
# --------------------------------------------------------------------------

def 溯源面板(方案):
    """方案 → 溯源面板（dict）。

    `步骤[i]` 的四要素：`块` / `版本` / `口径声明` / `源码行`。另附 `领域`、
    `导出名`、`结果变量`、`实参`、`说明`（方案里作者自己写的那句），最后带整份 `源码`。
    """
    源码, 行映射 = _源码分行(方案)
    glue = _glue()
    步骤表 = []
    for i, s in enumerate(方案.get(_F步骤) or []):
        元 = _块元(s)
        步骤表.append({
            '序': i + 1,
            '结果变量': glue.result_var(i),
            '块': 元.name,
            '领域': s.get(_F领域),
            '导出名': s.get(_F导出名),
            '版本': 元.version,
            '层级': 元.level,
            '稳定性': 元.stability,
            '口径声明': 元.description,
            '实参': list(s.get(_F参数) or []),
            '说明': s.get(_F说明) or '',
            '源码行': 行映射.get(i, ''),
        })
    return {
        _F需求: 方案.get(_F需求) or '',
        '步骤': 步骤表,
        '源码': 源码,
    }


def 面板文本(面板):
    """溯源面板 → 人读文本。"""
    行 = []
    行.append('溯源面板')
    行.append('=' * 60)
    if 面板.get(_F需求):
        行.append('需求：%s' % 面板[_F需求])
    行.append('步数：%d' % len(面板['步骤']))
    for 步 in 面板['步骤']:
        行.append('')
        行.append('第 %d 步 · %s/%s → %s' % (步['序'], 步['领域'], 步['块'], 步['导出名']))
        行.append('  版本  ：%s（层级 %s，稳定性 %s）' % (步['版本'], 步['层级'], 步['稳定性']))
        行.append('  结果  ：%s' % 步['结果变量'])
        行.append('  实参  ：%s' % ('、'.join(步['实参']) if 步['实参'] else '（粘合器自动推链）'))
        if 步['说明']:
            行.append('  作者说明：%s' % 步['说明'])
        行.append('  源码行：%s' % 步['源码行'])
        行.append('  口径声明：%s' % 步['口径声明'])
    return '\n'.join(行) + '\n'


# --------------------------------------------------------------------------
# 等价 SQL：实参解析
# --------------------------------------------------------------------------

def _解字面(源):
    """把共享常量的**极快表达式源文本**解成 Python 值。

    只认三种形状，认不出就原样回传（调用方会把它当「解不开」处理）：
    `“串”` → `str`；`【“a”, “b”】` → `list[str]`；数字 → `float`/`int`。
    """
    if not isinstance(源, str):
        return 源
    t = 源.strip()
    if len(t) >= 2 and t[0] == '“' and t[-1] == '”':
        return t[1:-1]
    if len(t) >= 2 and t[0] == '【' and t[-1] == '】':
        内 = t[1:-1].strip()
        if not 内:
            return []
        项 = [p.strip() for p in re.split(r'[,，]', 内)]
        return [_解字面(p) for p in 项]
    if t == '真':
        return True
    if t == '假':
        return False
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return 源


def _解实参(方案, 步骤):
    """把一步的 `参数` 逐个解成 `('表', CTE名)` / `('值', 值)` / `('未知', 原文)`。

    `赵果N` 是上游步骤的结果 → 当 CTE 名；其余先在 `共享` 里查名字再解字面量。
    查不到就是 `未知`——**不猜**，这一步在对照表里会被标成未登记。
    """
    共享 = {}
    for 项 in 方案.get(_F共享) or []:
        if isinstance(项, dict) and 项.get(_共享名) is not None:
            共享[项[_共享名]] = 项.get(_共享值)
    出 = []
    for 原 in 步骤.get(_F参数) or []:
        文 = str(原)
        if _结果变量形.match(文):
            出.append(('表', 文))
        elif 文 in 共享:
            值 = _解字面(共享[文])
            出.append(('值', 值) if 值 != 共享[文] or not isinstance(值, str)
                      else ('未知', 文))
        else:
            出.append(('未知', 文))
    return 出


def _串(值):
    """Python 值 → SQL 字面量。单引号翻倍，别的一律不做转义花活。"""
    if 值 is None:
        return 'NULL'
    if isinstance(值, bool):
        return 'TRUE' if 值 else 'FALSE'
    if isinstance(值, (int, float)):
        return repr(值)
    return "'%s'" % str(值).replace("'", "''")


def _维度串(值):
    """维度列参数（列表或单列）→ 逗号分隔的列名串。"""
    if isinstance(值, (list, tuple)):
        return ', '.join(str(c) for c in 值)
    return str(值)


def _表名(路径值):
    """CSV 路径 → 表名（去目录、去 `.csv`）。等价 SQL 里 `FROM` 的就是这个名字。"""
    base = os.path.basename(str(路径值))
    return base[:-4] if base.lower().endswith('.csv') else base


# --------------------------------------------------------------------------
# 等价 SQL：逐块登记表
# --------------------------------------------------------------------------
# 每个登记项是 `f(实参) -> str`。实参已解析成 `('表'|'值', x)` 对。
# 列名与聚合口径**逐字照块的固定列常量**，不是凭印象写的：
#   单车电耗现成 → ('记录数','非空记录数','单车电耗均值')，均值分母是非空记录数
#   停线汇总(表) → ('记录数','计入记录数','缺测记录数','零停线记录数',
#                   '停线合计分钟','停线均值分钟','最长停线分钟')
#   缺陷率       → ('缺陷数','产量','缺陷率')，先分组汇总再相除（SUM/SUM）
#   缺陷汇总     → ('缺陷数','记录条数')，按缺陷数降序
#   能耗汇总     → ('记录数','电耗','水耗','气耗')，三个度量都是 SUM
#   基线偏离     → ('对齐','基线值','观察值','偏离','升幅')
#   窗间对比     → ('对齐','基期值','比期值','差值','变化率')

def _sql_读表(实参):
    kind, 值 = 实参[0]
    return 'SELECT *\nFROM %s' % _表名(值)


def _sql_截期(实参):
    _, 表 = 实参[0]
    _, 列 = 实参[1]
    _, 起 = 实参[2]
    _, 止 = 实参[3]
    return ('SELECT *\nFROM %s\nWHERE %s BETWEEN %s AND %s'
            '  -- 闭区间，两端都含' % (表, 列, _串(起), _串(止)))


def _sql_截外(实参):
    _, 表 = 实参[0]
    _, 列 = 实参[1]
    _, 起 = 实参[2]
    _, 止 = 实参[3]
    return ('SELECT *\nFROM %s\nWHERE %s IS NOT NULL AND %s NOT BETWEEN %s AND %s'
            '  -- 补集：窗外全体' % (表, 列, 列, _串(起), _串(止)))


def _sql_车耗现(实参):
    _, 表 = 实参[0]
    _, 维 = 实参[1]
    维串 = _维度串(维)
    return ('SELECT %s,\n'
            '       COUNT(*)                    AS 记录数,\n'
            '       COUNT(energy_per_vehicle)   AS 非空记录数,\n'
            '       AVG(energy_per_vehicle)     AS 单车电耗均值\n'
            '       -- 口径 A：现成比率列记录等权平均，分母是**非空记录数**。\n'
            '       -- SQL 的 AVG 本就跳过 NULL，与块一致；别改写成 SUM/COUNT(*)。\n'
            'FROM %s\nGROUP BY %s\nORDER BY %s' % (维串, 表, 维串, 维串))


def _sql_计耗(实参):
    _, 表 = 实参[0]
    _, 维 = 实参[1]
    维串 = _维度串(维)
    return ('SELECT %s,\n'
            '       COUNT(*)                 AS 记录数,\n'
            '       SUM(electricity_kwh)     AS 电耗,\n'
            '       SUM(water_ton)           AS 水耗,\n'
            '       SUM(gas_m3)              AS 气耗\n'
            '       -- 绝对量侧（ADR-40 §5.5）：三个度量都是 SUM，不是均值。\n'
            'FROM %s\nGROUP BY %s\nORDER BY %s' % (维串, 表, 维串, 维串))


def _sql_计停表(实参):
    _, 表 = 实参[0]
    _, 维 = 实参[1]
    维串 = _维度串(维)
    return ('SELECT %s,\n'
            '       COUNT(*)                             AS 记录数,\n'
            '       COUNT(downtime_minutes)              AS 计入记录数,\n'
            '       COUNT(*) - COUNT(downtime_minutes)   AS 缺测记录数,\n'
            '       SUM(CASE WHEN downtime_minutes = 0 THEN 1 ELSE 0 END)'
            ' AS 零停线记录数,\n'
            '       SUM(downtime_minutes)                AS 停线合计分钟,\n'
            '       AVG(downtime_minutes)                AS 停线均值分钟,\n'
            '       MAX(downtime_minutes)                AS 最长停线分钟\n'
            '       -- 均值分母是**计入记录数**（非空行），不是记录数。\n'
            'FROM %s\nGROUP BY %s\nORDER BY %s' % (维串, 表, 维串, 维串))


def _sql_计陷(实参):
    _, 表 = 实参[0]
    _, 维 = 实参[1]
    维串 = _维度串(维)
    return ('SELECT %s,\n'
            '       SUM(defect_count) AS 缺陷数,\n'
            '       COUNT(*)          AS 记录条数\n'
            'FROM %s\nGROUP BY %s\nORDER BY 缺陷数 DESC, %s'
            '  -- 本块自己按缺陷数降序（排行榜口径）' % (维串, 表, 维串, 维串))


def _sql_陷率(实参):
    _, 缺 = 实参[0]
    _, 产 = 实参[1]
    _, 维 = 实参[2]
    维串 = _维度串(维)
    连接 = ' AND '.join('缺.%s = 产.%s' % (c.strip(), c.strip())
                        for c in 维串.split(','))
    投影 = ', '.join('缺.%s' % c.strip() for c in 维串.split(','))
    return ('SELECT %s,\n'
            '       缺.缺陷数,\n'
            '       产.产量,\n'
            '       CAST(缺.缺陷数 AS REAL) / 产.产量 AS 缺陷率\n'
            '       -- ADR-40 §5.3 采纳口径：**先分组汇总分子分母、再相除**\n'
            '       -- （SUM/SUM）。不是逐行率求平均——那一侧本身是错的。\n'
            'FROM (SELECT %s, SUM(defect_count)    AS 缺陷数 FROM %s GROUP BY %s) 缺\n'
            'JOIN (SELECT %s, SUM(actual_quantity) AS 产量   FROM %s GROUP BY %s) 产\n'
            '  ON %s\n'
            'ORDER BY 缺陷率 DESC'
            % (投影, 维串, 缺, 维串, 维串, 产, 维串, 连接))


def _两窗对照(观表, 基表, 维串, 度量列, 左名, 右名, 差名, 率名):
    """`基线偏离` / `窗间对比` 共用的两窗对照 SQL。

    `对齐` 那一列是三态（两窗都有 / 只这边有 / 只那边有），所以必须 **FULL OUTER
    JOIN**：内连接会把「只有一侧有」的组悄悄丢掉，那正是块要报出来的信息。
    注意 SQLite / MySQL 没有 FULL OUTER JOIN，贴过去要改写成两个 LEFT JOIN 的 UNION。
    """
    键 = [c.strip() for c in 维串.split(',')]
    连接 = ' AND '.join('甲.%s = 乙.%s' % (k, k) for k in 键)
    投影 = ', '.join('COALESCE(甲.%s, 乙.%s) AS %s' % (k, k, k) for k in 键)
    return ('SELECT %s,\n'
            '       CASE WHEN 甲.%s IS NULL THEN \'只基线窗有\'\n'
            '            WHEN 乙.%s IS NULL THEN \'只观察窗有\'\n'
            '            ELSE \'两窗都有\' END AS 对齐,\n'
            '       乙.%s AS %s,\n'
            '       甲.%s AS %s,\n'
            '       甲.%s - 乙.%s AS %s,\n'
            '       (甲.%s - 乙.%s) / 乙.%s AS %s\n'
            '       -- 甲 = %s、乙 = %s（两个表参数粘合器分不清，方案里必须显式写）。\n'
            'FROM %s 甲\nFULL OUTER JOIN %s 乙 ON %s\n'
            '  -- 内连接会丢掉「只有一侧有」的组，而那恰是「对齐」要报的事；\n'
            '  -- SQLite/MySQL 无 FULL OUTER JOIN，需改写成两个 LEFT JOIN 的 UNION。\n'
            'ORDER BY %s'
            % (投影, 键[0], 键[0],
               度量列, 左名, 度量列, 右名,
               度量列, 度量列, 差名,
               度量列, 度量列, 度量列, 率名,
               观表, 基表, 观表, 基表, 连接, ', '.join(键)))


def _sql_基偏(实参):
    _, 观 = 实参[0]
    _, 基 = 实参[1]
    _, 维 = 实参[2]
    _, 度 = 实参[3]
    return _两窗对照(观, 基, _维度串(维), 度, '基线值', '观察值', '偏离', '升幅')


def _sql_窗比(实参):
    _, 基 = 实参[0]
    _, 比 = 实参[1]
    _, 维 = 实参[2]
    _, 度 = 实参[3]
    return _两窗对照(比, 基, _维度串(维), 度, '基期值', '比期值', '差值', '变化率')


def _sql_判势(实参):
    _, 表 = 实参[0]
    _, 率 = 实参[1]
    _, 参 = 实参[2]
    _, 阈 = 实参[3]
    return ('SELECT *,\n'
            '       CASE WHEN %s IS NULL     THEN \'无法判断\'\n'
            '            WHEN %s >  %s       THEN \'上升\'\n'
            '            WHEN %s < -%s       THEN \'下降\'\n'
            '            ELSE \'持平\' END AS 趋势,\n'
            '       %s AS 参照系,\n'
            '       %s AS 判据比率列,\n'
            '       %s AS 持平阈值\n'
            '       -- 两端都算持平（|r| ≤ 阈值）；比率为空是「无法判断」，\n'
            '       -- **不折进持平**。阈值与比率同量纲（0-1 小数）。\n'
            'FROM %s' % (率, 率, _串(阈), 率, _串(阈),
                         _串(参), _串(率), _串(阈), 表))


def _sql_定序(实参):
    _, 表 = 实参[0]
    _, 列 = 实参[1]
    降 = 实参[2][1] if len(实参) > 2 else False
    方向 = 'DESC' if 降 is True else 'ASC'
    return ('SELECT *\nFROM %s\nORDER BY %s %s NULLS LAST'
            '  -- 块把空值一律垫底（升降序都一样），所以 NULLS LAST 不能省'
            % (表, 列, 方向))


def _sql_摘前(实参):
    _, 表 = 实参[0]
    _, n = 实参[1]
    return ('SELECT *\nFROM %s\nLIMIT %s'
            '  -- 本块只截取、不排序；要「前 N 名」得先经过 定序' % (表, n))


def _sql_达权(实参):
    _, 表 = 实参[0]
    _, 实列 = 实参[1]
    _, 计列 = 实参[2]
    return ('SELECT CAST(SUM(%s) AS REAL) / SUM(%s) AS 达成率,\n'
            '       SUM(%s) AS 实际产量合计,\n'
            '       SUM(%s) AS 计划产量合计,\n'
            '       COUNT(*) AS 参与行数\n'
            '       -- 产量加权侧（ADR-40 §5.1）：SUM/SUM，分母是计划产量合计。\n'
            'FROM %s' % (实列, 计列, 实列, 计列, 表))


def _sql_达均(实参):
    _, 表 = 实参[0]
    _, 列 = 实参[1]
    return ('SELECT AVG(%s)   AS 平均达成率,\n'
            '       SUM(%s)   AS 达成率合计,\n'
            '       COUNT(%s) AS 参与行数\n'
            '       -- 行级算术平均侧（ADR-40 §5.1）：每条记录等权，\n'
            '       -- 分母是参与行数。与加权侧是两个口径，别混。\n'
            'FROM %s' % (列, 列, 列, 表))


#: 块名 → 等价 SQL 生成函数。**只登记制造域**、且只登记「输出是一张表 / 一行标量」
#: 的块。没进这张表的块一律走「未登记」路径。
SQL登记表 = {
    '表载入': _sql_读表,
    '窗口': _sql_截期,
    '窗口补集': _sql_截外,
    '单车电耗现成': _sql_车耗现,
    '能耗汇总': _sql_计耗,
    '停线汇总表': _sql_计停表,
    '缺陷汇总': _sql_计陷,
    '缺陷率': _sql_陷率,
    '基线偏离': _sql_基偏,
    '窗间对比': _sql_窗比,
    '趋势判定': _sql_判势,
    '排序': _sql_定序,
    '取前N': _sql_摘前,
    '达成率权重': _sql_达权,
    '达成率均值': _sql_达均,
}

#: 刻意不登记的块 → 理由。**这张表比登记表更要紧**：它把「没写」和「不该写」分开。
不登记理由 = {
    '质量体检': '输出是四元组 [行数, 空值列与计数, 主键重复数, 外键孤儿行数]，'
                '不是一张表。四项互不同构（两项还是字典），硬塞成一条 SELECT '
                '会把它伪装成一行数据——那比没有 SQL 糟。',
    '多窗口': '入参是「多段窗口的列表」，段数由数据决定；展开成 N 个 OR 条件的 SQL '
              '要按实参现编列数，本工具不做这种依赖实参形状的模板。',
    '邻期关联': '多对多关联 + 双去重标记（左行序/右行序）在 SQL 里要靠窗口函数或子查询'
                '两种写法，选哪种会改变「膨胀在哪一侧」的呈现，属口径判断，不替业务拍。',
}


def 等价SQL(方案):
    """方案 → 等价 SQL 导出（dict）。

    `对照[i]` = `{序, 结果变量, 块, 源码行, SQL, 覆盖, 未登记理由?}`。
    `全文` 只有在**每一步都登记**时才给（一步一个 CTE 的 `WITH` 链）；否则为 `None`
    并在 `全文缺省原因` 里说清是哪几步没登记——**不给半截 SQL 让人以为能跑**。
    """
    源码, 行映射 = _源码分行(方案)
    glue = _glue()
    对照 = []
    未登记 = []
    for i, s in enumerate(方案.get(_F步骤) or []):
        块名 = s.get(_F块)
        变量 = glue.result_var(i)
        条 = {
            '序': i + 1,
            '结果变量': 变量,
            '块': 块名,
            '导出名': s.get(_F导出名),
            '源码行': 行映射.get(i, ''),
        }
        生成 = SQL登记表.get(块名)
        实参 = _解实参(方案, s)
        未知 = [文 for 类, 文 in 实参 if 类 == '未知']
        if 生成 is None:
            条['覆盖'] = False
            条['未登记理由'] = 不登记理由.get(
                块名, '本工具没有为这个块登记等价 SQL，拒绝猜一段出来。')
            条['SQL'] = None
            未登记.append(块名)
        elif 未知:
            条['覆盖'] = False
            条['未登记理由'] = ('实参 %s 既不是共享常量也不是上游结果，'
                                '本工具解不开它的值，拒绝猜。' % '、'.join(未知))
            条['SQL'] = None
            未登记.append(块名)
        else:
            try:
                条['SQL'] = 生成(实参)
                条['覆盖'] = True
            except (IndexError, TypeError, ValueError) as e:
                条['覆盖'] = False
                条['未登记理由'] = '按登记模板生成失败（%s），拒绝出半成品。' % e
                条['SQL'] = None
                未登记.append(块名)
        对照.append(条)

    全文 = None
    缺省原因 = ''
    if 对照 and all(c['覆盖'] for c in 对照):
        片 = []
        for c in 对照:
            片.append('%s AS (\n%s\n)' % (
                c['结果变量'],
                '\n'.join('  ' + l for l in c['SQL'].split('\n'))))
        末 = 对照[-1]['结果变量']
        全文 = ('-- %s\n-- 一步一个 CTE，CTE 名 = 方案里那一步的结果变量。\n'
                '-- 已知失真：CTE 里的 ORDER BY 在多数引擎里**不保证**传到外层，而块\n'
                '-- 是保证行序的（汇总块按维度键升序、缺陷两块按度量降序）。要拿到与\n'
                '-- 极快一致的行序，得把 ORDER BY 再写一遍在最外层。\n'
                'WITH %s\nSELECT * FROM %s;\n'
                % (方言, ',\n'.join(片), 末))
    else:
        缺省原因 = ('有 %d 步没有等价 SQL（%s），因此不出 WITH 全文——'
                    '半截 SQL 会被误当成「能跑的那份」。'
                    % (sum(1 for c in 对照 if not c['覆盖']),
                       '、'.join(sorted(set(未登记))) or '见对照表'))

    return {
        _F需求: 方案.get(_F需求) or '',
        '方言': 方言,
        '对照': 对照,
        '全文': 全文,
        '全文缺省原因': 缺省原因,
        '未登记块': sorted(set(未登记)),
    }


def 对照表文本(导出):
    """等价 SQL 导出 → 人读的「`.jk` 源码行 ↔ SQL」逐步对照表。"""
    行 = []
    行.append('等价 SQL 逐步对照表')
    行.append('=' * 60)
    if 导出.get(_F需求):
        行.append('需求：%s' % 导出[_F需求])
    行.append('方言：%s' % 导出['方言'])
    行.append('覆盖：%d/%d 步登记了等价 SQL'
              % (sum(1 for c in 导出['对照'] if c['覆盖']), len(导出['对照'])))
    for c in 导出['对照']:
        行.append('')
        行.append('-' * 60)
        行.append('第 %d 步 · %s → %s' % (c['序'], c['块'], c['导出名']))
        行.append('  .jk ：%s' % c['源码行'])
        if c['覆盖']:
            行.append('  SQL ：')
            for l in c['SQL'].split('\n'):
                行.append('        ' + l)
        else:
            行.append('  SQL ：未登记 —— %s' % c['未登记理由'])
    行.append('')
    行.append('=' * 60)
    if 导出['全文']:
        行.append('WITH 链全文（一步一个 CTE，只作展示、不执行）：')
        行.append('')
        行.append(导出['全文'].rstrip())
    else:
        行.append('无 WITH 链全文：%s' % 导出['全文缺省原因'])
    return '\n'.join(行) + '\n'


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(
        description='溯源面板 + 等价 SQL 导出（纯函数式，不连库、不执行 SQL）')
    p.add_argument('子命令', choices=['面板', 'SQL'])
    p.add_argument('方案', help='方案 JSON 的路径')
    p.add_argument('--json', action='store_true', help='出机读 JSON 而不是人读文本')
    p.add_argument('--出', dest='出', default=None,
                   help='写到这个文件（UTF-8 无 BOM）而不是 stdout。'
                        'Windows 控制台按 GBK 转码会把中文列名压坏，'
                        '产出物要存档时用这个而不是 shell 重定向')
    args = p.parse_args(argv)

    try:
        方案 = 读方案(args.方案)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        sys.stderr.write('读方案失败：%s\n' % e)
        return 1

    出 = 溯源面板(方案) if args.子命令 == '面板' else 等价SQL(方案)
    if args.json:
        文本 = json.dumps(出, ensure_ascii=False, indent=2) + '\n'
    else:
        文本 = 面板文本(出) if args.子命令 == '面板' else 对照表文本(出)
    if args.出:
        with open(args.出, 'w', encoding='utf-8', newline='\n') as f:
            f.write(文本)
    else:
        sys.stdout.write(文本)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
