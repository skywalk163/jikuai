# -*- coding: utf-8 -*-
"""v0.14.0 W12 · 10 个端到端 demo + 压缩比门禁。

两条防线：

1. `examples/blocks/demo/*.jk` 全部可跑通（进程内 `run_source`，参数化到
   每个 demo，失败时能直接定位是哪个脚本），外加一条子进程冒烟，确认
   `python -m jikuai <demo>` 这条真实 CLI 路径退出码为 0。
2. `tools/ai-bridge/bench_compress.py` 的压缩比中位数达标（≥8x）——把
   W12 的发布门槛钉进 pytest，防止后续改 demo 或改块把它悄悄打回。

`tools/ai-bridge` 目录名带连字符，不能直接 `import`，用 `importlib` 按文件
路径加载。
"""

import importlib.util
import io
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.main import run_source  # noqa: E402

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_DEMO = os.path.join(_ROOT, 'examples', 'blocks', 'demo')
_BENCH = os.path.join(_ROOT, 'tools', 'ai-bridge', 'bench_compress.py')

DEMO数 = 10


def _demo文件表():
    if not os.path.isdir(_DEMO):
        return []
    return [os.path.join(_DEMO, n) for n in sorted(os.listdir(_DEMO))
            if n.endswith('.jk')]


def _载入bench():
    spec = importlib.util.spec_from_file_location('bench_compress', _BENCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_demo数量为十():
    """W12 DoD：demo 目录恰好 10 个 .jk。"""
    assert len(_demo文件表()) == DEMO数


@pytest.mark.parametrize(
    'demo路径',
    [pytest.param(p, id=os.path.basename(p)) for p in _demo文件表()])
def test_demo跑通(demo路径, capsys):
    """每个 demo 进程内跑完不抛异常，且必须有 stdout 输出（不是空壳脚本）。"""
    with open(demo路径, 'r', encoding='utf-8') as f:
        源码 = f.read()
    run_source(源码, file=demo路径)
    出 = capsys.readouterr().out
    assert 出.strip(), '%s 没有任何输出' % os.path.basename(demo路径)


def test_demo顶部有方案JSON():
    """压缩比基准靠顶部注释里的方案 JSON 取数，10 个 demo 一个都不能缺。"""
    bench = _载入bench()
    缺 = []
    for p in _demo文件表():
        with open(p, 'r', encoding='utf-8') as f:
            方案, _ = bench.抽方案(f.read())
        if 方案 is None:
            缺.append(os.path.basename(p))
    assert not 缺, '缺方案 JSON：%s' % 缺


def test_单个demo走CLI退出码为零():
    """真实 CLI 路径冒烟：`python -m jikuai <demo>` 退出码 0。

    只挑一个 demo 走子进程（10 个全跑子进程太慢，进程内已逐个覆盖）。
    """
    目标 = os.path.join(_DEMO, '工资条-月薪两万.jk')
    assert os.path.isfile(目标)
    env = dict(os.environ,
               PYTHONPATH=os.path.join(_ROOT, 'src'),
               PYTHONIOENCODING='utf-8')
    r = subprocess.run([sys.executable, '-m', 'jikuai', 目标],
                       capture_output=True, text=True, encoding='utf-8',
                       env=env, cwd=_ROOT)
    assert r.returncode == 0, r.stderr
    assert '工资条' in r.stdout


def test_压缩比中位数达标():
    """W12 发布门槛：压缩比中位数 ≥8x。

    口径见 `bench_compress` 模块 docstring：裸写 = demo 自身编排 + 直接与
    传递依赖块的全部源码。tiktoken 缺失时走 UTF-8 字节近似，不 skip——
    近似误差对**比值**基本抵消，门槛照样有意义。
    """
    bench = _载入bench()
    报告 = bench.跑全量(_DEMO)
    assert 报告['有效数'] == DEMO数
    assert 报告['中位数压缩比'] >= bench.门槛, (
        '压缩比中位数 %.2fx 低于门槛 %.0fx'
        % (报告['中位数压缩比'], bench.门槛))
    assert 报告['达标'] is True


def test_压缩比逐条为正且裸写大于同源():
    """每个 demo 的裸写量必须严格大于「只数 demo 源码」的同源量——
    否则说明依赖闭包没算进去（比如块目录改名后 `导入` 正则失配）。"""
    bench = _载入bench()
    for d in bench.跑全量(_DEMO)['明细']:
        assert '错误' not in d, d
        assert d['依赖块'], '%s 没解析出任何依赖块' % d['demo']
        assert d['裸写token'] > d['同源token'], d['demo']
        assert d['压缩比'] > 0