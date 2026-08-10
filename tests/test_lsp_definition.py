# -*- coding: utf-8 -*-
"""v0.15.0 W14 · LSP textDocument/definition 协议级测试。

覆盖：内建块跳转、用户块跳转（ModuleLoader 回退路径）、不存在的块返回 null。
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
