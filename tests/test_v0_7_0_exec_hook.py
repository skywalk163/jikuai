# -*- coding: utf-8 -*-
'''极快 v0.7.0 · M6-P3 · T-M6-D05 · ExecHook 主包侧测试。

覆盖点：
    1. `Evaluator()` 无 hook 时行为与改造前完全一致（兼容红线）
    2. 记录型 hook：`before_stmt` 调用次数与顺序符合语句序列
    3. hook 抛异常不被静默吞掉（并说明 DAP 为何用 BaseException）
    4. 性能量测：hook=None vs hook=记录器（只输出数据，不做硬断言，避免 flaky）

本文件只依赖主包 `jikuai`，不依赖 `dap/`。
'''

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'dap'))

from jikuai.evaluator import Evaluator, ExecHook           # noqa: E402
from jikuai.frontend import compile_source                 # noqa: E402


def _run(source, hook=None):
    '''编译并求值一段极快源码，返回 (求值器, 最后一条语句的值)。'''
    result = compile_source(source, file='<test>')
    ev = Evaluator(hook=hook) if hook is not None else Evaluator()
    value = ev.eval(result.ast, source=source)
    return ev, value


class RecordingHook(ExecHook):
    '''记录每次 before_stmt 的 (节点类型名, 行号)。'''

    def __init__(self):
        self.calls = []
        self.breaks = []

    def before_stmt(self, node, env):
        self.calls.append((type(node).__name__, getattr(node, 'line', 0)))

    def on_break(self, node, env):
        self.breaks.append(getattr(node, 'line', 0))


class BoomError(Exception):
    '''普通 Exception 子类，用于验证 hook 异常不被吞掉。'''


class HardBoom(BaseException):
    '''BaseException 子类：DAP 的 TerminateDebug 采用同样策略。'''


class BoomHook(ExecHook):
    '''在第 n 次 before_stmt 抛出指定异常。'''

    def __init__(self, at=2, exc=BoomError):
        self.at = at
        self.exc = exc
        self.count = 0

    def before_stmt(self, node, env):
        self.count += 1
        if self.count == self.at:
            raise self.exc('hook 主动抛出')


# ─────────────── 1. 兼容红线：无 hook 行为不变 ───────────────

def test_无hook构造与求值行为不变_算术():
    ev, _ = _run('定义王甲=加 3 5。\n定义王乙=乘 王甲 2。\n')
    assert ev.global_env.get('王甲') == 8
    assert ev.global_env.get('王乙') == 16


def test_无hook构造与求值行为不变_人民币():
    ev, _ = _run('定义王价=￥99.90。\n定义王量=3。\n定义王总=王价乘王量。\n')
    assert str(ev.global_env.get('王总')) == '￥299.70'


def test_无hook构造与求值行为不变_控制流():
    src = '定义王计=0。\n重复 3 次：\n  定义王计=王计加1。\n。\n'
    ev, _ = _run(src)
    assert ev.global_env.get('王计') == 3


def test_无hook构造与求值行为不变_异常处理(capsys):
    # 除零被 `尝试` 捕获，catch 分支应执行（try/catch 体在独立子作用域，故用打印观察）
    src = '尝试：\n  定义王甲=除 10 0。\n捕获 王错：\n  打印 \"已捕获\"。\n。\n'
    _run(src)
    out = capsys.readouterr().out
    assert '已捕获' in out


def test_默认Evaluator的hook为None():
    ev = Evaluator()
    assert ev._hook is None


def test_ExecHook默认实现是no_op():
    hook = ExecHook()
    assert hook.before_stmt(None, None) is None
    assert hook.on_break(None, None) is None


# ─────────────── 2. 记录型 hook：次数与顺序 ───────────────

def test_hook按语句顺序逐条回调():
    src = '定义王甲=1。\n定义王乙=2。\n定义王丙=加 王甲 王乙。\n'
    hook = RecordingHook()
    ev, _ = _run(src, hook=hook)
    assert hook.calls == [('Define', 1), ('Define', 2), ('Define', 3)]
    assert ev.global_env.get('王丙') == 3


def test_hook覆盖顶层语句总数():
    src = '定义王甲=1。\n定义王乙=2。\n定义王丙=3。\n定义王丁=4。\n'
    hook = RecordingHook()
    _run(src, hook=hook)
    assert len(hook.calls) == 4
    assert [c[1] for c in hook.calls] == [1, 2, 3, 4]


def test_hook在循环体内每轮都回调():
    src = '定义王计=0。\n重复 3 次：\n  定义王计=王计加1。\n。\n'
    hook = RecordingHook()
    _run(src, hook=hook)
    inner = [c for c in hook.calls if c[1] == 3]
    assert len(inner) == 3, f'循环体语句应回调 3 次，实际 {hook.calls}'
    assert hook.calls[0] == ('Define', 1)


def test_hook在函数体内也回调():
    src = ('函数 王加一 接收 王数：\n'
           '  返回 王数加1。\n'
           '。\n'
           '定义王果=王加一(41)。\n')
    hook = RecordingHook()
    ev, _ = _run(src, hook=hook)
    assert ev.global_env.get('王果') == 42
    types = [c[0] for c in hook.calls]
    # 函数体内的 返回 语句应被回调（证明 hook 深入到函数体）
    assert 'Return' in types, f'函数体语句应被回调，实际 {hook.calls}'


def test_hook收到的env可读到已赋值变量():
    '''AC-M6-05-03 的单元级预演：回调时刻的 env 反映该点真实状态。'''
    snapshots = []

    class SnapHook(ExecHook):
        def before_stmt(self, node, env):
            snapshots.append(dict(env.vars))

    src = '定义王甲=1。\n定义王乙=2。\n定义王丙=3。\n'
    _run(src, hook=SnapHook())
    assert snapshots[0] == {}
    assert snapshots[1] == {'王甲': 1}
    assert snapshots[2] == {'王甲': 1, '王乙': 2}


# ─────────────── 3. hook 抛异常的语义 ───────────────

def test_hook抛出Exception不被吞掉_顶层():
    src = '定义王甲=1。\n定义王乙=2。\n'
    with pytest.raises(BoomError):
        _run(src, hook=BoomHook(at=2, exc=BoomError))


def test_hook抛出Exception不被吞掉_循环体内():
    src = '定义王计=0。\n重复 3 次：\n  定义王计=王计加1。\n。\n'
    with pytest.raises(BoomError):
        _run(src, hook=BoomHook(at=3, exc=BoomError))


def test_尝试块内普通Exception会被语言级捕获(capsys):
    '''记录既有语义：`尝试` 的兜底 `except Exception` 会吞掉普通 Exception。'''
    src = '尝试：\n  定义王甲=1。\n捕获 王错：\n  打印 \"落入捕获\"。\n。\n'
    # 第1次回调是 Try 节点本身（顶层）；第2次才是 try 体首条语句，命中后被 `尝试` 捕获
    _run(src, hook=BoomHook(at=2, exc=BoomError))
    out = capsys.readouterr().out
    assert '落入捕获' in out


def test_尝试块内BaseException穿透_故DAP用BaseException():
    '''BaseException 不被 `尝试` 捕获 → DAP 的 TerminateDebug 采用此策略。'''
    src = '尝试：\n  定义王甲=1。\n捕获 王错：\n  打印 \"不应到达\"。\n。\n'
    with pytest.raises(HardBoom):
        _run(src, hook=BoomHook(at=2, exc=HardBoom))


# ─────────────── 4. 性能量测（只输出，不硬断言） ───────────────

def test_hook开销量测_不做硬断言(capsys):
    src = '定义王计=0。\n重复 2000 次：\n  定义王计=王计加1。\n。\n'
    result = compile_source(src, file='<bench>')

    def timeit(hook_factory):
        best = None
        for _ in range(3):
            hook = hook_factory()
            ev = Evaluator(hook=hook) if hook is not None else Evaluator()
            t0 = time.perf_counter()
            ev.eval(result.ast, source=src)
            dt = time.perf_counter() - t0
            best = dt if best is None else min(best, dt)
        return best

    t_none = timeit(lambda: None)
    t_hook = timeit(RecordingHook)
    with capsys.disabled():
        print(f'\n[ExecHook 量测] hook=None: {t_none * 1000:.2f} ms  '
              f'hook=记录器: {t_hook * 1000:.2f} ms  '
              f'倍率: {t_hook / t_none:.2f}x')
    assert t_none > 0 and t_hook > 0
