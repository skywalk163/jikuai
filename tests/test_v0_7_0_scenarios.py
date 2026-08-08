# -*- coding: utf-8 -*-
"""v0.7.0 · M6-P1 场景示例三件套自动化测试（G13）。

覆盖 T-M6-E04..E05：
- 参数化遍历 examples/scenarios/*/main.jk（只测新增的目录，旧的 3 个 .jk 平铺文件不含 main.jk，天然被排除）
- subprocess 跑 python -m jikuai <main.jk>，断言退出码 0
- 快照比对（归一化后）：CRLF→LF、去尾部空行、UTF-8 解码、遮蔽时间戳
- UPDATE_SNAPSHOTS=1 时写回 expected.txt
- README 四段完整性（用途 / 输入 / 预期输出 / 前置）
- AC-M6-03-02：文本批处理示例调用了 M4/M5 新标准库模块
"""

import glob
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
_SRC = os.path.join(_ROOT, 'src')
_SCENARIOS_DIR = os.path.join(_ROOT, 'examples', 'scenarios')

# 本支线新增的三个场景目录（G13 只测这三个，不动旧的 3 个平铺 .jk）
_NEW_SCENARIOS = ['财务报表', '农历日程', '文本批处理']

# AC-M6-03-02：M4/M5 新增标准库模块白名单
_M4_M5_MODULES = {'简繁', '排版', '正则', '成语', '分词'}

# 遮蔽形如 2026-08-08T11:22:33 或 2026-08-08 11:22:33 的时间戳，避免快照不稳定。
# 本支线的示例一律用固定输入日期，不产生动态时间戳；此遮蔽仅作防御。
_RE_TIMESTAMP = re.compile(
    r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?'
)


def _normalize(text):
    """归一化：CRLF→LF、遮蔽时间戳、去尾部空行。"""
    text = text.replace('\r\n', '\n')
    text = _RE_TIMESTAMP.sub('<时间戳>', text)
    return text.rstrip('\n')


def _run_jk(main_jk):
    """subprocess 执行 main.jk，返回 (returncode, 归一化 stdout)。"""
    env = os.environ.copy()
    env['PYTHONPATH'] = _SRC
    env['PYTHONIOENCODING'] = 'utf-8'
    proc = subprocess.run(
        [sys.executable, '-X', 'utf8', '-m', 'jikuai', main_jk],
        capture_output=True, env=env, cwd=_ROOT,
    )
    out = proc.stdout.decode('utf-8', errors='replace')
    err = proc.stderr.decode('utf-8', errors='replace')
    return proc.returncode, _normalize(out), err


def _discover_main_jk():
    """发现所有场景 main.jk（只有新增的 3 个目录含 main.jk）。"""
    pattern = os.path.join(_SCENARIOS_DIR, '*', 'main.jk')
    return sorted(glob.glob(pattern))


def _scenario_name(main_jk):
    return os.path.basename(os.path.dirname(main_jk))


_MAIN_JK_FILES = _discover_main_jk()


def test_discovered_three_new_scenarios():
    """确认恰好发现三个新增场景目录，且旧的平铺 .jk 未被卷入。"""
    names = sorted(_scenario_name(p) for p in _MAIN_JK_FILES)
    assert names == sorted(_NEW_SCENARIOS), (
        '发现的场景目录与预期不符：%s' % names
    )


@pytest.mark.parametrize('main_jk', _MAIN_JK_FILES,
                         ids=[_scenario_name(p) for p in _MAIN_JK_FILES])
def test_scenario_runs_and_matches_snapshot(main_jk):
    """每个场景：退出码 0 且输出与 expected.txt 归一化后一致。"""
    rc, out, err = _run_jk(main_jk)
    assert rc == 0, '场景 %s 退出码非 0：\nSTDERR:\n%s' % (main_jk, err)

    expected_path = os.path.join(os.path.dirname(main_jk), 'expected.txt')

    if os.environ.get('UPDATE_SNAPSHOTS') == '1':
        with open(expected_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(out + '\n')
        pytest.skip('UPDATE_SNAPSHOTS=1：已写回 %s' % expected_path)

    assert os.path.isfile(expected_path), '缺少快照文件：%s' % expected_path
    with open(expected_path, 'r', encoding='utf-8') as f:
        expected = _normalize(f.read())

    assert out == expected, (
        '场景 %s 输出与快照不一致。\n'
        '（如为有意变更，运行 UPDATE_SNAPSHOTS=1 pytest 重新生成快照）\n'
        '--- 实际 ---\n%s\n--- 期望 ---\n%s' % (main_jk, out, expected)
    )


@pytest.mark.parametrize('main_jk', _MAIN_JK_FILES,
                         ids=[_scenario_name(p) for p in _MAIN_JK_FILES])
def test_scenario_readme_four_sections(main_jk):
    """README 必须含四段：用途 / 输入 / 预期输出 / 前置。"""
    readme = os.path.join(os.path.dirname(main_jk), 'README.md')
    assert os.path.isfile(readme), '缺少 README：%s' % readme
    with open(readme, 'r', encoding='utf-8') as f:
        text = f.read()
    for section in ('用途', '输入', '预期输出', '前置'):
        assert re.search(r'^#+\s*.*' + section, text, re.MULTILINE), (
            'README %s 缺少「%s」小节' % (readme, section)
        )


def test_ac_m6_03_02_text_scenario_uses_stdlib_module():
    """AC-M6-03-02：文本批处理示例源码调用至少一个 M4/M5 新标准库模块。"""
    main_jk = os.path.join(_SCENARIOS_DIR, '文本批处理', 'main.jk')
    assert os.path.isfile(main_jk), '缺少文本批处理场景 main.jk'
    with open(main_jk, 'r', encoding='utf-8') as f:
        src = f.read()

    imported = set()
    # 形如：导入 排版。 / 导入 排版 作为 X。
    for m in re.finditer(r'导入\s+([^\s。:：]+)', src):
        imported.add(m.group(1))
    # 形如：从 分词 导入 分词。
    for m in re.finditer(r'从\s+([^\s]+)\s+导入', src):
        imported.add(m.group(1))

    hit = imported & _M4_M5_MODULES
    assert hit, (
        'AC-M6-03-02 未满足：文本批处理示例未导入任何 M4/M5 新模块 %s，'
        '实际导入=%s' % (sorted(_M4_M5_MODULES), sorted(imported))
    )
