# -*- coding: utf-8 -*-
"""dunder 属性拦截（v0.12.0 安全加固）。

背景：缺陷 3 的修复让 `evaluator._member_lookup` 对任意 opaque Python 对象
放开 `getattr`（为了让 `hashlib.sha256(x).hexdigest()` 这类链式调用可用）。
这扩大了 pybridge 的暴露面——原先属性访问被无条件拒绝，现在可沿
`__globals__` / `__builtins__` / `__class__` 这类 dunder 链走到宿主环境。

两道闸：
1. 语法层：`_member_lookup` 在所有分支之前拒绝 `对象.__xxx__`
2. 反射层：`PyCallable._guard_dunder_reflection` 拒绝
   `getattr/setattr/delattr/hasattr` 的 dunder 字符串实参（堵住语法层的旁路）
"""
import pytest

from jikuai import run_source
from jikuai.evaluator import JiKuaiError


# ---- 语法层：对象.__xxx__ ------------------------------------------------

def test_pymodule_dunder_dict_被拒():
    """`builtins.__dict__` 是最短的逃逸路径——经它能直接摸到 eval/exec。"""
    with pytest.raises(JiKuaiError, match='dunder'):
        run_source('导入 蟒:builtins。\n打印 builtins.__dict__。')


def test_pymodule_dunder_name_被拒():
    with pytest.raises(JiKuaiError, match='dunder'):
        run_source('导入 蟒:os。\n打印 os.__name__。')


def test_opaque_对象_dunder_class_被拒():
    """opaque 对象走 `_member_lookup` 的 fallback 分支，同样受闸。"""
    with pytest.raises(JiKuaiError, match='dunder'):
        run_source('导入 蟒:builtins。\n定义赵表=builtins.list()。\n'
                   '打印 赵表.__class__。')


# ---- 反射层：getattr 字符串旁路 -----------------------------------------

def test_getattr_dunder_字符串被拒():
    """语法层拦不住把属性名藏进运行期字符串，反射层补上。"""
    with pytest.raises(JiKuaiError, match='dunder'):
        run_source('导入 蟒:builtins。\n定义赵表=builtins.list()。\n'
                   '打印 builtins.getattr(赵表 "__class__")。')


def test_hasattr_dunder_字符串被拒():
    with pytest.raises(JiKuaiError, match='dunder'):
        run_source('导入 蟒:builtins。\n定义赵表=builtins.list()。\n'
                   '打印 builtins.hasattr(赵表 "__dict__")。')


# ---- 不误伤：正常用法必须继续可用 ---------------------------------------

def test_opaque_普通方法仍可链式调用(capsys):
    """缺陷 3 的目标场景：不能因为加闸把它堵回去。"""
    run_source('导入 蟒:hashlib。\n导入 蟒:builtins。\n'
               '定义赵字=builtins.bytes("abc" "utf-8")。\n'
               '定义赵h=hashlib.sha256(赵字)。\n'
               '打印 赵h.hexdigest()。')
    out = capsys.readouterr().out.strip()
    assert out == ('ba7816bf8f01cfea414140de5dae2223b'
                   '00361a396177a9cb410ff61f20015ad')


def test_getattr_普通属性名仍可用(capsys):
    """反射闸只针对 dunder 实参，普通属性名不受影响。"""
    run_source('导入 蟒:hashlib。\n导入 蟒:builtins。\n'
               '定义赵字=builtins.bytes("abc" "utf-8")。\n'
               '定义赵h=hashlib.sha256(赵字)。\n'
               '打印 builtins.getattr(赵h "hexdigest")()。')
    assert capsys.readouterr().out.strip().startswith('ba7816bf')


def test_非反射函数收到dunder字符串不被拦(capsys):
    """普通函数拿到 `"__x__"` 只是普通数据，拦它属于误伤。"""
    run_source('导入 蟒:builtins。\n打印 builtins.len("__abc__")。')
    assert capsys.readouterr().out.strip() == '7'


def test_极快原生成员访问不受影响(capsys):
    """极快原生对象的成员名都是中文，永不以 `__` 开头。"""
    run_source('类 甲：\n  构造 接收 赵值：\n    自身.数据=赵值。\n  。\n。\n'
               '定义赵物=新建甲(7)。\n打印 赵物.数据。')
    assert capsys.readouterr().out.strip() == '7'


def test_块导入链路不受影响(capsys):
    """52 个块全靠 `从 blocks.X.Y 导入 Z`，不能被安全闸影响。"""
    run_source('从 blocks.数据.求和 导入 汇总。\n打印 汇总(列 1 2 3)。')
    assert capsys.readouterr().out.strip() == '6'
