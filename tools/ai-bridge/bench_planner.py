# -*- coding: utf-8 -*-
"""规划器基准 —— 上下文包 / 分档拒答 / 录像回放 一次跑完（v0.27.0 W160）。

## 这支 bench 与 `bench_chatbi.py` 量的不是同一层

`bench_chatbi.py` 量**检索层**：TF-IDF 会不会把正确的块捞进 top-K、会不会在负例上
自信地给出制造块名。本脚本量**规划器层**（`tools/ai-bridge/planner.py`）：

- `build_context()` 出的上下文包，候选白名单能不能**承载**一份正确方案；
- `拒答建议.覆盖`（词表覆盖判定，**无任何分数阈值**）在两档负例上的分档拒答率；
- `validate_filled()` 的五条硬规则，回放 `规划录像/` 里的回填 JSON 时判成什么。

**两层的数字不许互相掩盖**（AGENTS.md §六、v0.27.0 WBS W159 DoD）：规划器层在远离档
拒下了一半，不代表检索层的 0.0% 变好了——检索层一行没改，它照旧永远返回 top-K。

## 为什么回放要用全库当白名单

`validate_filled` 规则 2 是「块/领域/导出名必须来自上下文包 `候选`」。实测（W160）
正确方案用到的块在 TF-IDF 排名里最深落到 **第 89 名**（`Q_HID_004` 的 `表载入`），
默认 `top=8` 下白名单**装不下任何一份**正确方案。所以：

- 「白名单能不能装下正确方案」单独量成 ``白名单可承载率``，按 K 分档报（见下）；
- 回放本身用 ``--回放top`` （缺省 = 块库全量）建包，把规则 2 从瓶颈位置挪开，
  这样回放量到的才是**规则 1/3/4/5 + 组 + 跑**，而不是又一次量检索召回。

这不是给回放放水：两个数字都在同一份报表里，谁也没被藏起来。

**v0.28.0 W174 起上面那个 0.0% 不再是现状**：`build_context` 加了语义层直取旁路
（ADR-41 §9），K=8 承载率 **0.0% → 60.0% / 60.0%**（调优 / 留出）。本节保留原文是因为
它记的是**为什么 `--回放top` 缺省全量**——那条口径不随旁路改：回放要量的仍是规则
1/3/4/5 + 组 + 跑，不是又量一次召回。旁路带来的候选里有 `路径 = [语义层]` 的条目，
它们的 `分数` 恒 0.0，**不要**拿去和 TF-IDF 分数比大小。


## 录像回放的判定为什么登记在清单里

前 15 份录像转录自 `赛题/chatbi/产出/` 的 W145 手写方案（`模型` 字段如实写
`人工·v0.26.0 W145`）。其中 **4 份会被 W157 的规则 3 拒掉**，拒得对：

- W145 写方案时刻意把 `单车电耗现成`/`单车电耗重算`（`达成率权重`/`达成率均值`）
  两侧口径**并排跑**当人工交叉校验——对人是好报表，对协议是「两个不同的答案」；
- `窗间对比`/`基线偏离` 是 W154/W155 才有的块，W145 无从选起，只能在 `说明` 里
  写「这一步的相除要人来做」。

所以 `清单.json` 给每条录像登记 ``期望判定``（`通过`/`拒答`）与 ``期望拒因``
（要出现在拒因里的关键词）。回放绿的判据是**实际判定与登记一致**，不是「全部通过」。
这样它既是确定性回放（ADR-41 §8），又是校验器行为的回归锁：哪天补了环比块或改了
规则 3，本脚本当场红，逼人回来重新看这 4 条，而不是让它悄悄变绿。

**v0.28.0 W175 追加 3 份（共 18 份）**：`Q_PUB_004_v27` / `Q_HID_003_v27` /
`Q_HID_005_v27`，`模型` 写 `人工·v0.27.0 口径`。它们与上面那 4 条被拒的录像**问句相同**
（清单里用 `问句id` 指回评测集原 id），但按 W154/W155 的 `窗间对比`/`基线偏离` 显式选了
**一侧**口径，因此判定是 `通过` 且组/跑全通——W145 那 4 条**一字不改**保留，两代口径
并排摆着才看得出「规则 3 拒的到底是什么」。所以现在是 **14 通过 / 4 拒答**。

## 指标

正例两档（调优 15 / 留出 5，`期望` 取自评测集）：

- ``期望块覆盖率``：|top-K ∩ 期望| / |期望|，逐用例算后取平均（口径同 bench_chatbi）
- ``期望块完整命中率``：`期望` 全部进 top-K 的用例占比
- ``白名单可承载率``：**录像方案**用到的块全部进 top-K 的用例占比。分母仍是 **W145 那
  15 份**（`录像块表` 按录像 `id` 建，W175 追加的 3 份 `_v27` id 不与评测集 id 相撞，
  只进回放、不进这个口径）——不然 W174 已报的 0.0% → 60.0% 就换了分母，没法比。


负例两档（远离 10 / 近边缘 8，top 固定为规划器默认 8）：

- ``规划器层拒答率``：`拒答建议.覆盖 == False` 的用例占比。与检索层 0.0% 并列
- ``兄弟块诱骗率``：候选里出现该条 `兄弟块` 任一的用例占比。与检索层 0.625 并列

录像回放：

- ``判定一致率``：实际判定 == 清单登记的 `期望判定` 的录像占比（回放绿的判据）
- ``通过并跑通数``：校验通过 → `组` 无占位 → `跑` 无错误
- ``关键实体命中率``：`期望结果` 里的机读实体（`M003`/`L002`/`C005` 这类 ID）出现在
  `跑` 的 stdout 里的占比。**分母只算 `期望结果` 里真有此类 ID 的录像**；
  `期望结果` 的口径散文（「先汇总后相除」「夜班明显低于白班」）**不做自动判定**，
  那要人读——写清楚比假装量过了强。

## 用法

    python tools/ai-bridge/bench_planner.py
    python tools/ai-bridge/bench_planner.py --json
    python tools/ai-bridge/bench_planner.py --只回放 --门禁     # W161 的 G23 用

`--门禁` 下回放判定与清单不一致即退 1；缺省是报数模式，恒退 0（与 bench_chatbi 同）。

零第三方依赖、零网络、零 API key（ADR-41 §8：CI 只回放，真调模型只在本机人工跑）。
`跑` 会读 `赛题/chatbi/数据集/*.csv`，故必须能从仓库根定位到那些相对路径——本脚本
自己 `chdir` 到仓库根，不依赖调用方的当前目录。
"""

import argparse
import io
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence


def _reconfigure_utf8():
    """Windows GBK 控制台下强制 UTF-8 输出（与 bench_chatbi.py 同做法）。

    **只在 `__main__` 下调用**：它换掉 `sys.stdout`/`sys.stderr`，而 pytest 的
    capture 也接管这两个流。导入期就换会把 capture 的 tmpfile 关掉，整轮 pytest
    在 teardown 抛 `ValueError: I/O operation on closed file`、一个测试都跑不了。
    本模块要被 `tests/test_v0_27_0_w160_*.py` 直接导入，故这一步下沉到入口。
    """
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                      errors='replace', line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8',
                                      errors='replace', line_buffering=True)
    except Exception:
        pass


_HERE = os.path.abspath(os.path.dirname(__file__))
# 本目录的 select.py 会遮蔽标准库 select，所以脚本目录不能排在标准库前面。
# 但**不能像 bench_chatbi.py 那样把它从 sys.path 里删掉**：本模块会被 pytest 导入，
# 删掉之后同一轮里 `tests/test_glue_type.py` 的 `import bench_glue` 当场
# ModuleNotFoundError（conftest 加的那条被我们抹了）。改成「挪到末尾」——遮蔽和
# 可导入两件事同时满足。
sys.path[:] = ([p for p in sys.path if os.path.abspath(p) != _HERE]
               + [p for p in sys.path if os.path.abspath(p) == _HERE])
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

#: 正例两档（沿用 bench_chatbi.py 的文件名，不另立一套）
POS_TUNE = '评测集-chatbi.json'
POS_HOLD = '评测集-chatbi-留出.json'

#: 负例两档：档名 -> 文件名
NEG_TIERS: Dict[str, str] = {
    '远离': '评测集-chatbi-无覆盖.json',
    '近边缘': '评测集-chatbi-近边缘.json',
}

#: 录像目录与清单（ADR-41 §8）
录像目录 = '规划录像'
录像清单 = '清单.json'

#: 正例档报三个 K。8 是 `build_context` 的默认值，20/40 用来看白名单要多深才装得下
#: 一份正确方案——W160 实测最深的块排到第 89 名，这两个 K 是为了让那条曲线有形状。
POS_TOPS = (8, 20, 40)

#: 负例档固定用规划器默认 top。负例量的是「覆盖判定」，与 K 无关（覆盖判据是
#: 语义层业务词 + 候选非空），把 K 钉死免得读数的人以为调 K 能改善拒答率。
NEG_TOP = 8

#: 检索层的既有数字（`bench_chatbi.py` 实测，块库 137/139 均为此值），并列对照用。
#: 写死在这里是为了让两层的数字**出现在同一张表上**；它不参与任何计算。
检索层拒答率 = 0.0
检索层兄弟诱骗率 = 0.625

#: `期望结果` 里可机读的实体：车型/产线/客户/车间 ID 一律 `字母+三位数字`。
_实体 = re.compile(r'\b[A-Z]\d{3}\b')


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------

def load_evalset(name: str) -> dict:
    with open(os.path.join(_HERE, name), 'r', encoding='utf-8') as f:
        return json.load(f)


def _planner():
    """按路径加载 `planner.py`（它不进 wheel，见 ADR-41 §7）。"""
    import importlib.util
    path = os.path.join(_HERE, 'planner.py')
    spec = importlib.util.spec_from_file_location('_bench_planner_mod', path)
    if spec is None or spec.loader is None:      # pragma: no cover - 环境异常
        raise SystemExit('错误：%s 无法作为模块加载' % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def 块库块数() -> int:
    from jikuai.pkg.blocks import load_index
    index = load_index()
    if not index:
        raise SystemExit('错误：找不到 stdlib/blocks/索引.json，先跑 '
                         'scripts/generate_block_index.py')
    return len(index.get('块') or [])


def 读录像(目录: Optional[str] = None) -> List[dict]:
    """读清单 + 每条录像的回填信封。返回逐条 dict（含 `信封` 键）。

    `目录` 只为 W161 的 G23 反例而存在：门禁要能指向 `tmp_path` 里的一份篡改副本，
    验证「篡改块名 / 删 `参数` / 改 `期望判定`」三类反例真能让回放红。缺省走仓内
    `规划录像/`。
    """
    d = 目录 or os.path.join(_HERE, 录像目录)
    with open(os.path.join(d, 录像清单), 'r', encoding='utf-8') as f:
        清单 = json.load(f)
    出 = []
    for 条 in 清单.get('录像') or []:
        with open(os.path.join(d, 条['文件']), 'r', encoding='utf-8') as f:
            条 = dict(条)
            条['信封'] = json.load(f)
        出.append(条)
    return 出


# ---------------------------------------------------------------------------
# 正例：上下文包能不能承载一份正确方案
# ---------------------------------------------------------------------------

def 评上下文包(pl, 用例: Sequence[dict], 录像块表: Dict[str, List[str]],
             top: int) -> Dict[str, Any]:
    """逐条建包，量期望块覆盖与「白名单可承载」。"""
    覆盖 = []
    完整 = 0
    承载分母 = 0
    承载 = 0
    明细 = []
    for c in 用例:
        包 = pl.build_context(c['需求'], top=top)
        候选 = {x['名称'] for x in 包['候选']}
        期望 = set(c.get('期望') or ())
        命中 = 候选 & 期望
        覆盖.append(len(命中) / len(期望) if 期望 else 0.0)
        全中 = bool(期望) and 期望 <= 候选
        完整 += 1 if 全中 else 0
        方案块 = 录像块表.get(c['id'])
        装得下 = None
        if 方案块:
            承载分母 += 1
            装得下 = set(方案块) <= 候选
            承载 += 1 if 装得下 else 0
        明细.append({'id': c['id'], '期望缺': sorted(期望 - 候选),
                     '期望全中': 全中, '白名单装得下方案': 装得下})
    n = len(用例) or 1
    return {
        '用例数': len(用例),
        '期望块覆盖率': sum(覆盖) / n,
        '期望块完整命中率': 完整 / n,
        '白名单可承载率': (承载 / 承载分母) if 承载分母 else None,
        '有录像用例数': 承载分母,
        '明细': 明细,
    }


# ---------------------------------------------------------------------------
# 负例：规划器层的分档拒答与兄弟块诱骗
# ---------------------------------------------------------------------------

def 评负例(pl, 用例: Sequence[dict], top: int = NEG_TOP) -> Dict[str, Any]:
    行 = []
    for c in 用例:
        包 = pl.build_context(c['需求'], top=top)
        候选 = {x['名称'] for x in 包['候选']}
        兄弟 = list(c.get('兄弟块') or ())
        行.append({
            '需求': c['需求'],
            '覆盖': bool(包['拒答建议']['覆盖']),
            '语义命中': [h['业务词'] for h in 包['语义命中']],
            '兄弟块': 兄弟,
            '被诱骗兄弟块': [b for b in 兄弟 if b in 候选],
            '分歧告警数': len(包.get('分歧告警') or ()),
        })
    n = len(行) or 1
    有兄弟 = [r for r in 行 if r['兄弟块']]
    报 = {
        '用例数': len(行),
        '规划器层拒答率': sum(1 for r in 行 if not r['覆盖']) / n,
        '拒下条数': sum(1 for r in 行 if not r['覆盖']),
        '带分歧告警条数': sum(1 for r in 行 if r['分歧告警数']),
        '明细': 行,
    }
    if 有兄弟:
        报['带兄弟块用例数'] = len(有兄弟)
        报['兄弟块诱骗率'] = (sum(1 for r in 有兄弟 if r['被诱骗兄弟块'])
                             / len(有兄弟))
    return 报


# ---------------------------------------------------------------------------
# 录像回放：校验器 → 组 → 跑
# ---------------------------------------------------------------------------

def 回放一条(pl, 条: dict, 回放top: int, 期望结果: str,
           严格: bool = False) -> Dict[str, Any]:
    """回放单条录像。不抛异常：任何失败都收敛成结果字段，回放要跑完全档。"""
    from jikuai.pkg import blocks_cli as bc
    from jikuai.pkg.blocks import BlockError

    信封 = 条['信封']
    包 = pl.build_context(信封['需求'], top=回放top)
    理由 = pl.validate_filled(信封, 包, 严格=严格)
    出: Dict[str, Any] = {
        'id': 条['id'], '模型': 信封['模型'],
        '判定': '拒答' if 理由 else '通过',
        '拒因': 理由,
        '期望判定': 条.get('期望判定'),
        '期望拒因': list(条.get('期望拒因') or ()),
    }
    出['判定一致'] = _判定一致(出)
    if 理由:
        return 出
    try:
        方案 = bc._校验方案(信封['方案'])
        源码 = bc._组装(方案, 自动链式=False)
    except BlockError as e:
        出['组失败'] = str(e)
        return 出
    出['占位'] = ('需人工填参' in 源码) or ('?' in 源码)
    结果 = bc._执行源码(源码)
    出['跑错误'] = 结果.get('错误')
    stdout = 结果.get('stdout') or ''
    出['stdout字符数'] = len(stdout)
    出['跑通'] = (not 出['占位']) and not 出['跑错误']
    实体 = sorted(set(_实体.findall(期望结果 or '')))
    出['期望实体'] = 实体
    if 实体:
        出['实体命中'] = [e for e in 实体 if e in stdout]
        出['实体全中'] = len(出['实体命中']) == len(实体)
    return 出


def _判定一致(出: Dict[str, Any]) -> bool:
    """实际判定是否与清单登记一致。拒答还要求每个登记关键词都出现在某条拒因里。"""
    if 出['期望判定'] != 出['判定']:
        return False
    if 出['判定'] != '拒答':
        return True
    合 = '\n'.join(出['拒因'])
    return all(k in 合 for k in 出['期望拒因'])


def 回放(pl, 录像: Sequence[dict], 期望结果表: Dict[str, str],
       回放top: int, 严格: bool = False) -> Dict[str, Any]:
    行 = [回放一条(pl, 条, 回放top, 期望结果表.get(条['id'], ''), 严格)
          for 条 in 录像]
    n = len(行) or 1
    有实体 = [r for r in 行 if r.get('期望实体')]
    跑过 = [r for r in 行 if '跑通' in r]
    报 = {
        '录像数': len(行),
        '回放top': 回放top,
        '判定一致率': sum(1 for r in 行 if r['判定一致']) / n,
        '判定不一致': [r['id'] for r in 行 if not r['判定一致']],
        '通过数': sum(1 for r in 行 if r['判定'] == '通过'),
        '拒答数': sum(1 for r in 行 if r['判定'] == '拒答'),
        '通过并跑通数': sum(1 for r in 跑过 if r['跑通']),
        '组失败数': sum(1 for r in 行 if '组失败' in r),
        '明细': 行,
    }
    if 有实体:
        报['带机读实体录像数'] = len(有实体)
        报['关键实体命中率'] = (sum(1 for r in 有实体 if r.get('实体全中'))
                               / len(有实体))
    return 报


# ---------------------------------------------------------------------------
# 打印
# ---------------------------------------------------------------------------

_横 = '=' * 74


def _百(x: Optional[float]) -> str:
    return '—' if x is None else '%.1f%%' % (x * 100.0)


def _打正例(档名: str, r: Dict[str, Any], top: int):
    print('%6d │ %8s %8s %8s' % (top, _百(r['期望块覆盖率']),
                                  _百(r['期望块完整命中率']),
                                  _百(r['白名单可承载率'])))


def _打负例(档名: str, r: Dict[str, Any]):
    print(_横)
    print('负例·%s 档（%d 条 · top=%d）' % (档名, r['用例数'], NEG_TOP))
    print('  规划器层拒答率 %s（拒下 %d 条） ← 同档检索层 %s'
          % (_百(r['规划器层拒答率']), r['拒下条数'], _百(检索层拒答率)))
    if '兄弟块诱骗率' in r:
        print('  兄弟块诱骗率 %s（%d 条标了兄弟块） ← 同档检索层 %s'
              % (_百(r['兄弟块诱骗率']), r['带兄弟块用例数'],
                 _百(检索层兄弟诱骗率)))
    print('  带分歧告警 %d 条' % r['带分歧告警条数'])


def _打回放(r: Dict[str, Any]):
    print(_横)
    print('录像回放（%d 份 · 回放top=%d · 校验器 → 组 → 跑）' % (r['录像数'], r['回放top']))
    print('  判定一致率 %s%s' % (_百(r['判定一致率']),
                                 ('' if not r['判定不一致']
                                  else '  ⚠不一致：%s' % '、'.join(r['判定不一致']))))
    print('  校验通过 %d 份，其中组跑全通 %d 份；校验拒下 %d 份；组失败 %d 份'
          % (r['通过数'], r['通过并跑通数'], r['拒答数'], r['组失败数']))
    if '关键实体命中率' in r:
        print('  关键实体命中率 %s（%d 份录像的 期望结果 里有机读 ID；口径散文不自动判）'
              % (_百(r['关键实体命中率']), r['带机读实体录像数']))


def _打拒因(r: Dict[str, Any]):
    for 行 in r['明细']:
        if 行['判定'] != '拒答':
            continue
        print('  --- %s 被拒 %d 条 ---' % (行['id'], len(行['拒因'])))
        for t in 行['拒因']:
            print('    · %s' % t.splitlines()[0][:110])


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(argv: Optional[Sequence[str]] = None) -> int:
    """入口。`跑` 要读 `赛题/chatbi/数据集/*.csv` 的相对路径，故全程钉死工作目录到
    仓库根——**并在退出时还原**：本函数会被 pytest 在同一进程里调用，改了不还原会
    把后面的测试全带跑偏。
    """
    旧cwd = os.getcwd()
    try:
        return _run(argv)
    finally:
        os.chdir(旧cwd)


def _run(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description='规划器基准（上下文包 / 分档拒答 / 录像回放 一次报数）')
    p.add_argument('--回放top', dest='replay_top', type=int, default=None,
                   help='回放时上下文包的候选数上限。缺省 = 块库全量：把规则 2 的'
                        '白名单从瓶颈位置挪开，让回放量规则 1/3/4/5 与 组/跑')
    p.add_argument('--严格', dest='strict', action='store_true',
                   help='回放时开 validate_filled 的严格模式（规则 5）')
    p.add_argument('--只回放', dest='only_replay', action='store_true',
                   help='只跑录像回放，跳过正负例档（G23 门禁用，最省时）')
    p.add_argument('--门禁', dest='gate', action='store_true',
                   help='回放判定与清单登记不一致即退 1（缺省只报数、恒退 0）')
    p.add_argument('--拒因', dest='show_reasons', action='store_true',
                   help='打印被拒录像的逐条拒因首行')
    p.add_argument('--录像目录', dest='replay_dir', default=None,
                   help='录像目录（含 清单.json）。缺省 = 仓内 规划录像/；'
                        'G23 的反例用它指向 tmp_path 里的篡改副本')
    p.add_argument('--json', action='store_true', help='只输出 JSON 指标')
    args = p.parse_args(list(argv) if argv is not None else None)

    # `--录像目录` 先绝对化再 chdir：相对路径是相对调用方的 cwd，chdir 之后就变味了。
    录像dir = (os.path.abspath(args.replay_dir) if args.replay_dir else None)

    # `跑` 要读 赛题/chatbi/数据集/*.csv 的相对路径，钉死工作目录到仓库根。
    os.chdir(_REPO)

    pl = _planner()
    块数 = 块库块数()
    回放top = args.replay_top or 块数

    调优 = load_evalset(POS_TUNE)['用例']
    留出 = load_evalset(POS_HOLD)['用例']
    期望结果表 = {c['id']: c.get('期望结果', '') for c in 调优 + 留出}
    录像 = 读录像(录像dir)
    录像块表 = {条['id']: 条['块'] for 条 in 录像}

    报: Dict[str, Any] = {'块数': 块数, '录像数': len(录像)}
    报['回放'] = 回放(pl, 录像, 期望结果表, 回放top, args.strict)

    if not args.only_replay:
        报['正例档'] = {}
        for 档名, 用例 in (('调优', 调优), ('留出', 留出)):
            报['正例档'][档名] = {'条数': len(用例), '指标': {}}
            for k in POS_TOPS:
                r = 评上下文包(pl, 用例, 录像块表, k)
                r.pop('明细', None)
                报['正例档'][档名]['指标']['K=%d' % k] = r
        报['负例档'] = {}
        for 档名, 文件 in NEG_TIERS.items():
            r = 评负例(pl, load_evalset(文件)['用例'])
            报['负例档'][档名] = {k: v for k, v in r.items() if k != '明细'}
        报['两层对照'] = {
            '检索层拒答率': 检索层拒答率,
            '检索层兄弟块诱骗率': 检索层兄弟诱骗率,
            '说明': '检索层数字取自 bench_chatbi.py，本轮 retrieval.py 一行未改。'
                    '规划器层拒下的那部分**不改变**检索层仍是 0.0% 这个事实——'
                    '两层量的是不同的东西，不许互相掩盖。',
        }

    不一致 = 报['回放']['判定不一致']

    if args.json:
        清 = json.loads(json.dumps(报, ensure_ascii=False, default=float))
        清['回放'].pop('明细', None)
        print(json.dumps(清, ensure_ascii=False, indent=2))
        return 1 if (args.gate and 不一致) else 0

    print('块库 %d 块 · 录像 %d 份 · TF-IDF 启发式路径 · 零网络零 API key'
          % (块数, len(录像)))
    print('[分层] 本脚本量规划器层；检索层数字见 bench_chatbi.py，本轮未改检索器')

    if not args.only_replay:
        for 档名 in ('调优', '留出'):
            块 = 报['正例档'][档名]
            print(_横)
            print('正例·%s 档（%d 条）  ——  上下文包候选能不能装下正确方案'
                  % (档名, 块['条数']))
            print('%6s │ %-8s %-8s %-8s' % ('K', '期望覆盖', '期望全中', '白名单可承载'))
            for k in POS_TOPS:
                _打正例(档名, 块['指标']['K=%d' % k], k)
        for 档名 in NEG_TIERS:
            _打负例(档名, 报['负例档'][档名])

    _打回放(报['回放'])
    if args.show_reasons:
        _打拒因(报['回放'])

    if 不一致:
        print('⚠ 回放判定与 %s/%s 登记不一致：%s'
              % (录像目录, 录像清单, '、'.join(不一致)))
        if args.gate:
            return 1
    return 0


if __name__ == '__main__':
    _reconfigure_utf8()
    raise SystemExit(run())
