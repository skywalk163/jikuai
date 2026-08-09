# -*- coding: utf-8 -*-
"""性能基准的**正确性**守护测试（T8）。

不测速度——速度依赖硬件、不能进 CI 断言。只测两件事：

1. 每个基准的源文件存在、能解析、能跑通、输出等于声明的 `expected`。
   这是"基准数字有没有意义"的前置条件：如果两条链算的根本不是同一件事，
   加速比就是垃圾数字。
2. 基准表结构本身健康（有名字、有唯一源文件、`expected` 是非空字符串）。

规模已经调小到单条 ≤ 3 秒 / 全部约 8 秒，这个开销花在守护 T8 的语义正确性
上是划算的。任何 pytest -q 都会带上这层校验，防止后续改动悄悄改变了基准的
输出后加速比才被人肉发现。
"""

import io
import contextlib
import importlib.util
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'src'))


def _load_bench_module():
    """按路径加载 benches/run_bench.py，因为它不在包里。"""
    path = os.path.join(REPO_ROOT, 'benches', 'run_bench.py')
    spec = importlib.util.spec_from_file_location('_jikuai_bench_runner', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BENCH_MOD = _load_bench_module()


class TestBenchmarkTableIntegrity:
    def test_benchmarks_are_registered(self):
        assert len(BENCH_MOD.BENCHMARKS) >= 4

    def test_names_are_unique(self):
        names = [b.name for b in BENCH_MOD.BENCHMARKS]
        assert len(set(names)) == len(names)

    def test_source_files_are_unique(self):
        paths = [b.source_path for b in BENCH_MOD.BENCHMARKS]
        assert len(set(paths)) == len(paths)

    @pytest.mark.parametrize('bench',
                             BENCH_MOD.BENCHMARKS,
                             ids=[b.name for b in BENCH_MOD.BENCHMARKS])
    def test_source_exists(self, bench):
        assert os.path.isfile(bench.source_path), bench.source_path

    @pytest.mark.parametrize('bench',
                             BENCH_MOD.BENCHMARKS,
                             ids=[b.name for b in BENCH_MOD.BENCHMARKS])
    def test_expected_is_non_empty_string(self, bench):
        assert isinstance(bench.expected, str)
        assert bench.expected.strip()


class TestInterpreterOutputMatchesExpected:
    """真跑一遍：解释器输出必须精确等于声明的 expected。"""

    @pytest.mark.parametrize('bench',
                             BENCH_MOD.BENCHMARKS,
                             ids=[b.name for b in BENCH_MOD.BENCHMARKS])
    def test_interpreter_output(self, bench):
        from jikuai.main import run_source
        with open(bench.source_path, encoding='utf-8') as f:
            src = f.read()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_source(src)
        actual = buf.getvalue().strip()
        assert actual == bench.expected, (
            f'基准 {bench.name!r} 期望 {bench.expected!r}，实得 {actual!r}')


class TestNoAotSkipCleanly:
    """无 C 编译器时，`--no-aot` 应能正常完成，退出码 0。"""

    def test_dispatch_via_main(self, tmp_path, capsys):
        json_path = tmp_path / 'r.json'
        rc = BENCH_MOD.main(
            ['斐波那契', '--轮数', '1', '--no-aot', '--json', str(json_path)])
        assert rc == 0
        # JSON 有落盘且形状对
        import json
        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)
        assert data['compiler'] is None
        assert len(data['benchmarks']) == 1
        row = data['benchmarks'][0]
        assert row['name'] == '斐波那契'
        assert row['interp_samples']            # 至少 1 条采样
        assert row['aot_samples'] == []         # AOT 被跳过
