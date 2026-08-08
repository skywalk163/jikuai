# -*- coding: utf-8 -*-
"""极快语言 · LSP 桩 · 最小 JSON-RPC over stdio 传输层（ADR-15 · M4）。

LSP 底层是 JSON-RPC 2.0，消息以 HTTP 风格的头部 + JSON 体的帧格式承载：

    Content-Length: <字节数>\\r\\n
    \\r\\n
    <UTF-8 编码的 JSON 体>

本模块只做**帧的读写**，不含任何 LSP 语义（语义在 server.py）。
之所以自实现而非依赖 pygls：本机 pygls 为 2.x，API 与 M4 假设不符，
自实现可保证主包与 LSP 桩的物理隔离最干净（运行期 sys.modules 无 pygls），
且子进程协议测试的收发帧格式完全可控。M5 若切换 pygls，本层可整体替换。
"""

from __future__ import annotations

import json
from typing import Any, BinaryIO, Dict, Optional


def read_message(stream: BinaryIO) -> Optional[Dict[str, Any]]:
    """从二进制流读取一条 JSON-RPC 消息；流结束（EOF）时返回 None。

    严格按 LSP 帧格式解析：先逐行读头部直到空行，再按 Content-Length
    读取指定字节数的 JSON 体。Content-Length 缺失或非法视为协议错误，
    这里从宽处理为「按 0 字节读取」交由上层判空。
    """
    headers: Dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            # 头部尚未读完流就断了 —— 视为连接关闭
            return None
        # 头部按 ASCII 解码；空行（\r\n 或 \n）标志头部结束
        decoded = line.decode("ascii", errors="replace")
        if decoded in ("\r\n", "\n", "\r"):
            break
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    length = int(headers.get("content-length", "0") or "0")
    if length <= 0:
        return None
    body = _read_exact(stream, length)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def _read_exact(stream: BinaryIO, length: int) -> Optional[bytes]:
    """从流精确读取 length 字节；不足（EOF）返回 None。

    单次 read 在管道场景可能短读，故循环补齐，避免体被截断。
    """
    chunks = []
    remaining = length
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def write_message(stream: BinaryIO, message: Dict[str, Any]) -> None:
    """把一条 JSON-RPC 消息按帧格式写入二进制流并 flush。

    JSON 体用 UTF-8 且 ensure_ascii=False，保证中文诊断消息按原文传输；
    Content-Length 统计的是**字节数**而非字符数（含中文多字节）。
    """
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
    stream.write(header)
    stream.write(data)
    stream.flush()
