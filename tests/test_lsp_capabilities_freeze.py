# -*- coding: utf-8 -*-
"""v0.15.0 W13 · LSP capabilities 冻结契约测试。

`freeze_signature()` 是协议消费者（VS Code 扩展 / QA / Web 通道）判定
服务端能力是否稳定的唯一依据。本文件把它的**完整结构**锁死：任何字段
增删改都必须同时改这里，让契约变更在 code review 里显形。

契约变更历史：
    v0.6.0  M5 F3 冻结点：textDocumentSync(Full) + completion + hover
    v0.15.0 W14：+definitionProvider，textDocumentSync.change 1→2
    v0.15.0 W15：+executeCommandProvider（极快.选块）
"""

from __future__ import annotations

import os
import sys
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))
sys.path.insert(0, os.path.join(_ROOT, 'lsp'))


def _lsp_available():
    try:
        import importlib
        importlib.import_module('jikuai_lsp.capabilities')
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _lsp_available(),
    reason="jikuai_lsp 不可用，跳过 capabilities 冻结测试",
)


#: v0.15.0（W13+W14+W15）冻结签名的**完整**期望值。改这里等于改对外契约。
EXPECTED_FREEZE = {
    'completionProvider': {
        'resolveProvider': False,
        'triggerCharacters': ['.', '，'],
    },
    'definitionProvider': True,
    'diagnosticProvider': False,
    'executeCommandProvider': {
        'commands': ['极快.选块'],
    },
    'hoverProvider': True,
    'positionEncoding': 'utf-16',
    'textDocumentSync': {
        'change': 2,
        'openClose': True,
    },
}


def test_freeze_signature_exact_match():
    """freeze_signature() 必须逐字段等于 EXPECTED_FREEZE。"""
    from jikuai_lsp.capabilities import freeze_signature
    assert freeze_signature() == EXPECTED_FREEZE


def test_freeze_signature_top_level_keys_sorted():
    """freeze_signature 顶层 key 必须按字典序排列（规范化保证）。"""
    from jikuai_lsp.capabilities import freeze_signature
    keys = list(freeze_signature().keys())
    assert keys == sorted(keys), f"顶层 key 未排序：{keys}"


def test_freeze_signature_nested_keys_sorted():
    """嵌套 dict 的 key 也必须排序。"""
    from jikuai_lsp.capabilities import freeze_signature
    sig = freeze_signature()
    for name, value in sig.items():
        if isinstance(value, dict):
            keys = list(value.keys())
            assert keys == sorted(keys), f"{name} 的 key 未排序：{keys}"


def test_trigger_characters_order_is_contract():
    """triggerCharacters 是有序 list，顺序也是契约的一部分。"""
    from jikuai_lsp.capabilities import freeze_signature
    tc = freeze_signature()['completionProvider']['triggerCharacters']
    assert isinstance(tc, list)
    assert tc == ['.', '，'], "触发字符顺序变更需走 ADR 流程"


def test_server_capabilities_is_deep_copy():
    """server_capabilities() 返回深拷贝，调用方改动不污染 module 常量。"""
    from jikuai_lsp.capabilities import (
        server_capabilities, SERVER_CAPABILITIES)
    caps = server_capabilities()
    caps['textDocumentSync']['change'] = 999
    caps['completionProvider']['triggerCharacters'].append('X')
    assert SERVER_CAPABILITIES['textDocumentSync']['change'] != 999
    assert 'X' not in SERVER_CAPABILITIES['completionProvider']['triggerCharacters']


def test_sync_kind_is_incremental():
    """v0.15.0 W14 起 textDocumentSync.change 必须是 2（Incremental）。"""
    from jikuai_lsp.capabilities import (
        SERVER_CAPABILITIES, TEXT_DOCUMENT_SYNC_INCREMENTAL)
    assert SERVER_CAPABILITIES['textDocumentSync']['change'] == \
        TEXT_DOCUMENT_SYNC_INCREMENTAL


def test_execute_command_declares_select_block():
    """executeCommandProvider 必须声明 `极快.选块`。"""
    from jikuai_lsp.capabilities import SERVER_CAPABILITIES, COMMAND_SELECT_BLOCK
    cmds = SERVER_CAPABILITIES['executeCommandProvider']['commands']
    assert COMMAND_SELECT_BLOCK in cmds
    assert COMMAND_SELECT_BLOCK == '极快.选块'
