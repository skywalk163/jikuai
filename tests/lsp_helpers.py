# -*- coding: utf-8 -*-
"""LSP 协议测试共享辅助模块（v0.15.0 W13）。

从 test_v0_5_0_lsp_stub.py 精简而来的 helper 集，新测试文件共用。
不依赖 test_v0_5_0_lsp_stub.py 内部实现，避免脆弱跨文件耦合。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Callable, Dict, Optional

# ---------------------------------------------------------------------------
# 路径定位
# ---------------------------------------------------------------------------

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, 'src')
_LSP = os.path.join(_ROOT, 'lsp')
sys.path.insert(0, _SRC)
sys.path.insert(0, _LSP)


# ---------------------------------------------------------------------------
# 帧读写
# ---------------------------------------------------------------------------

def write_frame(stream, obj: Dict[str, Any]) -> None:
    """把 dict 序列化并按 LSP 帧格式写入 stream。"""
    body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    header = f"Content-Length: {len(body)}\r\n\r\n".encode('ascii')
    stream.write(header)
    stream.write(body)
    stream.flush()


def read_frame(stream, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """从 stream 读取一条 LSP 帧；超时或流关闭返回 None。"""
    deadline = time.time() + timeout
    headers: Dict[str, str] = {}
    while True:
        if time.time() > deadline:
            return None
        line = stream.readline()
        if not line:
            return None
        try:
            decoded = line.decode('ascii', errors='replace')
        except Exception:
            return None
        if decoded in ('\r\n', '\n', '\r'):
            break
        if ':' in decoded:
            k, v = decoded.split(':', 1)
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get('content-length', '0') or '0')
    if length <= 0:
        return None
    body = b''
    remaining = length
    while remaining > 0:
        if time.time() > deadline:
            return None
        chunk = stream.read(remaining)
        if not chunk:
            return None
        body += chunk
        remaining -= len(chunk)
    return json.loads(body.decode('utf-8'))


def read_until(stream, predicate: Callable[[Dict], bool],
               max_msgs: int = 10, timeout: float = 5.0) -> Optional[Dict]:
    """连续读消息直到 predicate(msg) 为真；返回该消息。"""
    for _ in range(max_msgs):
        msg = read_frame(stream, timeout=timeout)
        if msg is None:
            return None
        if predicate(msg):
            return msg
    return None


# ---------------------------------------------------------------------------
# 子进程创建
# ---------------------------------------------------------------------------

def start_lsp_process() -> subprocess.Popen:
    """启动 `python -m jikuai_lsp` 子进程。"""
    env = os.environ.copy()
    existing = env.get('PYTHONPATH', '')
    parts = [_SRC, _LSP]
    if existing:
        parts.append(existing)
    env['PYTHONPATH'] = os.pathsep.join(parts)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    return subprocess.Popen(
        [sys.executable, '-m', 'jikuai_lsp'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        bufsize=0,
    )


def stop_lsp_process(proc: subprocess.Popen) -> None:
    """安全停止 LSP 子进程。"""
    if proc.poll() is None:
        try:
            write_frame(proc.stdin, {"jsonrpc": "2.0", "method": "exit"})
        except Exception:
            pass
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)


# ---------------------------------------------------------------------------
# 协议对话辅助
# ---------------------------------------------------------------------------

def initialize(proc, msg_id: int = 1, workspace_folders=None) -> Optional[Dict]:
    """发送 initialize 请求并返回响应体。

    workspace_folders：可选 `[{"uri": ..., "name": ...}]`（W54 多根 workspace 测试用）。
    传 None 时不带该字段，等价于单根/无根客户端。
    """
    params: Dict[str, Any] = {
        "processId": None,
        "rootUri": None,
        "capabilities": {},
    }
    if workspace_folders is not None:
        params["workspaceFolders"] = workspace_folders
    write_frame(proc.stdin, {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "initialize",
        "params": params,
    })
    return read_until(
        proc.stdout,
        lambda m: m.get('id') == msg_id and 'result' in m,
        max_msgs=10,
    )


def initialized(proc) -> None:
    """发送 initialized 通知。"""
    write_frame(proc.stdin, {
        "jsonrpc": "2.0",
        "method": "initialized",
        "params": {},
    })


def did_open(proc, uri: str, text: str, version: int = 1) -> None:
    """发送 textDocument/didOpen 并消费对应的 publishDiagnostics。"""
    write_frame(proc.stdin, {
        "jsonrpc": "2.0",
        "method": "textDocument/didOpen",
        "params": {
            "textDocument": {
                "uri": uri,
                "languageId": "jikuai",
                "version": version,
                "text": text,
            }
        },
    })


def wait_diagnostics(proc, uri: str, timeout: float = 5.0) -> Optional[Dict]:
    """等待并返回指定 URI 的 publishDiagnostics 通知。"""
    return read_until(
        proc.stdout,
        lambda m: (m.get('method') == 'textDocument/publishDiagnostics'
                   and m.get('params', {}).get('uri') == uri),
        max_msgs=10,
        timeout=timeout,
    )


def request_completion(proc, uri: str, line: int, character: int,
                       msg_id: int = 10) -> Optional[Dict]:
    """发 textDocument/completion 请求，返回响应。"""
    write_frame(proc.stdin, {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "textDocument/completion",
        "params": {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        },
    })
    return read_until(
        proc.stdout,
        lambda m: m.get('id') == msg_id and ('result' in m or 'error' in m),
        max_msgs=10,
    )


def request_hover(proc, uri: str, line: int, character: int,
                  msg_id: int = 20) -> Optional[Dict]:
    """发 textDocument/hover 请求，返回响应。"""
    write_frame(proc.stdin, {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "textDocument/hover",
        "params": {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        },
    })
    return read_until(
        proc.stdout,
        lambda m: m.get('id') == msg_id and ('result' in m or 'error' in m),
        max_msgs=10,
    )


def request_definition(proc, uri: str, line: int, character: int,
                       msg_id: int = 30) -> Optional[Dict]:
    """发 textDocument/definition 请求，返回响应。"""
    write_frame(proc.stdin, {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "textDocument/definition",
        "params": {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        },
    })
    return read_until(
        proc.stdout,
        lambda m: m.get('id') == msg_id and ('result' in m or 'error' in m),
        max_msgs=10,
    )


def request_execute_command(proc, command: str, arguments: Any = None,
                            msg_id: int = 40) -> Optional[Dict]:
    """发 workspace/executeCommand 请求，返回响应。"""
    params: Dict[str, Any] = {"command": command}
    if arguments is not None:
        params["arguments"] = arguments
    write_frame(proc.stdin, {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "workspace/executeCommand",
        "params": params,
    })
    return read_until(
        proc.stdout,
        lambda m: m.get('id') == msg_id and ('result' in m or 'error' in m),
        max_msgs=10,
    )
