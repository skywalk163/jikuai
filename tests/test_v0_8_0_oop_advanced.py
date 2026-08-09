# -*- coding: utf-8 -*-
"""M9-4 · 面向对象进阶 — 私有成员封装 + 反射（是否是 / 类名）。

私有成员：以「私」开头的字段/方法只能经 `自身.` 访问，类外访问抛错。
反射：`是否是 实例 "类名"` 沿继承链判定；`类名 实例` 取所属类名。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from jikuai.main import run_source
from jikuai.evaluator import Evaluator, JiKuaiError


def _run(src):
    """执行源码，返回 (最终结果, evaluator)。"""
    ev = Evaluator()
    result = run_source(src, ev)
    return result, ev


def _run_capture(src, capsys):
    run_source(src, Evaluator())
    return capsys.readouterr().out


# ===========================================================================
# 私有成员封装
# ===========================================================================

_ACCOUNT = '''类 账户：
  构造 接收 赵初始：
    自身.私余额 = 赵初始。
  。
  方法 存钱 接收 赵金额：
    自身.私余额 = 加 自身.私余额 赵金额。
  。
  方法 查余额：
    返回 自身.私余额。
  。
。
'''


class TestPrivateMembers:
    def test_private_field_accessible_from_inside(self, capsys):
        src = _ACCOUNT + '定义 赵户 = 新建 账户(100)。\n赵户.存钱(50)。\n打印 赵户.查余额()。\n'
        out = _run_capture(src, capsys)
        assert '150' in out

    def test_private_field_denied_from_outside(self):
        src = _ACCOUNT + '定义 赵户 = 新建 账户(100)。\n打印 赵户.私余额。\n'
        with pytest.raises(JiKuaiError) as e:
            run_source(src, Evaluator())
        assert '私有成员不可从外部访问' in str(e.value)

    def test_private_method_denied_from_outside(self):
        src = '''类 服务：
  方法 私内部计算：
    返回 42。
  。
  方法 对外接口：
    返回 自身.私内部计算()。
  。
。
定义 赵服务 = 新建 服务()。
打印 赵服务.私内部计算()。
'''
        with pytest.raises(JiKuaiError) as e:
            run_source(src, Evaluator())
        assert '私有成员不可从外部访问' in str(e.value)

    def test_private_method_callable_from_inside(self, capsys):
        src = '''类 服务：
  方法 私内部计算：
    返回 42。
  。
  方法 对外接口：
    返回 自身.私内部计算()。
  。
。
定义 赵服务 = 新建 服务()。
打印 赵服务.对外接口()。
'''
        out = _run_capture(src, capsys)
        assert '42' in out

    def test_public_members_unaffected(self, capsys):
        # 不以「私」开头的成员照常从外部可访问
        src = '''类 点：
  构造 接收 赵x：
    自身.横坐标 = 赵x。
  。
。
定义 赵p = 新建 点(7)。
打印 赵p.横坐标。
'''
        out = _run_capture(src, capsys)
        assert '7' in out


# ===========================================================================
# 反射：是否是 / 类名
# ===========================================================================

_ANIMALS = '''类 动物：
  方法 叫：
    返回 "..."。
  。
。
类 狗 继承 动物：
  方法 叫：
    返回 "汪"。
  。
。
定义 赵狗 = 新建 狗()。
'''


class TestReflection:
    def test_is_instance_of_own_class(self, capsys):
        out = _run_capture(_ANIMALS + '打印 是否是 赵狗 "狗"。\n', capsys)
        assert '真' in out

    def test_is_instance_of_parent_class(self, capsys):
        # 子类实例对父类名也返回真（isinstance 语义）
        out = _run_capture(_ANIMALS + '打印 是否是 赵狗 "动物"。\n', capsys)
        assert '真' in out

    def test_is_instance_of_unrelated_class(self, capsys):
        out = _run_capture(_ANIMALS + '打印 是否是 赵狗 "猫"。\n', capsys)
        assert '假' in out

    def test_is_instance_non_object(self, capsys):
        # 非对象一律返回假，不报错
        out = _run_capture('打印 是否是 42 "动物"。\n', capsys)
        assert '假' in out

    def test_class_name(self, capsys):
        out = _run_capture(_ANIMALS + '打印 类名 赵狗。\n', capsys)
        assert '狗' in out

    def test_class_name_on_non_object_errors(self):
        with pytest.raises(JiKuaiError) as e:
            run_source('打印 类名 42。\n', Evaluator())
        assert '类名' in str(e.value)

    def test_polymorphic_dispatch_still_works(self, capsys):
        # 覆写方法的多态派发（最派生优先）——回归确认没被破坏
        out = _run_capture(_ANIMALS + '打印 赵狗.叫()。\n', capsys)
        assert '汪' in out

    def test_reflection_drives_branching(self, capsys):
        # 反射用于按类型分流的典型场景
        src = _ANIMALS + '''如果 是否是 赵狗 "动物" 那么：
  打印 "是动物"。
否则：
  打印 "不是动物"。
。
'''
        out = _run_capture(src, capsys)
        assert '是动物' in out


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
