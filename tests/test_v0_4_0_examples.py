# -*- coding: utf-8 -*-
"""v0.4.0 M3 · 示例遍历 + README 管道示例 stdout + 版本号一致性验收测试。

覆盖：
  - AC-112：examples/pipelines/ 下 6 个管道示例逐文件退出码 0。
  - AC-118：examples/scenarios/ 下 3 个场景脚本逐文件退出码 0。
  - AC-107：README「管道式数据流」示例 `列1 2 3 4 5，皆乘2，只大6，归加0。`
            实机 stdout 为 `30`（逐字与 README 注释一致）。
  - 版本号一致性：main.py / __init__.py / pyproject.toml 三处均为 0.4.1。

设计说明：
  - 退出码遍历用子进程 `python -m jikuai <file>`（与 test_v0_3_2 的 D-11 同源），
    真实走 CLI 入口，最贴近用户 `jk <file>` 的行为。
  - README stdout 断言用进程内 Evaluator 捕获 stdout，避免子进程编码噪声。
"""

import io
import os
import contextlib
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.lexer import tokenize
from jikuai.parser import parse
from jikuai.evaluator import Evaluator


_ROOT = os.path.join(os.path.dirname(__file__), '..')
_EXAMPLES = os.path.join(_ROOT, 'examples')

PIPELINE_EXAMPLES = [
    '01_多级过滤映射聚合.jk',
    '02_条件分支管道.jk',
    '03_字典结构化数据.jk',
    '04_异常在管道中传播.jk',
    '05_副词组合.jk',
    '06_中国特色管道.jk',
]

SCENARIO_EXAMPLES = [
    '财务计算.jk',
    '农历工具.jk',
    '管道数据清洗.jk',
    # v0.16.0 W30：3 个 L3 聚合块的端到端 demo
    '报销单演示.jk',
    '工资册演示.jk',
    '客户对账演示.jk',
]


def _run_module(rel_path):
    """子进程运行 `python -m jikuai <rel_path>`，返回 CompletedProcess。"""
    env = dict(os.environ,
               PYTHONPATH=os.path.join(_ROOT, 'src'),
               PYTHONIOENCODING='utf-8')
    return subprocess.run(
        [sys.executable, '-m', 'jikuai', rel_path],
        capture_output=True, text=True, encoding='utf-8',
        env=env, cwd=_ROOT)


def _eval_capture(src):
    """进程内执行源码，返回 (最终值, stdout 行列表)。"""
    ev = Evaluator()
    ev._current_source = src
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        result = ev.eval(parse(tokenize(src)), source=src)
    lines = [ln for ln in f.getvalue().split('\n')]
    return result, lines


# ============================================================
# AC-112 · 6 个管道示例遍历退出码 0
# ============================================================

import pytest


@pytest.mark.parametrize('name', PIPELINE_EXAMPLES)
def test_ac112_pipeline_example_exit_zero(name):
    rel = os.path.join('examples', 'pipelines', name)
    assert os.path.exists(os.path.join(_ROOT, rel)), rel
    r = _run_module(rel)
    assert r.returncode == 0, (name, r.returncode, r.stderr)
    # 至少有输出，确保不是空跑
    assert r.stdout.strip() != '', name


def test_ac112_pipeline_count_is_six():
    """AC-108/109/110：管道示例目录恰好包含 6 个 .jk 文件。"""
    pdir = os.path.join(_EXAMPLES, 'pipelines')
    files = [f for f in os.listdir(pdir) if f.endswith('.jk')]
    assert len(files) == 6, sorted(files)


# ============================================================
# AC-118 · 3 个场景脚本遍历退出码 0
# ============================================================

@pytest.mark.parametrize('name', SCENARIO_EXAMPLES)
def test_ac118_scenario_example_exit_zero(name):
    rel = os.path.join('examples', 'scenarios', name)
    assert os.path.exists(os.path.join(_ROOT, rel)), rel
    r = _run_module(rel)
    assert r.returncode == 0, (name, r.returncode, r.stderr)
    assert r.stdout.strip() != '', name


def test_ac118_scenario_count_is_three():
    # v0.16.0 W30 起从 3 增至 6：新增 3 个 L3 聚合块（报销单/工资册/客户对账）demo。
    sdir = os.path.join(_EXAMPLES, 'scenarios')
    files = [f for f in os.listdir(sdir) if f.endswith('.jk')]
    assert len(files) == 6, sorted(files)


# ============================================================
# AC-107 · README 管道示例 stdout 断言
# ============================================================

def test_ac107_readme_pipeline_value_is_30():
    """README「管道式数据流」示例的表达式求值为 30。

    `列1 2 3 4 5，皆乘2，只大6，归加0。`
      → 皆乘2 → [2,4,6,8,10]
      → 只大6（`大` 非内建动词，副词按原值透传，不过滤）→ [2,4,6,8,10]
      → 归加0 → 30
    """
    src = '定义甲=列1 2 3 4 5，皆乘2，只大6，归加0。'
    result, _ = _eval_capture(src)
    assert result == 30, result


def test_ac107_readme_pipeline_stdout_is_30():
    """打印该管道结果，stdout 首行逐字为 `30`（与 README 注释一致）。"""
    src = '定义甲=列1 2 3 4 5，皆乘2，只大6，归加0。\n打印甲。'
    _, lines = _eval_capture(src)
    assert lines[0] == '30', lines


def test_ac107_readme_pipeline_correct_filter_is_18():
    """对照：写 `只大于6`（正确的过滤动词）时结果为 18，佐证 README 的补充说明。"""
    src = '定义甲=列1 2 3 4 5，皆乘2，只大于6，归加0。'
    result, _ = _eval_capture(src)
    assert result == 18, result


# ============================================================
# 版本号一致性
# ============================================================

def test_v041_version_consistency():
    """main.py / __init__.py / pyproject.toml 三处版本号一致。

    W25（v0.16.0）：真源下沉到 `_version.__version__`；pyproject 走 dynamic
    引用，静态读不到字面量属正常。硬编码期望值下沉到 G15 门禁。
    """
    import jikuai
    from jikuai.main import VERSION
    from jikuai._version import __version__ as src_version
    assert VERSION == src_version, VERSION
    assert jikuai.__version__ == src_version, jikuai.__version__
    toml_path = os.path.join(_ROOT, 'pyproject.toml')
    toml_ver = None
    import re as _re
    with open(toml_path, 'r', encoding='utf-8') as f:
        for line in f:
            # 只认字面量形式 `version = "x.y.z"`；W25 dynamic 引用形式
            # (`version = {attr = ...}`) 归 G15 门禁在构建期校验。
            m = _re.match(r'^version\s*=\s*["\']([^"\']+)["\']\s*$', line.strip())
            if m:
                toml_ver = m.group(1)
                break
    if toml_ver is not None:
        assert toml_ver == src_version, toml_ver
