# -*- coding: utf-8 -*-
"""极快语言 · DAP 适配器 · JSON over stdio 帧传输层（M6-P3 · T-M6-D02）。

DAP 与 LSP 使用同一种帧格式（HTTP 风格头 + JSON 体）：

    Content-Length: <字节数>\\r\\n
    \\r\\n
    <UTF-8 编码的 JSON 体>

本模块只做帧的读写，不含任何 DAP 语义（语义在 adapter.py）。
按支线隔离要求，本文件不 import `jikuai_lsp`，思路虽复制但物理独立。
"""

from __future__ import annotations

import json
from typing import Any, BinaryIO, Dict, Optional


def read_message(stream: BinaryIO) -> Optional[Dict[str, Any]]:
    """从二进制流读取一条 DAP 消息；流结束（EOF）时返回 None。

    严格按帧格式解析：先逐行读头部直到空行，再按 Content-Length
    读取指定字节数的 JSON 体。Content-Length 缺失或非法视为协议错误，
    这里从宽处理为「按 0 字节读取」交由上层判空。
    """
    headers: Dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            # 头部尚未读完流就断了 —— 视为连接关闭
            return None
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
    """从流精确读取 length 字节；不足（EOF）返回 None。"""
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
    """把一条 DAP 消息按帧格式写入二进制流并 flush。"""
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
    stream.write(header)
    stream.write(data)
    stream.flush()
