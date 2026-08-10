# -*- coding: utf-8 -*-
"""极快语言 · LSP 服务器能力声明（v0.15.0 · 契约唯一真源）。

能力被定义为**可被测试直接断言的纯数据结构**（module-level dict），
避免测试反射运行期对象。

当前声明：
    - textDocumentSync：openClose + Incremental change（W14 起）
    - completionProvider：文本补全（触发字符 `.` 与 `，`）
    - hoverProvider：悬浮说明
    - definitionProvider：`导入` 点分块路径 → 块目录（W14）
    - documentSymbolProvider：单文件 AST 符号提纲——函数/类/导入（W32）
    - signatureHelpProvider：动词调用签名帮助，触发字符空格（W32）
    - executeCommandProvider：命令 `极快.选块`（W15）
    - publishDiagnostics 走服务端 push，故 diagnosticProvider 显式为 False
      （表示不提供 pull-based 诊断）

`positionEncoding` 显式声明 utf-16：与 `diagnostics.adapters` 及
`service.position` 的 UTF-16 单元口径保持一致。

`freeze_signature()` 是**对外契约判据**：返回按 key 排序的规范化 dict，
QA 与协议消费者以此判定契约是否稳定。变更必须同步
`tests/test_lsp_capabilities_freeze.py`，让改动在 code review 里显形。
"""

from __future__ import annotations

from typing import Any, Dict, List

#: 服务端标识。`serverInfo` 与包 `__version__` 共用这一份，避免三处版本各自漂移
#: （v0.15.0 W13 之前 pyproject=0.5.0 / __init__=0.5.0 / serverInfo=0.6.0 三不一致）。
SERVER_NAME = 'jikuai-lsp'
SERVER_VERSION = '0.15.0'

# LSP TextDocumentSyncKind：0=None / 1=Full / 2=Incremental
TEXT_DOCUMENT_SYNC_FULL = 1
TEXT_DOCUMENT_SYNC_INCREMENTAL = 2

#: 补全触发字符：`.` 用于 `模块.成员`，`，` 用于管道后候选。
COMPLETION_TRIGGER_CHARACTERS: List[str] = ['.', '，']

#: 极快 LSP 命令名（v0.15.0 W15）。命名规则：`极快.<动作>`——
#: `极快` 前缀避免与其它 LSP 服务器命名空间冲突（例如 Python 是 `python.`）。
COMMAND_SELECT_BLOCK = '极快.选块'

#: signatureHelp 触发字符：空格（动词后接参数的自然分隔符）。
#: 极快语法里动词调用形如 `加 1 2`，用户打完动词名后键入空格开始填参数，
#: 此时触发签名提示最自然。
SIGNATURE_HELP_TRIGGER_CHARACTERS: List[str] = [' ']

#: 纯数据能力声明；测试可直接断言 SERVER_CAPABILITIES['completionProvider']。
#:
#: 契约变更历史（改这里 = 改对外契约，必须过 freeze 测试）：
#:   v0.6.0  M5 F3 冻结点：textDocumentSync(Full) + completion + hover
#:   v0.15.0 W14：新增 definitionProvider；textDocumentSync.change 1 → 2
#:                （Incremental sync，配合 TextDocumentStore._apply_change）
#:   v0.15.0 W15：新增 executeCommandProvider（命令 `极快.选块`）
#:   v0.16.0 W32：新增 documentSymbolProvider + signatureHelpProvider
#:   v0.17.0 W38：新增 workspace.workspaceFolders（ADR-29 跨文件符号表前置）
SERVER_CAPABILITIES: Dict[str, Any] = {
    'textDocumentSync': {
        'openClose': True,
        # W14 起走增量同步。TextDocumentStore.did_change 对无 range 的 change
        # 会兜底为全文替换，因此声明 2 仍兼容规范里「客户端可发送 Full」的写法。
        'change': TEXT_DOCUMENT_SYNC_INCREMENTAL,
    },
    'completionProvider': {
        'resolveProvider': False,
        'triggerCharacters': list(COMPLETION_TRIGGER_CHARACTERS),
    },
    'hoverProvider': True,
    # W14：跳转到定义。只支持 `导入`/`从 … 导入` 语句里的点分块路径 → 块目录，
    # 用户符号跳转依赖 workspace 索引，留到后续版本。
    'definitionProvider': True,
    # W32：文档符号。单文件 AST 遍历，提取函数/类/导入三类符号。
    'documentSymbolProvider': True,
    # W32：签名帮助。复用 completion.py 的动词元数查询。
    'signatureHelpProvider': {
        'triggerCharacters': list(SIGNATURE_HELP_TRIGGER_CHARACTERS),
    },
    # W15：workspace/executeCommand。当前只暴露 `极快.选块`；未声明的 command
    # 由 server 层回 -32601 MethodNotFound，不静默返回 null。
    'executeCommandProvider': {
        'commands': [COMMAND_SELECT_BLOCK],
    },
    'diagnosticProvider': False,
    'positionEncoding': 'utf-16',
    # W38 ADR-29：声明支持 workspaceFolders。客户端会在 initialize params 里
    # 带 workspaceFolders 数组，服务端据此确定跨文件索引的扫描范围。
    # changeNotifications=True 让客户端在用户增删根目录时发
    # workspace/didChangeWorkspaceFolders 通知。
    'workspace': {
        'workspaceFolders': {
            'supported': True,
            'changeNotifications': True,
        },
    },
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
