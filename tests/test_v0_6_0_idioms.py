# -*- coding: utf-8 -*-
"""v0.6.0 · M5 · T-M5-S02：成语断言测试。"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib.util
import pytest

from jikuai import resources
from jikuai.main import run_source


def _load_idioms():
    path = resources.stdlib_path('成语.py')
    spec = importlib.util.spec_from_file_location('py_idioms', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

ID = _load_idioms()


# ===================== 库规模与版本 =====================

def test_库规模至少300条():
    assert ID.library_size() >= 300

def test_库规模上界():
    # 防止意外膨胀（本次交付口径为 300+，不是全量成语辞典）
    assert ID.library_size() < 2000

def test_版本号可读():
    v = ID.library_version()
    assert isinstance(v, str) and v

def test_集合与映射规模一致():
    assert len(ID.all_idioms()) == ID.library_size()


# ===================== 数据质量 =====================

def test_全部条目为汉字且长度合规():
    for w in ID.all_idioms():
        assert 3 <= len(w) <= 8, w
        assert all('\u4e00' <= ch <= '\u9fff' for ch in w), w

def test_全部条目有非空释义():
    for w, meaning in ID.IDIOMS.items():
        assert isinstance(meaning, str) and meaning.strip(), w

def test_主体为四字成语():
    four = sum(1 for w in ID.all_idioms() if len(w) == 4)
    assert four / ID.library_size() > 0.95


# ===================== 是成语 =====================

@pytest.mark.parametrize('word', [
    '一举两得', '胸有成竹', '学富五车', '兢兢业业', '实事求是',
    '掩耳盗铃', '画蛇添足', '茅塞顿开', '同舟共济', '未雨绸缪',
])
def test_命中库内成语(word):
    assert ID.is_idiom(word) is True

@pytest.mark.parametrize('word', [
    '随便一个词', '不是成语', '你好世界', '中国', 'abc', '',
])
def test_非成语返回假(word):
    assert ID.is_idiom(word) is False

def test_非字符串输入不抛错():
    assert ID.is_idiom(None) is False
    assert ID.is_idiom(123) is False
    assert ID.is_idiom(['一举两得']) is False

def test_两侧空白被裁剪():
    assert ID.is_idiom('  一举两得  ') is True


# ===================== 成语释义 =====================

def test_释义命中():
    assert ID.explain('胸有成竹') == '做事之前已有全面的考虑和打算'

def test_释义未命中返回None():
    assert ID.explain('不是成语') is None
    assert ID.explain('') is None
    assert ID.explain(None) is None

def test_每个库内成语都能取到释义():
    for w in ID.all_idioms():
        assert ID.explain(w) is not None


# ===================== O(1) 与不可变性 =====================

def test_集合是frozenset():
    assert isinstance(ID.all_idioms(), frozenset)

def test_调用方无法污染内部状态():
    s = ID.all_idioms()
    with pytest.raises(AttributeError):
        s.add('伪造成语')
    assert ID.is_idiom('伪造成语') is False


# ===================== 幂等（无副作用） =====================

def test_重复调用结果一致():
    first = [ID.is_idiom('一举两得'), ID.explain('一举两得'), ID.library_size()]
    for _ in range(5):
        assert [ID.is_idiom('一举两得'), ID.explain('一举两得'),
                ID.library_size()] == first


# ===================== 极快侧集成 =====================

def test_jk_是成语():
    assert run_source('导入 成语。\n成语.是成语("一举两得")。') is True
    assert run_source('导入 成语。\n成语.是成语("不是成语")。') is False

def test_jk_成语释义():
    r = run_source('导入 成语。\n成语.成语释义("学富五车")。')
    assert r == '读书多，学识丰富'

def test_jk_未命中返回空():
    assert run_source('导入 成语。\n成语.成语释义("不是成语")。') is None

def test_jk_未导出符号被拦截():
    from jikuai.evaluator import JiKuaiError
    with pytest.raises(JiKuaiError) as e:
        run_source('导入 成语。\n成语.library_size()。')
    assert 'JK-E5002' in str(e.value)