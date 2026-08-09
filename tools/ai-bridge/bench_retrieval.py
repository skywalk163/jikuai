# -*- coding: utf-8 -*-
"""检索质量基准 —— 对比 v0.12.0 关键词匹配 vs v0.13.0 TF-IDF 启发式。

指标（ADR-25 §5 W8-W9 的前置 baseline）：

- ``Recall@1`` / ``Recall@3``：top-K 里出现任一期望块的比例
- ``MRR``：期望块首次命中位置的倒数均值（未命中记 0）

用法::

    python tools/ai-bridge/bench_retrieval.py
    python tools/ai-bridge/bench_retrieval.py --top 5 --verbose

零第三方依赖，日常 CI 可跑。`--json` 输出供 CI 门禁比对。
"""

import argparse
import importlib.util
import io
import json
import os
import sys
from typing import Dict, List, Optional, Sequence


def _reconfigure_utf8():
    """Windows GBK 控制台下强制 UTF-8 输出（与 blocks_cli.py 同做法）。"""
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
# select.py 本身仍能用——`_load_keyword_selector()` 是按文件路径加载的。
sys.path[:] = [p for p in sys.path if os.path.abspath(p) != _HERE]
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.ai import Retriever  # noqa: E402

#: 评测集文件名。
EVALSET_NAME = '评测集.json'

#: 神经路径默认模型（与 generate_embeddings.py 的 DEFAULT_MODEL 一致）。
#: 实际用哪个由 `向量索引.元信息.json` 的「模型」字段决定，这里只是兜底。
DEFAULT_NEURAL_MODEL = 'shibing624/text2vec-base-chinese'



def _load_keyword_selector():
    """按路径载入同目录的 `select.py`。

    不能直接 `import select`——那是标准库的 select 模块（见 select.py 首部注释）。
    """
    spec = importlib.util.spec_from_file_location(
        '块选择器_bench', os.path.join(_HERE, 'select.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_evalset(path: Optional[str] = None) -> dict:
    """读评测集。"""
    target = path or os.path.join(_HERE, EVALSET_NAME)
    with open(target, 'r', encoding='utf-8') as f:
        return json.load(f)


def _metrics(ranked_names: Sequence[str], expected: Sequence[str],
             top: int) -> Dict[str, float]:
    """单条用例的指标。`ranked_names` 已按相关度降序。"""
    expect = set(expected)
    hit1 = 1.0 if ranked_names[:1] and ranked_names[0] in expect else 0.0
    hit3 = 1.0 if any(n in expect for n in ranked_names[:3]) else 0.0
    rr = 0.0
    for i, name in enumerate(ranked_names[:top], start=1):
        if name in expect:
            rr = 1.0 / i
            break
    return {'Recall@1': hit1, 'Recall@3': hit3, 'MRR': rr}


def evaluate(rank_fn, cases: List[dict], top: int,
             verbose: bool = False) -> Dict[str, float]:
    """跑一遍评测集。`rank_fn(需求, top) -> List[块名]`。"""
    totals = {'Recall@1': 0.0, 'Recall@3': 0.0, 'MRR': 0.0}
    for case in cases:
        query = case['需求']
        expected = case['期望']
        names = rank_fn(query, top)
        m = _metrics(names, expected, top)
        for k in totals:
            totals[k] += m[k]
        if verbose:
            flag = '✓' if m['Recall@3'] else '✗'
            print('  %s %s' % (flag, query))
            print('      期望 %s' % '/'.join(expected))
            print('      实得 %s' % ' > '.join(names[:top]))
    n = len(cases) or 1
    return {k: v / n for k, v in totals.items()}


def _load_blocks() -> List[dict]:
    from jikuai.pkg.blocks import load_index
    index = load_index()
    if not index:
        raise SystemExit('错误：找不到 stdlib/blocks/索引.json，先跑 '
                         'scripts/generate_block_index.py')
    return index.get('块') or []


def _build_neural_ranker(blocks: List[dict], cases: List[dict]):
    """构造神经路径 rank_fn。返回 `(rank_fn, 跳过原因)`，二者恰有一个非 None。

    查询向量必须由本层生成——运行时（`src/jikuai/ai/`）零依赖，不做模型推理
    （ADR-25 §2/§3.1）。模型名取自 `向量索引.元信息.json`，保证与索引同源；
    取错模型会在余弦阶段因维度不符抛 `RetrievalError`，不会静默给出坏排序。
    """
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

    model = SentenceTransformer(model_name)
    queries = [c['需求'] for c in cases]
    # 一次批量编码，避免逐条前向；顺序与 queries 一致
    vectors = model.encode(queries, normalize_embeddings=True)
    qvec = {q: [float(x) for x in v] for q, v in zip(queries, vectors)}
    retriever = Retriever(blocks, vector_index=vi, mode=MODE_NEURAL)

    def 神经_rank(query: str, top: int) -> List[str]:
        return [h.name for h in retriever.retrieve(
            query, top=top, query_vector=qvec.get(query))]

    return 神经_rank, None


def run(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description='极快检索质量基准（关键词 vs TF-IDF vs 神经）')
    p.add_argument('--top', type=int, default=5, help='候选数上限（默认 5）')
    p.add_argument('--verbose', action='store_true', help='逐条打印命中情况')
    p.add_argument('--json', action='store_true', help='只输出 JSON 指标')
    p.add_argument('--评测集', dest='evalset', default=None, help='评测集路径')
    p.add_argument('--no-neural', dest='no_neural', action='store_true',
                   help='跳过神经路径（默认有索引+有依赖就跑）')
    args = p.parse_args(argv)

    evalset = load_evalset(args.evalset)
    cases = evalset['用例']
    blocks = _load_blocks()

    # 基线：v0.12.0 关键词匹配
    keyword = _load_keyword_selector()
    index = {'块': blocks}

    def 关键词_rank(query: str, top: int) -> List[str]:
        return [c['名称'] for c in keyword.select_blocks(query, index, top=top)]

    # 新实现：TF-IDF 启发式（强制不走神经路径，保证可比）
    retriever = Retriever(blocks, vector_index=None)

    def tfidf_rank(query: str, top: int) -> List[str]:
        return [h.name for h in retriever.retrieve(query, top=top)]

    if not args.json:
        print('评测集：%d 条用例 · 块库：%d 块 · top=%d\n'
              % (len(cases), len(blocks), args.top))
        print('[基线] v0.12.0 关键词匹配')
    基线 = evaluate(关键词_rank, cases, args.top, args.verbose and not args.json)
    if not args.json:
        print('  Recall@1=%.1f%%  Recall@3=%.1f%%  MRR=%.4f\n'
              % (基线['Recall@1'] * 100, 基线['Recall@3'] * 100, 基线['MRR']))
        print('[启发式] v0.13.0 TF-IDF + 同义词 + 领域先验')
    新 = evaluate(tfidf_rank, cases, args.top, args.verbose and not args.json)
    if not args.json:
        print('  Recall@1=%.1f%%  Recall@3=%.1f%%  MRR=%.4f\n'
              % (新['Recall@1'] * 100, 新['Recall@3'] * 100, 新['MRR']))

    神经 = None
    跳过原因 = '--no-neural' if args.no_neural else None
    if 跳过原因 is None:
        神经_rank, 跳过原因 = _build_neural_ranker(blocks, cases)
        if 神经_rank is not None:
            if not args.json:
                print('[神经] v0.13.0 向量索引余弦')
            神经 = evaluate(神经_rank, cases, args.top,
                          args.verbose and not args.json)
            if not args.json:
                print('  Recall@1=%.1f%%  Recall@3=%.1f%%  MRR=%.4f\n'
                      % (神经['Recall@1'] * 100, 神经['Recall@3'] * 100,
                         神经['MRR']))

    if not args.json:
        print('Δ Recall@3 启发式−关键词 = %+.1f 个百分点'
              % ((新['Recall@3'] - 基线['Recall@3']) * 100))
        if 神经:
            print('Δ Recall@1 神经−启发式 = %+.1f 个百分点'
                  % ((神经['Recall@1'] - 新['Recall@1']) * 100))
            print('Δ Recall@3 神经−启发式 = %+.1f 个百分点'
                  % ((神经['Recall@3'] - 新['Recall@3']) * 100))
        elif 跳过原因:
            print('神经路径已跳过：%s' % 跳过原因)

    if args.json:
        报告 = {
            '用例数': len(cases),
            '块数': len(blocks),
            'top': args.top,
            '关键词基线': {k: round(v, 4) for k, v in 基线.items()},
            'TFIDF启发式': {k: round(v, 4) for k, v in 新.items()},
        }
        if 神经:
            报告['神经'] = {k: round(v, 4) for k, v in 神经.items()}
        else:
            报告['神经跳过'] = 跳过原因
        print(json.dumps(报告, ensure_ascii=False, indent=2))

    return 0


if __name__ == '__main__':
    raise SystemExit(run())
