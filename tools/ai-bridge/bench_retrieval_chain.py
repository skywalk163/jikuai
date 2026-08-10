# -*- coding: utf-8 -*-
"""链式检索质量基准 —— 评测「多步块序列」的召回与排序保真度。

与 bench_retrieval.py 互补：后者测单块命中（Recall / MRR），本脚本测多步
链式用例的序列级检索质量。评测集 schema 来自 评测集-链式.json。

指标（W27 定义）：

- ``步覆盖率``：|检索出的块 ∩ 期望序列| / |期望序列|（逐用例算后取平均）
- ``序列完整命中率``：期望序列全部出现在 top-K 的用例占比
- ``顺序保真度``：命中块在候选排序中的相对顺序与期望一致的比例（宽松偏序）

用法::

    python tools/ai-bridge/bench_retrieval_chain.py
    python tools/ai-bridge/bench_retrieval_chain.py --top 5 --verbose
    python tools/ai-bridge/bench_retrieval_chain.py --数据集 tools/ai-bridge/评测集-链式-留出.json
    python tools/ai-bridge/bench_retrieval_chain.py --json

零第三方依赖，日常 CI 可跑。
"""

import argparse
import importlib.util
import io
import json
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple


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
# 剔掉 Python 自动塞进 sys.path[0] 的脚本目录：本目录的 select.py 会遮蔽标准库
# select，神经路径 import sentence_transformers 时会连带炸掉 httpx/httpcore。
sys.path[:] = [p for p in sys.path if os.path.abspath(p) != _HERE]
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.ai import Retriever  # noqa: E402

#: 默认评测集。
EVALSET_NAME = '评测集-链式.json'

#: 神经路径默认模型（与 generate_embeddings.py 的 DEFAULT_MODEL 一致）。
DEFAULT_NEURAL_MODEL = 'shibing624/text2vec-base-chinese'


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _load_keyword_selector():
    """按路径载入同目录的 `select.py`。

    不能直接 `import select`——那是标准库的 select 模块。
    """
    spec = importlib.util.spec_from_file_location(
        '块选择器_bench_chain', os.path.join(_HERE, 'select.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_evalset(path: Optional[str] = None) -> dict:
    """读链式评测集。"""
    target = path or os.path.join(_HERE, EVALSET_NAME)
    with open(target, 'r', encoding='utf-8') as f:
        return json.load(f)


def _extract_expected_sequence(case: dict) -> List[str]:
    """从用例中提取期望块名序列（按步骤顺序）。"""
    steps = case.get('步骤') or []
    return [step['块'] for step in steps if '块' in step]


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------

def _step_coverage(ranked_names: Sequence[str],
                   expected_seq: Sequence[str]) -> float:
    """步覆盖率：|检索出 ∩ 期望序列| / |期望序列|。"""
    if not expected_seq:
        return 0.0
    retrieved_set = set(ranked_names)
    hits = sum(1 for name in expected_seq if name in retrieved_set)
    return hits / len(expected_seq)


def _complete_hit(ranked_names: Sequence[str],
                  expected_seq: Sequence[str]) -> float:
    """序列完整命中：期望序列全部出现在 top-K 中则为 1.0，否则 0.0。"""
    if not expected_seq:
        return 0.0
    retrieved_set = set(ranked_names)
    return 1.0 if all(name in retrieved_set for name in expected_seq) else 0.0


def _order_fidelity(ranked_names: Sequence[str],
                    expected_seq: Sequence[str]) -> float:
    """顺序保真度（宽松偏序版）。

    只看命中的块：期望序列中先出现的块在候选排名中 rank 不晚于后出现的块。
    统计所有期望序列中相邻命中对满足偏序的比例。

    若命中块不足 2 个，无序可比，返回 1.0（不惩罚覆盖率低的情况；覆盖率
    自有 步覆盖率 / 完整命中率 来衡量）。
    """
    # 构建 rank map：块名 -> 在 ranked_names 中的位置（越小越好）
    rank_map: Dict[str, int] = {}
    for i, name in enumerate(ranked_names):
        if name not in rank_map:  # 取首次出现位置
            rank_map[name] = i

    # 筛出期望序列中在候选里命中的子序列（保持期望顺序）
    hit_seq = [name for name in expected_seq if name in rank_map]
    if len(hit_seq) < 2:
        return 1.0  # 无序可比

    # 统计相邻对中满足偏序的比例
    pairs_total = len(hit_seq) - 1
    pairs_ok = 0
    for j in range(pairs_total):
        if rank_map[hit_seq[j]] <= rank_map[hit_seq[j + 1]]:
            pairs_ok += 1
    return pairs_ok / pairs_total


def _metrics_chain(ranked_names: Sequence[str],
                   expected_seq: Sequence[str]) -> Dict[str, float]:
    """单条用例的三项链式指标。"""
    return {
        '步覆盖率': _step_coverage(ranked_names, expected_seq),
        '序列完整命中率': _complete_hit(ranked_names, expected_seq),
        '顺序保真度': _order_fidelity(ranked_names, expected_seq),
    }


# ---------------------------------------------------------------------------
# 评测主循环
# ---------------------------------------------------------------------------

def evaluate_chain(rank_fn, cases: List[dict], top: int,
                   verbose: bool = False) -> Dict[str, float]:
    """跑一遍链式评测集。`rank_fn(需求, top) -> List[块名]`。"""
    totals = {'步覆盖率': 0.0, '序列完整命中率': 0.0, '顺序保真度': 0.0}
    valid_count = 0
    for case in cases:
        query = case['需求']
        expected_seq = _extract_expected_sequence(case)
        if not expected_seq:
            continue  # 跳过无步骤用例（不该出现，但防御性处理）
        valid_count += 1
        names = rank_fn(query, top)
        m = _metrics_chain(names, expected_seq)
        for k in totals:
            totals[k] += m[k]
        if verbose:
            flag = '✓' if m['序列完整命中率'] else '✗'
            print('  %s [%s] %s' % (flag, case.get('id', '?'), query))
            print('      期望序列 %s' % ' → '.join(expected_seq))
            print('      实得 top-%d %s' % (top, ' > '.join(names[:top])))
            print('      步覆盖=%.2f 完整=%d 序保=%.2f'
                  % (m['步覆盖率'], int(m['序列完整命中率']), m['顺序保真度']))
    n = valid_count or 1
    return {k: v / n for k, v in totals.items()}


# ---------------------------------------------------------------------------
# 神经路径构建（与 bench_retrieval.py 同模式）
# ---------------------------------------------------------------------------

def _load_blocks() -> List[dict]:
    from jikuai.pkg.blocks import load_index
    index = load_index()
    if not index:
        raise SystemExit('错误：找不到 stdlib/blocks/索引.json，先跑 '
                         'scripts/generate_block_index.py')
    return index.get('块') or []


def _build_neural_ranker(blocks: List[dict], cases: List[dict]):
    """构造神经路径 rank_fn。返回 `(rank_fn, 跳过原因)`，二者恰有一个非 None。"""
    from jikuai.ai.retrieval import (MODE_NEURAL, load_vector_index,
                                     vector_index_path)
    vi = load_vector_index()
    if vi is None:
        return None, ('向量索引.bin 缺失或格式不兼容，'
                      '先跑 python tools/ai-bridge/generate_embeddings.py')

    model_name = DEFAULT_NEURAL_MODEL
    meta_path = os.path.join(os.path.dirname(vector_index_path()),
                             '向量索引.元信息.json')
    if os.path.isfile(meta_path):
        with open(meta_path, 'r', encoding='utf-8') as f:
            model_name = json.load(f).get('模型') or model_name

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None, ('未安装 sentence-transformers，'
                      'pip install -r tools/ai-bridge/requirements-ai.txt')

    try:
        model = SentenceTransformer(model_name)
    except Exception as e:
        return None, '加载模型失败：%s' % e

    queries = list({c['需求'] for c in cases})  # 去重
    try:
        vectors = model.encode(queries, normalize_embeddings=True)
    except Exception as e:
        return None, '编码查询失败：%s' % e

    qvec = {q: [float(x) for x in v] for q, v in zip(queries, vectors)}
    retriever = Retriever(blocks, vector_index=vi, mode=MODE_NEURAL)

    def 神经_rank(query: str, top: int) -> List[str]:
        return [h.name for h in retriever.retrieve(
            query, top=top, query_vector=qvec.get(query))]

    return 神经_rank, None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description='极快链式检索质量基准（关键词 vs TF-IDF vs 神经）')
    p.add_argument('--top', type=int, default=None,
                   help='候选数上限（默认同时报 K=3/5/10 三档）')
    p.add_argument('--verbose', action='store_true', help='逐条打印命中情况')
    p.add_argument('--json', action='store_true', help='只输出 JSON 指标')
    p.add_argument('--数据集', dest='evalset', default=None, help='评测集路径')
    p.add_argument('--no-neural', dest='no_neural', action='store_true',
                   help='跳过神经路径（默认有索引+有依赖就跑）')
    args = p.parse_args(argv)

    evalset = load_evalset(args.evalset)
    cases = evalset['用例']
    blocks = _load_blocks()

    # 确定要跑的 K 值列表
    tops = [args.top] if args.top else [3, 5, 10]

    # 基线：v0.12.0 关键词匹配
    keyword = _load_keyword_selector()
    index = {'块': blocks}

    def 关键词_rank(query: str, top: int) -> List[str]:
        return [c['名称'] for c in keyword.select_blocks(query, index, top=top)]

    # TF-IDF 启发式（强制不走神经路径）
    retriever = Retriever(blocks, vector_index=None)

    def tfidf_rank(query: str, top: int) -> List[str]:
        return [h.name for h in retriever.retrieve(query, top=top)]

    # 神经路径
    神经_rank = None
    跳过原因 = '--no-neural' if args.no_neural else None
    if 跳过原因 is None:
        神经_rank, 跳过原因 = _build_neural_ranker(blocks, cases)
        if 跳过原因 is not None:
            # 优雅降级：打印原因并继续跑另两臂
            if not args.json:
                print('神经臂跳过（%s）\n' % 跳过原因)

    # -----------------------------------------------------------------------
    # 逐档跑三臂
    # -----------------------------------------------------------------------
    全部结果: Dict[int, dict] = {}  # K -> {'关键词': {...}, 'TFIDF': {...}, ...}

    for k in tops:
        结果_k: dict = {}

        if not args.json:
            print('═' * 60)
            print('  top-K = %d  ·  用例 %d 条  ·  块库 %d 块'
                  % (k, len(cases), len(blocks)))
            print('═' * 60)

        # 关键词臂
        if not args.json:
            print('\n[关键词] v0.12.0 关键词匹配')
        基线 = evaluate_chain(关键词_rank, cases, k,
                            args.verbose and not args.json)
        结果_k['关键词'] = 基线
        if not args.json:
            print('  步覆盖率=%.1f%%  完整命中率=%.1f%%  顺序保真度=%.1f%%'
                  % (基线['步覆盖率'] * 100, 基线['序列完整命中率'] * 100,
                     基线['顺序保真度'] * 100))

        # TF-IDF 臂
        if not args.json:
            print('\n[TF-IDF] v0.13.0 TF-IDF + 同义词 + 领域先验')
        新 = evaluate_chain(tfidf_rank, cases, k,
                          args.verbose and not args.json)
        结果_k['TFIDF'] = 新
        if not args.json:
            print('  步覆盖率=%.1f%%  完整命中率=%.1f%%  顺序保真度=%.1f%%'
                  % (新['步覆盖率'] * 100, 新['序列完整命中率'] * 100,
                     新['顺序保真度'] * 100))

        # 神经臂
        if 神经_rank is not None:
            if not args.json:
                print('\n[神经] v0.13.0 向量索引余弦')
            神经 = evaluate_chain(神经_rank, cases, k,
                                args.verbose and not args.json)
            结果_k['神经'] = 神经
            if not args.json:
                print('  步覆盖率=%.1f%%  完整命中率=%.1f%%  顺序保真度=%.1f%%'
                      % (神经['步覆盖率'] * 100, 神经['序列完整命中率'] * 100,
                         神经['顺序保真度'] * 100))
        else:
            结果_k['神经跳过'] = 跳过原因

        if not args.json:
            print('')

        全部结果[k] = 结果_k

    # -----------------------------------------------------------------------
    # 汇总
    # -----------------------------------------------------------------------
    if not args.json:
        print('─' * 60)
        print('汇总（步覆盖率 / 完整命中率 / 顺序保真度）')
        print('─' * 60)
        header = '%6s' % 'K'
        for arm in ['关键词', 'TFIDF', '神经']:
            header += '  │ %s' % arm.center(28)
        print(header)
        for k in tops:
            row = '%6d' % k
            r = 全部结果[k]
            for arm in ['关键词', 'TFIDF', '神经']:
                if arm in r:
                    m = r[arm]
                    row += '  │ %5.1f%% / %5.1f%% / %5.1f%%' % (
                        m['步覆盖率'] * 100,
                        m['序列完整命中率'] * 100,
                        m['顺序保真度'] * 100)
                else:
                    row += '  │ %s' % '（跳过）'.center(24)
            print(row)
        if 跳过原因 and 神经_rank is None:
            print('\n神经路径已跳过：%s' % 跳过原因)
        print('')

    if args.json:
        报告 = {
            '用例数': len(cases),
            '块数': len(blocks),
            'tops': tops,
            '结果': {},
        }
        for k in tops:
            r = 全部结果[k]
            报告_k: dict = {}
            for arm in ['关键词', 'TFIDF', '神经']:
                if arm in r:
                    报告_k[arm] = {kk: round(vv, 4) for kk, vv in r[arm].items()}
            if '神经跳过' in r:
                报告_k['神经跳过'] = r['神经跳过']
            报告['结果']['K=%d' % k] = 报告_k
        print(json.dumps(报告, ensure_ascii=False, indent=2))

    return 0


if __name__ == '__main__':
    raise SystemExit(run())
