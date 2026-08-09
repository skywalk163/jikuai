# -*- coding: utf-8 -*-
"""M9-1 · 分词 fuzz 测试 — 对 lexer 的无空格中文分词做随机/边界输入压测。

目标
----
暴露「用户定义名恰好是关键字前/后缀组合」「关键字嵌套拼接」
「百家姓前缀 + 关键字」等长尾场景下的分词错误。

策略
----
1. **关键字边界拼接**：两两关键字/动词/副词紧挨，验证能无崩溃地切词。
2. **百家姓 + 关键字前缀**：`赵如果`、`钱定义`、`孙函数` 等，验证不被当作标识符吞掉。
3. **随机汉字 + 关键字穿插**：生成随机合法极快源码片段，确保 tokenize 不抛未预期异常。
4. **已知边界 case**：手工收集已知的分词难点（来自 CHANGELOG 已知边界）。

所有测试只验证 **tokenize 不崩溃 + 返回非空 token 列表**，不断言具体 token
序列（那是功能测试的事）。Fuzz 测试关心的是「不管输入多奇怪都不该 panic」。
"""

import itertools
import random
import string
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from jikuai.lexer import tokenize
from jikuai.keywords import ALL_KEYWORDS, VERB_ARITY, ADVERBS, PUNCTUATION
from jikuai.surnames import USABLE_SURNAMES


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

_KEYWORDS = set(ALL_KEYWORDS)
_VERBS = set(VERB_ARITY.keys())
_ALL_RESERVED = sorted(_KEYWORDS | _VERBS | ADVERBS, key=len, reverse=True)
_SURNAMES = sorted(USABLE_SURNAMES)

# 用固定 seed 保证 CI 可重现
_RNG = random.Random(42)


def _safe_tokenize(source):
    """tokenize 不应对任何输入抛异常（除非是 ParseError 级别的诊断）。"""
    try:
        tokens = tokenize(source)
        assert isinstance(tokens, list)
        return tokens
    except Exception as e:
        # 只允许 ParseError / JiKuaiError（确实非法的语法），不允许 AttributeError/TypeError 等内部错误
        from jikuai.parser import ParseError
        from jikuai.evaluator import JiKuaiError
        if isinstance(e, (ParseError, JiKuaiError)):
            return []      # 不合法的极快代码，tokenize 层面报语法错误是可接受的
        raise AssertionError(
            f'tokenize 对输入 {source!r:.200s} 抛出非预期异常：'
            f'{type(e).__name__}: {e}') from e


# ---------------------------------------------------------------------------
# T1: 关键字/动词两两紧邻拼接
# ---------------------------------------------------------------------------

class TestKeywordBoundaryFuzz:
    """所有关键字两两拼接都能无崩溃地 tokenize。"""

    # 不跑全量笛卡尔积（太慢），随机采样 200 对
    _PAIRS = [(_ALL_RESERVED[i], _ALL_RESERVED[j])
              for i, j in ((_RNG.randrange(len(_ALL_RESERVED)),
                            _RNG.randrange(len(_ALL_RESERVED)))
                           for _ in range(200))]

    @pytest.mark.parametrize("kw1,kw2", _PAIRS[:50], ids=lambda *a: '')
    def test_two_keywords_adjacent(self, kw1, kw2):
        _safe_tokenize(kw1 + kw2)

    @pytest.mark.parametrize("kw1,kw2", _PAIRS[50:100], ids=lambda *a: '')
    def test_keyword_verb_adjacent(self, kw1, kw2):
        # 加句号收尾让它更像合法语句
        _safe_tokenize(kw1 + kw2 + '。')

    @pytest.mark.parametrize("kw1,kw2", _PAIRS[100:150], ids=lambda *a: '')
    def test_triple_keywords(self, kw1, kw2):
        third = _ALL_RESERVED[_RNG.randrange(len(_ALL_RESERVED))]
        _safe_tokenize(kw1 + kw2 + third)

    @pytest.mark.parametrize("kw1,kw2", _PAIRS[150:200], ids=lambda *a: '')
    def test_keyword_in_string_context(self, kw1, kw2):
        _safe_tokenize(f'定义 赵甲 = "{kw1}{kw2}"。')


# ---------------------------------------------------------------------------
# T2: 百家姓 + 关键字前缀组合
# ---------------------------------------------------------------------------

class TestSurnameKeywordFuzz:
    """百家姓开头 + 关键字（模拟用户把关键字当变量名的一部分）。"""

    _COMBOS = [(s, kw) for s, kw in
               ((_SURNAMES[_RNG.randrange(len(_SURNAMES))],
                 _ALL_RESERVED[_RNG.randrange(len(_ALL_RESERVED))])
                for _ in range(100))]

    @pytest.mark.parametrize("surname,kw", _COMBOS[:50], ids=lambda *a: '')
    def test_surname_prefix_keyword(self, surname, kw):
        # `赵如果` / `钱打印` / `孙定义` 等
        _safe_tokenize(f'定义 {surname}{kw} = 1。')

    @pytest.mark.parametrize("surname,kw", _COMBOS[50:], ids=lambda *a: '')
    def test_surname_keyword_assignment(self, surname, kw):
        _safe_tokenize(f'{surname}{kw} = 加 1 2。')


# ---------------------------------------------------------------------------
# T3: 随机合法极快语句片段
# ---------------------------------------------------------------------------

_TEMPLATES = [
    '定义 {id} = {verb} {num} {num}。',
    '打印 {verb} {id} {num}。',
    '如果 {id} {cmp} {num} 那么：\n  打印 {id}。\n。',
    '遍历 {id} 于 范围 1 {num}：\n  打印 {id}。\n。',
    '函数 {id} 接收 {id2}：\n  返回 {verb} {id2} {num}。\n。',
]

_CMP_VERBS = ['大于', '小于', '等于', '大于等于', '小于等于']


def _random_id():
    surname = _SURNAMES[_RNG.randrange(len(_SURNAMES))]
    tail_chars = '甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥'
    tail = ''.join(_RNG.choice(tail_chars) for _ in range(_RNG.randint(1, 3)))
    return surname + tail


def _random_stmt():
    tpl = _RNG.choice(_TEMPLATES)
    verbs = list(_VERBS - {'打印'})
    return tpl.format(
        id=_random_id(), id2=_random_id(),
        verb=_RNG.choice(verbs), num=_RNG.randint(1, 100),
        cmp=_RNG.choice(_CMP_VERBS),
    )


class TestRandomStatementFuzz:
    """随机生成 100 条极快语句，确保 tokenize 不崩溃。"""

    _STMTS = [_random_stmt() for _ in range(100)]

    @pytest.mark.parametrize("source", _STMTS[:50], ids=lambda *a: '')
    def test_random_statement_batch1(self, source):
        tokens = _safe_tokenize(source)
        assert len(tokens) > 0

    @pytest.mark.parametrize("source", _STMTS[50:], ids=lambda *a: '')
    def test_random_statement_batch2(self, source):
        tokens = _safe_tokenize(source)
        assert len(tokens) > 0


# ---------------------------------------------------------------------------
# T4: 已知边界 case（来自 CHANGELOG「已知边界」）
# ---------------------------------------------------------------------------

class TestKnownEdgeCases:
    """手工收集的分词难点。"""

    def test_identifier_contains_verb_char(self):
        # `赵只在主程序里` → 「只」是副词，会在此处断开
        _safe_tokenize('定义 赵只在主程序里 = 1。')

    def test_identifier_contains_add_verb(self):
        # `助手.相加` → 「加」是动词，会断开
        _safe_tokenize('赵助手.相加(1, 2)。')

    def test_mixed_chinese_english_identifier(self):
        # `自身.AI可用` → 分词可能在 AI/可用 处断
        _safe_tokenize('自身.AI可用 = 真。')

    def test_keyword_as_identifier_suffix(self):
        # 变量名末尾恰好是关键字
        _safe_tokenize('定义 赵如果甲 = 1。')
        _safe_tokenize('定义 赵定义乙 = 2。')
        _safe_tokenize('定义 赵函数丙 = 3。')

    def test_all_keywords_as_string_content(self):
        # 所有关键字出现在字符串内，不应触发分词
        all_kw_str = ''.join(_ALL_RESERVED[:20])
        _safe_tokenize(f'打印 "{all_kw_str}"。')

    def test_empty_and_whitespace(self):
        _safe_tokenize('')
        _safe_tokenize('   ')
        _safe_tokenize('\n\n\n')
        _safe_tokenize('\t\t')

    def test_only_punctuation(self):
        _safe_tokenize('。，：（）【】「」、')
        _safe_tokenize('.,;:()[]{}')

    def test_very_long_identifier(self):
        long_id = '赵' + '甲' * 200
        _safe_tokenize(f'定义 {long_id} = 1。')

    def test_repeated_keywords(self):
        _safe_tokenize('如果如果如果如果')
        _safe_tokenize('打印打印打印打印')
        _safe_tokenize('定义定义定义定义')

    def test_unicode_edge(self):
        # CJK 扩展 B 区汉字
        _safe_tokenize('定义 赵𠀀 = 1。')
        # 全角数字
        _safe_tokenize('打印 １２３。')

    def test_comment_boundary(self):
        # 注释后紧接关键字
        _safe_tokenize('-- 这是注释\n打印 1。')
        _safe_tokenize('#这是注释\n定义 赵甲 = 2。')

    def test_string_with_newlines(self):
        _safe_tokenize('定义 赵甲 = "行一\n行二"。')

    def test_number_touching_chinese(self):
        # 数字紧接汉字
        _safe_tokenize('打印 123加456。')
        _safe_tokenize('打印 3乘5。')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
