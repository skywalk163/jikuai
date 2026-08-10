# -*- coding: utf-8 -*-
"""W26 · commit message 卫生检查器单测。"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPT_PATH = os.path.join(REPO_ROOT, 'scripts', 'check_commit_msg.py')


def _load():
    spec = importlib.util.spec_from_file_location('_cm_check', SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_accepts_normal_message():
    mod = _load()
    assert mod.check("feat: 新增 X\n\n正文说明") == []


def test_accepts_message_with_hash_comment_lines():
    mod = _load()
    text = "feat: 新增 X\n# 这是 git 自动加的注释\n\n正文"
    assert mod.check(text) == []


def test_rejects_empty():
    mod = _load()
    assert mod.check("") != []
    assert mod.check("   \n\n  ") != []


def test_rejects_command_substitution():
    """`a299768` 的实际字面量。"""
    mod = _load()
    problems = mod.check("$(cat <<'EOF'\n...")
    assert any("$(" in p for p in problems), problems


def test_rejects_heredoc_start():
    mod = _load()
    problems = mod.check("feat: X\ncat <<EOF\nbody")
    assert any("heredoc" in p for p in problems), problems


def test_rejects_first_line_pure_punctuation():
    mod = _load()
    problems = mod.check("---\n\n正文")
    assert any("首行" in p for p in problems), problems
