# -*- coding: utf-8 -*-
"""极快语言 · LSP 服务器能力声明（v0.6.0 · M5 · T-M5-L07 · F3 冻结点）。

能力被定义为**可被测试直接断言的纯数据结构**（module-level dict），
避免测试反射运行期对象。

M5（F3 冻结）声明：
    - textDocumentSync：openClose + Full change
    - completionProvider：文本补全（触发字符 `.` 与 `，`）
    - hoverProvider：悬浮说明
    - publishDiagnostics 走服务端 push，故 diagnosticProvider 显式为 False
      （表示不提供 pull-based 诊断）

`positionEncoding` 显式声明 utf-16：与 `diagnostics.adapters` 及
`service.position` 的 UTF-16 单元口径保持一致。

`freeze_signature()` 是 **F3 冻结契约**：返回按 key 排序的规范化 dict，
QA 与协议消费者以此判定契约是否稳定；一经发布只增不改不复用。
"""

from __future__ import annotations

from typing import Any, Dict, List

# LSP TextDocumentSyncKind：0=None / 1=Full / 2=Incremental
TEXT_DOCUMENT_SYNC_FULL = 1
TEXT_DOCUMENT_SYNC_INCREMENTAL = 2

#: 补全触发字符：`.` 用于 `模块.成员`，`，` 用于管道后候选。
COMPLETION_TRIGGER_CHARACTERS: List[str] = ['.', '，']

#: 纯数据能力声明；测试可直接断言 SERVER_CAPABILITIES['completionProvider']。
SERVER_CAPABILITIES: Dict[str, Any] = {
    'textDocumentSync': {
        'openClose': True,
        'change': TEXT_DOCUMENT_SYNC_FULL,
    },
    'completionProvider': {
        'resolveProvider': False,
        'triggerCharacters': list(COMPLETION_TRIGGER_CHARACTERS),
    },
    'hoverProvider': True,
    'diagnosticProvider': False,
    'positionEncoding': 'utf-16',
}


def _deep_copy(value: Any) -> Any:
    """浅层递归拷贝（capabilities 结构简单，无需 copy.deepcopy）。"""
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


def server_capabilities() -> Dict[str, Any]:
    """返回能力声明的深拷贝，避免调用方误改 module-level 常量。"""
    return _deep_copy(SERVER_CAPABILITIES)


def _normalize(value: Any) -> Any:
    """按 key 排序递归规范化，用于生成稳定的冻结签名。

    list 保序：triggerCharacters 的顺序也是契约的一部分。
    """
    if isinstance(value, dict):
        return {k: _normalize(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def freeze_signature() -> Dict[str, Any]:
    """返回 F3 冻结签名：capabilities 的规范化（key 排序）字典。

    测试断言这个 dict 结构稳定——F3 冻结点后新增能力须走 ADR 变更流程。
    """
    return _normalize(SERVER_CAPABILITIES)
