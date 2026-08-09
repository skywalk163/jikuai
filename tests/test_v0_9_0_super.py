# -*- coding: utf-8 -*-
"""M10-1 · 显式父类方法调用（super）。

语法：`父类.方法名(参数)`。语义对齐 Python 的 `super()`：
- 起点是「当前方法定义所在类」的父类，而非 `实例.klass` 的父类；
- 因此三层继承里逐层 `父类.X()` 会正确地沿链上溯，不会无限递归。

`父类` 是隐式绑定（同 `自身`），只能在方法体内使用，且只能作为
成员访问的接收者出现——不能作为值赋值/传参/返回。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from jikuai.main import run_source
from jikuai.evaluator import Evaluator, JiKuaiError


def _cap(src, capsys):
    run_source(src, Evaluator())
    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# 基础：两层继承，子类方法内调用父类同名方法
# ---------------------------------------------------------------------------

_ANIMAL = '''类 动物：
  构造 接收 赵名：
    自身.名字 = 赵名。
  。
  方法 叫声：
    返回 自身.名字。
  。
。
类 狗 继承 动物：
  方法 叫声：
    定义 钱父 = 父类.叫声()。
    返回 拼接 钱父 "-汪汪"。
  。
。
'''


class TestSuperBasic:
    def test_super_calls_parent_method(self, capsys):
        src = _ANIMAL + '定义 赵旺 = 新建 狗("旺财")。\n打印 赵旺.叫声()。\n'
        assert '旺财-汪汪' in _cap(src, capsys)

    def test_super_with_args(self, capsys):
        src = '''类 计算器：
  方法 运算 接收 赵甲 钱乙：
    返回 加 赵甲 钱乙。
  。
。
类 双倍计算器 继承 计算器：
  方法 运算 接收 赵甲 钱乙：
    定义 孙基 = 父类.运算(赵甲, 钱乙)。
    返回 乘 孙基 2。
  。
。
定义 赵器 = 新建 双倍计算器()。
打印 赵器.运算(3, 4)。
'''
        assert '14' in _cap(src, capsys)


# ---------------------------------------------------------------------------
# 三层继承：父类起点必须是「定义类的父」，否则会无限递归
# ---------------------------------------------------------------------------

class TestSuperThreeLevel:
    def test_three_level_chain(self, capsys):
        src = '''类 甲：
  方法 描述：
    返回 "甲"。
  。
。
类 乙 继承 甲：
  方法 描述：
    返回 拼接 父类.描述() "-乙"。
  。
。
类 丙 继承 乙：
  方法 描述：
    返回 拼接 父类.描述() "-丙"。
  。
。
定义 赵丙 = 新建 丙()。
打印 赵丙.描述()。
'''
        assert '甲-乙-丙' in _cap(src, capsys)


# ---------------------------------------------------------------------------
# 私有方法可经 父类 调用（父类 与 自身 同属类内接收者）
# ---------------------------------------------------------------------------

class TestSuperPrivate:
    def test_super_can_call_private_parent_method(self, capsys):
        src = '''类 基类：
  方法 私秘钥：
    返回 "秘钥123"。
  。
。
类 子类 继承 基类：
  方法 取秘钥：
    返回 父类.私秘钥()。
  。
。
定义 赵子 = 新建 子类()。
打印 赵子.取秘钥()。
'''
        assert '秘钥123' in _cap(src, capsys)


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------

class TestSuperErrors:
    def test_super_outside_method_raises(self):
        with pytest.raises(JiKuaiError) as e:
            run_source('打印 父类。\n', Evaluator())
        assert '父类' in str(e.value)

    def test_super_no_parent_raises(self):
        src = '''类 孤立：
  方法 测试：
    返回 父类.测试()。
  。
。
定义 赵甲 = 新建 孤立()。
赵甲.测试()。
'''
        with pytest.raises(JiKuaiError) as e:
            run_source(src, Evaluator())
        msg = str(e.value)
        assert '父类' in msg or '无可用' in msg

    def test_super_unknown_method_raises(self):
        src = '''类 基类：
  方法 甲：
    返回 1。
  。
。
类 子类 继承 基类：
  方法 乙：
    返回 父类.不存在()。
  。
。
定义 赵子 = 新建 子类()。
赵子.乙()。
'''
        with pytest.raises(JiKuaiError) as e:
            run_source(src, Evaluator())
        assert '父类中无方法' in str(e.value) or '不存在' in str(e.value)

    def test_super_as_value_rejected(self):
        """裸 `父类` 作为赋值右值应被拒绝（DP-3）。"""
        src = '''类 基类：
  方法 甲：
    返回 1。
  。
。
类 子类 继承 基类：
  方法 乙：
    定义 钱x = 父类。
    返回 钱x。
  。
。
定义 赵子 = 新建 子类()。
赵子.乙()。
'''
        with pytest.raises(JiKuaiError) as e:
            run_source(src, Evaluator())
        assert '父类' in str(e.value)


# ---------------------------------------------------------------------------
# 关键字注册一致性
# ---------------------------------------------------------------------------

def test_super_keyword_registered():
    from jikuai import keywords
    assert '父类' in keywords.KW_SUPER
    assert '父类' in keywords.ALL_KEYWORDS
