# -*- coding: utf-8 -*-
"""M9-3 · AOT 子集扩容 — 控制流（如果 / 当 / 重复 / 跳出 / 跳过）。

两层验证：
1. **门禁层**：`subset_gate.check` 必须接受控制流，且 `describe_subset()`
   不再把它们列为不支持。
2. **codegen 层**：生成的 C 必须结构正确。有 C 编译器时进一步
   **编译并运行**，把输出与解释器输出逐字节比对——这是唯一能证明
   「AOT 与解释器语义一致」的办法，比检查 C 文本靠谱得多。

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
    import shutil
    return any(shutil.which(c) for c in ('gcc', 'clang', 'cc', 'cl'))


requires_cc = pytest.mark.skipif(
    not _have_c_compiler(), reason='环境无 C 编译器，跳过编译-运行比对')


# ===========================================================================
# 门禁：控制流现在被接受
# ===========================================================================

class TestGateAcceptsControlFlow:
    def test_if_accepted(self):
        passed, diags = _gate_ok(
            '定义 赵甲 = 5。\n如果 赵甲 大于 1 那么：\n  打印 1。\n。\n')
        assert passed, [d.message for d in diags]

    def test_if_else_accepted(self):
        passed, _ = _gate_ok(
            '定义 赵甲 = 0。\n如果 赵甲 大于 1 那么：\n  打印 "大"。\n'
            '否则：\n  打印 "小"。\n。\n')
        assert passed

    def test_if_elif_else_accepted(self):
        passed, _ = _gate_ok(
            '定义 赵甲 = 2。\n'
            '如果 赵甲 大于 5 那么：\n  打印 "大"。\n'
            '否则如果 赵甲 大于 1 那么：\n  打印 "中"。\n'
            '否则：\n  打印 "小"。\n。\n')
        assert passed

    def test_while_accepted(self):
        passed, _ = _gate_ok(
            '定义 赵甲 = 0。\n当 赵甲 小于 3：\n'
            '  赵甲 = 加 赵甲 1。\n。\n打印 赵甲。\n')
        assert passed

    def test_repeat_accepted(self):
        passed, _ = _gate_ok('重复 3 次：\n  打印 "嗨"。\n。\n')
        assert passed

    def test_break_continue_accepted(self):
        passed, _ = _gate_ok(
            '定义 赵甲 = 0。\n当 真：\n'
            '  赵甲 = 加 赵甲 1。\n'
            '  如果 赵甲 小于 3 那么：\n    跳过。\n  。\n'
            '  跳出。\n。\n打印 赵甲。\n')
        assert passed

    def test_for_still_rejected(self):
        # 遍历需要可迭代对象运行时，仍在子集外
        passed, diags = _gate_ok('遍历 赵i 于 列 1 2：\n  打印 赵i。\n。\n')
        assert not passed
        assert any('范围' in d.message for d in diags)

    def test_describe_subset_no_longer_lists_control_flow(self):
        d = describe_subset()
        unsupported = set(d['unsupported_node_types'])
        for node in ('If', 'While', 'Repeat', 'Break', 'Continue', 'For'):
            assert node not in unsupported, f'{node} 应已移出不支持清单'
        # 仍不支持的（T2a 起 FuncDef 已进子集，闭包 Lambda 顶上来）
        for node in ('Lambda', 'ClassDef', 'ListLit'):
            assert node in unsupported


# ===========================================================================
# codegen：生成的 C 结构正确
# ===========================================================================

class TestCodegenControlFlow:
    def test_if_emits_c_if(self):
        c = _gen('定义 赵甲 = 5。\n如果 赵甲 大于 1 那么：\n  打印 1。\n。\n')
        assert 'if (jk_truthy(' in c

    def test_if_else_emits_else(self):
        c = _gen('定义 赵甲 = 0。\n如果 赵甲 大于 1 那么：\n  打印 1。\n'
                 '否则：\n  打印 2。\n。\n')
        assert '} else {' in c

    def test_elif_emits_else_if(self):
        c = _gen('定义 赵甲 = 2。\n如果 赵甲 大于 5 那么：\n  打印 1。\n'
                 '否则如果 赵甲 大于 1 那么：\n  打印 2。\n。\n')
        assert '} else if (jk_truthy(' in c

    def test_while_emits_c_while(self):
        c = _gen('定义 赵甲 = 0。\n当 赵甲 小于 3：\n  赵甲 = 加 赵甲 1。\n。\n')
        assert 'while (jk_truthy(' in c

    def test_repeat_emits_counted_for(self):
        c = _gen('重复 3 次：\n  打印 1。\n。\n')
        assert 'for (long long jk_i' in c
        assert 'long long jk_cnt' in c

    def test_break_continue_emitted(self):
        c = _gen('当 真：\n  如果 假 那么：\n    跳过。\n  。\n  跳出。\n。\n')
        assert 'break;' in c
        assert 'continue;' in c

    def test_var_assigned_in_loop_gets_slot(self):
        # 只在循环体内赋值的变量也要拿到 main 级槽位
        c = _gen('当 假：\n  定义 赵乙 = 1。\n。\n打印 赵乙。\n')
        assert '/* 赵乙 */' in c

    def test_break_outside_loop_rejected(self):
        # 门禁放过了（Break 已在子集内），codegen 必须自己兜住
        r = compile_source('跳出。\n')
        with pytest.raises(CodegenError) as e:
            generate_c(r.ast)
        assert '跳出' in str(e.value)

    def test_unknown_name_still_rejected(self):
        r = compile_source('打印 赵没定义过。\n')
        with pytest.raises(CodegenError) as e:
            generate_c(r.ast)
        assert '未定义的名称' in str(e.value)


# ===========================================================================
# 端到端：编译成原生二进制并与解释器输出逐字节比对
# ===========================================================================

_E2E_CASES = [
    # (用例名, 源码)
    ('if_then', '定义 赵甲 = 5。\n如果 赵甲 大于 1 那么：\n  打印 "大"。\n。\n'),
    ('if_else', '定义 赵甲 = 0。\n如果 赵甲 大于 1 那么：\n  打印 "大"。\n'
                '否则：\n  打印 "小"。\n。\n'),
    ('if_elif', '定义 赵甲 = 2。\n'
                '如果 赵甲 大于 5 那么：\n  打印 "大"。\n'
                '否则如果 赵甲 大于 1 那么：\n  打印 "中"。\n'
                '否则：\n  打印 "小"。\n。\n'),
    ('while_sum', '定义 赵和 = 0。\n定义 赵i = 1。\n'
                  '当 赵i 小于等于 10：\n'
                  '  赵和 = 加 赵和 赵i。\n'
                  '  赵i = 加 赵i 1。\n。\n打印 赵和。\n'),
    ('repeat', '重复 3 次：\n  打印 "嗨"。\n。\n'),
    ('nested', '定义 赵计 = 0。\n定义 赵i = 0。\n'
               '当 赵i 小于 3：\n'
               '  定义 赵j = 0。\n'
               '  当 赵j 小于 3：\n'
               '    赵计 = 加 赵计 1。\n'
               '    赵j = 加 赵j 1。\n  。\n'
               '  赵i = 加 赵i 1。\n。\n打印 赵计。\n'),
    ('break_continue', '定义 赵和 = 0。\n定义 赵i = 0。\n'
                       '当 真：\n'
                       '  赵i = 加 赵i 1。\n'
                       '  如果 赵i 大于 10 那么：\n    跳出。\n  。\n'
                       '  如果 赵i 取余 2 等于 0 那么：\n    跳过。\n  。\n'
                       '  赵和 = 加 赵和 赵i。\n。\n打印 赵和。\n'),
    ('rmb_in_loop', '定义 赵总 = ￥0.00。\n重复 3 次：\n'
                    '  赵总 = 加 赵总 ￥19.99。\n。\n打印 赵总。\n'),
]


@requires_cc
@pytest.mark.parametrize('name,src', _E2E_CASES, ids=[c[0] for c in _E2E_CASES])
def test_aot_matches_interpreter(name, src, tmp_path):
    """AOT 原生产物的 stdout 必须与解释器逐字节一致。"""
    expected = _interpret(src)

    src_file = tmp_path / f'{name}.jk'
    src_file.write_text(src, encoding='utf-8')
    exe = tmp_path / (f'{name}.exe' if os.name == 'nt' else name)

    result = build(BuildOptions(source_file=str(src_file),
                                output_path=str(exe)))
    assert result.ok, f'AOT 构建失败：{result}'
    assert exe.exists(), '构建声称成功但产物不存在'

    proc = subprocess.run([str(exe)], capture_output=True, text=True,
                          encoding='utf-8')
    assert proc.returncode == 0, f'产物运行失败：{proc.stderr}'
    assert proc.stdout == expected, (
        f'AOT 与解释器输出不一致\n'
        f'AOT      ={proc.stdout!r}\n解释器={expected!r}')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
