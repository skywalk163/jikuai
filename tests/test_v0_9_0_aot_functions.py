# -*- coding: utf-8 -*-
"""T2a · AOT 用户函数 codegen 测试（FuncDef / FuncCall / Return）。

两层验证：
1. **门禁层**：`subset_gate.check` 接受顶层函数定义、函数调用与返回；
   仍拒绝嵌套函数、闭包、间接调用、函数外返回。
2. **codegen 层**：生成的 C 必须结构正确（前向声明 + 函数体 + main 调用）。
   有 C 编译器时进一步编译并运行，把输出与解释器逐字节比对。

没有 C 编译器的环境自动 skip 编译类用例，门禁与 codegen 文本用例照跑。
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools', 'aot'))

import pytest

from jikuai.frontend import compile_source
from jikuai.diagnostics.sink import ListSink
from jikuai.diagnostics.codes import JK_E7001
# 注意：AST 节点类必须在**模块导入期**取到。
# tests/test_v0_7_0_aot.py::TestIsolation 会把 jikuai.* 从 sys.modules 里删掉再
# 重新 import，之后在函数体里 `from jikuai.ast_nodes import X` 拿到的是**新**
# 类对象，与 jikuai_aot 里持有的旧类不是同一个，isinstance 全部落空。
from jikuai.ast_nodes import (
    Call as AstCall,
    Define as AstDefine,
    FuncDef as AstFuncDef,
    Lambda as AstLambda,
    NumberLit as AstNumberLit,
    Program as AstProgram,
)
from jikuai_aot.subset_gate import check, describe_subset, is_supported
from jikuai_aot.codegen import generate_c, CodegenError
from jikuai_aot.driver import BuildOptions, build


def _gate_ok(src):
    """跑门禁，返回 (是否通过, 诊断列表)。"""
    r = compile_source(src)
    sink = ListSink()
    passed = check(r.ast, sink, file='t.jk')
    return passed, sink.drain()


def _gen(src):
    """编译到 C 源码（要求先过门禁）。"""
    r = compile_source(src)
    passed, diags = _gate_ok(src)
    assert passed, '门禁拒绝了本应支持的源码：{}'.format(
        [d.message for d in diags])
    return generate_c(r.ast)


def _interpret(src):
    """用解释器跑一遍，返回 stdout（用于与 AOT 产物比对）。"""
    proc = subprocess.run(
        [sys.executable, '-c',
         'import sys; from jikuai.main import run_source; '
         'run_source(sys.stdin.read())'],
        input=src, capture_output=True, text=True, encoding='utf-8',
        env={**os.environ, 'PYTHONPATH': os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'),
            'JIKUAI_DIAGNOSTICS': 'off',
            'PYTHONUTF8': '1'},
    )
    assert proc.returncode == 0, f'解释器执行失败：{proc.stderr}'
    return proc.stdout


def _have_c_compiler():
    # 直接问驱动，别在测试里另抄候选清单——抄一份就会与 detect_c_compiler 漂移
    # （W104 给它加了 zig / CC 环境变量支持）。
    from jikuai_aot.driver import detect_c_compiler
    return detect_c_compiler() is not None


requires_cc = pytest.mark.skipif(
    not _have_c_compiler(), reason='环境无 C 编译器，跳过编译-运行比对')


# ===========================================================================
# 门禁：顶层函数定义/调用/返回 现在被接受
# ===========================================================================

class TestGateAcceptsFunctions:
    def test_simple_func_accepted(self):
        src = '函数 甲：\n  打印 1。\n。\n甲()。\n'
        passed, diags = _gate_ok(src)
        assert passed, [d.message for d in diags]

    def test_func_with_params_accepted(self):
        src = '函数 甲 接收 赵x：\n  打印 赵x。\n。\n甲(42)。\n'
        passed, diags = _gate_ok(src)
        assert passed, [d.message for d in diags]

    def test_return_in_func_accepted(self):
        src = '函数 甲 接收 赵x：\n  返回 赵x 加 1。\n。\n打印 甲(5)。\n'
        passed, diags = _gate_ok(src)
        assert passed, [d.message for d in diags]

    def test_recursion_accepted(self):
        src = ('函数 阶乘 接收 赵n：\n'
               '  如果 赵n 小于等于 1 那么：\n    返回 1。\n  。\n'
               '  返回 赵n 乘 阶乘(赵n 减 1)。\n。\n打印 阶乘(5)。\n')
        passed, diags = _gate_ok(src)
        assert passed, [d.message for d in diags]

    def test_mutual_call_accepted(self):
        src = ('函数 甲 接收 赵x：\n  返回 赵x 加 1。\n。\n'
               '函数 乙 接收 赵x：\n  返回 甲(赵x) 乘 2。\n。\n'
               '打印 乙(3)。\n')
        passed, diags = _gate_ok(src)
        assert passed, [d.message for d in diags]


# ===========================================================================
# 门禁：仍应拒绝的上下文相关特性
# ===========================================================================

class TestGateRejectsContextual:
    def _check_rejected(self, src, feature_keyword):
        r = compile_source(src)
        sink = ListSink()
        passed = check(r.ast, sink, file='t.jk')
        diags = sink.drain()
        assert not passed, '预期拒绝但门禁通过'
        assert len(diags) >= 1
        d = diags[0]
        assert d.code == JK_E7001
        assert feature_keyword in d.message, \
            '诊断消息不含 {!r}：{!r}'.format(feature_keyword, d.message)

    def test_nested_funcdef_rejected(self):
        src = ('函数 外 接收 赵x：\n'
               '  函数 内 接收 赵y：\n    返回 赵y。\n  。\n'
               '  返回 内(赵x)。\n。\n')
        self._check_rejected(src, '嵌套函数定义')

    def test_return_outside_func_rejected(self):
        self._check_rejected('返回 1。\n', '函数外')

    def test_lambda_still_rejected(self):
        # Lambda 仍然在 UNSUPPORTED_NODE_TYPES 里。当前 parser 无面向用户的
        # lambda 语法，因此直接构造 AST 验证门禁反应。AST 类用模块顶部的别名
        # （见文件头注释：避免 TestIsolation 重载后拿到不同的类对象）。
        prog = AstProgram(body=[
            AstDefine(name='赵f', value=AstLambda(
                params=['赵x'],
                body=[AstCall(verb='打印', args=[AstNumberLit(value=1)])],
            )),
        ])
        sink = ListSink()
        passed = check(prog, sink, file='t.jk')
        diags = sink.drain()
        assert not passed
        assert any('匿名函数' in d.message for d in diags)

    def test_describe_subset_no_longer_lists_func(self):
        d = describe_subset()
        unsupported = set(d['unsupported_node_types'])
        for node in ('FuncDef', 'FuncCall', 'Return'):
            assert node not in unsupported, f'{node} 应已移出不支持清单'
        # 仍不支持的
        for node in ('Lambda', 'ClassDef', 'MemberAccess'):
            assert node in unsupported
        # 上下文相关特性存在
        ctx = d.get('unsupported_contextual_features', {})
        assert '嵌套函数定义' in ctx


# ===========================================================================
# codegen：生成的 C 结构正确
# ===========================================================================

class TestCodegenFunctions:
    def test_forward_declaration(self):
        src = '函数 甲：\n  打印 1。\n。\n甲()。\n'
        c = _gen(src)
        # 应有前向声明
        assert 'static JKValue jk_fn1(void)' in c
        # 应有函数体
        assert 'jk_fn1(void)' in c
        # main 里应有调用
        assert 'jk_fn1()' in c

    def test_params_in_signature(self):
        src = '函数 甲 接收 赵x 赵y：\n  返回 赵x 加 赵y。\n。\n打印 甲(1, 2)。\n'
        c = _gen(src)
        # 两参函数签名
        assert 'JKValue jk_pa' in c
        # 调用时传两个参数
        assert 'jk_fn1(' in c

    def test_return_emits_return(self):
        src = '函数 甲 接收 赵x：\n  返回 赵x。\n。\n打印 甲(42)。\n'
        c = _gen(src)
        assert 'return ' in c

    def test_implicit_nil_return(self):
        src = '函数 甲：\n  打印 1。\n。\n甲()。\n'
        c = _gen(src)
        # 函数体末尾应有隐式 return jk_nil()
        assert 'return jk_nil()' in c

    def test_recursion_compiles(self):
        src = ('函数 阶乘 接收 赵n：\n'
               '  如果 赵n 小于等于 1 那么：\n    返回 1。\n  。\n'
               '  返回 赵n 乘 阶乘(赵n 减 1)。\n。\n打印 阶乘(5)。\n')
        c = _gen(src)
        # 递归：函数体内调用自己
        assert c.count('jk_fn1(') >= 2  # 声明 + 至少一处递归调用

    def test_globals_visible_in_func(self):
        src = ('定义 赵全局 = 100。\n'
               '函数 读全局：\n  返回 赵全局。\n。\n打印 读全局()。\n')
        c = _gen(src)
        assert 'static JKValue jk_var1' in c  # 文件作用域全局

    def test_func_modifies_global(self):
        src = ('定义 赵计 = 0。\n'
               '函数 加一：\n  赵计 = 加 赵计 1。\n。\n'
               '加一()。\n加一()。\n打印 赵计。\n')
        c = _gen(src)
        assert 'jk_var1' in c

    def test_unknown_func_rejected(self):
        src = '打印 不存在()。\n'
        r = compile_source(src)
        with pytest.raises(CodegenError) as e:
            generate_c(r.ast)
        assert '未定义的函数' in str(e.value)

    def test_nested_funcdef_in_codegen_rejected(self):
        """如果 subset_gate 没拦住的嵌套 FuncDef，codegen 也会兜底报错。"""
        inner_func = AstFuncDef(name='内', params=[],
                                body=[AstCall(verb='打印', args=[])])
        outer_func = AstFuncDef(name='外', params=[], body=[inner_func])
        prog = AstProgram(body=[outer_func])
        with pytest.raises(CodegenError) as e:
            generate_c(prog)
        assert '嵌套' in str(e.value)


# ===========================================================================
# 端到端：编译成原生二进制并与解释器输出逐字节比对
# ===========================================================================

_E2E_CASES = [
    # (用例名, 源码)
    ('simple_func',
     '函数 打招呼：\n  打印 "你好"。\n。\n打招呼()。\n'),
    ('func_with_param',
     '函数 翻倍 接收 赵x：\n  返回 赵x 乘 2。\n。\n打印 翻倍(21)。\n'),
    ('factorial',
     '函数 阶乘 接收 赵n：\n'
     '  如果 赵n 小于等于 1 那么：\n    返回 1。\n  。\n'
     '  返回 赵n 乘 阶乘(赵n 减 1)。\n。\n打印 阶乘(5)。\n'),
    ('fibonacci',
     '函数 斐波那契 接收 赵n：\n'
     '  如果 赵n 小于等于 0 那么：\n    返回 0。\n  。\n'
     '  如果 赵n 等于 1 那么：\n    返回 1。\n  。\n'
     '  返回 斐波那契(赵n 减 1) 加 斐波那契(赵n 减 2)。\n。\n'
     '打印 斐波那契(10)。\n'),
    ('func_calls_func',
     '函数 甲 接收 赵x：\n  返回 赵x 加 1。\n。\n'
     '函数 乙 接收 赵x：\n  返回 甲(赵x) 乘 2。\n。\n'
     '打印 乙(3)。\n'),
    ('implicit_nil',
     '函数 无返回 接收 赵x：\n  打印 赵x。\n。\n'
     '定义 赵结果 = 无返回(42)。\n打印 赵结果。\n'),
    ('func_global_modify',
     '定义 赵计 = 0。\n'
     '函数 加一：\n  赵计 = 加 赵计 1。\n。\n'
     '加一()。\n加一()。\n加一()。\n打印 赵计。\n'),
    ('multi_params',
     '函数 三数之和 接收 赵a 赵b 赵c：\n'
     '  返回 赵a 加 赵b 加 赵c。\n。\n'
     '打印 三数之和(10, 20, 30)。\n'),
]


@requires_cc
@pytest.mark.parametrize('name,src', _E2E_CASES, ids=[c[0] for c in _E2E_CASES])
def test_aot_functions_match_interpreter(name, src, tmp_path):
    """AOT 原生产物的 stdout 必须与解释器逐字节一致。"""
    expected = _interpret(src)

    src_file = tmp_path / f'{name}.jk'
    src_file.write_text(src, encoding='utf-8')
    exe = tmp_path / (f'{name}.exe' if os.name == 'nt' else name)

    result = build(BuildOptions(source_file=str(src_file),
                                output_path=str(exe)))
    assert result.ok, f'AOT 构建失败：{result.message}'
    assert exe.exists(), '构建声称成功但产物不存在'

    proc = subprocess.run([str(exe)], capture_output=True, text=True,
                          encoding='utf-8')
    assert proc.returncode == 0, f'产物运行失败：{proc.stderr}'
    assert proc.stdout == expected, (
        f'AOT 与解释器输出不一致\n'
        f'AOT      ={proc.stdout!r}\n解释器={expected!r}')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
