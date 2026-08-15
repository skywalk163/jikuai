# -*- coding: utf-8 -*-
"""v0.6.0 · M5 · T-M5-S03：中文分词测试（正向最大匹配）。"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib.util
import pytest

from jikuai.main import run_source


def _load_seg():
    stdlib = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'stdlib'))
    path = os.path.join(stdlib, '分词.py')
    spec = importlib.util.spec_from_file_location('py_seg', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

SEG = _load_seg()


# ===================== 词典规模与质量 =====================

# v0.23.0 W111：下界从 500 上调到 40000。
# 理由（ADR-38 §9 / v0.22.0「守卫绿≠守卫在守」教训）：词典扩到 5.9 万后，
# `>= 500` 这条断言永远成立，等于没在守——词典文件被清空到只剩几百条也照样绿。
# 上界哨同理：防止有人把 THUOCL 全部 11 个词表或 jieba 全量塞进来（会到 30 万+）。
词典下界 = 40000
词典上界 = 80000


def test_词典规模在约定区间():
    n = SEG.dictionary_size()
    assert 词典下界 <= n <= 词典上界, (
        '词典 %d 条，超出约定区间 [%d, %d]。'
        '若确实要调整规模，先改 ADR-38 §3 再改本断言。' % (n, 词典下界, 词典上界))

def test_词典无单字词():
    for w in SEG.all_words():
        assert len(w) >= 2, w

def test_词典条目全为汉字():
    for w in SEG.all_words():
        assert all('\u4e00' <= ch <= '\u9fff' for ch in w), w

def test_最长词长度与词典一致():
    assert SEG.max_word_length() == max(len(w) for w in SEG.all_words())

def test_词长上限为8():
    """ADR-38 §4 性能决策：入库词长截断到 8 字。"""
    assert SEG.MAX_WORD_LEN_LIMIT == 8
    for w in SEG.all_words():
        assert len(w) <= 8, w

def test_词典是frozenset不可变():
    assert isinstance(SEG.all_words(), frozenset)
    with pytest.raises(AttributeError):
        SEG.all_words().add('伪造词')


# ===================== 必留种子与产物一致性（ADR-38） =====================

def _repo(*parts):
    return os.path.normpath(os.path.join(os.path.dirname(__file__), '..', *parts))


def _读种子():
    words = set()
    with open(_repo('tools', 'dict', '种子词.txt'), encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                words.add(line)
    return words


def test_种子集全部在库():
    """ADR-38 §3.2：现有 565 条人工审校词表是必留集，不得被词频截断掉。

    实测过的坑：jieba 按通用语料词频取 top20000 会漏掉其中 91 条
    （人工智能/分词/防火墙/语料库/程序员…），因为极快最需要的技术词
    在通用语料里恰是低频词。
    """
    seed = _读种子()
    assert len(seed) >= 500, '种子词表本身异常：只有 %d 条' % len(seed)
    missing = seed - SEG.all_words()
    assert not missing, (
        '必留种子有 %d 条不在词典里：%s' % (len(missing), sorted(missing)[:20]))


def test_词典文件sha256与元信息一致():
    """词典是生成产物；元信息里的 sha256 是它的记账。两者不符说明有人手改了词典。"""
    import hashlib, json
    with open(SEG.dictionary_path(), 'rb') as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    with open(_repo('stdlib', '分词词典.元信息.json'), encoding='utf-8') as f:
        meta = json.load(f)
    assert actual == meta['sha256'], (
        '词典文件与元信息不一致。手改过词典就跑 '
        '`python tools/dict/重生成词典.py` 重新生成，别手改 sha256。')
    assert meta['词条数'] == SEG.dictionary_size()
    assert meta['词长范围'] == [SEG.MIN_WORD_LEN, SEG.MAX_WORD_LEN_LIMIT]


def test_词典文件缺失时抛异常而不是静默降级():
    """ADR-38 §8：静默降级会把「词典没打包进去」变成线上悄悄劣化。"""
    import importlib.util
    stdlib = _repo('stdlib')
    path = os.path.join(stdlib, '分词.py')
    src = open(path, encoding='utf-8').read()
    # 在隔离命名空间里执行，把 DICT_FILE 指向不存在的路径
    src = src.replace('"分词词典.txt")', '"不存在的词典.txt")')
    ns = {'__file__': path, '__name__': 'seg_missing_dict'}
    with pytest.raises(RuntimeError, match='分词词典文件缺失'):
        exec(compile(src, path, 'exec'), ns)


# ===================== 正向最大匹配 =====================

def test_基本切分():
    assert SEG.segment('中国程序员') == ['中国', '程序员']

def test_长词优先():
    # 编程语言（4字）优先于 编程（2字）
    assert '编程语言' in SEG.segment('中文编程语言')

def test_整句切分():
    r = SEG.segment('中国程序员的中文编程语言')
    assert r == ['中国', '程序员', '的', '中文', '编程语言']

def test_词典外汉字单字兜底():
    """B3：词典没收的汉字串逐字切。

    v0.23.0 W110 换样本：原样本 '欢迎' 在 565 条小词典时代确实是词典外，
    扩到 2.9 万条后它成了正常收录词，这条断言随之失真。改用语义上不成词的
    '猫桌'，并**先断言它确实不在词典里**——否则这条测试哪天又会悄悄变空转。
    """
    assert '猫桌' not in SEG.all_words(), '样本已进入词典，请换一个词典外样本'
    assert SEG.segment('猫桌') == ['猫', '桌']


# ===================== 兜底策略 B1/B2/B3 =====================

def test_B1_空白不产出词项():
    assert SEG.segment('中国 程序员') == ['中国', '程序员']
    assert SEG.segment('  中国\t程序员\n') == ['中国', '程序员']

def test_B2_半角字母数字整体成词():
    assert SEG.segment('JiKuai') == ['JiKuai']
    assert SEG.segment('北京2026') == ['北京', '2026']
    # 版本 在词典内，先被 FMM 切出；v0 走 B2；中国 再回主路径
    assert SEG.segment('版本v0中国') == ['版本', 'v0', '中国']

def test_B3_标点单字成词():
    assert SEG.segment('。，、') == ['。', '，', '、']
    assert SEG.segment('中国!') == ['中国', '!']

def test_纯标点每个标点单独一项():
    text = '。。，！？'
    assert SEG.segment(text) == list(text)


# ===================== 边界条件 =====================

def test_空字符串返回空列表():
    assert SEG.segment('') == []

def test_None返回空列表():
    assert SEG.segment(None) == []

def test_纯空白返回空列表():
    assert SEG.segment('   ') == []

def test_非字符串输入归一():
    assert SEG.segment(2026) == ['2026']

def test_单字输入():
    assert SEG.segment('中') == ['中']


# ===================== 返回值独立性 =====================

def test_返回新列表调用方修改不污染():
    r = SEG.segment('中国程序员')
    r.append('污染')
    assert SEG.segment('中国程序员') == ['中国', '程序员']

def test_两次调用返回不同对象():
    a = SEG.segment('中国')
    b = SEG.segment('中国')
    assert a == b
    assert a is not b


# ===================== 极快侧集成 =====================

def test_jk_分词():
    assert run_source('导入 分词。\n分词.分词("中国程序员")。') == ['中国', '程序员']

def test_jk_空输入():
    assert run_source('导入 分词。\n分词.分词("")。') == []

def test_jk_长句():
    r = run_source('导入 分词。\n分词.分词("中国程序员的中文编程语言")。')
    assert r == ['中国', '程序员', '的', '中文', '编程语言']