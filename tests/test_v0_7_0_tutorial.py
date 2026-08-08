# -*- coding: utf-8 -*-
"""v0.7.0 · M6-P2 官方教程自动化测试（G14）。

覆盖 T-M6-T05：
- 用 scripts/extract_tutorial_snippets.py 抽取教程可运行片段，逐片段 subprocess 执行
- 断言退出码 0；有 expect 标注的断言归一化 stdout 相等
- 断言教程覆盖 AC-M6-04-02 的最小闭环四步（章节文件存在且非空）
- 断言 06-Python互操作.md 转述了安全边界（含「不受信任」与「沙箱」关键字）
"""

import importlib.util
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
_SRC = os.path.join(_ROOT, 'src')
_DOCS_TUT = os.path.join(_ROOT, 'docs', '教程')
_EXTRACTOR = os.path.join(_ROOT, 'scripts', 'extract_tutorial_snippets.py')

# AC-M6-04-02 最小闭环四步 → 对应章节文件
_MIN_LOOP = {
    '安装': '01-安装与运行.md',
    '第一个程序': '02-第一个程序.md',
    '用标准库': '05-标准库.md',
    '用VSCode扩展': '07-开发工具链.md',
}


def _load_extractor():
    """以文件路径隔离导入抽取器（scripts/ 不是包，不污染 sys.path）。"""
    spec = importlib.util.spec_from_file_location(
        '_jk_tutorial_extractor', _EXTRACTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_extractor = _load_extractor()
_RUNNABLE = _extractor.runnable_snippets(_DOCS_TUT)
_ALL = _extractor.extract_snippets(_DOCS_TUT)


def _normalize(text):
    return text.replace('\r\n', '\n').rstrip('\n')


def _run_snippet(code):
    """把片段写进临时 .jk 文件并执行，返回 (rc, 归一化 stdout, stderr)。"""
    env = os.environ.copy()
    env['PYTHONPATH'] = _SRC
    env['PYTHONIOENCODING'] = 'utf-8'
    fd, path = tempfile.mkstemp(suffix='.jk', prefix='教程片段_')
    os.close(fd)
    try:
        body = code if code.endswith('\n') else code + '\n'
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(body)
        proc = subprocess.run(
            [sys.executable, '-X', 'utf8', '-m', 'jikuai', path],
            capture_output=True, env=env, cwd=_ROOT,
        )
        return (proc.returncode,
                _normalize(proc.stdout.decode('utf-8', errors='replace')),
                proc.stderr.decode('utf-8', errors='replace'))
    finally:
        if os.path.exists(path):
            os.remove(path)


# ============================================================
# 抽取器自身
# ============================================================

def test_extractor_cli_exit_zero():
    """抽取器可独立运行，退出码 0，且输出片段清单。"""
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    proc = subprocess.run(
        [sys.executable, '-X', 'utf8', _EXTRACTOR],
        capture_output=True, env=env, cwd=_ROOT,
    )
    out = proc.stdout.decode('utf-8', errors='replace')
    assert proc.returncode == 0, proc.stderr.decode('utf-8', errors='replace')
    assert '片段总数' in out


def test_extractor_cli_json_exit_zero():
    """--json 模式也是退出码 0，且输出合法 JSON。"""
    import json
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    proc = subprocess.run(
        [sys.executable, '-X', 'utf8', _EXTRACTOR, '--json'],
        capture_output=True, env=env, cwd=_ROOT,
    )
    assert proc.returncode == 0, proc.stderr.decode('utf-8', errors='replace')
    data = json.loads(proc.stdout.decode('utf-8'))
    assert isinstance(data, list) and data


def test_has_runnable_snippets():
    """教程里必须有可运行片段，否则 G14 形同虚设。"""
    assert len(_RUNNABLE) >= 20, '可运行片段过少：%d' % len(_RUNNABLE)


def test_extractor_annotation_parsing():
    """抽取器的标注解析：run / expect / 多行 expect / 纯展示。"""
    text = (
        '<!-- run: true -->\n'
        '<!-- expect: 8 -->\n'
        '```jikuai\n'
        '打印 加 3 5。\n'
        '```\n'
        '\n'
        '正文段落，重置挂起标注。\n'
        '\n'
        '```jikuai\n'
        '打印 1。\n'
        '```\n'
        '\n'
        '<!-- run: true -->\n'
        '<!-- expect: 1 -->\n'
        '<!-- expect: 2 -->\n'
        '```jikuai\n'
        '打印 1。\n'
        '打印 2。\n'
        '```\n'
    )
    snips = _extractor.extract_from_text(text, '测试.md')
    assert len(snips) == 3
    assert snips[0]['run'] is True and snips[0]['expect'] == '8'
    assert snips[1]['run'] is False and snips[1]['expect'] is None
    assert snips[2]['run'] is True and snips[2]['expect'] == '1\n2'


# ============================================================
# 逐片段执行（G14 主门禁）
# ============================================================

@pytest.mark.parametrize('snippet', _RUNNABLE,
                         ids=[s['id'] for s in _RUNNABLE])
def test_tutorial_snippet_runs(snippet):
    """每个 run: true 片段必须退出码 0；有 expect 的还要 stdout 相等。"""
    rc, out, err = _run_snippet(snippet['code'])
    assert rc == 0, (
        '教程片段 %s 退出码 %d\n--- 源码 ---\n%s\n--- STDERR ---\n%s'
        % (snippet['id'], rc, snippet['code'], err)
    )
    if snippet['expect'] is not None:
        expected = _normalize(snippet['expect'])
        assert out == expected, (
            '教程片段 %s 输出与 expect 标注不符。\n'
            '--- 源码 ---\n%s\n--- 实际 ---\n%s\n--- 期望 ---\n%s'
            % (snippet['id'], snippet['code'], out, expected)
        )


# ============================================================
# AC-M6-04-02 最小闭环覆盖
# ============================================================

@pytest.mark.parametrize('step,filename', sorted(_MIN_LOOP.items()))
def test_min_loop_chapter_exists_and_nonempty(step, filename):
    """最小闭环四步各有对应章节，文件存在且非空。"""
    path = os.path.join(_DOCS_TUT, filename)
    assert os.path.isfile(path), '最小闭环「%s」缺章节：%s' % (step, filename)
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    assert len(text.strip()) > 200, (
        '最小闭环「%s」章节 %s 内容过少（%d 字符）' % (step, filename, len(text.strip()))
    )


def test_tutorial_chapters_all_present():
    """00 - 08 九个章节齐全。"""
    names = sorted(f for f in os.listdir(_DOCS_TUT) if f.endswith('.md'))
    prefixes = sorted(n.split('-', 1)[0] for n in names)
    assert prefixes == ['00', '01', '02', '03', '04', '05', '06', '07', '08'], (
        '章节编号不齐：%s' % names
    )


def test_intro_documents_annotation_convention():
    """首章必须说明代码片段标注约定（run / expect）。"""
    path = os.path.join(_DOCS_TUT, '00-简介.md')
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    assert 'run: true' in text
    assert 'expect:' in text


# ============================================================
# 安全边界转述
# ============================================================

def test_python_interop_chapter_restates_security_boundary():
    """06-Python互操作.md 必须转述安全边界（「不受信任」与「沙箱」）。"""
    path = os.path.join(_DOCS_TUT, '06-Python互操作.md')
    assert os.path.isfile(path)
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    assert '不受信任' in text, '缺少「不受信任」关键字'
    assert '沙箱' in text, '缺少「沙箱」关键字'
    assert '安全边界' in text, '应引用 docs/安全边界.md'


def test_known_limits_chapter_covers_four_traps():
    """08-已知限制.md 覆盖四个必讲坑。"""
    path = os.path.join(_DOCS_TUT, '08-已知限制.md')
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    for keyword in ('JK-W1001', '反斜杠', '替代', '沙箱'):
        assert keyword in text, '08-已知限制.md 缺少「%s」' % keyword
