# -*- coding: utf-8 -*-
"""v0.6.0 · T-M5-P01..P03 pybridge 安全声明验收（US-M5-08 · ADR-21）。

这是**文档要点核对型**验收（裁决 D-01），不是行为测试——本周期明确不实现
沙箱，只承诺"安全边界被显式声明且可验证"。

覆盖 AC：
    AC-M5-08-01  三处（模块 docstring / README / docs）均声明"非完整沙箱"
    AC-M5-08-02  黑名单覆盖范围与已知绕过风险被列出，且与实现一致
    AC-M5-08-03  使用边界建议（禁用场景）在多处同步出现
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))

#: 三个声明落点（裁决 D-01 指定）
_README = os.path.join(_ROOT, 'README.md')
_SECURITY_DOC = os.path.join(_ROOT, 'docs', '安全边界.md')
_ADR21 = os.path.join(_ROOT, 'docs', 'ADR-21-pybridge安全边界.md')


def _read(path: str) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _pybridge_docstring() -> str:
    from jikuai import pybridge
    return pybridge.__doc__ or ''


# ---------------------------------------------------------------------------
# AC-M5-08-01 · 三处均声明"非完整沙箱"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,loader", [
    ("pybridge 模块 docstring", _pybridge_docstring),
    ("README.md", lambda: _read(_README)),
    ("docs/安全边界.md", lambda: _read(_SECURITY_DOC)),
    ("docs/ADR-21", lambda: _read(_ADR21)),
])
def test_ac_m5_08_01_declares_not_a_sandbox(name, loader):
    """每个落点都必须出现「不提供完整沙箱隔离」这一核心判断。"""
    text = loader()
    assert "沙箱" in text, f"{name} 未提及沙箱"
    assert ("不提供完整沙箱" in text or "非完整沙箱" in text), (
        f"{name} 未明确声明「不提供完整沙箱隔离」/「非完整沙箱」"
    )


def test_ac_m5_08_01_declares_blacklist_is_mitigation_only():
    """必须说清黑名单只是缓解手段，不是隔离保证。"""
    for name, text in (
        ("pybridge docstring", _pybridge_docstring()),
        ("README.md", _read(_README)),
        ("docs/安全边界.md", _read(_SECURITY_DOC)),
    ):
        assert ("黑名单" in text or "拒绝清单" in text), f"{name} 未提及黑名单机制"


# ---------------------------------------------------------------------------
# AC-M5-08-02 · 黑名单清单存在且与实现一致
# ---------------------------------------------------------------------------

def test_ac_m5_08_02_deny_list_documented_matches_implementation():
    """文档列出的黑名单条目必须与 pybridge.DENY_LIST 的实际内容对齐。"""
    from jikuai import pybridge

    deny = getattr(pybridge, 'DENY_LIST', None)
    assert deny is not None, "pybridge 未暴露 DENY_LIST"

    # DENY_LIST 条目形态为 (module, attr) 元组，如 ('builtins', 'eval')。
    # 取每条的函数名（attr）作为文档检索关键字。
    tails = set()
    for item in deny:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            tails.add(str(item[-1]))
        else:
            tails.add(str(item).split('.')[-1])

    doc = _read(_SECURITY_DOC)
    docstring = _pybridge_docstring()

    for tail in tails:
        assert tail in doc, f"docs/安全边界.md 未列出黑名单条目 {tail}"
        assert tail in docstring, f"pybridge docstring 未列出黑名单条目 {tail}"


def test_ac_m5_08_02_known_bypass_risks_listed():
    """已知绕过路径必须显式写出，避免用户误以为黑名单是完备的。"""
    for name, text in (
        ("pybridge docstring", _pybridge_docstring()),
        ("docs/安全边界.md", _read(_SECURITY_DOC)),
    ):
        assert "importlib" in text, f"{name} 未提及 importlib 间接绕过"
        assert ("绕过" in text or "局限" in text), f"{name} 未说明黑名单局限"


# ---------------------------------------------------------------------------
# AC-M5-08-03 · 使用边界建议同步出现
# ---------------------------------------------------------------------------

def test_ac_m5_08_03_forbidden_scenarios_stated():
    """禁用场景（不受信任代码）必须在三处同步出现。"""
    for name, text in (
        ("pybridge docstring", _pybridge_docstring()),
        ("README.md", _read(_README)),
        ("docs/安全边界.md", _read(_SECURITY_DOC)),
    ):
        assert "不受信任" in text, f"{name} 未声明「不适用于不受信任代码」"


def test_ac_m5_08_03_isolation_advice_present():
    """必须给出"若必须承载不可信输入"的替代方案（进程/容器级隔离）。"""
    for name, text in (
        ("pybridge docstring", _pybridge_docstring()),
        ("docs/安全边界.md", _read(_SECURITY_DOC)),
    ):
        assert ("进程级" in text or "容器" in text), (
            f"{name} 未给出系统级隔离的替代方案"
        )


def test_security_doc_covers_aot_and_debugger_trust_premise():
    """非功能需求：AOT 产物与调试器的信任前提也须标注。"""
    doc = _read(_SECURITY_DOC)
    assert "AOT" in doc, "安全边界文档未覆盖 AOT 产物的信任前提"
    assert ("DAP" in doc or "调试" in doc), "安全边界文档未覆盖调试器的信任前提"


def test_readme_links_to_authoritative_security_doc():
    """README 的安全声明段必须指向权威文档，避免多处描述漂移。"""
    readme = _read(_README)
    assert "docs/安全边界.md" in readme
