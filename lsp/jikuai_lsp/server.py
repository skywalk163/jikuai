# -*- coding: utf-8 -*-
"""极快语言 · LSP 服务器（v0.15.0 · W13 → W15）。

在 v0.6.0 M5 基础上扩展：
    - W14：`textDocument/definition`；`didChange` 走增量同步（sync_kind=2）
    - W15：`workspace/executeCommand` 命令 `极快.选块`（三通道协议 schema）
自实现 JSON-RPC over stdio（transport.py），不依赖 pygls。
主包 jikuai 惰性导入，保证物理隔离（sys.modules 无 pygls / jikuai_lsp 泄漏）。
"""

from __future__ import annotations

import sys
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, unquote

from .transport import read_message, write_message
from .capabilities import (
    server_capabilities, SERVER_NAME, SERVER_VERSION,
    TEXT_DOCUMENT_SYNC_INCREMENTAL, COMMAND_SELECT_BLOCK,
)

# LSP SymbolKind（仅列本文件用到的）。数值出自 LSP 3.17 规范表。
_SYMBOL_KIND_MODULE = 2
_SYMBOL_KIND_CLASS = 5
_SYMBOL_KIND_FUNCTION = 12

# JSON-RPC 错误码（子集，只列本文件用到的）
_ERR_METHOD_NOT_FOUND = -32601
_ERR_INVALID_PARAMS = -32602
_ERR_INTERNAL = -32603

# 惰性导入主包：运行期才需要，避免模块级副作用。
_jikuai_imported = False
_SessionHost = None
_TextDocumentStore = None
_utf16_to_codepoint = None
_codepoint_to_utf16 = None
_to_lsp_diagnostic = None
_complete_lsp = None
_verb_documentation = None
_keyword_documentation = None
_verb_arity_text = None
_ModuleLoader = None
_blocks_root = None
_ai_retrieval = None
_schema = None
_compile_source = None
_VERB_ARITY = None
_ADVERBS = None
_ast_FuncDef = None
_ast_ClassDef = None
_ast_Import = None


def _ensure_jikuai():
    """惰性导入主包的 service / completion / diagnostics / 块生态层。

    W14/W15 追加：`module_loader` / `pkg.blocks` / `ai.retrieval` / `service.schema`。
    W32 追加：`frontend.compile_source`（documentSymbol 走 AST）、
             `keywords.VERB_ARITY` + `completion.verb_arity_text`（signatureHelp
             复用 completion.py 已有的动词元数查询）、`ast_nodes` 里的三类节点。
    """
    global _jikuai_imported, _SessionHost, _TextDocumentStore
    global _utf16_to_codepoint, _codepoint_to_utf16, _to_lsp_diagnostic
    global _complete_lsp, _verb_documentation, _keyword_documentation
    global _verb_arity_text
    global _ModuleLoader, _blocks_root, _ai_retrieval, _schema
    global _compile_source, _VERB_ARITY, _ADVERBS
    global _ast_FuncDef, _ast_ClassDef, _ast_Import
    if _jikuai_imported:
        return
    from jikuai.service import SessionHost, TextDocumentStore
    from jikuai.service.position import utf16_to_codepoint, codepoint_to_utf16
    from jikuai.service import schema
    from jikuai.diagnostics.adapters import to_lsp_diagnostic
    from jikuai.completion import (
        complete_lsp, verb_documentation, keyword_documentation,
        verb_arity_text)
    from jikuai.module_loader import ModuleLoader
    from jikuai.pkg.blocks import blocks_root
    from jikuai.ai import retrieval as ai_retrieval
    from jikuai.frontend import compile_source
    from jikuai.keywords import VERB_ARITY, ADVERBS
    from jikuai.ast_nodes import FuncDef, ClassDef, Import
    _SessionHost = SessionHost
    _TextDocumentStore = TextDocumentStore
    _utf16_to_codepoint = utf16_to_codepoint
    _codepoint_to_utf16 = codepoint_to_utf16
    _to_lsp_diagnostic = to_lsp_diagnostic
    _complete_lsp = complete_lsp
    _verb_documentation = verb_documentation
    _keyword_documentation = keyword_documentation
    _verb_arity_text = verb_arity_text
    _ModuleLoader = ModuleLoader
    _blocks_root = blocks_root
    _ai_retrieval = ai_retrieval
    _schema = schema
    _compile_source = compile_source
    _VERB_ARITY = VERB_ARITY
    _ADVERBS = ADVERBS
    _ast_FuncDef = FuncDef
    _ast_ClassDef = ClassDef
    _ast_Import = Import
    _jikuai_imported = True


_TOKEN_BOUNDARY = set(' \t\r\n。，、：:；;=（）()【】[]「」{}<>+-*/%!?"\'`|&^~@$#\\')


def _token_at(line_text: str, cp_col: int) -> str:
    """取 1-based 码点列所在的 token（连续非边界字符段）。"""
    if not line_text:
        return ''
    idx = cp_col - 1
    if idx < 0:
        idx = 0
    if idx >= len(line_text):
        idx = len(line_text) - 1
    if line_text[idx] in _TOKEN_BOUNDARY:
        idx -= 1
        if idx < 0 or line_text[idx] in _TOKEN_BOUNDARY:
            return ''
    start = idx
    while start > 0 and line_text[start - 1] not in _TOKEN_BOUNDARY:
        start -= 1
    end = idx
    while end + 1 < len(line_text) and line_text[end + 1] not in _TOKEN_BOUNDARY:
        end += 1
    return line_text[start:end + 1]


# `导入 blocks.数据.方差` 或 `从 blocks.数据.方差 导入 离差`
# dotpath 由汉字/字母/数字/下划线/点组成；不吃末尾的中文句号。
_RE_IMPORT_DOTPATH = re.compile(
    r'(?:导入|从)\s*(?P<path>[^\s。，,]+)'
)


def _dotpath_at(line_text: str, cp_col: int) -> Optional[str]:
    """如果 1-based 码点列 cp_col 落在某个 `导入 x.y.z` 的 dotpath 上，返回该 dotpath。

    只识别含 `.` 的点分路径（扁平 `导入 x` 走 completion 就够了）。
    """
    if not line_text:
        return None
    # 目标位置索引（0-based，且要 clip 到行内）
    target = cp_col - 1
    if target < 0:
        target = 0
    if target >= len(line_text):
        target = len(line_text) - 1
    for m in _RE_IMPORT_DOTPATH.finditer(line_text):
        path = m.group('path')
        if '.' not in path:
            continue
        # dotpath 的实际字符范围
        start = m.start('path')
        end = m.end('path')
        if start <= target < end:
            return path
    return None


def _path_to_file_uri(p: str) -> str:
    """把绝对路径转成 file:// URI（跨平台，兼容 Windows 盘符）。"""
    return Path(p).resolve().as_uri()


def _uri_to_path(uri: str) -> Optional[str]:
    """把 file:// URI 还原成本机路径。非 file scheme 返回 None。"""
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme != 'file':
        return None
    path = unquote(parsed.path)
    # Windows 上 file:///C:/... 解析出的 path 会是 '/C:/...'，去掉前导斜杠
    if path.startswith('/') and len(path) >= 3 and path[2] == ':':
        path = path[1:]
    return path


# ───── W32 · documentSymbol / signatureHelp 纯函数工具 ─────

def _utf16_line_length(line_text: str) -> int:
    """行文本的 UTF-16 code unit 总宽度（BMP 外字符算 2）。"""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in line_text)


#: 语句终止符。signatureHelp 向前扫动词时不得跨越句号，
#: 否则 `打印 1。加 ` 会把上一句的 `打印` 当成当前签名。
_STATEMENT_ENDERS = '。;；'

#: 参数推进分隔符：空格 / 制表符 / 中文逗号（管道 `，` 会把左值作为下一动词首参）。
_ARG_SEPARATORS = ' \t，,'


def _split_tokens(text: str):
    """把文本切成 `(token, start_idx, end_idx)` 三元组（end 为开区间上界）。"""
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] in _TOKEN_BOUNDARY:
            i += 1
            continue
        start = i
        while i < n and text[i] not in _TOKEN_BOUNDARY:
            i += 1
        out.append((text[start:i], start, i))
    return out


def _find_verb_before(left: str, verb_arity: Dict[str, int]):
    """在光标左侧文本里找「当前动词调用」，返回 `(动词名, 动词结束索引)`。

    识别顺序（从光标端往左）：
      1. 先按语句终止符截断——不跨句取动词。
      2. token 整体命中 VERB_ARITY（`加 1 2` 里的 `加`）。
      3. token 以某个动词开头（免空格写法 `打印1` 里的 `打印`，取最长匹配）。
    都不命中返回 `(None, -1)`。
    """
    if not left:
        return None, -1
    # 只看最后一句
    cut = -1
    for ch in _STATEMENT_ENDERS:
        cut = max(cut, left.rfind(ch))
    offset = cut + 1
    segment = left[offset:]
    tokens = _split_tokens(segment)
    for token, start, end in reversed(tokens):
        if token in verb_arity:
            return token, offset + end
        # 免空格调用：动词紧贴参数（`打印1`）。取最长前缀匹配，避免
        # `大写` 抢在 `大写金额` 前面命中。
        best = None
        for length in range(len(token), 0, -1):
            candidate = token[:length]
            if candidate in verb_arity:
                best = candidate
                break
        if best is not None:
            return best, offset + start + len(best)
    return None, -1


def _count_arg_separators(tail: str) -> int:
    """由「动词末尾到光标之间的文本」推断 0-based activeParameter。

    规则：数已开始书写的参数个数。若 tail 以分隔符收尾，说明用户刚敲完
    分隔符、准备写下一个参数 → 索引 = 已写参数数；否则最后那个参数正在
    书写中 → 索引 = 已写参数数 - 1。
    """
    tokens = _split_tokens(tail)
    written = len(tokens)
    if tail and tail[-1] in _ARG_SEPARATORS:
        return written
    return max(0, written - 1)


def _build_signature_parameters(verb: str, arity: int,
                                is_adverb: bool) -> List[str]:
    """按动词元数生成参数标签列表。命名与 `completion.verb_documentation` 同构。"""
    if is_adverb or arity == -2:
        # 副词是高阶操作：`列 ...，皆<动词> [初值]`
        return ['<动词>', '[初值]']
    if arity == -1:
        return ['arg1', '…argN']
    if arity <= 0:
        return []
    return [f'arg{i + 1}' for i in range(arity)]


def _build_signature_label(verb: str, parameters: List[str],
                           arity_text: str = '') -> str:
    """签名首行：`加 arg1 arg2 · 2 元`。

    元数短语放在参数之后并用 `·` 隔开——参数标签走**子串匹配**定位，
    后缀里不含 `argN` 字样，不会误命中。
    """
    label = f'{verb} {" ".join(parameters)}' if parameters else verb
    if arity_text:
        label = f'{label} · {arity_text}'
    return label


class LspServer:
    """极快 LSP 服务器（v0.16.0 · W32）。

    支持：initialize / shutdown / exit / didOpen / didChange / didClose /
    textDocument/completion / textDocument/hover / textDocument/definition /
    textDocument/documentSymbol / textDocument/signatureHelp /
    workspace/executeCommand / publishDiagnostics。
    """

    def __init__(self, reader=None, writer=None):
        self._reader = reader or sys.stdin.buffer
        self._writer = writer or sys.stdout.buffer
        self._host = None          # SessionHost 惰性创建
        self._module_loader = None  # ModuleLoader 惰性创建
        self._initialized = False
        self._shutdown_requested = False
        self._running = True
        # W38 ADR-29：initialize 收到的 workspaceFolders uri 列表（多根）。
        # 无根（客户端直接打开单文件）时为空列表——不是 None，省掉调用方判空。
        self._workspace_folders: List[str] = []
        self._logger = logging.getLogger("jikuai_lsp")

    def _get_host(self):
        """惰性创建 SessionHost（首次使用时才导入主包）。"""
        if self._host is None:
            _ensure_jikuai()
            self._host = _SessionHost(_TextDocumentStore())
        return self._host

    def _get_module_loader(self):
        """惰性创建 ModuleLoader（definition 只用到 try_resolve，不需要 evaluator）。"""
        if self._module_loader is None:
            _ensure_jikuai()
            # try_resolve 只走 resolve() 的静态路径，不访问 evaluator，
            # 传 None 是安全的。真正的 load() 才需要 evaluator。
            self._module_loader = _ModuleLoader(evaluator=None)
        return self._module_loader

    # ───── 主循环 ─────

    def run(self) -> int:
        """运行 LSP 消息循环，返回退出码（0=正常退出）。"""
        while self._running:
            msg = read_message(self._reader)
            if msg is None:
                break
            try:
                self._dispatch(msg)
            except Exception as e:
                # 单条消息处理失败不终止服务；请求则回 InternalError
                self._logger.exception("dispatch 异常：%s", e)
                msg_id = msg.get("id")
                if msg_id is not None:
                    self._send_error(msg_id, _ERR_INTERNAL, f"内部错误：{e}")
        return 0

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        """路由一条 JSON-RPC 消息。"""
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            self._handle_initialize(msg_id, msg.get("params", {}))
        elif method == "initialized":
            self._initialized = True
        elif method == "shutdown":
            self._handle_shutdown(msg_id)
        elif method == "exit":
            self._running = False
        elif method == "textDocument/didOpen":
            self._handle_did_open(msg.get("params", {}))
        elif method == "textDocument/didChange":
            self._handle_did_change(msg.get("params", {}))
        elif method == "textDocument/didClose":
            self._handle_did_close(msg.get("params", {}))
        elif method == "textDocument/completion":
            self._handle_completion(msg_id, msg.get("params", {}))
        elif method == "textDocument/hover":
            self._handle_hover(msg_id, msg.get("params", {}))
        elif method == "textDocument/definition":
            self._handle_definition(msg_id, msg.get("params", {}))
        elif method == "textDocument/documentSymbol":
            self._handle_document_symbol(msg_id, msg.get("params", {}))
        elif method == "textDocument/signatureHelp":
            self._handle_signature_help(msg_id, msg.get("params", {}))
        elif method == "workspace/executeCommand":
            self._handle_execute_command(msg_id, msg.get("params", {}))
        elif method == "workspace/didChangeWorkspaceFolders":
            self._handle_did_change_workspace_folders(msg.get("params", {}))
        elif msg_id is not None:
            self._send_error(msg_id, _ERR_METHOD_NOT_FOUND,
                             f"方法未实现：{method}")
        # 未知通知静默忽略（LSP 规范要求）

    # ───── 生命周期 ─────

    def _handle_initialize(self, msg_id: Any, params: Dict) -> None:
        """返回 capabilities + serverInfo。serverInfo 版本源自 capabilities 模块。

        W38：解析 params.workspaceFolders，记录到 self._workspace_folders。
        后续 W39 的符号索引构建时会根据这些根目录递归扫描 .jk 文件。
        """
        # LSP 3.16+: workspaceFolders 在 InitializeParams 里（可能为 null）
        folders = params.get("workspaceFolders") or []
        self._workspace_folders = [
            f.get("uri", "") for f in folders if isinstance(f, dict)
        ]
        self._send_response(msg_id, {
            "capabilities": server_capabilities(),
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    def _handle_shutdown(self, msg_id: Any) -> None:
        self._shutdown_requested = True
        self._send_response(msg_id, None)

    def _handle_did_change_workspace_folders(self, params: Dict) -> None:
        """W38：增量增删 workspaceFolders（ADR-29 决策点 4「失效策略」）。

        规范里 `event.added` / `event.removed` 都是 WorkspaceFolder 数组。
        用增量增删而非整表替换：客户端只告诉你差量，重扫全部根没有必要，
        而且 W39 起索引按根挂条目，整表替换会把没动过的根白重建一遍。

        移除按 uri 相等匹配。顺序保持「先删后加」——同一个 uri 先 removed
        再 added（客户端重挂同一根目录）时结果是「在」，符合直觉。
        """
        event = params.get("event") or {}
        removed = {f.get("uri") for f in (event.get("removed") or [])
                   if isinstance(f, dict)}
        if removed:
            self._workspace_folders = [
                u for u in self._workspace_folders if u not in removed]
        for f in (event.get("added") or []):
            if not isinstance(f, dict):
                continue
            uri = f.get("uri", "")
            if uri and uri not in self._workspace_folders:
                self._workspace_folders.append(uri)

    # ───── 文本同步 ─────

    def _handle_did_open(self, params: Dict) -> None:
        td = params.get("textDocument", {})
        uri = td.get("uri", "")
        host = self._get_host()
        host.store.did_open(uri, td.get("text", ""), td.get("version", 0))
        host.invalidate(uri)
        self._publish_diagnostics(uri)

    def _handle_did_change(self, params: Dict) -> None:
        """W14：走 Incremental sync（sync_kind=2）。

        TextDocumentStore._apply_change 已实现按 UTF-16 range 增量替换。
        规范允许客户端在增量模式下发送**无 range** 的 change（表示全文替换），
        `did_change` 的增量分支已把这种情况兜底为整篇替换。因此原有
        `test_did_change_publishes_diagnostics`（无 range 全文替换）仍然绿。
        """
        td = params.get("textDocument", {})
        uri = td.get("uri", "")
        host = self._get_host()
        host.store.did_change(
            uri, td.get("version", 0), params.get("contentChanges", []),
            sync_kind=TEXT_DOCUMENT_SYNC_INCREMENTAL)
        host.invalidate(uri)
        self._publish_diagnostics(uri)

    def _handle_did_close(self, params: Dict) -> None:
        uri = params.get("textDocument", {}).get("uri", "")
        host = self._get_host()
        host.store.did_close(uri)
        host.invalidate(uri)
        # 清除编辑器上的诊断标记
        self._send_notification("textDocument/publishDiagnostics",
                                {"uri": uri, "diagnostics": []})

    # ───── 诊断推送 ─────

    def _publish_diagnostics(self, uri: str) -> None:
        """走 SessionHost.compile_and_diagnose 编译并推送诊断。

        正式路径可同时报告 ParseError（error）与 JK-W1001（副词透传警告）。
        列换算经 line_text_provider 走 UTF-16 口径。
        """
        host = self._get_host()
        out: List[Dict] = []
        try:
            diags = host.compile_and_diagnose(uri)
            provider = host.store.line_text_provider(uri)
            for d in diags:
                out.append(_to_lsp_diagnostic(d, line_text_provider=provider))
        except Exception as e:
            # frontend 未兜底的异常（如 ParseError）→ 投影 e.info 为最小诊断
            self._logger.debug("编译异常：%s", e)
            info = getattr(e, 'info', None)
            if info is not None:
                try:
                    from jikuai.diagnostics import codes as _codes
                    from jikuai.diagnostics.adapters import from_error_info
                    diag = from_error_info(info, _codes.JK_E1001)
                    provider = host.store.line_text_provider(uri)
                    out.append(_to_lsp_diagnostic(diag, line_text_provider=provider))
                except Exception:
                    pass
        self._send_notification("textDocument/publishDiagnostics",
                                {"uri": uri, "diagnostics": out})

    # ───── Completion ─────

    def _handle_completion(self, msg_id: Any, params: Dict) -> None:
        """LSP 位置 → service.position 换算 1-based 码点 → completion.complete。"""
        _ensure_jikuai()
        host = self._get_host()
        uri = params.get("textDocument", {}).get("uri", "")
        pos = params.get("position", {})
        lsp_line = int(pos.get("line", 0))
        lsp_char = int(pos.get("character", 0))
        text = host.store.get(uri) or ""
        lines = host.store.lines_of(uri) or []
        line_text = lines[lsp_line] if 0 <= lsp_line < len(lines) else ""
        cp_line = lsp_line + 1
        cp_col = _utf16_to_codepoint(line_text, lsp_char)
        items = _complete_lsp(text, cp_line, cp_col)
        self._send_response(msg_id, {"isIncomplete": False, "items": items})

    # ───── Hover ─────

    def _handle_hover(self, msg_id: Any, params: Dict) -> None:
        """光标下 token 是内建动词/关键字 → 返回中文说明；否则返回 null。"""
        _ensure_jikuai()
        host = self._get_host()
        uri = params.get("textDocument", {}).get("uri", "")
        pos = params.get("position", {})
        lsp_line = int(pos.get("line", 0))
        lsp_char = int(pos.get("character", 0))
        lines = host.store.lines_of(uri) or []
        if lsp_line < 0 or lsp_line >= len(lines):
            self._send_response(msg_id, None)
            return
        line_text = lines[lsp_line]
        cp_col = _utf16_to_codepoint(line_text, lsp_char)
        token = _token_at(line_text, cp_col)
        if not token:
            self._send_response(msg_id, None)
            return
        doc = _verb_documentation(token)
        if doc is None:
            doc = _keyword_documentation(token)
        if doc is None:
            self._send_response(msg_id, None)
            return
        self._send_response(msg_id, {
            "contents": {"kind": "markdown", "value": doc},
        })

    # ───── Definition（W14） ─────

    def _block_dir_of(self, dotpath: str) -> Optional[str]:
        """`blocks.<领域>.<块名>` → `blocks_root()/<领域>/<块名>/`（存在才返回）。

        这是 WBS W14 指定的内建块映射路径。用户块（工作区里自己写的
        `<领域>/<块名>/`）不走这里，交给 `try_resolve` 按文档目录搜索。
        """
        parts = dotpath.split('.')
        if len(parts) != 3 or parts[0] != 'blocks':
            return None
        领域, 块名 = parts[1], parts[2]
        if not 领域 or not 块名:
            return None
        候选目录 = Path(_blocks_root()) / 领域 / 块名
        return str(候选目录) if 候选目录.is_dir() else None

    @staticmethod
    def _block_entry_in(块目录: str, 块名: str) -> Optional[str]:
        """块目录里的主文件：`<块名>.jk` 优先，`main.jk` 兜底。

        LSP `Location.uri` 必须指向**可打开的文档**——编辑器对目录 URI 会报
        「Unable to open: is a directory」。所以这里把 WBS 说的「块目录」
        落到该目录的入口 `.jk` 上，跳转才真正可用。
        """
        # 与 blocks_cli._主jk 同源逻辑；W16 时可下沉到 jikuai.pkg.blocks 共享。
        d = Path(块目录)
        for 名 in (块名 + '.jk', 'main.jk'):
            p = d / 名
            if p.is_file():
                return str(p)
        return None

    def _handle_definition(self, msg_id: Any, params: Dict) -> None:
        """`导入 blocks.<领域>.<块>` / `从 <点分路径> 导入 X` → 块入口文件 URI。

        解析顺序：
          1. 光标必须压在含 `.` 的 dotpath 上，否则 null。
          2. `blocks.<领域>.<块名>` → `blocks_root()/<领域>/<块名>/` 的入口 `.jk`。
          3. 否则回落 `ModuleLoader.try_resolve`（覆盖用户块 / 三级 dotpath 优先级）。
          4. 都不命中 → null。
        """
        _ensure_jikuai()
        host = self._get_host()
        uri = params.get("textDocument", {}).get("uri", "")
        pos = params.get("position", {})
        lsp_line = int(pos.get("line", 0))
        lsp_char = int(pos.get("character", 0))
        lines = host.store.lines_of(uri) or []
        if lsp_line < 0 or lsp_line >= len(lines):
            self._send_response(msg_id, None)
            return
        line_text = lines[lsp_line]
        cp_col = _utf16_to_codepoint(line_text, lsp_char)
        dotpath = _dotpath_at(line_text, cp_col)
        if not dotpath:
            self._send_response(msg_id, None)
            return

        目标 = None
        try:
            块目录 = self._block_dir_of(dotpath)
            if 块目录:
                目标 = self._block_entry_in(块目录, dotpath.split('.')[-1])
            if not 目标:
                # current_file 从 uri 反推：ModuleLoader 以该目录为首个搜索路径，
                # 用户块（工作区自建）由此命中。
                loader = self._get_module_loader()
                目标 = loader.try_resolve(
                    dotpath, current_file=_uri_to_path(uri))
        except Exception as e:
            self._logger.debug("definition 解析失败：%s", e)
            目标 = None
        if not 目标:
            self._send_response(msg_id, None)
            return
        self._send_response(msg_id, {
            "uri": _path_to_file_uri(目标),
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 0},
            },
        })

    # ───── DocumentSymbol（W32） ─────

    def _handle_document_symbol(self, msg_id: Any, params: Dict) -> None:
        """遍历 AST 提取顶层三类符号：函数（FuncDef）、类（ClassDef）、导入（Import）。

        返回 LSP `DocumentSymbol[]`（包含 `name`/`kind`/`range`/`selectionRange`）。
        单文件即可，不做跨文件符号索引（rename/references 依赖这层跨文件表，
        本轮不实现，见 README 缺口表 v0.17.0 计划）。

        - 排序：按 AST 顺序，即源码书写顺序（`Program.body` 天然有序）。
        - 编译失败：兜底走 `parse_with_import_whitelist`，实在拿不到 AST 就返回 []。
        - Range/UTF-16：AST 携带 1-based 码点行列；用 `codepoint_to_utf16` 换算，
          与 LSP 其它 handler 口径一致。
        """
        _ensure_jikuai()
        host = self._get_host()
        uri = params.get("textDocument", {}).get("uri", "")
        text = host.store.get(uri) or ""
        lines = host.store.lines_of(uri) or []
        # 空文档 / 无行 → 空符号列表
        if not lines:
            self._send_response(msg_id, [])
            return
        ast = self._try_get_ast(text, uri)
        if ast is None or not getattr(ast, 'body', None):
            self._send_response(msg_id, [])
            return
        symbols: List[Dict] = []
        for node in ast.body:
            sym = self._ast_node_to_symbol(node, lines)
            if sym is not None:
                symbols.append(sym)
        self._send_response(msg_id, symbols)

    def _try_get_ast(self, text: str, uri: str):
        """尽力拿到 AST；`compile_source` 内部对 ParseError 已兜底转诊断，
        绝大多数情况下仍能返回合法 Program。彻底崩溃则返回 None。
        """
        try:
            result = _compile_source(text, file=uri)
            return result.ast
        except Exception as e:
            self._logger.debug("documentSymbol 编译失败：%s", e)
            return None

    def _ast_node_to_symbol(self, node, lines: List[str]) -> Optional[Dict]:
        """把一个顶层 AST 节点投影成 LSP DocumentSymbol；不认识返回 None。"""
        if isinstance(node, _ast_FuncDef):
            return self._make_symbol(
                name=node.name,
                kind=_SYMBOL_KIND_FUNCTION,
                start_line=getattr(node, 'line', 0) or 1,
                start_col=getattr(node, 'col', 0) or 1,
                end_line=getattr(node, 'end_line', 0)
                or (getattr(node, 'line', 0) or 1),
                lines=lines,
                selection_name=node.name,
            )
        if isinstance(node, _ast_ClassDef):
            return self._make_symbol(
                name=node.name,
                kind=_SYMBOL_KIND_CLASS,
                start_line=getattr(node, 'line', 0) or 1,
                start_col=getattr(node, 'col', 0) or 1,
                # ClassDef 由 parser 显式标注 end_line（收尾 `。` 那一行）
                end_line=getattr(node, 'end_line', 0)
                or (getattr(node, 'line', 0) or 1),
                lines=lines,
                selection_name=node.name,
            )
        if isinstance(node, _ast_Import):
            # module 允许 `蟒:os.path` / `blocks.数据.求和` 等；直接用作符号名。
            module = getattr(node, 'module', '') or ''
            if not module:
                return None
            return self._make_symbol(
                name=module,
                kind=_SYMBOL_KIND_MODULE,
                start_line=getattr(node, 'line', 0) or 1,
                start_col=getattr(node, 'col', 0) or 1,
                end_line=getattr(node, 'line', 0) or 1,
                lines=lines,
                selection_name=module,
            )
        return None

    def _make_symbol(self, name: str, kind: int, start_line: int, start_col: int,
                     end_line: int, lines: List[str],
                     selection_name: str) -> Dict:
        """构造 DocumentSymbol 字典。

        坐标换算：AST 是 1-based 码点，LSP 是 0-based UTF-16。
        Range：起点 = 声明关键字位置（如 `函数`/`类`/`导入`），
               终点 = end_line 行末（若 end_line == start_line 则同一行行末）。
        SelectionRange：尝试在起始行内定位 selection_name 的位置，命中就用；
        否则退化为 Range 起点后一位。
        """
        total_lines = len(lines)
        s_line = max(1, min(start_line, total_lines))
        e_line = max(s_line, min(end_line, total_lines))
        start_line_text = lines[s_line - 1]
        end_line_text = lines[e_line - 1]
        start_char_utf16 = _codepoint_to_utf16(start_line_text, start_col)
        end_char_utf16 = _utf16_line_length(end_line_text)
        # 定位选择范围（selectionRange）
        sel_line = s_line
        sel_col_cp = start_col           # 兜底：从声明关键字开始
        try:
            idx = start_line_text.find(selection_name, start_col - 1)
            if idx < 0:
                # 声明关键字在关键字位置右侧找不到 name，退化到全行搜索
                idx = start_line_text.find(selection_name)
            if idx >= 0:
                sel_col_cp = idx + 1
        except Exception:
            pass
        sel_start_utf16 = _codepoint_to_utf16(start_line_text, sel_col_cp)
        sel_end_utf16 = _codepoint_to_utf16(
            start_line_text, sel_col_cp + len(selection_name))
        return {
            "name": name,
            "kind": kind,
            "range": {
                "start": {"line": s_line - 1, "character": start_char_utf16},
                "end": {"line": e_line - 1, "character": end_char_utf16},
            },
            "selectionRange": {
                "start": {"line": sel_line - 1, "character": sel_start_utf16},
                "end": {"line": sel_line - 1, "character": sel_end_utf16},
            },
        }

    # ───── SignatureHelp（W32） ─────

    def _handle_signature_help(self, msg_id: Any, params: Dict) -> None:
        """光标处若在内建动词调用范围内 → 返回 SignatureHelp。

        识别策略（保守而稳定，不依赖 AST）：
          1. 取光标所在行 0..col-1 之间的文本片段。
          2. 从该片段的**光标端**向前扫，找出第一个 VERB_ARITY 里的名字。
             找不到 → null。
          3. 命中的动词为「当前签名」，参数列表按 arity 生成 `arg1..argN`
             （或 `-1` 时 `arg1..`）。副词按副词签名给出。
          4. 通过统计动词末尾到光标之间的**空格/中文逗号**次数推断
             `activeParameter`（管道 `，` 也算参数推进——极快语法里管道左侧
             会作为下一动词的隐式首参）。
        """
        _ensure_jikuai()
        host = self._get_host()
        uri = params.get("textDocument", {}).get("uri", "")
        pos = params.get("position", {})
        lsp_line = int(pos.get("line", 0))
        lsp_char = int(pos.get("character", 0))
        lines = host.store.lines_of(uri) or []
        if lsp_line < 0 or lsp_line >= len(lines):
            self._send_response(msg_id, None)
            return
        line_text = lines[lsp_line]
        cp_col = _utf16_to_codepoint(line_text, lsp_char)
        # cp_col 是 1-based；取光标左侧内容（不含光标处字符）
        left = line_text[:max(0, cp_col - 1)]
        verb, verb_end_idx = _find_verb_before(left, _VERB_ARITY)
        if verb is None:
            self._send_response(msg_id, None)
            return
        arity = _VERB_ARITY.get(verb, 0)
        is_adverb = verb in _ADVERBS
        parameters = _build_signature_parameters(verb, arity, is_adverb)
        # 元数短语直接复用 completion.py 的 `verb_arity_text`（只读查询）
        label = _build_signature_label(verb, parameters, _verb_arity_text(verb))
        # activeParameter：数动词末尾到光标之间的分隔符个数
        tail = left[verb_end_idx:]
        active = _count_arg_separators(tail)
        # 副词或可变元数：最后一个 parameter 也可持续高亮
        if parameters:
            active = min(active, max(0, len(parameters) - 1))
        else:
            active = 0
        signature = {
            "label": label,
            "documentation": {
                "kind": "markdown",
                "value": (_verb_documentation(verb)
                          or f"**{verb}** —— 内建动词"),
            },
            "parameters": [{"label": p} for p in parameters],
        }
        self._send_response(msg_id, {
            "signatures": [signature],
            "activeSignature": 0,
            "activeParameter": active,
        })

    # ───── ExecuteCommand（W15） ─────

    def _handle_execute_command(self, msg_id: Any, params: Dict) -> None:
        """`workspace/executeCommand`。

        分派表当前只有一条：`极快.选块`。未知命令回 -32601，
        与「dispatch 里未实现的 method」保持同一语义，不静默返回 null。
        """
        _ensure_jikuai()
        command = params.get("command")
        arguments = params.get("arguments") or []
        if command == COMMAND_SELECT_BLOCK:
            self._exec_select_block(msg_id, arguments)
            return
        self._send_error(msg_id, _ERR_METHOD_NOT_FOUND,
                         f"未知命令：{command!r}")

    def _exec_select_block(self, msg_id: Any, arguments: List[Any]) -> None:
        """`极快.选块`：`{需求, top?}` → 协议 `选响应` 信封 `{需求, 候选[]}`。

        入参约定：`arguments[0]` 是一份 dict，字段与 `jk 块 选 --json` 的
        请求语义对齐。返回结构完全走 `service.schema.make_select_envelope` +
        `candidate_from_hit`，字段与 CLI / Web 输出逐字一致——这是三通道协议
        同构的关键（v0.15.0 W20，见 `docs/协议-三通道.md`）。

        本命令不走神经检索（LSP 里起 sidecar 子进程会拖住编辑器响应），
        所以永远不带 `降级说明`。
        """
        if not arguments or not isinstance(arguments[0], dict):
            self._send_error(msg_id, _ERR_INVALID_PARAMS,
                             "arguments 需为 `[{需求: str, top?: int}]`")
            return
        payload = arguments[0]
        需求 = payload.get('需求')
        if not isinstance(需求, str) or not 需求.strip():
            self._send_error(msg_id, _ERR_INVALID_PARAMS,
                             "缺少非空「需求」字段")
            return
        top_raw = payload.get('top', 5)
        try:
            top = int(top_raw)
        except (TypeError, ValueError):
            self._send_error(msg_id, _ERR_INVALID_PARAMS,
                             "top 必须是正整数")
            return
        if top <= 0:
            self._send_error(msg_id, _ERR_INVALID_PARAMS,
                             "top 必须是正整数")
            return
        try:
            hits = _ai_retrieval.retrieve(需求, top=top)
        except Exception as e:
            self._send_error(msg_id, _ERR_INTERNAL,
                             f"检索失败：{e}")
            return
        候选 = [_schema.candidate_from_hit(h) for h in hits]
        self._send_response(msg_id, _schema.make_select_envelope(需求, 候选))

    # ───── JSON-RPC 发送 ─────

    def _send_response(self, msg_id: Any, result: Any) -> None:
        write_message(self._writer,
                      {"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _send_error(self, msg_id: Any, code: int, message: str) -> None:
        write_message(self._writer, {
            "jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}})

    def _send_notification(self, method: str, params: Dict) -> None:
        write_message(self._writer,
                      {"jsonrpc": "2.0", "method": method, "params": params})


def main() -> int:
    """入口：启动 LSP 服务器，返回退出码。"""
    logging.basicConfig(
        level=logging.DEBUG,
        format="[jikuai-lsp] %(levelname)s %(message)s",
        stream=sys.stderr,  # LSP 规范：stdout 留给协议，日志走 stderr
    )
    return LspServer().run()
