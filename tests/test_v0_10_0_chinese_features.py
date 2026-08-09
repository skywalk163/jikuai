# -*- coding: utf-8 -*-
"""v0.10.0 - T4: 中文正则 + 成语/歇后语断言动词测试。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from jikuai.evaluator import Evaluator, JiKuaiError
from jikuai.keywords import VERB_ARITY


# ===================== 动词元数注册 =====================

class TestVerbArity:
    """5 个新动词在 VERB_ARITY 中注册了正确的元数。"""

    @pytest.mark.parametrize('verb,arity', [
        ('匹配', 2),      # 匹配
        ('查找', 2),      # 查找
        ('替换正则', 3),  # 替换正则
        ('中文字符', 1),  # 中文字符
        ('成语断言', 1),  # 成语断言
        ('歇后语断言', 2),  # 歇后语断言
    ])
    def test_arity_registered(self, verb, arity):
        assert verb in VERB_ARITY
        assert VERB_ARITY[verb] == arity

    def test_verbs_also_in_evaluator(self):
        e = Evaluator()
        for v in ['匹配', '查找', '替换正则',
                  '中文字符', '成语断言', '歇后语断言']:
            assert v in e.verbs, f'{v} not in evaluator verbs'


# ===================== 匹配（全匹配） =====================

class TestPiPei:
    """匹配 动词：re.fullmatch 语义。"""

    def test_chinese_fullmatch_true(self):
        from jikuai.evaluator import _regex_full_match
        assert _regex_full_match('你好世界', r'[一-鿿]+') is True

    def test_chinese_fullmatch_false(self):
        from jikuai.evaluator import _regex_full_match
        assert _regex_full_match('hello世界', r'[一-鿿]+') is False

    def test_fullmatch_empty(self):
        from jikuai.evaluator import _regex_full_match
        assert _regex_full_match('', '.*') is True

    def test_fullmatch_digits(self):
        from jikuai.evaluator import _regex_full_match
        assert _regex_full_match('12345', r'\d+') is True

    def test_invalid_regex_raises_jikuai_error(self):
        from jikuai.evaluator import _regex_full_match
        with pytest.raises(JiKuaiError) as exc:
            _regex_full_match('abc', '[unclosed')
        assert '正则表达式不合法' in str(exc.value)


# ===================== 查找 =====================

class TestChaZhao:
    """查找 动词：re.findall 语义。"""

    def test_find_numbers_in_mixed(self):
        from jikuai.evaluator import _regex_find_all
        result = _regex_find_all('今天气温25度明天降到10度', r'\d+')
        assert result == ['25', '10']

    def test_find_returns_list(self):
        from jikuai.evaluator import _regex_find_all
        result = _regex_find_all('abc', r'\d+')
        assert result == []

    def test_find_chinese_words(self):
        from jikuai.evaluator import _regex_find_all
        result = _regex_find_all('我爱北京和上海', r'[一-鿿]+')
        assert result == ['我爱北京和上海']

    def test_invalid_regex_raises(self):
        from jikuai.evaluator import _regex_find_all
        with pytest.raises(JiKuaiError):
            _regex_find_all('abc', '(unclosed')


# ===================== 替换正则 =====================

class TestTiHuanZhengZe:
    """替换正则 动词：re.sub 语义。"""

    def test_replace_digits(self):
        from jikuai.evaluator import _regex_sub
        assert _regex_sub('电话010-1234', r'\d+', 'X') == '电话X-X'

    def test_replace_no_match(self):
        from jikuai.evaluator import _regex_sub
        assert _regex_sub('hello', 'xyz', 'abc') == 'hello'

    def test_replace_chinese(self):
        from jikuai.evaluator import _regex_sub
        assert _regex_sub('你好世界', '世界', '中国') == '你好中国'

    def test_invalid_pattern_raises(self):
        from jikuai.evaluator import _regex_sub
        with pytest.raises(JiKuaiError):
            _regex_sub('x', '[bad', 'y')


# ===================== 中文字符 =====================

class TestZhongWenZiFu:
    """中文字符 动词：提取 CJK 字符。"""

    def test_extract_from_mixed(self):
        from jikuai.evaluator import _extract_cjk
        result = _extract_cjk('Hello世界123你好!')
        assert result == ['世', '界', '你', '好']

    def test_no_chinese(self):
        from jikuai.evaluator import _extract_cjk
        assert _extract_cjk('Hello World 123') == []

    def test_all_chinese(self):
        from jikuai.evaluator import _extract_cjk
        assert _extract_cjk('中国人民') == ['中', '国', '人', '民']

    def test_empty_string(self):
        from jikuai.evaluator import _extract_cjk
        assert _extract_cjk('') == []


# ===================== 成语断言 =====================

class TestChengYuDuanYan:
    """成语断言 动词：判定四字成语。"""

    def test_known_idiom_true(self):
        from jikuai.evaluator import _assert_idiom
        assert _assert_idiom('守株待兔') is True

    def test_non_idiom_false(self):
        from jikuai.evaluator import _assert_idiom
        assert _assert_idiom('随便一个') is False

    def test_wrong_length_false(self):
        from jikuai.evaluator import _assert_idiom
        assert _assert_idiom('三个字') is False

    def test_empty_string_false(self):
        from jikuai.evaluator import _assert_idiom
        assert _assert_idiom('') is False

    def test_none_input_false(self):
        from jikuai.evaluator import _assert_idiom
        assert _assert_idiom(None) is False

    def test_multiple_known(self):
        from jikuai.evaluator import _assert_idiom
        for idiom in ['画蛇添足', '亡羊补牢',
                      '掩耳盗铃', '胸有成竹']:
            assert _assert_idiom(idiom) is True


# ===================== 歇后语断言 =====================

class TestXieHouYuDuanYan:
    """歇后语断言 动词：判定歇后语对。"""

    def test_correct_pair_true(self):
        from jikuai.evaluator import _assert_xiehouyu
        assert _assert_xiehouyu('竹篮打水', '一场空') is True

    def test_wrong_second_half_false(self):
        from jikuai.evaluator import _assert_xiehouyu
        assert _assert_xiehouyu('竹篮打水', '自身难保') is False

    def test_unknown_first_half_false(self):
        from jikuai.evaluator import _assert_xiehouyu
        assert _assert_xiehouyu('不存在的前半', '一场空') is False

    def test_multiple_known(self):
        from jikuai.evaluator import _assert_xiehouyu
        assert _assert_xiehouyu('泥菩萨过江', '自身难保') is True
        assert _assert_xiehouyu('芝麻开花', '节节高') is True
        assert _assert_xiehouyu('小葱拌豆腐', '一清二白') is True

    def test_empty_inputs_false(self):
        from jikuai.evaluator import _assert_xiehouyu
        assert _assert_xiehouyu('', '一场空') is False
        assert _assert_xiehouyu('竹篮打水', '') is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
