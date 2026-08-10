# -*- coding: utf-8 -*-
"""v0.17.0 W38 · LSP workspaceFolders 接线测试（ADR-29 决策点 2）。

覆盖 ADR-29「索引范围」定的三种情形 + 动态增删根：
    单根 / 多根 / 无根 / didChangeWorkspaceFolders 增 / 删 / 增删混合

为什么走**进程内**直测 `LspServer` 而不是起子进程：本周只接线 `workspaceFolders`
的**状态记录**，没有对外可观测的响应变化（capabilities 由
`test_lsp_capabilities_freeze` 守）。直测 `_workspace_folders` 比让子进程把
内部状态回吐出来更直接，也不用为测试在协议里开后门。
"""

from __future__ import annotations

import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))
sys.path.insert(0, os.path.join(_ROOT, 'lsp'))

from jikuai_lsp.server import LspServer


def _server() -> LspServer:
    """造一个只写不读的 LspServer；initialize 的响应丢进 BytesIO 不校验。"""
    return LspServer(reader=io.BytesIO(), writer=io.BytesIO())


def _folder(uri: str, name: str = '') -> dict:
    return {'uri': uri, 'name': name or uri.rsplit('/', 1)[-1]}


# ---- initialize 三种情形 ----------------------------------------------

def test_单根():
    """最常见形态：客户端给一个 workspaceFolder。"""
    s = _server()
    s._handle_initialize(1, {'workspaceFolders': [_folder('file:///proj')]})
    assert s._workspace_folders == ['file:///proj']


def test_多根():
    """多根按客户端给的顺序记录——扫描顺序影响同名符号的定义排序，是契约。"""
    s = _server()
    s._handle_initialize(1, {'workspaceFolders': [
        _folder('file:///a'), _folder('file:///b'), _folder('file:///c'),
    ]})
    assert s._workspace_folders == ['file:///a', 'file:///b', 'file:///c']


def test_无根_字段缺失():
    """客户端直接打开单文件时不带 workspaceFolders → 空列表，不是 None。"""
    s = _server()
    s._handle_initialize(1, {'capabilities': {}})
    assert s._workspace_folders == []


def test_无根_字段为null():
    """规范允许 workspaceFolders 显式为 null，同样落空列表。"""
    s = _server()
    s._handle_initialize(1, {'workspaceFolders': None})
    assert s._workspace_folders == []


def test_脏数据被过滤():
    """数组里混进非对象项不能把 initialize 打挂——降级为忽略该项。"""
    s = _server()
    s._handle_initialize(1, {'workspaceFolders': [
        _folder('file:///ok'), 'file:///裸字符串', None, 42,
    ]})
    assert s._workspace_folders == ['file:///ok']


def test_initialize_仍然回capabilities():
    """接线不能影响 initialize 的既有响应——workspace 能力必须在里面。"""
    buf = io.BytesIO()
    s = LspServer(reader=io.BytesIO(), writer=buf)
    s._handle_initialize(1, {'workspaceFolders': [_folder('file:///proj')]})
    payload = buf.getvalue().decode('utf-8')
    assert '"capabilities"' in payload
    assert 'workspaceFolders' in payload
    assert 'serverInfo' in payload


# ---- workspace/didChangeWorkspaceFolders 动态增删 ----------------------

def test_动态增根():
    s = _server()
    s._handle_initialize(1, {'workspaceFolders': [_folder('file:///a')]})
    s._handle_did_change_workspace_folders(
        {'event': {'added': [_folder('file:///b')], 'removed': []}})
    assert s._workspace_folders == ['file:///a', 'file:///b']


def test_动态删根():
    s = _server()
    s._handle_initialize(1, {'workspaceFolders': [
        _folder('file:///a'), _folder('file:///b')]})
    s._handle_did_change_workspace_folders(
        {'event': {'added': [], 'removed': [_folder('file:///a')]}})
    assert s._workspace_folders == ['file:///b']


def test_动态增删混合_先删后加():
    """同一 uri 先 removed 再 added（客户端重挂同一根）结果应是「在」。"""
    s = _server()
    s._handle_initialize(1, {'workspaceFolders': [_folder('file:///a')]})
    s._handle_did_change_workspace_folders({'event': {
        'added': [_folder('file:///a'), _folder('file:///c')],
        'removed': [_folder('file:///a')],
    }})
    assert s._workspace_folders == ['file:///a', 'file:///c']


def test_动态增根不重复():
    """已在表里的 uri 再 added 一次不该出现两条——索引会按根挂条目，重复即双计。"""
    s = _server()
    s._handle_initialize(1, {'workspaceFolders': [_folder('file:///a')]})
    s._handle_did_change_workspace_folders(
        {'event': {'added': [_folder('file:///a')], 'removed': []}})
    assert s._workspace_folders == ['file:///a']


def test_删不存在的根是无操作():
    s = _server()
    s._handle_initialize(1, {'workspaceFolders': [_folder('file:///a')]})
    s._handle_did_change_workspace_folders(
        {'event': {'added': [], 'removed': [_folder('file:///不存在')]}})
    assert s._workspace_folders == ['file:///a']


def test_空event不炸():
    """通知体缺 event 或为空时静默无操作（LSP 通知不许回错误）。"""
    s = _server()
    s._handle_initialize(1, {'workspaceFolders': [_folder('file:///a')]})
    s._handle_did_change_workspace_folders({})
    s._handle_did_change_workspace_folders({'event': {}})
    assert s._workspace_folders == ['file:///a']


def test_dispatch_路由到workspaceFolders变更():
    """`workspace/didChangeWorkspaceFolders` 必须真的接进 _dispatch。

    不接进 dispatch 的 handler 等于没写——这条守的是接线本身。
    """
    s = _server()
    s._handle_initialize(1, {'workspaceFolders': [_folder('file:///a')]})
    s._dispatch({
        'jsonrpc': '2.0',
        'method': 'workspace/didChangeWorkspaceFolders',
        'params': {'event': {'added': [_folder('file:///b')], 'removed': []}},
    })
    assert s._workspace_folders == ['file:///a', 'file:///b']
