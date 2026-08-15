# -*- coding: utf-8 -*-
"""G15 · 版本号单一真源门禁的直测（W25 · v0.16.0）。

`scripts/check_stdlib_contract.py` 的 `_check_version_consistency` 是 G15 的核心。
此文件直调它，正反两条：
- 一致：默认仓库状态返回空列表
- 不一致：临时改 vscode `package.json` 或 CHANGELOG 应被识别
"""

import importlib.util
import json
import os
import shutil
import sys

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPT_PATH = os.path.join(REPO_ROOT, 'scripts', 'check_stdlib_contract.py')


def _load_gate_module():
    """把 `check_stdlib_contract.py` 作为模块加载（脚本非包）。"""
    spec = importlib.util.spec_from_file_location('_g15_gate', SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    src_path = os.path.join(REPO_ROOT, 'src')
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    spec.loader.exec_module(mod)
    return mod


def test_g15_consistent_by_default():
    """默认仓库状态，G15 无问题。"""
    mod = _load_gate_module()
    problems = mod._check_version_consistency()
    assert problems == [], problems


def test_g15_source_version_reads():
    """真源 `_version.__version__` 可读，非空 semver 三段式。"""
    mod = _load_gate_module()
    v = mod._read_source_version()
    parts = v.split('.')
    assert len(parts) >= 3, v
    assert all(p.isdigit() for p in parts[:3]), v


def test_g15_detects_vscode_drift(tmp_path, monkeypatch):
    """把 vscode/package.json 的 version 改坏，G15 必须给出对应问题。"""
    mod = _load_gate_module()
    pkg_path = os.path.join(REPO_ROOT, 'editors', 'vscode', 'package.json')
    backup = pkg_path + '.g15_bak'
    shutil.copy2(pkg_path, backup)
    try:
        with open(pkg_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data['version'] = '9.9.9-drift'
        with open(pkg_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        problems = mod._check_version_consistency()
        assert any('vscode' in p and '9.9.9-drift' in p for p in problems), problems
    finally:
        shutil.move(backup, pkg_path)


def test_g15_detects_changelog_drift(tmp_path):
    """把 CHANGELOG 最新条目号改坏，G15 必须给出对应问题。"""
    mod = _load_gate_module()
    log_path = os.path.join(REPO_ROOT, 'CHANGELOG.md')
    with open(log_path, 'r', encoding='utf-8') as f:
        original = f.read()
    # 在最顶端塞一个假的更高版本条目
    fake = "# 极快 JiKuai · 变更日志\n\n## v9.9.9（fake）\n\n"
    tampered = original.replace("# 极快 JiKuai · 变更日志\n", fake, 1)
    try:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(tampered)
        problems = mod._check_version_consistency()
        assert any('CHANGELOG' in p and '9.9.9' in p for p in problems), problems
    finally:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(original)


# --- W118（v0.24.0）：lsp / dap 两处新投影 ---------------------------------

_LSP_VERSION = os.path.join('lsp', 'jikuai_lsp', '_version.py')
_DAP_VERSION = os.path.join('dap', 'jikuai_dap', '_version.py')


def _读版本(相对路径):
    """独立读一个 `_version.py` 的 `__version__`。

    刻意**不**复用门禁自己的 `_read_attr_version`——那样只是在重复断言门禁的
    实现，门禁读错了这条测试会跟着一起错。这里用 `exec` 真跑一遍取值。
    """
    命名空间 = {}
    路径 = os.path.join(REPO_ROOT, 相对路径)
    with open(路径, encoding='utf-8') as f:
        exec(compile(f.read(), 路径, 'exec'), 命名空间)
    return 命名空间['__version__']


def test_lsp与dap版本与主包一致():
    """W118（v0.24.0）：三包同号发布，G15 之外再留一个 pytest 兜底
    （G15 的历史教训是「跑门禁的人当场看不到红」）。"""
    主 = _读版本(os.path.join('src', 'jikuai', '_version.py'))
    assert _读版本(_LSP_VERSION) == 主
    assert _读版本(_DAP_VERSION) == 主


def test_g15_detects_lsp_drift():
    """把 lsp 的 `_version.py` 改坏，G15 必须点名它。

    没有这条，「G15 把 lsp 纳进来了」只是个声明——投影清单里写错文件名
    （读不到 → 恒为 None → 恒报「读不到版本号」而不是真在比）也一样能让
    上面那条正向测试通过。这是 v0.22.0「守卫绿≠守卫在守」的直接应用。
    """
    mod = _load_gate_module()
    path = os.path.join(REPO_ROOT, _LSP_VERSION)
    with open(path, 'r', encoding='utf-8') as f:
        original = f.read()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(original.replace('__version__ = "', '__version__ = "9.9.9-drift', 1))
        problems = mod._check_version_consistency()
        assert any('jikuai_lsp' in p and '9.9.9-drift' in p for p in problems), problems
    finally:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(original)


def test_lsp与dap的pyproject不再写死版本字面量():
    """两个 pyproject 必须走 dynamic，否则又会出现「改了 _version.py
    但发出去的包还是旧号」——lsp 就是这么停在 0.15.0 停了八个版本的。"""
    import re
    for 子包, 属性 in (('lsp', 'jikuai_lsp._version.__version__'),
                       ('dap', 'jikuai_dap._version.__version__')):
        路径 = os.path.join(REPO_ROOT, 子包, 'pyproject.toml')
        with open(路径, 'r', encoding='utf-8') as f:
            文本 = f.read()
        assert not re.search(r'(?m)^version\s*=\s*["\']', 文本), \
            '%s/pyproject.toml 还写死了 version 字面量' % 子包
        assert 属性 in 文本, '%s/pyproject.toml 的 dynamic version 没指向 _version' % 子包


def test_lsp与dap钉了jikuai依赖下界():
    """PyPI 上 0.4.1 及更早的 jikuai 是坏包（wheel 里零个 stdlib，
    见 BACKLOG §10）。无下界时解析器可能把它拽回来。"""
    for 子包 in ('lsp', 'dap'):
        路径 = os.path.join(REPO_ROOT, 子包, 'pyproject.toml')
        with open(路径, 'r', encoding='utf-8') as f:
            文本 = f.read()
        assert '"jikuai>=0.24.0"' in 文本, '%s/pyproject.toml 没钉 jikuai 下界' % 子包
