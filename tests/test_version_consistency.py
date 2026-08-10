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
