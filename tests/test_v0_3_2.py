# -*- coding: utf-8 -*-
"""v0.3.2 · D-10 / D-11 / D-12 验收测试。

D-10：变参动词后接中缀表达式 —— parser 中缀合并（方案 A）+ evaluator 元数守卫（方案 B）
D-11：`python -m jikuai` 入口可用
D-12：`_class_regions` / `_prescan_self_fields` 切到 `_scan_src`，注释/字符串不污染白名单
"""

import io
import os
import subprocess
import sys
import contextlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.lexer import tokenize, Lexer
from jikuai.parser import parse
from jikuai.evaluator import Evaluator, JiKuaiError
from jikuai.ast_nodes import Call, Ident
from jikuai.errors import ErrorCategory


_ROOT = os.path.join(os.path.dirname(__file__), '..')


def _run(src):
    ev = Evaluator()
    return ev.eval(parse(tokenize(src)), source=src)


def _run_capture(src):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        _run(src)
    return [ln for ln in out.getvalue().strip().split('\n') if ln != '']


# ============================================================
# T-D10 · 方案 A（parser 中缀合并）
# ============================================================

def test_d10_print_infix_add():
    """`打印 郑数 加 2` → 7（郑数=5）。"""
    assert _run_capture('定义 郑数 = 5。\n打印 郑数 加 2。') == ['7']


def test_d10_print_infix_multiply():
    """`打印 郑数 乘 郑数` → 25（郑数=5）。"""
    assert _run_capture('定义 郑数 = 5。\n打印 郑数 乘 郑数。') == ['25']


def test_d10_list_with_infix_tail():
    """`列 1 2 加 3` → [1, 5]（第 2 元素为 加(2,3)）。"""
    assert _run('列 1 2 加 3。') == [1, 5]


def test_d10_prefix_verb_call_unchanged():
    """边界：首 token 是二元动词 `打印 加 1 2` → 3（走 _parse_verb_call 原路径）。"""
    assert _run_capture('打印 加 1 2。') == ['3']


def test_d10_existing_infix_still_works():
    """常规中缀 `3 加 5` 顶层表达式仍为 8（零回归）。"""
    assert _run('3 加 5。') == 8


# ============================================================
# T-D10 · 方案 B（evaluator 元数守卫）
# ============================================================

def test_d10_arity_guard_too_few():
    """手写 Call('加', [Ident('X')]) 直接 eval → SYNTAX 且中文诊断，不含 lambda。"""
    ev = Evaluator()
    ev.global_env.set('X', 1)
    node = Call(verb='加', args=[Ident(name='X')])
    try:
        ev._eval_node(node, ev.global_env)
        raise AssertionError('应抛出 SYNTAX 诊断')
    except JiKuaiError as e:
        assert e.info is not None
        assert e.info.category == ErrorCategory.SYNTAX, e.info.category
        assert '需要 2 个参数，实际收到 1 个' in e.info.message, e.info.message
        assert 'lambda' not in e.info.message
        assert '_setup_builtins' not in e.info.message
        assert 'positional' not in e.info.message


def test_d10_arity_guard_too_many():
    """固定元数动词收到过多实参也报中文 SYNTAX。"""
    ev = Evaluator()
    ev.global_env.set('X', 1)
    node = Call(verb='负', args=[Ident(name='X'), Ident(name='X')])  # 负 是 1 元
    try:
        ev._eval_node(node, ev.global_env)
        raise AssertionError('应抛出 SYNTAX 诊断')
    except JiKuaiError as e:
        assert e.info is not None and e.info.category == ErrorCategory.SYNTAX
        assert '需要 1 个参数，实际收到 2 个' in e.info.message


def test_d10_variadic_verb_skips_guard():
    """变参动词（打印/拼接/列）不受元数守卫限制。"""
    assert _run('拼接 "a" "b" "c"。') == 'abc'
    assert _run('列 1 2 3 4 5。') == [1, 2, 3, 4, 5]


def test_d10_no_python_leak_in_message():
    """回归 D-10 原始复现：不再泄漏 Python 异常文本。"""
    try:
        # 直接构造 1 实参给二元动词，走 _eval_Call 守卫
        ev = Evaluator()
        ev.global_env.set('郑数', 5)
        ev._eval_node(Call(verb='加', args=[Ident(name='郑数')]), ev.global_env)
        raise AssertionError('应抛出')
    except JiKuaiError as e:
        text = e.info.message if e.info else str(e)
        assert 'missing' not in text
        assert 'argument' not in text


# ============================================================
# T-D11 · python -m jikuai 入口
# ============================================================

def _run_module(args):
    env = dict(os.environ, PYTHONPATH=os.path.join(_ROOT, 'src'),
               PYTHONIOENCODING='utf-8')
    return subprocess.run([sys.executable, '-m', 'jikuai'] + args,
                          capture_output=True, text=True, encoding='utf-8',
                          env=env, cwd=_ROOT)


def _run_module_main(args):
    env = dict(os.environ, PYTHONPATH=os.path.join(_ROOT, 'src'),
               PYTHONIOENCODING='utf-8')
    return subprocess.run([sys.executable, '-m', 'jikuai.main'] + args,
                          capture_output=True, text=True, encoding='utf-8',
                          env=env, cwd=_ROOT)


def test_d11_module_runs_example():
    """`python -m jikuai examples/hello.jk` 退出码 0，且与 jikuai.main 输出一致。"""
    r1 = _run_module(['examples/hello.jk'])
    r2 = _run_module_main(['examples/hello.jk'])
    assert r1.returncode == 0, (r1.returncode, r1.stderr)
    assert r1.stdout and r1.stdout == r2.stdout, (r1.stdout, r2.stdout)


def test_d11_module_version_flag():
    """`python -m jikuai -v` 与 `python -m jikuai.main -v` 输出一致，含 0.4.1。"""
    r1 = _run_module(['-v'])
    r2 = _run_module_main(['-v'])
    assert r1.returncode == 0
    assert r1.stdout == r2.stdout
    assert '0.4.1' in r1.stdout


def test_d11_module_help_flag():
    """`python -m jikuai -h` 与 `python -m jikuai.main -h` 输出一致。"""
    r1 = _run_module(['-h'])
    r2 = _run_module_main(['-h'])
    assert r1.returncode == 0
    assert r1.stdout and r1.stdout == r2.stdout
    assert '0.4.1' in r1.stdout


# ============================================================
# T-D12 · _scan_src 掩码防污染
# ============================================================

def test_d12_string_literal_not_polluting_whitelist():
    """反证 1：多行字符串内含 `类 X：\\n 自身.伪 = 1\\n。` → `伪` 不进白名单。"""
    src = '定义 郑文 = "类 X：\n  自身.伪 = 1\n。"。\n打印 郑文。'
    lx = Lexer(src)
    lx.tokenize()
    assert '伪' not in lx.get_user_defs(), lx.get_user_defs()


def test_d12_comment_class_not_polluting_whitelist():
    """反证 2：`-- 类 X：` 注释后跟 `自身.Y = 1` → `Y` 不进白名单。"""
    src = '-- 类 X：\n-- 自身.Y = 1\n打印 1。'
    lx = Lexer(src)
    lx.tokenize()
    assert 'Y' not in lx.get_user_defs(), lx.get_user_defs()


def test_d12_real_class_field_still_collected():
    """正例：真实类体内 `自身.余额 = 0` 仍进白名单（不误伤）。"""
    src = '类 王账户：\n  构造：\n    自身.余额 = 0。\n  。\n。'
    lx = Lexer(src)
    lx.tokenize()
    assert '余额' in lx.get_user_defs(), lx.get_user_defs()


def test_d12_examples_oop_and_zhang_unaffected():
    """正例：oop.jk / 小张的一天.jk 用 run_source 执行零回归。"""
    from jikuai.main import run_source
    for name in ['oop.jk', '小张的一天.jk']:
        with open(os.path.join(_ROOT, 'examples', name), encoding='utf-8') as f:
            src = f.read()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run_source(src)   # 抛异常即失败
