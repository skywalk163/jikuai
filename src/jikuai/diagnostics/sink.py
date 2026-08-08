# -*- coding: utf-8 -*-
"""极快语言 · 诊断 Sink（ADR-14 · F1 冻结）。

Sink 是诊断的**汇聚点**：编译器（lexer / parser / evaluator）与 stdlib
在生成诊断时把对象 `emit` 给 Sink，由消费者（CLI / LSP）之后统一取用。

内建两个实现：
    - ``ListSink``：收集到内存列表，`drain()` 时按 sort_key 稳定排序返回并清空。
    - ``NullSink``：丢弃所有诊断，用于环境变量 ``JIKUAI_DIAGNOSTICS=off`` 场景，
      在需要极致性能或临时静默诊断输出时使用。

环境变量守护开关：
    ``JIKUAI_DIAGNOSTICS=off``  → `make_default_sink()` 返回 NullSink。
    其他任何值（含未设置）      → 返回 ListSink。
"""

from __future__ import annotations

import os
from typing import List, Protocol, runtime_checkable

from .model import Diagnostic


@runtime_checkable
class DiagnosticSink(Protocol):
    """诊断汇聚协议。实现方只需提供 ``emit``。"""

    def emit(self, diagnostic: Diagnostic) -> None: ...


class ListSink:
    """把诊断收集到内存列表；`drain()` 返回稳定排序快照并清空缓冲。"""

    __slots__ = ('_buf',)

    def __init__(self) -> None:
        self._buf: List[Diagnostic] = []

    def emit(self, diagnostic: Diagnostic) -> None:
        self._buf.append(diagnostic)

    def drain(self) -> List[Diagnostic]:
        """按 ``Diagnostic.sort_key`` 稳定排序后返回列表快照，并清空内部缓冲。

        决定性排序服务 AC-M4-01-03：同源码两次编译必须产出等价诊断序列。
        """
        snapshot = sorted(self._buf, key=Diagnostic.sort_key)
        self._buf = []
        return snapshot

    def peek(self) -> List[Diagnostic]:
        """预览当前缓冲内容而不清空。返回浅拷贝副本，避免调用方误改内部状态。"""
        return list(self._buf)

    def __len__(self) -> int:
        return len(self._buf)


class NullSink:
    """丢弃所有诊断的空实现；无 buffer 无副作用。"""

    __slots__ = ()

    def emit(self, diagnostic: Diagnostic) -> None:  # noqa: ARG002
        return None


def make_default_sink() -> DiagnosticSink:
    """按环境变量决定默认 Sink 实现。

    - ``JIKUAI_DIAGNOSTICS=off`` → NullSink（关闭诊断收集）
    - 其他                       → ListSink

    这是 G8 门禁「回退开关有守护」的新增守护点：
    `tests/test_env_switches.py` 会覆盖此函数三态。
    """
    value = os.environ.get("JIKUAI_DIAGNOSTICS", "").strip().lower()
    if value == "off":
        return NullSink()
    return ListSink()
