# -*- coding: utf-8 -*-
"""下标取值（`Index`）的中文诊断 —— ADR-37 §2.5 · W104 第一步。

背景：v0.22.0 之前 `_eval_Index` 只写 `obj[idx]` / `obj[int(idx)]`，Python 原生异常
直通到统一包装处（`evaluator.py` 的 `_eval_node` 兜底），用户看到的是
`list index out of range` / `'乙'` / `'int' object is not subscriptable` 这类英文原文，
与 ADR-09「诊断不得泄漏实现细节」冲突。本文件把修好后的中文文案钉住——AOT
（ADR-37 第一切片）要逐字对齐这份文案，所以文案本身就是契约，不能悄悄改。

同时钉住两条**故意保留**的现状语义：负下标按尾部计数、小数下标截断。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import io
import contextlib

import pytest

from jikuai.errors import ErrorCategory
from jikuai.evaluator import JiKuaiError
from jikuai.main import run_source


def _跑(src):
    """执行一段极快源码，返回 stdout。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_source(src)
    return buf.getvalue()


def _诊断(src):
    """执行必须失败，返回 ErrorInfo。

    断言 `info is not None`：没有 info 就意味着走了兜底包装（即 Python 原文直通），
    那正是本文件要防的回归。
    """
    with pytest.raises(JiKuaiError) as e:
        _跑(src)
    info = getattr(e.value, 'info', None)
    assert info is not None, f'诊断没带 ErrorInfo，说明又退回兜底包装了：{e.value!r}'
    return info


# ─────────────────────── 越界 ───────────────────────

def test_列表下标越界给中文诊断():
    info = _诊断('定义赵表=列 10 20 30。\n打印 赵表[9]。\n')
    assert info.category is ErrorCategory.RUNTIME
    assert info.message == '下标越界：列表长度为 3，下标 9 超出有效范围（-3 到 2）'


def test_列表负下标越界也给中文诊断():
    info = _诊断('定义赵表=列 10 20 30。\n打印 赵表[-4]。\n')
    assert info.category is ErrorCategory.RUNTIME
    assert '下标越界' in info.message
    assert '-4' in info.message


def test_空列表取下标给中文诊断():
    info = _诊断('定义赵表=列。\n打印 赵表[0]。\n')
    assert info.category is ErrorCategory.RUNTIME
    assert '长度为 0' in info.message


# ─────────────────────── 字典键 ───────────────────────

def test_字典键不存在给中文诊断():
    info = _诊断('定义赵d={"甲": 1}。\n打印 赵d["乙"]。\n')
    assert info.category is ErrorCategory.RUNTIME
    assert info.message == '键不存在：字典里没有键 「乙」'


def test_字典整数键不存在给中文诊断():
    """整数键不加引号——引号是字符串的标记，别让用户以为键是 `"2"`。"""
    info = _诊断('定义赵d={1: "壹"}。\n打印 赵d[2]。\n')
    assert info.message == '键不存在：字典里没有键 2'


def test_不可哈希的东西当键给类型诊断():
    """与 `_eval_DictLit` 的 ADR-23b 检查同口径：那边管构造，这边管取值。"""
    info = _诊断('定义赵d={"甲": 1}。\n打印 赵d[列 1 2]。\n')
    assert info.category is ErrorCategory.TYPE
    assert '列表不能作为字典的键' in info.message


# ─────────────────────── 类型不对 ───────────────────────

def test_对标量取下标给类型诊断():
    info = _诊断('定义赵n=5。\n打印 赵n[0]。\n')
    assert info.category is ErrorCategory.TYPE
    assert info.message == '整数不支持下标取值'


def test_非数字当序列下标给类型诊断():
    info = _诊断('定义赵表=列 10 20 30。\n打印 赵表["甲"]。\n')
    assert info.category is ErrorCategory.TYPE
    assert info.message == '下标必须是整数，收到字符串 「甲」'


def test_人民币取下标用中文类型名():
    """兜底映射：`RMB` 是 `evaluator.RMB`，不显式映射就会漏出 Python 类名 `RMB`。"""
    info = _诊断('定义赵钱=￥9.90。\n打印 赵钱[0]。\n')
    assert info.message == '人民币不支持下标取值'


def test_人民币当下标用中文类型名():
    info = _诊断('定义赵表=列 10 20 30。\n打印 赵表[￥1.00]。\n')
    assert info.message == '下标必须是整数，收到人民币 ￥1.00'


# ───────────────── 与 AOT 的文案对齐（ADR-37 §2.5） ─────────────────

def test_中文类型名与aot运行时同词():
    """解释器的 `_中文类型名` 与 AOT `jk_typename` 必须是同一套词。

    钉这条是因为两套映射写在两个文件里，改一处忘另一处不会有任何报错——直到用户
    发现同一份 `.jk` 解释器说「人民币」、AOT 二进制说「RMB」。这里不比对 C 源码
    结构，只要求每个中文词的 UTF-8 转义形式都出现在生成的 C 运行时里。
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools', 'aot'))
    from jikuai_aot import codegen

    from jikuai.evaluator import _中文类型名
    for 词 in sorted(set(_中文类型名.values())):
        assert codegen._c_escape(词) in codegen._C_RUNTIME, \
            f'AOT C 运行时里找不到中文类型名「{词}」，两路文案会不一致'



# ─────────────────────── 不泄漏实现细节（ADR-09） ───────────────────────

@pytest.mark.parametrize('src', [
    '定义赵表=列 10 20 30。\n打印 赵表[9]。\n',
    '定义赵d={"甲": 1}。\n打印 赵d["乙"]。\n',
    '定义赵n=5。\n打印 赵n[0]。\n',
    '定义赵表=列 10 20 30。\n打印 赵表["甲"]。\n',
    '定义赵d={"甲": 1}。\n打印 赵d[列 1 2]。\n',
])
def test_下标诊断不含python实现细节(src):
    """ADR-09：这五条以前全是 Python 英文原文，现在一个都不许漏出去。"""
    msg = _诊断(src).message
    for 泄漏 in ('index out of range', 'subscriptable', 'invalid literal',
                 'unhashable', 'Traceback', 'KeyError', 'TypeError',
                 'IndexError', 'ValueError', 'int()', 'list', 'dict', 'str'):
        assert 泄漏 not in msg, (泄漏, msg)


def test_下标诊断带行号与源码行():
    """诊断要能定位，否则用户在长文件里照样瞎找。"""
    info = _诊断('定义赵表=列 10 20 30。\n定义赵别=1。\n打印 赵表[9]。\n')
    assert info.line == 3
    assert '赵表[9]' in info.source_line


# ─────────────────── 故意保留的现状语义（不是 bug，别顺手改） ───────────────────

def test_负下标按尾部计数不报错():
    assert _跑('定义赵表=列 10 20 30。\n打印 赵表[-1]。\n') == '30\n'


def test_小数下标沿用截断语义():
    """ADR-37 §2.5 与 §4：`1.7` 截断成下标 1，不报错也不提示。

    这看着像 bug，但它是 v0.22.0 之前就有的行为，改它属于破坏性变更，得单独立项。
    这条测试的作用是：真要改的时候，必须先看见它红。
    """
    assert _跑('定义赵表=列 10 20 30。\n打印 赵表[1.7]。\n') == '20\n'


def test_字符串按下标取字符():
    assert _跑('定义赵s="你好"。\n打印 赵s[1]。\n') == '好\n'
