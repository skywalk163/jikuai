# -*- coding: utf-8 -*-
"""v0.6.0 · M5 · T-M5-S04：分词幂等性专项（门禁 G12）。

覆盖三条验收标准：
- AC-M5-07-01 同输入连续 N>=3（这里取 5）次调用输出完全一致
- AC-M5-07-02 分词前后其他模块/全局可观察状态无差异（无全域污染）
- AC-M5-07-03 交替调用分词与其他 stdlib 模块，各模块输出与单独调用一致

采样策略说明（AC-02）
====================
首次导入模块本身会合法地改变全局状态（sys.modules 增加条目、构建词典）。
因此所有快照类断言都先做一次 **warmup** 调用，再采样「前」快照，
这样比对的是「稳态下调用一次分词」的净影响，而不是首次加载成本。
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import copy
import importlib.util

import pytest

from jikuai.main import run_source

STDLIB = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'stdlib'))


def _load(stem, alias):
    path = os.path.join(STDLIB, stem + '.py')
    spec = importlib.util.spec_from_file_location(alias, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SEG = _load('分词', 'g12_seg')
RE = _load('正则', 'g12_regex')
ZH = _load('简繁', 'g12_zh')
TYPO = _load('排版', 'g12_typo')
IDIOM = _load('成语', 'g12_idiom')


#: 覆盖中文、英数混排、标点、空白、空串、纯标点等形态
SAMPLES = [
    '中国程序员的中文编程语言',
    '北京上海广州深圳',
    '极快JiKuai是2026年的中文编程语言',
    '数据库服务器网络安全',
    '中国 程序员\t中文',
    '。，、！？',
    '',
    '   ',
    '中',
    '人工智能与机器学习的关系',
]


# ===================================================================
# AC-M5-07-01：同输入连续 5 次调用输出完全一致
# ===================================================================

@pytest.mark.parametrize('text', SAMPLES)
def test_AC_M5_07_01_连续五次调用输出相等(text):
    outputs = [SEG.segment(text) for _ in range(5)]
    first = outputs[0]
    for i, out in enumerate(outputs[1:], start=2):
        assert out == first, '第 %d 次调用与第 1 次不一致：%r vs %r' % (i, out, first)


@pytest.mark.parametrize('text', SAMPLES)
def test_AC_M5_07_01_极快侧连续五次调用输出相等(text):
    src = '导入 分词。\n分词.分词("%s")。' % text
    outputs = [run_source(src) for _ in range(5)]
    assert all(o == outputs[0] for o in outputs)


def test_AC_M5_07_01_返回值互不共享():
    # 每次返回新 list：改一次不影响下一次
    a = SEG.segment('中国程序员')
    a.append('污染')
    a[0] = '篡改'
    assert SEG.segment('中国程序员') == ['中国', '程序员']


def test_AC_M5_07_01_交错输入不串味():
    # 交错不同输入 5 轮，每个输入的结果都与其基线相同
    baseline = {t: SEG.segment(t) for t in SAMPLES}
    for _ in range(5):
        for t in SAMPLES:
            assert SEG.segment(t) == baseline[t]


# ===================================================================
# AC-M5-07-02：调用前后全局可观察状态无差异
# ===================================================================

def _global_snapshot():
    """采集全局可观察状态快照。

    覆盖：sys.modules 键集合、sys.path、os.environ、分词模块的关键属性。
    只取可比较的不可变投影，避免快照本身引入副作用。
    """
    return {
        'sys.modules': frozenset(sys.modules.keys()),
        'sys.path': tuple(sys.path),
        'os.environ': dict(os.environ),
        'WORDS_id': id(SEG.WORDS),
        'WORDS': SEG.WORDS,
        'MAX_WORD_LEN': SEG.MAX_WORD_LEN,
        'module_dict_keys': frozenset(vars(SEG).keys()),
    }


def test_AC_M5_07_02_单次调用不改变全局状态():
    SEG.segment('warmup 中国程序员')       # warmup：吃掉首次加载的合法差异
    before = _global_snapshot()
    SEG.segment('中国程序员的中文编程语言')
    after = _global_snapshot()
    assert after == before


def test_AC_M5_07_02_多次多样输入不改变全局状态():
    SEG.segment('warmup')
    before = _global_snapshot()
    for _ in range(3):
        for t in SAMPLES:
            SEG.segment(t)
    after = _global_snapshot()
    assert after == before


def test_AC_M5_07_02_词典对象身份不变():
    SEG.segment('warmup')
    ident = id(SEG.WORDS)
    snapshot = set(SEG.WORDS)
    for t in SAMPLES:
        SEG.segment(t)
    assert id(SEG.WORDS) == ident, '词典对象被替换，说明存在写入'
    assert set(SEG.WORDS) == snapshot, '词典内容被修改'
    assert len(SEG.WORDS) == len(snapshot)


def test_AC_M5_07_02_源码无global与可变模块级容器():
    """静态检查：实现里不得出现 global 语句。

    这是 G12 的结构性防回归——只要有人日后加了 `global`，本条立刻红。
    """
    path = os.path.join(STDLIB, '分词.py')
    with open(path, 'r', encoding='utf-8') as f:
        source = f.read()
    import re as _re
    assert _re.search(r'^\s*global\s+', source, _re.M) is None

    # 词典必须是 frozenset（不可变），不是 set / dict / list
    assert isinstance(SEG.WORDS, frozenset)


def test_AC_M5_07_02_不影响其他模块的可观察状态():
    SEG.segment('warmup')
    before = (
        ZH.mapping_size(),
        len(IDIOM.all_idioms()),
        TYPO.punctuation_rules(),
    )
    for t in SAMPLES:
        SEG.segment(t)
    after = (
        ZH.mapping_size(),
        len(IDIOM.all_idioms()),
        TYPO.punctuation_rules(),
    )
    assert after == before


# ===================================================================
# AC-M5-07-03：交替调用分词与其他 stdlib 模块，各自输出与单独调用一致
# ===================================================================

_ZH_INPUT = '中国计算机学会发布国家标准'
_TYPO_INPUT = '极快JiKuai是第1个中文编程语言,值得一试!'
_RE_INPUT = 'abc中国123'
_IDIOM_INPUT = '胸有成竹'
_SEG_INPUT = '中国程序员的中文编程语言'


def _standalone_baselines():
    """各模块单独调用的基线（互不交错）。"""
    return {
        '分词': SEG.segment(_SEG_INPUT),
        '简繁_繁': ZH.to_traditional(_ZH_INPUT),
        '简繁_简': ZH.to_simplified(ZH.to_traditional(_ZH_INPUT)),
        '排版': TYPO.normalize_text(_TYPO_INPUT),
        '正则_搜索': RE.search('[一-鿿]+', _RE_INPUT),
        '正则_匹配': RE.match('[a-z]+[一-鿿]+[0-9]+', _RE_INPUT),
        '成语_是': IDIOM.is_idiom(_IDIOM_INPUT),
        '成语_释义': IDIOM.explain(_IDIOM_INPUT),
    }


def test_AC_M5_07_03_交替调用各模块输出不变():
    baseline = _standalone_baselines()
    for _ in range(5):
        # 每一步都夹一次分词，模拟真实交替使用
        assert SEG.segment(_SEG_INPUT) == baseline['分词']
        assert ZH.to_traditional(_ZH_INPUT) == baseline['简繁_繁']
        assert SEG.segment(_SEG_INPUT) == baseline['分词']
        assert ZH.to_simplified(ZH.to_traditional(_ZH_INPUT)) == baseline['简繁_简']
        assert SEG.segment(_SEG_INPUT) == baseline['分词']
        assert TYPO.normalize_text(_TYPO_INPUT) == baseline['排版']
        assert SEG.segment(_SEG_INPUT) == baseline['分词']
        assert RE.search('[一-鿿]+', _RE_INPUT) == baseline['正则_搜索']
        assert RE.match('[a-z]+[一-鿿]+[0-9]+', _RE_INPUT) == baseline['正则_匹配']
        assert SEG.segment(_SEG_INPUT) == baseline['分词']
        assert IDIOM.is_idiom(_IDIOM_INPUT) == baseline['成语_是']
        assert IDIOM.explain(_IDIOM_INPUT) == baseline['成语_释义']


def test_AC_M5_07_03_逆序交替同样成立():
    baseline = _standalone_baselines()
    for _ in range(3):
        assert IDIOM.explain(_IDIOM_INPUT) == baseline['成语_释义']
        assert TYPO.normalize_text(_TYPO_INPUT) == baseline['排版']
        assert SEG.segment(_SEG_INPUT) == baseline['分词']
        assert RE.search('[一-鿿]+', _RE_INPUT) == baseline['正则_搜索']
        assert ZH.to_traditional(_ZH_INPUT) == baseline['简繁_繁']
        assert SEG.segment(_SEG_INPUT) == baseline['分词']


def test_AC_M5_07_03_排版幂等在交替中不退化():
    # G2 不退化：排版的 f(f(x)) == f(x) 在与分词交替时依旧成立
    once = TYPO.normalize_text(_TYPO_INPUT)
    for _ in range(3):
        SEG.segment(_SEG_INPUT)
        assert TYPO.normalize_text(once) == once
        SEG.segment(_SEG_INPUT)


def test_AC_M5_07_03_极快侧交替调用():
    """在极快语言层面交替导入并调用四个模块，结果与单独调用一致。"""
    seg_only = run_source('导入 分词。\n分词.分词("%s")。' % _SEG_INPUT)
    zh_only = run_source('导入 简繁。\n简繁.转繁体("%s")。' % _ZH_INPUT)
    typo_only = run_source('导入 排版。\n排版.规范化文本("%s")。' % _TYPO_INPUT)
    idiom_only = run_source('导入 成语。\n成语.成语释义("%s")。' % _IDIOM_INPUT)

    mixed_src = (
        '导入 分词。\n导入 简繁。\n导入 排版。\n导入 成语。\n'
        '定义 甲 = 分词.分词("%s")。\n'
        '定义 乙 = 简繁.转繁体("%s")。\n'
        '定义 丙 = 分词.分词("%s")。\n'
        '定义 丁 = 排版.规范化文本("%s")。\n'
        '定义 戊 = 分词.分词("%s")。\n'
        '定义 己 = 成语.成语释义("%s")。\n'
        % (_SEG_INPUT, _ZH_INPUT, _SEG_INPUT, _TYPO_INPUT, _SEG_INPUT, _IDIOM_INPUT)
    )
    # 逐个断言：分别再跑一遍取值，确认交替上下文没有改变任一结果
    assert run_source(mixed_src + '甲。') == seg_only
    assert run_source(mixed_src + '丙。') == seg_only
    assert run_source(mixed_src + '戊。') == seg_only
    assert run_source(mixed_src + '乙。') == zh_only
    assert run_source(mixed_src + '丁。') == typo_only
    assert run_source(mixed_src + '己。') == idiom_only


def test_AC_M5_07_03_极快侧分词结果稳定跨多次求值():
    src = '导入 分词。\n分词.分词("%s")。' % _SEG_INPUT
    results = [run_source(src) for _ in range(5)]
    assert all(r == results[0] for r in results)
    # 深拷贝比对，排除同一对象被复用造成的假通过
    assert results[0] == copy.deepcopy(results[-1])