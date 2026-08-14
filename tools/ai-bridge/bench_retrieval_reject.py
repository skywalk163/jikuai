# -*- coding: utf-8 -*-
"""拒答基准 —— 量「块库没有覆盖的需求」上检索会不会自信地胡说。

## 为什么需要这个 bench

`bench_retrieval.py` / `bench_retrieval_chain.py` 量的是**条件召回**：它们的每条
用例，正确答案都在块库里。所以 Recall@3 回答的是「答案已知在库里，能不能找到」，
**不回答**「随便来一个需求，能不能识别出库里根本没有」。

后者才是 agent 接入的生死线。人在编辑器里看到「二维码 → 唯一码」会笑一下换个做法；
agent 拿到的是几个像样的中文块名加一个分数，它会照着组、代码会跑通、结果是错的。
**没命中不可怕，自信地错才致命。**

`retrieval.py` 的 `_TFIDFIndex.search()` 结尾是 `scores.sort(); return scores[:top]`
—— 没有分数阈值，没有拒答路径，永远返回 top-K。本 bench 就是把这件事量成数字，
并回答「有没有一个可用的置信信号能把无覆盖挡掉」。

## 两档负例

负例分两档，因为难度差一个量级：

- **远离档**（`评测集-无覆盖*.json`）：需求落在块库 6 个域之外（K8s / CUDA / 图像）
- **近边缘档**（`评测集-近边缘*.json`）：需求落在域**之内**，但库里没有块能做——
  通常是已有块的「兄弟能力」缺位（有中位数没众数、有身份证没车牌、有求和没累计）

近边缘档才是 agent 真实提问的分布：措辞贴着块词面，语义却无覆盖。两档都报，并额外
报**交叉检验**（阈值在远离档调优集上选，套到近边缘留出集）——只在容易的负例上调阈值
会不会给出虚假的安全感。

两档都只收「库里连部分能力都没有」的用例。部分覆盖的模糊用例（年终奖单独计税 vs
个税块的月度速算、加速折旧 vs 折旧块的直线法）标签不可靠，一律不进集。

## 指标

对某个置信信号和阈值 τ，判定规则是「信号 < τ 则拒答」，于是：

- ``正确拒答率``：负例中被正确拒掉的比例（越高越好）
- ``误拒率``：正例中被错误拒掉的比例（越低越好）
- ``净收益`` = 正确拒答率 − 误拒率（Youden J，单一比较量）
- ``可分性AUC``：随机取一正一负，正例信号更高的概率。0.5 = 完全不可分

候选信号（都是「越大越可能有覆盖」）：

- ``绝对分``：top-1 的原始分
- ``分差``：top-1 − top-2
- ``突出度``：top-1 − top-2..top-K 的均值

阈值只在**调优集**上选，然后原样套到**留出集**报数——这是项目既定的双集规矩
（见 `评测集-留出.json` 说明）。留出集的数字才算真数。

## 用法

    python tools/ai-bridge/bench_retrieval_reject.py
    python tools/ai-bridge/bench_retrieval_reject.py --verbose
    python tools/ai-bridge/bench_retrieval_reject.py --json

零第三方依赖。只评 TF-IDF 启发式路径——那是 `retrieve()` 不带查询向量时的默认
路径，也是词面幻觉的发生地（ADR-25 §2：运行时不做模型推理）。
"""

import argparse
import importlib.util
import io
import json
import os
import sys
from typing import Callable, Dict, List, Optional, Sequence, Tuple


def _reconfigure_utf8():
    """Windows GBK 控制台下强制 UTF-8 输出（与 bench_retrieval.py 同做法）。"""
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                      errors='replace', line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8',
                                      errors='replace', line_buffering=True)
    except Exception:
        pass


_reconfigure_utf8()

_HERE = os.path.abspath(os.path.dirname(__file__))
# 剔掉脚本目录：本目录的 select.py 会遮蔽标准库 select（同 bench_retrieval.py）
sys.path[:] = [p for p in sys.path if os.path.abspath(p) != _HERE]
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.ai import Retriever  # noqa: E402

#: 正例集（应答）：调优 / 留出
POS_TUNE = '评测集.json'
POS_HOLD = '评测集-留出.json'

#: 负例档（应拒）：档名 -> (调优集, 留出集)。难度递增，近边缘档是真实风险分布。
NEG_TIERS: Dict[str, Tuple[str, str]] = {
    '远离': ('评测集-无覆盖.json', '评测集-无覆盖-留出.json'),
    '近边缘': ('评测集-近边缘.json', '评测集-近边缘-留出.json'),
}

#: 交叉检验：阈值选自哪一档的调优集，套到哪一档的留出集
CROSS_FROM, CROSS_TO = '远离', '近边缘'

#: 置信信号名 -> fn(需求, top-K 分数列表, top-K 块名列表) -> 标量。
#: 约定：**越大越可能「有覆盖」**（应答），越小越该拒。
#: 前三个是相关度分数派（v1 基线，已知在近边缘档失效）；其余来自
#: `覆盖信号.py` 的残余覆盖派（方向 1 原型）。
SignalFn = Callable[[str, Sequence[float], Sequence[str]], float]


def _载入覆盖模型():
    """按路径载入同目录的 `覆盖信号.py`（脚本目录已被剔出 sys.path）。"""
    spec = importlib.util.spec_from_file_location(
        '覆盖信号_bench', os.path.join(_HERE, '覆盖信号.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _建信号(blocks: List[dict],
          虚词语料: Optional[Sequence[str]] = None) -> Dict[str, SignalFn]:
    覆盖 = _载入覆盖模型()
    模型 = 覆盖.取模型(blocks)
    if 虚词语料 is not None:
        模型.装载查询语料(虚词语料)

    def 绝对分(q, s, n):
        return s[0] if s else 0.0

    def 分差(q, s, n):
        return (s[0] - s[1]) if len(s) > 1 else (s[0] if s else 0.0)

    def 突出度(q, s, n):
        if len(s) > 1:
            return s[0] - sum(s[1:]) / len(s[1:])
        return s[0] if s else 0.0

    return {
        # -- 相关度派（v1 基线，已知近边缘失效）--
        '绝对分': 绝对分,
        '分差': 分差,
        '突出度': 突出度,
        # -- 覆盖派 v1（全 token，被虚词噪声淹）--
        '库覆盖': lambda q, s, n: 模型.库覆盖(q),
        '顶块覆盖': lambda q, s, n: 模型.顶块覆盖(q, n),
        # -- 覆盖派 v2：限内（剔 df=0 库外词）--
        '顶块覆盖_限内': lambda q, s, n: 模型.顶块覆盖(q, n, 限内=True),
        '并集覆盖_限内': lambda q, s, n: 模型.候选并集覆盖(q, n, 限内=True),
        # -- 覆盖派 v3：限内 + 去虚（再剔查询侧高频措辞词）--
        '顶块覆盖_限内去虚': lambda q, s, n: 模型.顶块覆盖(
            q, n, 限内=True, 去虚=True),
        '并集覆盖_限内去虚': lambda q, s, n: 模型.候选并集覆盖(
            q, n, 限内=True, 去虚=True),
        '顶块覆盖_限内去虚多字': lambda q, s, n: 模型.顶块覆盖(
            q, n, 多字=True, 限内=True, 去虚=True),
        # -- 组合门：库覆盖(远离) × 顶块覆盖_限内去虚(近边缘)取较小 --
        '双门': lambda q, s, n: min(
            模型.库覆盖(q),
            模型.顶块覆盖(q, n, 限内=True, 去虚=True)),
    }


def _load_cases(name: str, override: Optional[str] = None) -> List[dict]:
    target = override or os.path.join(_HERE, name)
    with open(target, 'r', encoding='utf-8') as f:
        return json.load(f)['用例']


def _load_blocks() -> List[dict]:
    from jikuai.pkg.blocks import load_index
    index = load_index()
    if not index:
        raise SystemExit('错误：找不到 stdlib/blocks/索引.json，先跑 '
                         'scripts/generate_block_index.py')
    return index.get('块') or []


def 采样(retriever: Retriever, cases: List[dict], top: int,
       信号表: Dict[str, SignalFn]) -> List[dict]:
    """对每条需求跑一次检索，记下各信号取值与 top-3 块名（供 --verbose 复核）。"""
    rows = []
    for case in cases:
        query = case['需求']
        hits = retriever.retrieve(query, top=top)
        scores = [h.score for h in hits]
        名单 = [h.name for h in hits]
        rows.append({
            '需求': query,
            '类别': case.get('类别', ''),
            '候选数': len(hits),
            'top3': 名单[:3],
            '信号': {名: fn(query, scores, 名单)
                   for 名, fn in 信号表.items()},
        })
    return rows


def _auc(正: Sequence[float], 负: Sequence[float]) -> float:
    """P(随机正例信号 > 随机负例信号)，并列算 0.5。集合很小，直接全对比较。"""
    if not 正 or not 负:
        return 0.5
    赢 = 0.0
    for p in 正:
        for n in 负:
            if p > n:
                赢 += 1.0
            elif p == n:
                赢 += 0.5
    return 赢 / (len(正) * len(负))


def _rates(正: Sequence[float], 负: Sequence[float],
           τ: float) -> Tuple[float, float]:
    """判定规则：信号 < τ 即拒答。返回 (正确拒答率, 误拒率)。"""
    正确拒答 = sum(1 for x in 负 if x < τ) / (len(负) or 1)
    误拒 = sum(1 for x in 正 if x < τ) / (len(正) or 1)
    return 正确拒答, 误拒


def _候选阈值(值: Sequence[float]) -> List[float]:
    """取观测值之间的中点做候选阈值，外加两端。避免阈值正好压在样本点上。"""
    唯一 = sorted(set(值))
    if not 唯一:
        return [0.0]
    候选 = [唯一[0] - 1e-9]
    候选 += [(a + b) / 2.0 for a, b in zip(唯一, 唯一[1:])]
    候选.append(唯一[-1] + 1e-9)
    return 候选


def 选阈值(正: Sequence[float], 负: Sequence[float]) -> dict:
    """在调优集上选阈值。给两个工作点：净收益最大，以及误拒=0 下拒答最多。"""
    最佳 = {'阈值': 0.0, '正确拒答率': 0.0, '误拒率': 0.0, '净收益': 0.0}
    保守 = {'阈值': 0.0, '正确拒答率': 0.0, '误拒率': 0.0}
    for τ in _候选阈值(list(正) + list(负)):
        拒, 误 = _rates(正, 负, τ)
        净 = 拒 - 误
        # 净收益最大；并列时偏向误拒更低的
        if 净 > 最佳['净收益'] + 1e-12 or (
                abs(净 - 最佳['净收益']) <= 1e-12 and 误 < 最佳['误拒率']):
            最佳 = {'阈值': τ, '正确拒答率': 拒, '误拒率': 误, '净收益': 净}
        if 误 <= 1e-12 and 拒 > 保守['正确拒答率']:
            保守 = {'阈值': τ, '正确拒答率': 拒, '误拒率': 误}
    return {'净收益最大': 最佳, '零误拒': 保守}


def _解析负例档覆盖(项: Optional[List[str]]) -> Dict[str, Tuple[str, str]]:
    """`--负例档 名=调优路径:留出路径` 可重复；不给则用内置 NEG_TIERS。"""
    if not 项:
        return dict(NEG_TIERS)
    档 = {}
    for 条 in 项:
        名, _, 路径 = 条.partition('=')
        调优, _, 留出 = 路径.partition(':')
        if not (名 and 调优 and 留出):
            raise SystemExit('错误：--负例档 格式应为 名=调优路径:留出路径')
        档[名] = (调优, 留出)
    return 档


def run(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description='极快检索拒答基准（无覆盖需求上会不会自信地胡说）')
    p.add_argument('--top', type=int, default=5, help='候选数上限（默认 5）')
    p.add_argument('--verbose', action='store_true',
                   help='打印各档留出负例的实际 top-3 返回（幻觉明细）')
    p.add_argument('--json', action='store_true', help='只输出 JSON 指标')
    p.add_argument('--正例调优', dest='pos_tune', default=None)
    p.add_argument('--正例留出', dest='pos_hold', default=None)
    p.add_argument('--负例档', dest='neg_tiers', action='append', default=None,
                   help='名=调优路径:留出路径，可重复；默认用内置两档')
    args = p.parse_args(argv)

    blocks = _load_blocks()
    # 强制 TF-IDF：不给 vector_index，retrieve() 不带查询向量即走启发式路径
    retriever = Retriever(blocks, vector_index=None)

    # 先把用例读进来：虚词表要从**调优集**查询统计，喂留出集就是看裁判答案调参
    档定义 = _解析负例档覆盖(args.neg_tiers)
    原始 = {
        '正例调优': _load_cases(POS_TUNE, args.pos_tune),
        '正例留出': _load_cases(POS_HOLD, args.pos_hold),
    }
    for 名, (调优, 留出) in 档定义.items():
        原始['负例%s调优' % 名] = _load_cases(调优)
        原始['负例%s留出' % 名] = _load_cases(留出)

    虚词语料 = [c['需求'] for k, v in 原始.items() if k.endswith('调优')
            for c in v]
    信号表 = _建信号(blocks, 虚词语料)

    正例 = {
        '调优': 采样(retriever, 原始['正例调优'], args.top, 信号表),
        '留出': 采样(retriever, 原始['正例留出'], args.top, 信号表),
    }
    负例 = {}
    for 名 in 档定义:
        负例[名] = {
            '调优': 采样(retriever, 原始['负例%s调优' % 名], args.top, 信号表),
            '留出': 采样(retriever, 原始['负例%s留出' % 名], args.top, 信号表),
        }

    def 值(rows: List[dict], 信号: str) -> List[float]:
        return [r['信号'][信号] for r in rows]

    档报告 = {}
    for 档名, 集 in 负例.items():
        信号报告 = {}
        for 信号 in 信号表:
            工作点 = 选阈值(值(正例['调优'], 信号), 值(集['调优'], 信号))
            留出实测 = {}
            for 点名, wp in 工作点.items():
                拒, 误 = _rates(值(正例['留出'], 信号), 值(集['留出'], 信号),
                              wp['阈值'])
                留出实测[点名] = {'阈值': wp['阈值'], '正确拒答率': 拒,
                              '误拒率': 误, '净收益': 拒 - 误}
            信号报告[信号] = {
                '调优可分性AUC': _auc(值(正例['调优'], 信号), 值(集['调优'], 信号)),
                '留出可分性AUC': _auc(值(正例['留出'], 信号), 值(集['留出'], 信号)),
                '调优工作点': 工作点,
                '留出实测': 留出实测,
            }
        档报告[档名] = {
            '用例数': {'调优': len(集['调优']), '留出': len(集['留出'])},
            '留出返回空候选数': sum(1 for r in 集['留出'] if r['候选数'] == 0),
            '信号': 信号报告,
            '最优信号_按调优AUC': max(
                信号报告, key=lambda s: 信号报告[s]['调优可分性AUC']),
        }

    # 交叉检验：阈值在容易档上选，套到难档留出集
    交叉 = {}
    if CROSS_FROM in 负例 and CROSS_TO in 负例:
        for 信号 in 信号表:
            wp = 档报告[CROSS_FROM]['信号'][信号]['调优工作点']
            条目 = {}
            for 点名 in ('净收益最大', '零误拒'):
                τ = wp[点名]['阈值']
                拒, 误 = _rates(值(正例['留出'], 信号),
                              值(负例[CROSS_TO]['留出'], 信号), τ)
                条目[点名] = {'阈值': τ, '正确拒答率': 拒, '误拒率': 误,
                           '净收益': 拒 - 误}
            交叉[信号] = 条目

    报告 = {
        '块数': len(blocks),
        'top': args.top,
        '正例用例数': {k: len(v) for k, v in 正例.items()},
        '现状_无阈值': {
            '正确拒答率': 0.0, '误拒率': 0.0,
            '说明': 'retrieval.py 无分数阈值，永远返回 top-K',
        },
        '负例档': 档报告,
        '交叉检验': {
            '阈值选自': '%s·调优集' % CROSS_FROM,
            '套用于': '%s·留出集' % CROSS_TO,
            '结果': 交叉,
        },
    }

    if args.json:
        print(json.dumps(报告, ensure_ascii=False, indent=2, default=float))
        return 0

    print('块库 %d 块 · top=%d' % (len(blocks), args.top))
    print('正例 调优 %d / 留出 %d' % (len(正例['调优']), len(正例['留出'])))
    for 档名, r in 档报告.items():
        print('负例·%s 档 调优 %d / 留出 %d（留出返回空候选 %d）'
              % (档名, r['用例数']['调优'], r['用例数']['留出'],
                 r['留出返回空候选数']))
    print('\n[现状] retrieval.py 无分数阈值：正确拒答率 0.0%  误拒率 0.0%\n')

    for 档名, r in 档报告.items():
        print('=' * 74)
        print('负例档：%s（最优信号 %s）' % (档名, r['最优信号_按调优AUC']))
        for 信号, s in r['信号'].items():
            标 = ' ←' if 信号 == r['最优信号_按调优AUC'] else '  '
            print(' [%s]%s AUC 调优=%.3f 留出=%.3f'
                  % (信号, 标, s['调优可分性AUC'], s['留出可分性AUC']))
            for 点名 in ('净收益最大', '零误拒'):
                t = s['调优工作点'][点名]
                h = s['留出实测'][点名]
                print('   %-6s τ=%.4f | 调优 拒%.1f%% 误拒%.1f%%'
                      ' | 留出 拒%.1f%% 误拒%.1f%% 净%+.1f%%'
                      % (点名, t['阈值'],
                         t['正确拒答率'] * 100, t['误拒率'] * 100,
                         h['正确拒答率'] * 100, h['误拒率'] * 100,
                         h['净收益'] * 100))
        print()

    if 交叉:
        print('=' * 74)
        print('交叉检验：阈值选自 %s·调优集，套到 %s·留出集'
              % (CROSS_FROM, CROSS_TO))
        for 信号, 条目 in 交叉.items():
            for 点名, v in 条目.items():
                print('  %-6s %-6s τ=%.4f -> 拒%.1f%% 误拒%.1f%% 净%+.1f%%'
                      % (信号, 点名, v['阈值'], v['正确拒答率'] * 100,
                         v['误拒率'] * 100, v['净收益'] * 100))
        print()

    if args.verbose:
        for 档名, 集 in 负例.items():
            最优 = 档报告[档名]['最优信号_按调优AUC']
            print('--- %s 档·留出负例的实际 top-3（应拒却给了块 = 幻觉）---' % 档名)
            for r in sorted(集['留出'], key=lambda r: -r['信号'][最优]):
                print('  %s=%.4f  %-26s -> %-28s [%s]'
                      % (最优, r['信号'][最优], r['需求'],
                         '、'.join(r['top3']), r['类别']))
            print()
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
