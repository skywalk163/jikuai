# -*- coding: utf-8 -*-
"""极快 · 性能基准（T8）。

设计取舍
--------
1. **只测计算密集**。字符串/列表运算目前不在 AOT 子集里，测了也没得比。
   四个基准都刻意选纯整数 + 控制流，全部落在当前 AOT 子集内，才能对齐比对。

2. **两条独立执行链**：
   - **解释器**：`subprocess.run` 起独立 Python 进程，让 GC / 编译缓存 / 全局
     状态清零，避免"上一个基准的 pyc 缓存加速下一个"这种伪造收益。
   - **AOT**：`build()` 产出原生二进制 → `subprocess.run` 跑二进制。

3. **测量**：warmup 1 次 → 采样 5 次 → 取中位数，与 `scripts/bench_compile.py`
   同步。用中位数而不是均值：GC 抖动是长尾分布，均值会被拉偏。

4. **正确性守护**：每个基准声明 `预期输出`，两条链的 stdout 都必须与它精确
   匹配。任何一条对不上就把整份基准报告标为"结果不可信"，宁可什么都不报，
   也不报错误的加速比——性能基准最大的坑是"你测的根本不是同一件事"。

5. **无 C 编译器降级**：只跑解释器一列，AOT 一栏空着 + 明确原因。不假装
   "AOT 速度 = 解释器速度"，那种数字上线后会误导后续决策。

用法
----
    python benches/run_bench.py              # 运行全部
    python benches/run_bench.py 斐波那契      # 只跑名字匹配的
    python benches/run_bench.py --轮数 10    # 覆盖默认 5 轮采样
    python benches/run_bench.py --json out.json
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional


HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
PROGRAMS_DIR = os.path.join(HERE, 'programs')

# 让子进程和本进程都能找到 src/ 与 tools/aot/
sys.path.insert(0, os.path.join(REPO_ROOT, 'src'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools', 'aot'))


# ---------------------------------------------------------------------------
# 基准定义
# ---------------------------------------------------------------------------

@dataclass
class Benchmark:
    """一个基准：一段极快源码 + 期望输出。"""
    name: str
    source_path: str
    expected: str
    描述: str


BENCHMARKS: List[Benchmark] = [
    Benchmark(
        name='斐波那契',
        source_path=os.path.join(PROGRAMS_DIR, '斐波那契.jk'),
        expected='17711',
        描述='递归压力：fib(22)，考察函数调用与栈行为',
    ),
    Benchmark(
        name='求和_十万',
        source_path=os.path.join(PROGRAMS_DIR, '求和_十万.jk'),
        expected='5000050000',
        描述='范围 for 循环 + 全局累加器：Σ 1..10^5',
    ),
    Benchmark(
        name='Collatz',
        source_path=os.path.join(PROGRAMS_DIR, 'Collatz.jk'),
        expected='59542',
        描述='while 循环 + 整数分支：对 1..10^3 累加 Collatz 步数',
    ),
    Benchmark(
        name='嵌套循环',
        source_path=os.path.join(PROGRAMS_DIR, '嵌套循环.jk'),
        expected='30000',
        描述='嵌套 for + 取模判定：300×300 = 9 万次迭代',
    ),
]


# ---------------------------------------------------------------------------
# 一次测量
# ---------------------------------------------------------------------------

@dataclass
class MeasureResult:
    """单条测量结果。所有时间单位：秒。"""
    samples: List[float] = field(default_factory=list)
    stdout: str = ''
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.samples)

    @property
    def median(self) -> float:
        return statistics.median(self.samples) if self.samples else float('nan')

    @property
    def best(self) -> float:
        return min(self.samples) if self.samples else float('nan')


def _time_subprocess(argv: List[str], env=None, cwd=None) -> tuple:
    """跑一次子进程，返回 (elapsed_seconds, stdout, returncode)。"""
    t0 = time.perf_counter()
    proc = subprocess.run(
        argv, capture_output=True, text=True, encoding='utf-8',
        env=env, cwd=cwd,
    )
    dt = time.perf_counter() - t0
    return dt, proc.stdout, proc.returncode


def _repeat(fn, rounds: int) -> MeasureResult:
    """warmup 1 次 + 采样 N 次；任何一次错就整轮判定失败。"""
    r = MeasureResult()
    # warmup
    try:
        _dt, out, rc = fn()
        if rc != 0:
            r.error = f'warmup returncode={rc}, stdout={out!r}'
            return r
        r.stdout = out.strip()
    except Exception as e:
        r.error = f'warmup 抛异常：{e}'
        return r
    # measured
    for _ in range(rounds):
        try:
            dt, out, rc = fn()
        except Exception as e:
            r.error = f'采样抛异常：{e}'
            return r
        if rc != 0:
            r.error = f'returncode={rc}'
            return r
        if out.strip() != r.stdout:
            r.error = '输出不稳定，两次采样 stdout 不同'
            return r
        r.samples.append(dt)
    return r


# ---------------------------------------------------------------------------
# 两条执行链
# ---------------------------------------------------------------------------

def _interpreter_argv(source_path: str) -> List[str]:
    return [
        sys.executable, '-c',
        # 保持简洁：直接 exec 源码文件；不打印额外内容，让 stdout 就是程序输出
        'import sys; from jikuai.main import run_source; '
        f'run_source(open({source_path!r}, encoding="utf-8").read())',
    ]


def _run_interpreter(bench: Benchmark, rounds: int) -> MeasureResult:
    env = {**os.environ,
           'PYTHONPATH': os.path.join(REPO_ROOT, 'src'),
           'PYTHONIOENCODING': 'utf-8',
           'JIKUAI_DIAGNOSTICS': 'off'}
    argv = _interpreter_argv(bench.source_path)
    return _repeat(lambda: _time_subprocess(argv, env=env), rounds)


def _compile_aot(bench: Benchmark, workdir: str):
    """一次性把 .jk 编译成原生二进制；失败返回 None + 原因。"""
    from jikuai_aot.driver import build, BuildOptions
    exe = os.path.join(workdir, bench.name + ('.exe' if os.name == 'nt' else ''))
    result = build(BuildOptions(
        source_file=bench.source_path, output_path=exe, keep_temp=False))
    if not result.ok:
        return None, f'AOT 构建失败（exit={result.exit_code}）：{result.message}'
    return exe, None


def _run_aot(bench: Benchmark, exe: str, rounds: int) -> MeasureResult:
    return _repeat(lambda: _time_subprocess([exe]), rounds)


def _detect_c_compiler() -> Optional[str]:
    try:
        from jikuai_aot.driver import detect_c_compiler
    except Exception:
        import shutil
        for c in ('gcc', 'clang', 'cc', 'cl'):
            path = shutil.which(c)
            if path:
                return c
        return None
    return detect_c_compiler()


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def _fmt_ms(seconds: float) -> str:
    if seconds != seconds:                # NaN
        return '     -   '
    ms = seconds * 1000.0
    if ms < 100:
        return f'{ms:7.2f} ms'
    return f'{ms:7.1f} ms'


def _fmt_speedup(interp: float, aot: float) -> str:
    if aot != aot or aot == 0 or interp != interp:
        return '   -  '
    r = interp / aot
    return f'{r:5.2f}×'


def report(rows: List[dict], compiler: Optional[str]) -> str:
    lines = []
    lines.append('=' * 78)
    lines.append('极快 JiKuai · 性能基准（T8）')
    lines.append('=' * 78)
    lines.append(f'Python {sys.version.split()[0]}  ·  '
                 f'AOT 编译器：{compiler or "未检测到 · AOT 一列跳过"}')
    lines.append('')
    lines.append(f'{"基准":<14} {"解释器 (中位)":<14} {"AOT (中位)":<14} '
                 f'{"加速比":<10} {"状态"}')
    lines.append('-' * 78)
    for row in rows:
        speedup = _fmt_speedup(row['interp_median'], row['aot_median'])
        status = row['status']
        lines.append(f'{row["name"]:<14} {_fmt_ms(row["interp_median"]):<14} '
                     f'{_fmt_ms(row["aot_median"]):<14} {speedup:<10} {status}')
    lines.append('-' * 78)
    lines.append('说明：')
    lines.append('  - "解释器" 与 "AOT" 均含子进程启动开销，属于**端到端**用时；')
    lines.append('    AOT 二进制的稳态吞吐要看下面 `--json` 里的 samples 分布，')
    lines.append('    最小值（best）更接近纯 CPU 时间。')
    lines.append('  - 加速比 = 解释器中位 / AOT 中位。无 C 编译器时该列为 -。')
    lines.append('  - 每个基准都用相同源码跑两条链，stdout 必须精确一致，')
    lines.append('    否则整行标为"输出不匹配"并作废。')
    lines.append('')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='极快性能基准')
    ap.add_argument('filter', nargs='?', default=None,
                    help='按名字过滤（子串匹配）')
    ap.add_argument('--轮数', '--rounds', type=int, default=5,
                    dest='rounds', help='采样次数（默认 5）')
    ap.add_argument('--json', dest='json_path', default=None,
                    help='把详细结果写入 JSON 文件')
    ap.add_argument('--no-aot', action='store_true',
                    help='强制跳过 AOT 分支（即使装了 gcc）')
    args = ap.parse_args(argv)

    if args.rounds < 1:
        print('轮数必须 ≥ 1', file=sys.stderr)
        return 2

    selected = [b for b in BENCHMARKS
                if args.filter is None or args.filter in b.name]
    if not selected:
        print(f'没有名字包含 {args.filter!r} 的基准', file=sys.stderr)
        return 2

    compiler = None if args.no_aot else _detect_c_compiler()

    import tempfile
    rows = []
    with tempfile.TemporaryDirectory(prefix='jikuai-bench-') as workdir:
        for bench in selected:
            row = {
                'name': bench.name,
                'source': os.path.relpath(bench.source_path, REPO_ROOT),
                'expected': bench.expected,
                'interp_samples': [],
                'aot_samples': [],
                'interp_median': float('nan'),
                'aot_median': float('nan'),
                'status': 'ok',
            }

            # 解释器
            r_interp = _run_interpreter(bench, args.rounds)
            row['interp_samples'] = r_interp.samples
            row['interp_median'] = r_interp.median
            if not r_interp.ok:
                row['status'] = f'解释器失败：{r_interp.error}'
            elif r_interp.stdout != bench.expected:
                row['status'] = (f'解释器输出不匹配：'
                                 f'期望 {bench.expected!r}，实得 {r_interp.stdout!r}')

            # AOT
            if compiler and row['status'] == 'ok':
                exe, err = _compile_aot(bench, workdir)
                if err:
                    row['status'] = err
                else:
                    r_aot = _run_aot(bench, exe, args.rounds)
                    row['aot_samples'] = r_aot.samples
                    row['aot_median'] = r_aot.median
                    if not r_aot.ok:
                        row['status'] = f'AOT 运行失败：{r_aot.error}'
                    elif r_aot.stdout != bench.expected:
                        row['status'] = (f'AOT 输出不匹配：期望 '
                                         f'{bench.expected!r}，实得 {r_aot.stdout!r}')
            elif not compiler:
                row['status'] = '无 C 编译器（AOT 跳过）'

            rows.append(row)

    print(report(rows, compiler))

    if args.json_path:
        payload = {
            'python': sys.version,
            'compiler': compiler,
            'rounds': args.rounds,
            'benchmarks': rows,
        }
        with open(args.json_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f'详细数据已写入 {args.json_path}')

    # 只要有基准报"失败"（不是"无 C 编译器"），退出码非零，便于 CI 拦截
    if any('失败' in r['status'] or '不匹配' in r['status'] for r in rows):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
