# -*- coding: utf-8 -*-
"""chatbi 制造域选块基准 —— 四档一次跑完（v0.26.0 W143）。

## 为什么是四档

沿用 AGENTS.md §六的既有纪律，不新造一套规矩：

- **调优集**（`评测集-chatbi.json`）：赛题 `question_public.csv` 10 题 + ADR-40 §6
  第 1 条的 5 个预置异常。量的是**条件召回**——答案已知在制造域 25 块里，检索能不能
  捞出来。调参只准在这一档上做。
- **留出集**（`评测集-chatbi-留出.json`）：`question_hidden.csv` 5 题。
  **只当裁判，绝不看它的 miss 明细去调参。** 允许看的只有本脚本打印的汇总指标；
  不许对留出档开 ``--verbose`` 逐条看哪条没中，更不许据此改同义词表、块描述或评测集。
  只有留出集也涨才算真涨（`评测集-留出.json` 说明里的老规矩）。为把这条纪律从
  「文档里的愿望」变成「手上的阻力」，``--verbose`` 对留出档默认不生效，要看必须显式
  加 ``--看留出明细``（打出来会连带一行违纪警告）。
- **两档负例**：`评测集-chatbi-无覆盖.json`（远离档：预测/优化/可视化/实时接入，
  整类能力不在库里）与 `评测集-chatbi-近边缘.json`（近边缘档：兄弟能力缺位，
  口径差一档）。**必须分档报数，只报远离档等于自欺。**

`retrieval.py` 没有分数阈值、没有拒答路径（`search()` 结尾就是
`scores.sort(); return scores[:top]`），所以两档负例上的拒答率**如实报 0**。
这是台架事实不是缺陷：AGENTS.md §四已记四轮实测证伪了所有基于分数的自动拒答方案
（近边缘档 AUC 0.52–0.67），本脚本不加阈值、不做拒答判定，只把「检索会不会自信地
给出制造块名」量成数字，判断权留给读数的人。

## 指标

正例两档（与 `bench_retrieval.py` / `bench_retrieval_chain.py` 同族，改名处已标注）：

- ``块覆盖率``：|top-K ∩ 期望| / |期望|，逐用例算后取平均
  （对应链式 bench 的 ``步覆盖率``；chatbi 的 `期望` 是**无序块集**而非有序步骤序列，
  故不叫「步」、也不评顺序保真度）
- ``完整命中率``：`期望` 全部出现在 top-K 的用例占比（对应链式 bench 的 ``序列完整命中率``）
- ``Recall@1`` / ``Recall@3`` / ``MRR``：与 `bench_retrieval.py` 完全同义
  （top-K 里出现**任一**期望块）

负例两档：

- ``拒答率``：返回空候选的用例占比（现状恒为 0）
- ``制造块占位率``：top-1 落在制造域块上的用例占比 —— 词面幻觉的强度
- ``兄弟块诱骗率``（仅近边缘档）：top-K 里出现了该条 `兄弟块` 任一的用例占比。
  这是近边缘档真正要量的东西：候选看着对、口径差一档，照着组会跑通但结果是错的。

## 用法

    python tools/ai-bridge/bench_chatbi.py
    python tools/ai-bridge/bench_chatbi.py --top 5 --verbose
    python tools/ai-bridge/bench_chatbi.py --json

零第三方依赖（标准库 + 本仓库 `jikuai` 包）。只评 TF-IDF 启发式路径：不传
`vector_index`，`retrieve()` 不带查询向量即走启发式，也是词面幻觉的发生地
（ADR-25 §2：运行时不做模型推理）。G12 红（embeddings 未生成）时本脚本照样跑，
数字不受影响。
"""

import argparse
import io
import json
import os
import sys
from typing import Dict, List, Optional, Sequence


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

#: 正例两档（应答）：调优 / 留出
POS_TUNE = '评测集-chatbi.json'
POS_HOLD = '评测集-chatbi-留出.json'

#: 负例两档（应拒）：档名 -> 文件名。难度递增，近边缘档是真实风险分布。
NEG_TIERS: Dict[str, str] = {
    '远离': '评测集-chatbi-无覆盖.json',
    '近边缘': '评测集-chatbi-近边缘.json',
}

#: 本轮评的域。制造域块数从 索引.json 现数，不在脚本里手抄。
DOMAIN = '制造'

#: 正例档默认三个 K（与 bench_retrieval_chain.py 的 tops 同默认）。
POS_TOPS = (3, 5, 10)


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------

def load_evalset(name: str, override: Optional[str] = None) -> dict:
    """读评测集（整个 JSON，含 说明/纪律/字段 等元信息）。"""
    target = override or os.path.join(_HERE, name)
    with open(target, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_blocks() -> List[dict]:
    from jikuai.pkg.blocks import load_index
    index = load_index()
    if not index:
        raise SystemExit('错误：找不到 stdlib/blocks/索引.json，先跑 '
                         'scripts/generate_block_index.py')
    return index.get('块') or []


def _domain_names(blocks: Sequence[dict], domain: str) -> set:
    """索引里 领域 是列表，取含 domain 的块名集合。"""
    return {b['名称'] for b in blocks if domain in (b.get('领域') or [])}


# ---------------------------------------------------------------------------
# 指标：正例档
# ---------------------------------------------------------------------------

def _metrics_pos(ranked: Sequence[str], expected: Sequence[str],
                 top: int) -> Dict[str, float]:
    """单条正例的指标。`ranked` 已按相关度降序。"""
    expect = set(expected)
    got = set(ranked[:top])
    覆盖 = (len(got & expect) / len(expect)) if expect else 0.0
    完整 = 1.0 if expect and expect <= got else 0.0
    hit1 = 1.0 if ranked[:1] and ranked[0] in expect else 0.0
    hit3 = 1.0 if any(n in expect for n in ranked[:3]) else 0.0
    rr = 0.0
    for i, name in enumerate(ranked[:top], start=1):
        if name in expect:
            rr = 1.0 / i
            break
    return {'块覆盖率': 覆盖, '完整命中率': 完整,
            'Recall@1': hit1, 'Recall@3': hit3, 'MRR': rr}


def evaluate_pos(rank_fn, cases: List[dict], top: int,
                 verbose: bool = False) -> Dict[str, float]:
    """跑一遍正例档。`rank_fn(需求, top) -> List[块名]`。"""
    keys = ('块覆盖率', '完整命中率', 'Recall@1', 'Recall@3', 'MRR')
    totals = {k: 0.0 for k in keys}
    for case in cases:
        expected = case['期望']
        ranked = rank_fn(case['需求'], top)
        m = _metrics_pos(ranked, expected, top)
        for k in keys:
            totals[k] += m[k]
        if verbose:
            缺 = [n for n in expected if n not in set(ranked[:top])]
            flag = '✓' if m['完整命中率'] else '✗'
            print('  %s [%s] %s' % (flag, case.get('id', '?'), case['需求']))
            print('      期望 %s' % '、'.join(expected))
            print('      实得 top-%d %s' % (top, ' > '.join(ranked[:top])))
            print('      块覆盖=%.2f 完整=%d 未命中 %s'
                  % (m['块覆盖率'], int(m['完整命中率']),
                     '、'.join(缺) if 缺 else '（无）'))
    n = len(cases) or 1
    return {k: v / n for k, v in totals.items()}


# ---------------------------------------------------------------------------
# 指标：负例档
# ---------------------------------------------------------------------------

def evaluate_neg(rank_fn, cases: List[dict], top: int,
                 域内: set) -> dict:
    """跑一遍负例档。不做任何拒答判定——检索层没有阈值，只如实记它返回了什么。"""
    行 = []
    for case in cases:
        ranked = rank_fn(case['需求'], top)
        兄弟 = case.get('兄弟块') or []
        行.append({
            '需求': case['需求'],
            '类别': case.get('类别', ''),
            '候选数': len(ranked),
            'top3': list(ranked[:3]),
            'top1域内': bool(ranked) and ranked[0] in 域内,
            '兄弟块': 兄弟,
            '兄弟被返回': [n for n in 兄弟 if n in set(ranked[:top])],
        })
    n = len(行) or 1
    有兄弟 = [r for r in 行 if r['兄弟块']]
    报告 = {
        '用例数': len(行),
        '返回空候选数': sum(1 for r in 行 if r['候选数'] == 0),
        '拒答率': sum(1 for r in 行 if r['候选数'] == 0) / n,
        '制造块占位率': sum(1 for r in 行 if r['top1域内']) / n,
        '明细': 行,
    }
    if 有兄弟:
        报告['兄弟块诱骗率'] = (sum(1 for r in 有兄弟 if r['兄弟被返回'])
                          / len(有兄弟))
        报告['带兄弟块用例数'] = len(有兄弟)
    return 报告


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _print_pos(档名: str, 结果: Dict[int, Dict[str, float]], 条数: int):
    print('=' * 74)
    print('正例·%s 档（%d 条）' % (档名, 条数))
    print('%6s │ %-9s %-9s %-9s %-9s %-7s'
          % ('K', '块覆盖率', '完整命中率', 'Recall@1', 'Recall@3', 'MRR'))
    for k, m in 结果.items():
        print('%6d │ %8.1f%% %8.1f%% %8.1f%% %8.1f%% %7.4f'
              % (k, m['块覆盖率'] * 100, m['完整命中率'] * 100,
                 m['Recall@1'] * 100, m['Recall@3'] * 100, m['MRR']))
    print('')


def _print_neg(档名: str, r: dict, top: int):
    print('=' * 74)
    print('负例·%s 档（%d 条 · top=%d）' % (档名, r['用例数'], top))
    print('  拒答率 %.1f%%（返回空候选 %d 条）  制造块占位率 %.1f%%'
          % (r['拒答率'] * 100, r['返回空候选数'], r['制造块占位率'] * 100))
    if '兄弟块诱骗率' in r:
        print('  兄弟块诱骗率 %.1f%%（%d 条标了兄弟块）'
              % (r['兄弟块诱骗率'] * 100, r['带兄弟块用例数']))
    print('')


def run(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description='chatbi 制造域选块基准（调优/留出/远离/近边缘 四档一次报数）')
    p.add_argument('--top', type=int, default=5,
                   help='负例档候选数上限（默认 5，同 bench_retrieval_reject.py）')
    p.add_argument('--pos-top', dest='pos_top', type=int, default=None,
                   help='正例档只跑单个 K（默认同时报 K=3/5/10）')
    p.add_argument('--verbose', action='store_true',
                   help='逐条打印命中/幻觉明细（留出档默认不打，见 --看留出明细）')
    p.add_argument('--看留出明细', dest='peek_hold', action='store_true',
                   help='违纪开关：打印留出档 miss 明细。留出集只当裁判，'
                        '看了 miss 明细再调参即作废本轮数字')
    p.add_argument('--json', action='store_true', help='只输出 JSON 指标')
    p.add_argument('--正例调优', dest='pos_tune', default=None)
    p.add_argument('--正例留出', dest='pos_hold', default=None)
    args = p.parse_args(argv)

    blocks = _load_blocks()
    域内 = _domain_names(blocks, DOMAIN)
    # 强制 TF-IDF：不给 vector_index，retrieve() 不带查询向量即走启发式路径
    retriever = Retriever(blocks, vector_index=None)

    def rank(query: str, top: int) -> List[str]:
        return [h.name for h in retriever.retrieve(query, top=top)]

    调优 = load_evalset(POS_TUNE, args.pos_tune)
    留出 = load_evalset(POS_HOLD, args.pos_hold)
    负例集 = {名: load_evalset(f) for 名, f in NEG_TIERS.items()}

    tops = [args.pos_top] if args.pos_top else list(POS_TOPS)

    正例报告: Dict[str, Dict[int, Dict[str, float]]] = {}
    for 档名, 集, 可看明细 in (('调优', 调优, args.verbose),
                          ('留出', 留出, args.verbose and args.peek_hold)):
        cases = 集['用例']
        本档 = {}
        for k in tops:
            if 可看明细 and not args.json:
                print('--- 正例·%s 档 逐条明细（K=%d）---' % (档名, k))
            本档[k] = evaluate_pos(rank, cases, k,
                                 可看明细 and not args.json)
        正例报告[档名] = 本档

    负例报告 = {名: evaluate_neg(rank, 集['用例'], args.top, 域内)
             for 名, 集 in 负例集.items()}

    报告 = {
        '块数': len(blocks),
        '%s域块数' % DOMAIN: len(域内),
        '负例top': args.top,
        '正例tops': tops,
        '现状_无阈值': {
            '拒答率': 0.0,
            '说明': 'retrieval.py 无分数阈值、无拒答路径，永远返回 top-K；'
                  '本脚本不加阈值，两档负例的拒答率如实报 0',
        },
        '正例档': {
            档名: {'用例数': len((调优 if 档名 == '调优' else 留出)['用例']),
                 '指标': {'K=%d' % k: {kk: round(vv, 4)
                                     for kk, vv in m.items()}
                        for k, m in 本档.items()}}
            for 档名, 本档 in 正例报告.items()
        },
        '负例档': {
            名: {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in r.items() if k != '明细'}
            for 名, r in 负例报告.items()
        },
        '留出集纪律': 留出.get('纪律', ''),
    }

    if args.json:
        print(json.dumps(报告, ensure_ascii=False, indent=2))
        return 0

    print('块库 %d 块（其中 %s 域 %d 块） · 正例 K=%s · 负例 top=%d · TF-IDF 启发式路径'
          % (len(blocks), DOMAIN, len(域内),
             '/'.join(str(k) for k in tops), args.top))
    print('正例 调优 %d 条 / 留出 %d 条 ；负例 %s'
          % (len(调优['用例']), len(留出['用例']),
             ' / '.join('%s %d 条' % (名, len(集['用例']))
                        for 名, 集 in 负例集.items())))
    print('\n[现状] retrieval.py 无分数阈值：两档负例拒答率 0.0%（本脚本不加阈值）\n')

    _print_pos('调优', 正例报告['调优'], len(调优['用例']))
    _print_pos('留出', 正例报告['留出'], len(留出['用例']))
    print('留出集纪律：%s\n' % 留出.get('纪律', '（文件里没写纪律，按违约处理）'))

    for 名 in NEG_TIERS:
        _print_neg(名, 负例报告[名], args.top)

    if args.verbose:
        for 名 in NEG_TIERS:
            print('--- %s 档负例的实际 top-3（应拒却给了块 = 幻觉）---' % 名)
            for r in 负例报告[名]['明细']:
                标 = '⚠兄弟' if r['兄弟被返回'] else '     '
                print('  %s %-30s -> %-26s [%s]'
                      % (标, r['需求'], '、'.join(r['top3']), r['类别']))
            print('')
    if args.peek_hold and not args.json:
        print('⚠ 已用 --看留出明细 打开留出档 miss 明细：本轮数字不得再用于调参。')
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
