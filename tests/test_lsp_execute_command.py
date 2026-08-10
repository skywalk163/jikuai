# -*- coding: utf-8 -*-
"""v0.15.0 W15 · LSP workspace/executeCommand 协议级测试。

覆盖：正常选块 / 缺 需求 参数 / 未知命令 / 返回字段过 schema.validate_candidate。
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
    request_execute_command,
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
    reason="jikuai_lsp 依赖不可用，跳过 executeCommand 测试",
)


@pytest.fixture
def lsp():
    proc = start_lsp_process()
    resp = initialize(proc)
    assert resp is not None
    initialized(proc)
    yield proc
    stop_lsp_process(proc)


class TestSelectBlock:
    """workspace/executeCommand: 极快.选块。"""

    def test_normal_select_block(self, lsp):
        """正常需求 → 返回协议 `选响应` 信封 `{需求, 候选[]}`（W20 改用 make_select_envelope）。"""
        resp = request_execute_command(
            lsp, '极快.选块',
            arguments=[{'需求': '求和', 'top': 3}],
            msg_id=400,
        )
        assert resp is not None
        result = resp.get('result')
        assert result is not None, f"选块应返回结果，实际：{resp}"
        assert '需求' in result
        assert result['需求'] == '求和'
        assert '候选' in result
        assert isinstance(result['候选'], list)
        assert len(result['候选']) > 0

    def test_select_block_passes_select_envelope_schema(self, lsp):
        """返回的整份响应过 schema.validate_select_envelope 零错误（W20 升级）。"""
        from jikuai.service.schema import validate_select_envelope, validate_candidate
        resp = request_execute_command(
            lsp, '极快.选块',
            arguments=[{'需求': '排序'}],
            msg_id=401,
        )
        assert resp is not None
        result = resp.get('result')
        assert result is not None
        # 整信封校验（W20 新增）
        errs = validate_select_envelope(result)
        assert errs == [], f"选响应校验失败：{errs}，原始值：{result}"
        # 逐候选校验仍保留（双重保险）
        for 候选 in result['候选']:
            errs = validate_candidate(候选)
            assert errs == [], f"候选校验失败：{errs}，原始值：{候选}"

    def test_select_block_default_top(self, lsp):
        """不传 top 默认返回 5 条（或索引里所有块，取较小值）。"""
        resp = request_execute_command(
            lsp, '极快.选块',
            arguments=[{'需求': '数据处理'}],
            msg_id=402,
        )
        assert resp is not None
        result = resp.get('result')
        assert result is not None
        assert len(result['候选']) <= 5

    def test_select_block_carries_export_name(self, lsp):
        """W37：LSP 通道候选必须带 `导出名`，且对目录名≠导出名的块回真实导出名。

        `个税` 块（领域 财务）导出 `缴税`。v0.16.0 客户端拼 `导入 <名称>` 时
        对这类块是错的；协议补齐后 LSP 必须**直接给出**正确的 `导出名`，
        无需客户端兜底。
        """
        from jikuai.service.schema import export_table
        resp = request_execute_command(
            lsp, '极快.选块',
            arguments=[{'需求': '个人所得税', 'top': 8}],
            msg_id=403,
        )
        assert resp is not None
        result = resp.get('result')
        assert result is not None
        表 = export_table()
        命中 = {}
        for c in result['候选']:
            assert c.get('导出名'), f"候选缺 `导出名`：{c!r}"
            assert c['导出名'] == 表.get(c['名称'], c['名称']), c
            命中[c['名称']] = c['导出名']
        assert 命中.get('个税') == '缴税', f'目录名≠导出名的块必须回真实导出名：{命中!r}'



class TestSelectBlockErrors:
    """参数校验与错误响应。"""

    def test_missing_requirement(self, lsp):
        """缺少 需求 字段 → 错误响应（不是 null）。"""
        resp = request_execute_command(
            lsp, '极快.选块',
            arguments=[{'top': 3}],
            msg_id=410,
        )
        assert resp is not None
        assert 'error' in resp, f"缺需求应返回错误，实际：{resp}"
        assert resp['error']['code'] == -32602  # InvalidParams

    def test_empty_requirement(self, lsp):
        """需求 为空字符串 → 错误响应。"""
        resp = request_execute_command(
            lsp, '极快.选块',
            arguments=[{'需求': ''}],
            msg_id=411,
        )
        assert resp is not None
        assert 'error' in resp

    def test_no_arguments(self, lsp):
        """arguments 为空 → 错误响应。"""
        resp = request_execute_command(
            lsp, '极快.选块',
            arguments=[],
            msg_id=412,
        )
        assert resp is not None
        assert 'error' in resp


class TestUnknownCommand:
    """未知命令。"""

    def test_unknown_command_returns_error(self, lsp):
        """未声明的命令 → JSON-RPC 错误（-32601），不是 null。"""
        resp = request_execute_command(
            lsp, '极快.不存在的命令',
            arguments=[{}],
            msg_id=420,
        )
        assert resp is not None
        assert 'error' in resp, f"未知命令应返回错误：{resp}"
        assert resp['error']['code'] == -32601  # MethodNotFound
