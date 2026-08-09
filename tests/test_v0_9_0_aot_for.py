# -*- coding: utf-8 -*-
"""T2b · AOT `遍历`（For）codegen 测试。

覆盖两条链路：

1. **门禁层**：`subset_gate.check` 对 `遍历 变量 于 范围 ...` 与
   `遍历 变量 于 【字面量列表】` 放行；对遍历任意可迭代对象（变量、
   动词返回值、管道…）继续拒绝，并给出上下文相关的 JK-E7001。
2. **codegen 层**：生成的 C 中间码含 for(...) 结构、步长为 0 的
   jk_fatal 兜底、循环变量声明与作用域块。

有 C 编译器时把 AOT 产物跑起来，与解释器输出逐字节比对；没有编译器
自动跳过端到端用例，只跑门禁与 codegen 文本用例。

语法约定（关键，避免踩坑）
------------------------
JiKuai 里 `范围(1, 5)` 会被解析成 `Call(verb='范围',
args=[Pipeline([1, 5])])` —— 括号-逗号是**管道**语法，会把两个值折叠成
一个（`范围(1,5)` 与 `范围(5)` 等价，解释器实测输出 0..4）。因此**多参数
范围的规范写法是空格分隔**：`范围 起 止`、`范围 起 止 步`。门禁对管道形态
直接判「超出 AOT 受支持子集：管道」（Pipeline 本就不在子集内），这与解释器
语义一致，故意**不**做「解包成多参数」的特殊处理，避免语义漂移。
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
# AST 节点类必须在**模块导入期**取到（沿用 v0_7 / v0_9 测试的做法：
# TestIsolation 会重载 jikuai.*，函数体里再 import 会拿到不同的类对象）。
from jikuai.ast_nodes import (
    Call as AstCall,
    For as AstFor,
    Ident as AstIdent,
    ListLit as AstListLit,
    NumberLit as AstNumberLit,
    Program as AstProgram,
)
from jikuai_aot.subset_gate import (
    FOR_ITERABLE_FEATURE,
    FOR_RANGE_ARITY_FEATURE,
    RANGE_ARITY,
    RANGE_VERB,
    check,
    describe_subset,
)
from jikuai_aot.codegen import CodegenError, generate_c
from jikuai_aot.driver import BuildOptions, build


# ===========================================================================
# 工具
# ===========================================================================

def _gate(src):
    """跑门禁，返回 (是否通过, 诊断列表)。"""
    r = compile_source(src)
    sink = ListSink()
    ok = check(r.ast, sink, file='t.jk')
    return ok, sink.drain()


def _gen(src):
    """编译到 C 源码（要求先过门禁）。"""
    r = compile_source(src)
    ok, diags = _gate(src)
    assert ok, '门禁拒绝了本应支持的源码：{}'.format([d.message for d in diags])
    return generate_c(r.ast)


def _interpret(src):
    """用解释器跑一遍，返回 stdout（用于与 AOT 产物比对）。"""
    proc = subprocess.run(
        [sys.executable, '-c',
         'import sys; from jikuai.main import run_source; '
         'run_source(sys.stdin.read())'],
        input=src, capture_output=True, text=True,
        env={**os.environ,
             'PYTHONPATH': os.path.join(
                 os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'src'),
             'JIKUAI_DIAGNOSTICS': 'off'},
    )
    assert proc.returncode == 0, f'解释器执行失败：{proc.stderr}'
    return proc.stdout


def _have_c_compiler():
    import shutil
    return any(shutil.which(c) for c in ('gcc', 'clang', 'cc', 'cl'))


requires_cc = pytest.mark.skipif(
    not _have_c_compiler(), reason='环境无 C 编译器，跳过编译-运行比对')


# ===========================================================================
# 门禁：范围遍历与字面量列表遍历现在被接受
# ===========================================================================

class TestGateAcceptsFor:
    def test_range_one_arg(self):
        ok, diags = _gate('遍历 赵i 于 范围 3：\n  打印 赵i。\n。\n')
        assert ok, [d.message for d in diags]

    def test_range_two_args(self):
        ok, diags = _gate('遍历 赵i 于 范围 1 10：\n  打印 赵i。\n。\n')
        assert ok, [d.message for d in diags]

    def test_range_three_args(self):
        ok, diags = _gate('遍历 赵i 于 范围 0 10 2：\n  打印 赵i。\n。\n')
        assert ok, [d.message for d in diags]

    def test_range_negative_step(self):
        ok, diags = _gate('遍历 赵i 于 范围 5 0 负 1：\n  打印 赵i。\n。\n')
        assert ok, [d.message for d in diags]

    def test_listlit_iteration(self):
        ok, diags = _gate('遍历 赵项 于 【10，20，30】：\n  打印 赵项。\n。\n')
        assert ok, [d.message for d in diags]

    def test_range_variable_bounds(self):
        # 起/止本身可以是变量或表达式，只要它们各自在子集内
        src = ('定义 赵n = 5。\n'
               '遍历 赵i 于 范围 1 赵n：\n  打印 赵i。\n。\n')
        ok, diags = _gate(src)
        assert ok, [d.message for d in diags]

    def test_nested_for(self):
        src = ('遍历 赵i 于 范围 1 3：\n'
               '  遍历 赵j 于 范围 1 3：\n'
               '    打印 赵i 赵j。\n  。\n。\n')
        ok, diags = _gate(src)
        assert ok, [d.message for d in diags]

    def test_break_continue_inside_for(self):
        src = ('遍历 赵i 于 范围 1 10：\n'
               '  如果 赵i 等于 3 那么：\n    跳过。\n  。\n'
               '  如果 赵i 大于 5 那么：\n    跳出。\n  。\n'
               '  打印 赵i。\n。\n')
        ok, diags = _gate(src)
        assert ok, [d.message for d in diags]

    def test_for_inside_function(self):
        src = ('函数 求和 接收 赵n：\n'
               '  定义 赵s = 0。\n'
               '  遍历 赵i 于 范围 1 赵n：\n'
               '    赵s = 加 赵s 赵i。\n  。\n'
               '  返回 赵s。\n。\n打印 求和(5)。\n')
        ok, diags = _gate(src)
        assert ok, [d.message for d in diags]


# ===========================================================================
# 门禁：仍应拒绝的遍历源
# ===========================================================================

class TestGateRejectsIterables:
    def _check_rejected(self, src, keyword):
        r = compile_source(src)
        sink = ListSink()
        ok = check(r.ast, sink, file='t.jk')
        diags = sink.drain()
        assert not ok, '预期拒绝但门禁通过'
        assert diags, '预期至少一条 JK-E7001'
        assert diags[0].code == JK_E7001
        assert any(keyword in d.message for d in diags), \
            '诊断消息未含 {!r}：{!r}'.format(
                keyword, [d.message for d in diags])

    def test_variable_iterable_rejected(self):
        # 遍历变量：静态期不知道它是不是可迭代、有多长，需要运行时
        self._check_rejected(
            '定义 赵表 = 列 1 2。\n遍历 赵i 于 赵表：\n  打印 赵i。\n。\n',
            '范围')

    def test_verb_call_iterable_rejected(self):
        # 动词返回值（`列 1 2`）是运行时容器，仍不支持
        self._check_rejected(
            '遍历 赵i 于 列 1 2：\n  打印 赵i。\n。\n', '范围')

    def test_pipeline_iterable_rejected(self):
        # `范围(1, 5)` 的 `(1, 5)` 是管道；Pipeline 本身在子集外
        self._check_rejected(
            '遍历 赵i 于 范围(1, 5)：\n  打印 赵i。\n。\n', '管道')

    def test_rejected_message_mentions_supported_forms(self):
        # 消息必须把可用写法写全，用户不必翻文档
        ok, diags = _gate('遍历 赵i 于 列 1 2：\n  打印 赵i。\n。\n')
        assert not ok
        msg = diags[0].message
        assert '范围' in msg and '字面量列表' in msg, msg
        # notes 里给出为什么不支持
        assert any('迭代器' in n or '堆容器' in n for n in diags[0].notes), \
            diags[0].notes

    def test_range_zero_args_rejected(self):
        # 0 参数的 `范围` 语法上写不出来，但要防止直接构造 AST 绕过
        prog = AstProgram(body=[
            AstFor(var='赵i',
                   iterable=AstCall(verb=RANGE_VERB, args=[]),
                   body=[AstCall(verb='打印', args=[AstIdent(name='赵i')])]),
        ])
        sink = ListSink()
        ok = check(prog, sink, file='t.jk')
        assert not ok
        diags = sink.drain()
        assert any('参数个数' in d.message for d in diags), \
            [d.message for d in diags]

    def test_range_four_args_rejected(self):
        prog = AstProgram(body=[
            AstFor(var='赵i',
                   iterable=AstCall(verb=RANGE_VERB, args=[
                       AstNumberLit(value=1), AstNumberLit(value=2),
                       AstNumberLit(value=3), AstNumberLit(value=4),
                   ]),
                   body=[AstCall(verb='打印', args=[AstIdent(name='赵i')])]),
        ])
        sink = ListSink()
        ok = check(prog, sink, file='t.jk')
        assert not ok
        assert any('参数个数' in d.message for d in sink.drain())

    def test_body_still_subset_checked(self):
        # For 被接受不等于整棵子树放行：循环体里的子集外特性照样要报
        src = '遍历 赵i 于 范围 1 3：\n  打印 归 加 列 1 2。\n。\n'
        r = compile_source(src)
        sink = ListSink()
        ok = check(r.ast, sink, file='t.jk')
        assert not ok
        assert any('副词' in d.message or '列表' in d.message
                   for d in sink.drain())

    def test_range_bounds_still_subset_checked(self):
        # 起/止也要过门禁：这里用不在白名单里的动词 求和
        src = '遍历 赵i 于 范围 1 求和 列 1 2：\n  打印 赵i。\n。\n'
        r = compile_source(src)
        sink = ListSink()
        ok = check(r.ast, sink, file='t.jk')
        assert not ok


class TestDescribeSubset:
    def test_for_not_in_unsupported_node_types(self):
        d = describe_subset()
        assert 'For' not in set(d['unsupported_node_types']), \
            '`For` 已改为上下文相关判定，不该再挂在节点类型清单上'
        # 仍不支持的
        for node in ('Lambda', 'ClassDef', 'ListLit', 'Pipeline'):
            assert node in set(d['unsupported_node_types'])

    def test_context_features_include_for(self):
        ctx = describe_subset().get('unsupported_contextual_features', {})
        assert FOR_ITERABLE_FEATURE in ctx
        assert FOR_RANGE_ARITY_FEATURE in ctx

    def test_range_constants_exported(self):
        assert RANGE_VERB == '范围'
        assert RANGE_ARITY == frozenset({1, 2, 3})


# ===========================================================================
# codegen：生成的 C 结构正确
# ===========================================================================

class TestCodegenFor:
    def test_range_emits_counted_for(self):
        c = _gen('遍历 赵i 于 范围 1 10：\n  打印 赵i。\n。\n')
        # 起/止/步各存一个 long long 临时（只求值一次）
        assert 'long long jk_rs' in c
        assert 'long long jk_re' in c
        assert 'long long jk_rp' in c
        assert 'for (long long jk_ri' in c
        # 循环变量带原名注释，便于读生成的 C
        assert '/* 赵i */' in c

    def test_range_direction_is_step_sign_aware(self):
        """步长符号自适应：正步用 ix < end，负步用 ix > end。"""
        c = _gen('遍历 赵i 于 范围 5 0 负 1：\n  打印 赵i。\n。\n')
        assert '> 0) ?' in c, '缺少按步长符号选择比较方向的三元表达式'
        assert '<' in c and '>' in c

    def test_range_emits_zero_step_fatal(self):
        c = _gen('遍历 赵i 于 范围 0 10 2：\n  打印 赵i。\n。\n')
        # 步长 0 在解释器里是 ValueError，这里编译成运行期停机
        assert 'jk_fatal(' in c
        assert '== 0)' in c

    def test_range_one_arg_starts_at_zero_step_one(self):
        c = _gen('遍历 赵i 于 范围 3：\n  打印 赵i。\n。\n')
        assert 'jk_as_int(jk_int(0LL))' in c   # start = 0
        assert 'jk_as_int(jk_int(1LL))' in c   # step  = 1

    def test_bounds_evaluated_once_before_loop(self):
        """止值只求值一次：循环体改动 赵n 不该影响剩余轮数。"""
        src = ('定义 赵n = 3。\n'
               '遍历 赵i 于 范围 1 赵n：\n'
               '  赵n = 加 赵n 1。\n。\n')
        c = _gen(src)
        loop_line = [ln for ln in c.splitlines()
                     if 'for (long long jk_ri' in ln][0]
        # 循环条件里只有 long long 临时，不含全局槽位
        assert 'jk_var' not in loop_line, loop_line

    def test_listlit_emits_stack_array(self):
        c = _gen('遍历 赵项 于 【10，20，30】：\n  打印 赵项。\n。\n')
        # 栈上数组 + 下标遍历，不落堆
        assert 'JKValue jk_farr' in c
        for lit in ('jk_int(10LL)', 'jk_int(20LL)', 'jk_int(30LL)'):
            assert lit in c
        assert 'for (long long jk_fi' in c
        assert '/* 赵项 */' in c

    def test_empty_listlit_short_circuits(self):
        """空列表零轮：C 不允许零长度数组初始化，必须短路掉整个 for。"""
        prog = AstProgram(body=[
            AstFor(var='赵x', iterable=AstListLit(items=[]),
                   body=[AstCall(verb='打印', args=[AstIdent(name='赵x')])]),
        ])
        c = generate_c(prog)
        assert '空列表遍历' in c
        assert 'jk_farr' not in c

    def test_break_and_continue_pass_through(self):
        c = _gen('遍历 赵i 于 范围 1 10：\n'
                 '  如果 赵i 等于 3 那么：\n    跳过。\n  。\n'
                 '  如果 赵i 大于 5 那么：\n    跳出。\n  。\n'
                 '  打印 赵i。\n。\n')
        assert 'break;' in c
        assert 'continue;' in c

    def test_nested_loops_use_distinct_c_names(self):
        c = _gen('遍历 赵i 于 范围 1 3：\n'
                 '  遍历 赵j 于 范围 1 3：\n'
                 '    打印 赵i 赵j。\n  。\n。\n')
        assert '/* 赵i */' in c and '/* 赵j */' in c
        # 两层循环各有自己的计数器
        assert c.count('for (long long jk_ri') == 2

    def test_loop_var_is_block_scoped(self):
        """循环变量声明在 for 块内，离开循环即失效（对齐 loop_env 语义）。"""
        c = _gen('遍历 赵i 于 范围 1 3：\n  打印 赵i。\n。\n')
        assert 'JKValue jk_lv' in c
        # 没有任何 Define/Assign 赵i，所以不应给它分配顶层槽位
        assert '/* 赵i */\nstatic JKValue' not in c

    def test_loop_var_shadows_global_of_same_name(self):
        src = ('定义 赵i = 99。\n'
               '遍历 赵i 于 范围 1 3：\n  打印 赵i。\n。\n'
               '打印 赵i。\n')
        c = _gen(src)
        assert 'static JKValue jk_var1' in c    # 全局 赵i
        assert 'jk_int(99LL)' in c
        assert 'JKValue jk_lv' in c             # 循环局部 赵i

    def test_accumulator_writes_global(self):
        """循环体给外层变量赋值走全局槽位（解释器 update 沿链命中外层）。"""
        src = ('定义 赵和 = 0。\n'
               '遍历 赵i 于 范围 1 5：\n'
               '  赵和 = 加 赵和 赵i。\n。\n打印 赵和。\n')
        c = _gen(src)
        assert 'static JKValue jk_var1' in c
        assert 'jk_var1 = jk_add(' in c

    def test_for_inside_function_compiles(self):
        src = ('函数 求和 接收 赵n：\n'
               '  定义 赵s = 0。\n'
               '  遍历 赵i 于 范围 1 赵n：\n'
               '    赵s = 加 赵s 赵i。\n  。\n'
               '  返回 赵s。\n。\n打印 求和(5)。\n')
        c = _gen(src)
        assert 'static JKValue jk_fn1(' in c
        assert 'for (long long jk_ri' in c

    def test_codegen_rejects_unknown_iterable(self):
        """绕过门禁直接调 codegen：兜底 CodegenError，不静默产出错代码。"""
        prog = AstProgram(body=[
            AstFor(var='赵i', iterable=AstIdent(name='赵表'),
                   body=[AstCall(verb='打印', args=[AstIdent(name='赵i')])]),
        ])
        with pytest.raises(CodegenError) as e:
            generate_c(prog)
        assert '范围' in str(e.value)

    def test_codegen_rejects_bad_range_arity(self):
        prog = AstProgram(body=[
            AstFor(var='赵i',
                   iterable=AstCall(verb=RANGE_VERB, args=[]),
                   body=[AstCall(verb='打印', args=[AstIdent(name='赵i')])]),
        ])
        with pytest.raises(CodegenError) as e:
            generate_c(prog)
        assert '参数个数' in str(e.value)


# ===========================================================================
# 端到端：编译成原生二进制并与解释器输出逐字节比对
# ===========================================================================

_E2E_CASES = [
    ('range_1_arg',
     '遍历 赵i 于 范围 3：\n  打印 赵i。\n。\n'),
    ('range_2_args',
     '遍历 赵i 于 范围 1 5：\n  打印 赵i。\n。\n'),
    ('range_3_args_step2',
     '遍历 赵i 于 范围 0 10 2：\n  打印 赵i。\n。\n'),
    ('range_countdown',
     '遍历 赵i 于 范围 5 0 负 1：\n  打印 赵i。\n。\n'),
    ('range_empty',
     '打印 "前"。\n遍历 赵i 于 范围 5 5：\n  打印 赵i。\n。\n打印 "后"。\n'),
    ('listlit_ints',
     '遍历 赵项 于 【10，20，30】：\n  打印 赵项。\n。\n'),
    ('listlit_strings',
     '遍历 赵项 于 【"甲"，"乙"，"丙"】：\n  打印 赵项。\n。\n'),
    ('nested_for',
     '遍历 赵i 于 范围 1 3：\n'
     '  遍历 赵j 于 范围 1 3：\n'
     '    打印 赵i 赵j。\n  。\n。\n'),
    ('accum_sum',
     '定义 赵和 = 0。\n'
     '遍历 赵i 于 范围 1 11：\n'
     '  赵和 = 加 赵和 赵i。\n。\n打印 赵和。\n'),
    ('break_in_for',
     '遍历 赵i 于 范围 1 10：\n'
     '  如果 赵i 大于 3 那么：\n    跳出。\n  。\n'
     '  打印 赵i。\n。\n'),
    ('continue_in_for',
     '遍历 赵i 于 范围 1 6：\n'
     '  如果 赵i 等于 3 那么：\n    跳过。\n  。\n'
     '  打印 赵i。\n。\n'),
    ('for_in_func',
     '函数 求和 接收 赵n：\n'
     '  定义 赵s = 0。\n'
     '  遍历 赵i 于 范围 1 赵n：\n'
     '    赵s = 加 赵s 赵i。\n  。\n'
     '  返回 赵s。\n。\n打印 求和(11)。\n'),
    ('loopvar_shadows_global',
     '定义 赵i = 99。\n'
     '遍历 赵i 于 范围 1 3：\n  打印 赵i。\n。\n'
     '打印 赵i。\n'),
    ('bounds_evaluated_once',
     '定义 赵n = 3。\n'
     '遍历 赵i 于 范围 1 赵n：\n'
     '  赵n = 加 赵n 1。\n  打印 赵i。\n。\n打印 赵n。\n'),
]


@requires_cc
@pytest.mark.parametrize('name,src', _E2E_CASES, ids=[c[0] for c in _E2E_CASES])
def test_aot_for_matches_interpreter(name, src, tmp_path):
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