# -*- coding: utf-8 -*-
"""v0.13.0 M3 W6-W7：语义块检索（retrieval.py）单元测试。

覆盖：
- TF-IDF 启发式检索的基本正确性与同义词/领域先验
- 向量索引 I/O（读写 round-trip）
- 神经路径余弦检索
- AUTO / 环境变量模式切换与降级
- 便捷 API 的进程级缓存
- 检索质量 baseline 门禁（不劣化于关键词基线）
"""

import array
import json
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.ai import retrieval  # noqa: E402
from jikuai.ai.retrieval import (  # noqa: E402
    MAGIC,
    MODE_HEURISTIC,
    MODE_NEURAL,
    PATH_HEURISTIC,
    PATH_NEURAL,
    Hit,
    RetrievalError,
    Retriever,
    VectorIndex,
    load_vector_index,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def 样例块():
    """一小组块，覆盖三个领域，用于隔离测试（不依赖真实索引）。"""
    return [
        {'名称': '求和', '领域': ['数据'], '层级': 0,
         '描述': '对数值列表求和，返回总和', '稳定性': 'stable'},
        {'名称': '均值', '领域': ['数据'], '层级': 0,
         '描述': '求数值列表的算术平均值', '稳定性': 'stable'},
        {'名称': '去重', '领域': ['数据'], '层级': 0,
         '描述': '移除列表中的重复元素', '稳定性': 'stable'},
        {'名称': '身份证', '领域': ['中文'], '层级': 0,
         '描述': '校验中国大陆身份证号，返回真或假', '稳定性': 'stable'},
        {'名称': '农历', '领域': ['中文'], '层级': 0,
         '描述': '把公历年月日转成完整农历日期字符串', '稳定性': 'stable'},
        {'名称': '唯一码', '领域': ['工具'], '层级': 0,
         '描述': '生成不重复的唯一标识码', '稳定性': 'stable'},
    ]


# ---------------------------------------------------------------------------
# TF-IDF 启发式检索
# ---------------------------------------------------------------------------

def test_启发式_块名精确命中排第一(样例块):
    r = Retriever(样例块, vector_index=None)
    hits = r.retrieve('求和', top=3)
    assert hits
    assert hits[0].name == '求和'
    assert hits[0].path == PATH_HEURISTIC


def test_启发式_同义词改写能召回(样例块):
    """'加起来' 不含块名'求和'，靠同义词表召回。"""
    r = Retriever(样例块, vector_index=None)
    names = [h.name for h in r.retrieve('把这些数字加起来', top=3)]
    assert '求和' in names


def test_启发式_描述语义命中(样例块):
    r = Retriever(样例块, vector_index=None)
    names = [h.name for h in r.retrieve('去掉重复的元素', top=3)]
    assert '去重' in names


def test_启发式_领域先验(样例块):
    """公历转农历 → 中文领域块靠前。"""
    r = Retriever(样例块, vector_index=None)
    names = [h.name for h in r.retrieve('把公历转成农历', top=3)]
    assert '农历' in names[:2]


def test_空查询返回空(样例块):
    r = Retriever(样例块, vector_index=None)
    assert r.retrieve('', top=3) == []


def test_空块库返回空():
    r = Retriever([], vector_index=None)
    assert r.retrieve('求和', top=3) == []


def test_top_截断(样例块):
    r = Retriever(样例块, vector_index=None)
    assert len(r.retrieve('数字', top=2)) <= 2


def test_结果按分数降序(样例块):
    r = Retriever(样例块, vector_index=None)
    hits = r.retrieve('求和平均去重', top=5)
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# 向量索引 I/O round-trip
# ---------------------------------------------------------------------------

def _write_test_index(path, names, vectors, dim, qmin=-1.0, qmax=1.0):
    """按 ADR-25 §4 格式写一个测试索引。vectors 是 int16 二维列表。"""
    with open(path, 'wb') as f:
        f.write(MAGIC)
        f.write(struct.pack('<HH', 1, dim))
        f.write(struct.pack('<I', len(names)))
        f.write(struct.pack('<ff', qmin, qmax))
        for name, vec in zip(names, vectors):
            nb = name.encode('utf-8')
            f.write(struct.pack('<H', len(nb)))
            f.write(nb)
            f.write(array.array('h', vec).tobytes())


def test_向量索引_读写往返(tmp_path):
    p = str(tmp_path / '向量索引.bin')
    names = ['求和', '均值']
    vectors = [[100, -200, 300, -400], [500, 600, -700, 800]]
    _write_test_index(p, names, vectors, dim=4)

    vi = load_vector_index(p)
    assert vi is not None
    assert vi.dim == 4
    assert vi.count == 2
    assert vi.names == names
    assert list(vi.vectors[0]) == vectors[0]
    assert list(vi.vectors[1]) == vectors[1]


def test_向量索引_文件不存在返回None(tmp_path):
    assert load_vector_index(str(tmp_path / '不存在.bin')) is None


def test_向量索引_魔数错误返回None(tmp_path):
    p = str(tmp_path / 'bad.bin')
    with open(p, 'wb') as f:
        f.write(b'XXXX' + struct.pack('<HH', 1, 4))
    assert load_vector_index(p) is None


def test_向量索引_版本不兼容返回None(tmp_path):
    p = str(tmp_path / 'v99.bin')
    with open(p, 'wb') as f:
        f.write(MAGIC + struct.pack('<HH', 99, 4))
    assert load_vector_index(p) is None


# ---------------------------------------------------------------------------
# 神经路径
# ---------------------------------------------------------------------------

def test_神经检索_余弦命中最近向量(样例块):
    # 给前三个块（数据领域）造正交的 int16 向量
    names = ['求和', '均值', '去重']
    vectors = [
        [30000, 0, 0, 0],
        [0, 30000, 0, 0],
        [0, 0, 30000, 0],
    ]
    vi = VectorIndex(version=1, dim=4, count=3, qmin=-1.0, qmax=1.0,
                     names=names, vectors=[array.array('h', v) for v in vectors])
    r = Retriever(样例块, vector_index=vi, mode=MODE_NEURAL)
    # 查询向量对齐'均值'
    hits = r.retrieve('随便什么文本', top=3, query_vector=[0.0, 1.0, 0.0, 0.0])
    assert hits[0].name == '均值'
    assert hits[0].path == PATH_NEURAL


def test_神经检索_无查询向量降级启发式(样例块):
    vi = VectorIndex(version=1, dim=4, count=1, qmin=-1.0, qmax=1.0,
                     names=['求和'], vectors=[array.array('h', [1, 2, 3, 4])])
    r = Retriever(样例块, vector_index=vi, mode=MODE_NEURAL)
    hits = r.retrieve('求和', top=2)  # 不给 query_vector
    assert hits
    assert hits[0].path == PATH_HEURISTIC


def test_神经检索_维度不符报错(样例块):
    vi = VectorIndex(version=1, dim=4, count=1, qmin=-1.0, qmax=1.0,
                     names=['求和'], vectors=[array.array('h', [1, 2, 3, 4])])
    r = Retriever(样例块, vector_index=vi, mode=MODE_NEURAL)
    with pytest.raises(RetrievalError):
        r.retrieve('求和', top=2, query_vector=[1.0, 2.0])  # 2 != 4


# ---------------------------------------------------------------------------
# 模式切换与环境变量
# ---------------------------------------------------------------------------

def test_AUTO_无索引走启发式(样例块):
    r = Retriever(样例块, vector_index=None)
    assert r.mode == MODE_HEURISTIC


def test_AUTO_有索引走神经(样例块):
    vi = VectorIndex(version=1, dim=4, count=1, qmin=-1.0, qmax=1.0,
                     names=['求和'], vectors=[array.array('h', [1, 2, 3, 4])])
    r = Retriever(样例块, vector_index=vi)
    assert r.mode == MODE_NEURAL


def test_环境变量强制启发式(样例块, monkeypatch):
    monkeypatch.setenv('JIKUAI_AI_RETRIEVAL', 'heuristic')
    vi = VectorIndex(version=1, dim=4, count=1, qmin=-1.0, qmax=1.0,
                     names=['求和'], vectors=[array.array('h', [1, 2, 3, 4])])
    r = Retriever(样例块, vector_index=vi)
    assert r.mode == MODE_HEURISTIC
    # 即便给了查询向量也不走神经
    hits = r.retrieve('求和', top=2, query_vector=[1.0, 2.0, 3.0, 4.0])
    assert all(h.path == PATH_HEURISTIC for h in hits)


# ---------------------------------------------------------------------------
# 便捷 API 与缓存
# ---------------------------------------------------------------------------

def test_模块级retrieve走真实索引():
    retrieval.reset_cache()
    hits = retrieval.retrieve('把一组数字求和', top=3)
    assert hits
    assert any(h.name in ('求和', '批量统计') for h in hits[:3])


def test_describe状态():
    retrieval.reset_cache()
    d = retrieval.describe()
    assert d['块数'] >= 52
    assert '模式' in d


def test_Hit_as_dict(样例块):
    h = Hit(score=1.5, name='求和', domain='数据', description='求和', path=PATH_HEURISTIC)
    d = h.as_dict()
    assert d['名称'] == '求和'
    assert d['分数'] == 1.5


# ---------------------------------------------------------------------------
# 质量 baseline 门禁（ADR-25 §5：TF-IDF fallback 不劣化）
# ---------------------------------------------------------------------------

def _load_evalset():
    p = os.path.join(os.path.dirname(__file__), '..', 'tools', 'ai-bridge', '评测集.json')
    with open(p, 'r', encoding='utf-8') as f:
        return json.load(f)['用例']


def _load_real_blocks():
    from jikuai.pkg.blocks import load_index
    return (load_index() or {}).get('块') or []


def test_baseline_TFIDF_Recall3不劣化():
    """TF-IDF 启发式在评测集上 Recall@3 必须 ≥ 75%（明显优于关键词 70%）。

    这是 ADR-25 §5「fallback 不劣化」门禁的测试化。数字设 0.75 而非 0.9，
    留出块库扩容/评测集调整的波动空间，但仍卡住"不得退回关键词水平"。
    """
    cases = _load_evalset()
    blocks = _load_real_blocks()
    assert blocks, '真实索引缺失'
    r = Retriever(blocks, vector_index=None)
    hit3 = 0
    for case in cases:
        names = [h.name for h in r.retrieve(case['需求'], top=3)]
        if any(n in set(case['期望']) for n in names):
            hit3 += 1
    recall3 = hit3 / len(cases)
    assert recall3 >= 0.75, 'Recall@3=%.2f 劣化到门禁线下' % recall3


def test_baseline_TFIDF_Recall1下界():
    """TF-IDF 启发式 Recall@1 不得低于 60%。

    Recall@3=100% 但 Recall@1 很低意味着块总在第2/3位，对交互体验不好。
    门禁设 60%（当前实测 69.6%，留 ~10pp 余量防块库扩容波动）。
    """
    cases = _load_evalset()
    blocks = _load_real_blocks()
    assert blocks, '真实索引缺失'
    r = Retriever(blocks, vector_index=None)
    hit1 = 0
    for case in cases:
        names = [h.name for h in r.retrieve(case['需求'], top=1)]
        if names and names[0] in set(case['期望']):
            hit1 += 1
    recall1 = hit1 / len(cases)
    assert recall1 >= 0.60, 'Recall@1=%.2f 劣化到门禁线下' % recall1
