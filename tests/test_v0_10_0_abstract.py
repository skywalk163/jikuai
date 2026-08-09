"""T6 - abstract classes and interfaces via naming convention.

抽象类：类名以「抽」开头 —— 不可实例化（JK-E4002）。
接口：类名以「协」开头 —— 不可实例化（JK-E4002），所有方法隐式抽象。
具体子类未实现全部抽象方法 —— JK-E4003。
`是否实现 实例 "协类名"` —— 结构类型（鸭子类型）判定。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from jikuai.main import run_source
from jikuai.evaluator import Evaluator, JiKuaiError


def _run_capture(src, capsys):
    run_source(src, Evaluator())
    return capsys.readouterr().out


_ABSTRACT_SHAPE = """类 抽形状：
  方法 面积：
    抛出 "未实现"。
  。
  方法 周长：
    空。
  。
  方法 描述：
    返回 "一个形状"。
  。
。
"""


class TestAbstractClass:
    def test_cannot_instantiate_abstract_class(self):
        src = _ABSTRACT_SHAPE + "新建 抽形状()。\n"
        with pytest.raises(JiKuaiError) as e:
            run_source(src, Evaluator())
        assert "JK-E4002" in str(e.value)
        assert "抽形状" in str(e.value)

    def test_concrete_subclass_implements_all(self, capsys):
        src = _ABSTRACT_SHAPE + """类 圆 继承 抽形状：
  构造 接收 赵半径：
    自身.半径 = 赵半径。
  。
  方法 面积：
    返回 乘 乘 3 自身.半径 自身.半径。
  。
  方法 周长：
    返回 乘 6 自身.半径。
  。
。
定义 赵圆 = 新建 圆(10)。
打印 赵圆.面积()。
"""
        out = _run_capture(src, capsys)
        assert "300" in out

    def test_concrete_subclass_missing_method(self):
        src = _ABSTRACT_SHAPE + """类 方块 继承 抽形状：
  构造 接收 赵边：
    自身.边 = 赵边。
  。
  方法 面积：
    返回 乘 自身.边 自身.边。
  。
。
定义 赵方 = 新建 方块(5)。
"""
        with pytest.raises(JiKuaiError) as e:
            run_source(src, Evaluator())
        assert "JK-E4003" in str(e.value)
        assert "周长" in str(e.value)

    def test_abstract_concrete_method_inherited(self, capsys):
        src = _ABSTRACT_SHAPE + """类 圆 继承 抽形状：
  方法 面积：
    返回 314。
  。
  方法 周长：
    返回 62。
  。
。
定义 赵圆 = 新建 圆()。
打印 赵圆.描述()。
"""
        out = _run_capture(src, capsys)
        assert "一个形状" in out


_INTERFACE = """类 协可序列化：
  方法 序列化：
    空。
  。
  方法 反序列化：
    空。
  。
。
"""


class TestInterface:
    def test_cannot_instantiate_interface(self):
        src = _INTERFACE + "新建 协可序列化()。\n"
        with pytest.raises(JiKuaiError) as e:
            run_source(src, Evaluator())
        assert "JK-E4002" in str(e.value)
        assert "协可序列化" in str(e.value)

    def test_concrete_implements_interface(self, capsys):
        src = _INTERFACE + """类 用户 继承 协可序列化：
  方法 序列化：
    返回 "json"。
  。
  方法 反序列化：
    返回 "obj"。
  。
。
定义 赵用户 = 新建 用户()。
打印 赵用户.序列化()。
"""
        out = _run_capture(src, capsys)
        assert "json" in out

    def test_concrete_missing_interface_method(self):
        src = _INTERFACE + """类 用户 继承 协可序列化：
  方法 序列化：
    返回 "json"。
  。
。
定义 赵用户 = 新建 用户()。
"""
        with pytest.raises(JiKuaiError) as e:
            run_source(src, Evaluator())
        assert "JK-E4003" in str(e.value)
        assert "反序列化" in str(e.value)


class TestImplementsCheck:
    def test_implements_true(self, capsys):
        src = """类 协可打印：
  方法 打印自身：
    空。
  。
。
类 文档：
  方法 打印自身：
    返回 "doc"。
  。
。
定义 赵文档 = 新建 文档()。
打印 是否实现 赵文档 "协可打印"。
"""
        out = _run_capture(src, capsys)
        assert "真" in out

    def test_implements_false(self, capsys):
        src = """类 协可打印：
  方法 打印自身：
    空。
  。
。
类 猫：
  方法 叫：
    返回 "喵"。
  。
。
定义 赵猫 = 新建 猫()。
打印 是否实现 赵猫 "协可打印"。
"""
        out = _run_capture(src, capsys)
        assert "假" in out

    def test_implements_non_instance(self, capsys):
        src = """类 协甲：
  方法 乙：
    空。
  。
。
打印 是否实现 42 "协甲"。
"""
        out = _run_capture(src, capsys)
        assert "假" in out

    def test_implements_structural_not_inheritance(self, capsys):
        src = """类 协可排序：
  方法 比较：
    空。
  。
。
类 数值：
  方法 比较：
    返回 1。
  。
。
定义 赵数值 = 新建 数值()。
打印 是否实现 赵数值 "协可排序"。
"""
        out = _run_capture(src, capsys)
        assert "真" in out


class TestThreeLevelInheritance:
    def test_interface_abstract_concrete_chain(self, capsys):
        src = """类 协可比较：
  方法 比较：
    空。
  。
。
类 抽数值 继承 协可比较：
  方法 绝对值：
    抛出 "未实现"。
  。
  方法 符号：
    返回 "+"。
  。
。
类 整数值 继承 抽数值：
  构造 接收 赵值：
    自身.值 = 赵值。
  。
  方法 比较：
    返回 自身.值。
  。
  方法 绝对值：
    返回 自身.值。
  。
。
定义 赵数 = 新建 整数值(42)。
打印 赵数.比较()。
打印 赵数.绝对值()。
打印 赵数.符号()。
"""
        out = _run_capture(src, capsys)
        assert "42" in out
        assert "+" in out

    def test_three_level_missing_method(self):
        src = """类 协可比较：
  方法 比较：
    空。
  。
。
类 抽数值 继承 协可比较：
  方法 绝对值：
    抛出 "未实现"。
  。
。
类 整数值 继承 抽数值：
  方法 绝对值：
    返回 1。
  。
。
定义 赵数 = 新建 整数值()。
"""
        with pytest.raises(JiKuaiError) as e:
            run_source(src, Evaluator())
        assert "JK-E4003" in str(e.value)
        assert "比较" in str(e.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])