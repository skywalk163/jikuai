# -*- coding: utf-8 -*-
"""v0.4.1 · D-13 验收测试。

D-13：`evaluator._eval_Try` 的 `except Exception` 兜底分支把控制流信号
（`ReturnSignal` / `BreakSignal` / `ContinueSignal`）也吞了，导致：

  函数 王甲：
    尝试：返回 1。捕获 e：返回 2。。
  。
  王甲()   -- 期望 1，修复前得到 2

修复方向（ADR-08 一致）：在 `尝试` 的 except 链最前面，把三种控制流信号
`raise` 透传出去，只留 `JiKuaiError` 与非控制流 `Exception` 走 `捕获` 分支；
`最终` 分支即使在控制流透传时也必须执行。

用例：
- test_d13_try_return_transparent_in_function：函数体内 尝试 { 返回 X } → 返回 X
- test_d13_try_break_transparent_in_loop：循环体内 尝试 { 跳出 } → 跳出
- test_d13_try_continue_transparent_in_loop：循环体内 尝试 { 跳过 } → 跳过
- test_d13_finally_runs_when_return_transparent：最终 分支存在时，
  返回 仍透传给外层，且 最终 语句已执行（副作用可观测）
- test_d13_jikuai_error_still_caught_by_catch：真错误仍被 捕获 分支接住（回归防护）
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.lexer import tokenize
from jikuai.parser import parse
from jikuai.evaluator import Evaluator


def run(src):
    """执行源码并返回最终值。"""
    ev = Evaluator()
    return ev.eval(parse(tokenize(src)))


# ---------- 用例 1：函数体内 尝试 { 返回 X } 应透传 ----------

def test_d13_try_return_transparent_in_function():
    """尝试:返回 1。捕获 e:返回 2。 修复前返回 2，修复后返回 1。"""
    src = '''\
函数 王甲：
  尝试：
    返回 1。
  捕获 赵错误：
    返回 2。
  。
。
王甲()。'''
    assert run(src) == 1


def test_d13_try_return_transparent_no_catch():
    """无 捕获 分支时（仅 尝试 + 最终），返回 也必须透传。"""
    src = '''\
函数 王甲：
  尝试：
    返回 42。
  最终：
    打印 "清理"。
  。
。
王甲()。'''
    # 修复前会因 finally 覆盖或 except Exception 吞信号导致返回 None
    assert run(src) == 42


# ---------- 用例 2：循环体内 尝试 { 跳出 } 应透传 ----------

def test_d13_try_break_transparent_in_loop():
    """循环内 尝试 { 跳出 } 应中断循环而非被 尝试 吞掉。"""
    src = '''\
定义赵计=0。
遍历 赵项 于 列 1 2 3 4 5：
  尝试：
    如果 赵项 等于 3 那么：
      跳出。
    。
    定义赵计=赵计加 1。
  捕获 赵错误：
    定义赵计=999。
  。
。
赵计。'''
    # 前两次循环各 +1；第三次触发 跳出 → 修复前会走 捕获 分支把 赵计 置为 999
    assert run(src) == 2


def test_d13_try_continue_transparent_in_loop():
    """循环内 尝试 { 跳过 } 应跳到下一轮而非被 尝试 吞掉。"""
    src = '''\
定义赵计=0。
遍历 赵项 于 列 1 2 3 4 5：
  尝试：
    如果 赵项 等于 3 那么：
      跳过。
    。
    定义赵计=赵计加 1。
  捕获 赵错误：
    定义赵计=999。
  。
。
赵计。'''
    # 1/2/4/5 各 +1；3 被 跳过 → 应为 4
    # 修复前 跳过 会被 捕获 分支接住，把 赵计 置为 999，同时后续元素也走异常路径
    assert run(src) == 4


# ---------- 用例 3：最终 分支在 返回 透传时仍会执行 ----------

def test_d13_finally_runs_when_return_transparent(capsys):
    """最终 分支存在时，返回 仍透传给外层，且 最终 语句已执行。

    `最终` 体在独立子作用域求值（`定义` 只写本层），因此用 `打印` 的
    stdout 副作用观测它是否执行，而非用外层标记变量。
    """
    src = '''\
函数 王甲：
  尝试：
    返回 100。
  捕获 赵错误：
    返回 200。
  最终：
    打印 "最终已执行"。
  。
。
王甲()。'''
    # 修复后：返回 100（返回透传），且 最终 执行了
    # 修复前：返回 被 except Exception 吞进 捕获 分支 → 200
    assert run(src) == 100
    assert '最终已执行' in capsys.readouterr().out



# ---------- 用例 4：真错误仍被 捕获 分支接住（回归防护） ----------

def test_d13_jikuai_error_still_caught_by_catch():
    """确保 D-13 修复没有把真正的 JiKuaiError 也顺手透传出去。"""
    src = '''\
函数 王甲：
  尝试：
    抛出 "业务异常"。
    返回 1。
  捕获 赵错误：
    返回 2。
  。
。
王甲()。'''
    assert run(src) == 2
