# -*- coding: utf-8 -*-
"""v0.15.0 W14 · LSP textDocument/definition 协议级测试。

覆盖：内建块跳转、用户块跳转（ModuleLoader 回退路径）、不存在的块返回 null。

v0.18.0 W54 追加 `TestDefinitionMultiRoot`：multi-root workspace 的块路径解析
（声明顺序第一个赢 / 第二根兜底 / 单根不变 / 根外不解析 / 内建不被遮蔽）。
"""

from __future__ import annotations

import os
import sys
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))
sys.path.insert(0, os.path.join(_ROOT, 'lsp'))
sys.path.insert(0, os.path.join(_ROOT, 'tests'))

from lsp_helpers import (
    start_lsp_process, stop_lsp_process, initialize, initialized,
    did_open, wait_diagnostics, request_definition,
)


def _lsp_available():
    try:
        import importlib
        importlib.import_module('jikuai_lsp')
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _lsp_available(),
    reason="jikuai_lsp 依赖不可用，跳过 LSP definition 测试",
)


@pytest.fixture
def lsp():
    proc = start_lsp_process()
    resp = initialize(proc)
    assert resp is not None
    initialized(proc)
    yield proc
    stop_lsp_process(proc)


class TestDefinitionBuiltinBlock:
    """内建块（stdlib/blocks/ 下已有的块）跳转。"""

    def test_definition_block_data_variance(self, lsp):
        """从 `从 blocks.数据.方差 导入 离差` 跳转到方差.jk。"""
        uri = "file:///tmp/def_block.jk"
        src = "从 blocks.数据.方差 导入 离差\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        # 光标在 blocks.数据.方差 的 '方' 上：'从 blocks.数据.方差 ...'
        # '从 ' = 2 码点，然后 'blocks.数据.方差' 从 char 2 开始
        # 'blocks.数据.方差' 共 10 码点 → char 2..11
        # '方' 在 char 9
        resp = request_definition(lsp, uri, line=0, character=9, msg_id=300)
        assert resp is not None
        result = resp.get('result')
        assert result is not None, "内建块应返回 definition Location"
        assert 'uri' in result
        # URI 应以 方差.jk 结尾
        assert result['uri'].endswith('.jk'), f"URI 应指向 .jk 文件：{result['uri']}"
        assert '方差' in result['uri'] or '%E6%96%B9%E5%B7%AE' in result['uri']

    def test_definition_block_with_from_import(self, lsp):
        """`导入 blocks.数据.求和` 也能跳转。"""
        uri = "file:///tmp/def_block2.jk"
        src = "导入 blocks.数据.求和\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        # '导入 ' = 3 码点，'blocks.数据.求和' 从 char 3 开始
        # 'blocks' 3..8, '.' 9, '数据' 10..11, '.' 12, '求和' 13..14
        resp = request_definition(lsp, uri, line=0, character=13, msg_id=301)
        assert resp is not None
        result = resp.get('result')
        assert result is not None
        assert result['uri'].endswith('.jk')


class TestDefinitionUserBlock:
    """用户块（工作区自建，走 ModuleLoader 回退路径）跳转。"""

    def test_definition_user_block_in_workspace(self, lsp, tmp_path):
        """文档同目录下的 blocks/<领域>/<块>/<块>.jk 也能跳转。

        `blocks_root()` 里没有这个领域，所以必然走 `try_resolve` 回退分支，
        以文档自身目录为搜索起点——这条路径是「用户块」的唯一依据。
        """
        块目录 = tmp_path / 'blocks' / '自造域' / '自造块'
        块目录.mkdir(parents=True)
        (块目录 / '自造块.jk').write_text(
            '定义 甲 赋值 1。\n导出 甲。\n', encoding='utf-8')

        主文件 = tmp_path / '主.jk'
        主文件.write_text('从 blocks.自造域.自造块 导入 甲\n', encoding='utf-8')
        uri = 主文件.as_uri()

        did_open(lsp, uri, '从 blocks.自造域.自造块 导入 甲\n')
        wait_diagnostics(lsp, uri)
        # '从 ' = 2 码点；'blocks'=2..7, '.'=8, '自造域'=9..11, '.'=12, '自造块'=13..15
        resp = request_definition(lsp, uri, line=0, character=14, msg_id=320)
        assert resp is not None
        result = resp.get('result')
        assert result is not None, "用户块应能跳转（try_resolve 回退路径）"
        assert result['uri'].endswith('.jk')
        # 必须指向 tmp 目录里的那份，而不是内建 blocks
        assert result['uri'] == (块目录 / '自造块.jk').as_uri(), \
            f"跳到了错误的文件：{result['uri']}"


class TestDefinitionNonExistent:
    """不存在的块返回 null。"""

    def test_definition_nonexistent_block(self, lsp):
        """导入不存在块 → null。"""
        uri = "file:///tmp/def_nonexist.jk"
        src = "从 blocks.数据.不存在块 导入 甲\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        resp = request_definition(lsp, uri, line=0, character=9, msg_id=310)
        assert resp is not None
        assert resp.get('result') is None

    def test_definition_no_dotpath(self, lsp):
        """光标不在 dotpath 上 → null。"""
        uri = "file:///tmp/def_nodot.jk"
        src = "打印 1。\n"
        did_open(lsp, uri, src)
        wait_diagnostics(lsp, uri)
        resp = request_definition(lsp, uri, line=0, character=0, msg_id=311)
        assert resp is not None
        assert resp.get('result') is None


class TestDefinitionMultiRoot:
    """v0.18.0 W54 · multi-root workspace 的块路径解析。

    `_handle_definition` 在 `blocks_root()` 之后、`try_resolve` 之前，
    按 `workspaceFolders` 声明顺序搜 `<根>/blocks/<领域>/<块名>/`。
    """

    @staticmethod
    def _建根(根目录, 领域, 块名, 正文):
        """在 <根>/blocks/<领域>/<块名>/<块名>.jk 造一个块，返回入口文件 Path。"""
        块目录 = 根目录 / 'blocks' / 领域 / 块名
        块目录.mkdir(parents=True)
        入口 = 块目录 / (块名 + '.jk')
        入口.write_text(正文, encoding='utf-8')
        return 入口

    def test_multi_root_declared_order_first_wins(self, tmp_path):
        """两个根都有 `blocks.多根域.多根块` → 取 workspaceFolders 里的第一个。"""
        根甲 = tmp_path / '根甲'
        根乙 = tmp_path / '根乙'
        根甲.mkdir()
        根乙.mkdir()
        入口甲 = self._建根(根甲, '多根域', '多根块',
                          '定义 甲 赋值 1。\n导出 甲。\n')
        入口乙 = self._建根(根乙, '多根域', '多根块',
                          '定义 甲 赋值 2。\n导出 甲。\n')

        proc = start_lsp_process()
        try:
            resp = initialize(proc, workspace_folders=[
                {"uri": 根甲.as_uri(), "name": "根甲"},
                {"uri": 根乙.as_uri(), "name": "根乙"},
            ])
            assert resp is not None
            initialized(proc)

            # 文档放在**两个根之外**，确保命中不是靠 try_resolve 的同目录搜索
            文档 = tmp_path / '外部主.jk'
            src = '从 blocks.多根域.多根块 导入 甲\n'
            文档.write_text(src, encoding='utf-8')
            uri = 文档.as_uri()
            did_open(proc, uri, src)
            wait_diagnostics(proc, uri)

            # '从 '=2 码点；'blocks'=2..7, '.'=8, '多根域'=9..11, '.'=12, '多根块'=13..15
            r = request_definition(proc, uri, line=0, character=14, msg_id=330)
            assert r is not None
            result = r.get('result')
            assert result is not None, "多根 workspace 里的块应能跳转"
            assert result['uri'] == 入口甲.as_uri(), \
                f"应取声明顺序第一个根（根甲），实际：{result['uri']}"
            assert result['uri'] != 入口乙.as_uri()
        finally:
            stop_lsp_process(proc)

    def test_multi_root_second_root_when_first_lacks(self, tmp_path):
        """第一个根没有该块、第二个根有 → 命中第二个根。"""
        根甲 = tmp_path / '根甲'
        根乙 = tmp_path / '根乙'
        根甲.mkdir()
        根乙.mkdir()
        # 根甲只放一个**别的**块，确保它不是空目录也照样不命中
        self._建根(根甲, '多根域', '无关块', '定义 甲 赋值 0。\n导出 甲。\n')
        入口乙 = self._建根(根乙, '多根域', '独有块',
                          '定义 甲 赋值 9。\n导出 甲。\n')

        proc = start_lsp_process()
        try:
            resp = initialize(proc, workspace_folders=[
                {"uri": 根甲.as_uri(), "name": "根甲"},
                {"uri": 根乙.as_uri(), "name": "根乙"},
            ])
            assert resp is not None
            initialized(proc)

            文档 = tmp_path / '外部主2.jk'
            src = '从 blocks.多根域.独有块 导入 甲\n'
            文档.write_text(src, encoding='utf-8')
            uri = 文档.as_uri()
            did_open(proc, uri, src)
            wait_diagnostics(proc, uri)

            r = request_definition(proc, uri, line=0, character=14, msg_id=331)
            assert r is not None
            result = r.get('result')
            assert result is not None, "第二个根里的块也应命中"
            assert result['uri'] == 入口乙.as_uri()
        finally:
            stop_lsp_process(proc)

    def test_single_root_behavior_unchanged(self, tmp_path):
        """单根（只声明一个 workspaceFolder）行为与 v0.17.0 一致。"""
        根 = tmp_path / '单根'
        根.mkdir()
        入口 = self._建根(根, '单根域', '单根块',
                        '定义 甲 赋值 1。\n导出 甲。\n')

        proc = start_lsp_process()
        try:
            resp = initialize(proc, workspace_folders=[
                {"uri": 根.as_uri(), "name": "单根"},
            ])
            assert resp is not None
            initialized(proc)

            文档 = tmp_path / '外部主3.jk'
            src = '从 blocks.单根域.单根块 导入 甲\n'
            文档.write_text(src, encoding='utf-8')
            uri = 文档.as_uri()
            did_open(proc, uri, src)
            wait_diagnostics(proc, uri)

            r = request_definition(proc, uri, line=0, character=14, msg_id=332)
            assert r is not None
            assert r.get('result') is not None
            assert r['result']['uri'] == 入口.as_uri()
        finally:
            stop_lsp_process(proc)

    def test_outside_all_roots_not_resolved(self, tmp_path):
        """块目录既不在 blocks_root() 也不在任何声明根下 → null。

        守的是「多根解析不能变成全盘搜索」：块在 tmp_path 下但**不在**声明的
        根甲/根乙 里，且文档也不在它旁边，就该解析失败而不是碰巧找到。
        """
        根甲 = tmp_path / '根甲'
        根乙 = tmp_path / '根乙'
        根甲.mkdir()
        根乙.mkdir()
        # 块放在第三个目录，没有被声明为 workspaceFolder
        根外 = tmp_path / '根外'
        根外.mkdir()
        self._建根(根外, '域外', '域外块', '定义 甲 赋值 1。\n导出 甲。\n')

        proc = start_lsp_process()
        try:
            resp = initialize(proc, workspace_folders=[
                {"uri": 根甲.as_uri(), "name": "根甲"},
                {"uri": 根乙.as_uri(), "name": "根乙"},
            ])
            assert resp is not None
            initialized(proc)

            # 文档放在 根甲 里（不是 根外），所以 try_resolve 的同目录搜索也够不到
            文档 = 根甲 / '主.jk'
            src = '从 blocks.域外.域外块 导入 甲\n'
            文档.write_text(src, encoding='utf-8')
            uri = 文档.as_uri()
            did_open(proc, uri, src)
            wait_diagnostics(proc, uri)

            r = request_definition(proc, uri, line=0, character=13, msg_id=333)
            assert r is not None
            assert r.get('result') is None, \
                f"未声明为根的目录里的块不该被解析到：{r.get('result')}"
        finally:
            stop_lsp_process(proc)

    def test_builtin_block_wins_over_workspace_root(self, tmp_path):
        """内建块优先于工作区同名块——工作区不该悄悄遮蔽标准库。"""
        根 = tmp_path / '遮蔽根'
        根.mkdir()
        # 在工作区里造一个与内建 `数据.求和` 同名的块
        影子 = self._建根(根, '数据', '求和', '定义 甲 赋值 999。\n导出 甲。\n')

        proc = start_lsp_process()
        try:
            resp = initialize(proc, workspace_folders=[
                {"uri": 根.as_uri(), "name": "遮蔽根"},
            ])
            assert resp is not None
            initialized(proc)

            文档 = tmp_path / '外部主4.jk'
            src = '从 blocks.数据.求和 导入 汇总\n'
            文档.write_text(src, encoding='utf-8')
            uri = 文档.as_uri()
            did_open(proc, uri, src)
            wait_diagnostics(proc, uri)

            # '从 '=2；'blocks'=2..7, '.'=8, '数据'=9..10, '.'=11, '求和'=12..13
            r = request_definition(proc, uri, line=0, character=12, msg_id=334)
            assert r is not None
            result = r.get('result')
            assert result is not None
            assert result['uri'] != 影子.as_uri(), \
                "内建 stdlib 块不该被工作区同名块遮蔽"
            assert result['uri'].endswith('.jk')
        finally:
            stop_lsp_process(proc)
