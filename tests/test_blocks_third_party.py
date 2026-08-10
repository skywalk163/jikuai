# -*- coding: utf-8 -*-
"""v0.15.0 W22 · ADR-27 第三方块注册表 — 扫描、合并与命名空间隔离测试。

被测模块：`jikuai.pkg.blocks`。

覆盖：
- scan_blocks(roots=[...]) 能同时扫到内置根与第三方根
- 第三方块带非空 `命名空间` 字段
- 内置与第三方同名块可共存（命名空间不同）
- 同命名空间内块名重复仍报错
- G13 跨命名空间：两个不同命名空间的块导出名同名被检测
- generate_index 输出条目含 `命名空间` 字段
- Retriever 能检索到第三方块（索引加载后能查到）
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.pkg import blocks  # noqa: E402
from jikuai.pkg.blocks import (  # noqa: E402
    BLOCK_METADATA_NAME, BUILTIN_NAMESPACE, NAMESPACE_KEY, PKG_ROOTS_ENV,
    BlockError, BlockMetadata,
    extra_roots, generate_index, scan_blocks,
    check_export_globally_unique,
)
from jikuai.ai.retrieval import Retriever  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _minimal_data(**overrides):
    """构造最小合法块元数据。"""
    data = {
        '名称': '测试块',
        '版本': '0.1.0',
        '层级': 0,
        '领域': ['数据'],
        '描述': '单元测试用的假块',
    }
    data.update(overrides)
    return data


def _write_block(dir_path, data, filename=BLOCK_METADATA_NAME):
    """在 dir_path 下写 块.json，返回文件路径。"""
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, filename)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# 测试：scan_blocks 多根扫描
# ---------------------------------------------------------------------------

class TestScanMultipleRoots:
    """scan_blocks(roots=[内置根, 第三方根]) 的行为。"""

    def test_扫到两边_且第三方带命名空间(self, tmp_path):
        """内置根下的块命名空间为空；第三方根下的块命名空间为目录第一级。"""
        # 内置根（命名空间=空）
        builtin = tmp_path / 'builtin'
        _write_block(str(builtin / '数据' / '求和'),
                     _minimal_data(名称='求和', 稳定性='stable'))

        # 第三方根：命名空间=社区
        third = tmp_path / 'third'
        _write_block(str(third / '社区' / '数据' / '方差'),
                     _minimal_data(名称='方差', 稳定性='stable'))

        found = scan_blocks(roots=[str(builtin), str(third)])
        names = [(b.namespace, b.name) for b in found]
        assert ('', '求和') in names
        assert ('社区', '方差') in names

    def test_第三方块namespace属性非空(self, tmp_path):
        """第三方根下扫到的块 .namespace 是目录名。"""
        third = tmp_path / 'ext'
        _write_block(str(third / '实验室' / '工具' / '计时'),
                     _minimal_data(名称='计时', 领域=['工具']))

        found = scan_blocks(roots=[str(tmp_path / 'empty'), str(third)])
        计时 = next(b for b in found if b.name == '计时')
        assert 计时.namespace == '实验室'
        assert 计时.qualified_name == '实验室.工具.计时'


# ---------------------------------------------------------------------------
# 测试：内置与第三方同名共存（命名空间不同）
# ---------------------------------------------------------------------------

class TestNamespaceIsolation:
    """跨命名空间同名块可以共存。"""

    def test_同名不同命名空间共存(self, tmp_path):
        """内置块 `求和` 与第三方块 `社区/数据/求和` 不冲突。"""
        builtin = tmp_path / 'builtin'
        _write_block(str(builtin / '数据' / '求和'),
                     _minimal_data(名称='求和', 稳定性='stable'))

        third = tmp_path / 'third'
        _write_block(str(third / '社区' / '数据' / '求和'),
                     _minimal_data(名称='求和', 稳定性='stable'))

        found = scan_blocks(roots=[str(builtin), str(third)])
        ns_names = [(b.namespace, b.name) for b in found]
        assert ('', '求和') in ns_names
        assert ('社区', '求和') in ns_names
        assert len(found) == 2

    def test_同命名空间内重名仍报错(self, tmp_path):
        """内置根下的同名块（同命名空间=空）应抛 BlockError。"""
        builtin = tmp_path / 'builtin'
        _write_block(str(builtin / '数据' / '求和'),
                     _minimal_data(名称='求和'))
        _write_block(str(builtin / '工具' / '求和'),
                     _minimal_data(名称='求和', 领域=['工具']))

        with pytest.raises(BlockError) as exc:
            scan_blocks(root=str(builtin))
        assert '重复' in str(exc.value) or '命名空间' in str(exc.value)


# ---------------------------------------------------------------------------
# 测试：G13 跨命名空间导出名唯一
# ---------------------------------------------------------------------------

class TestG13CrossNamespace:
    """G13 门禁应检测跨命名空间的导出名碰撞。"""

    def test_跨命名空间导出名冲突被检测(self):
        """两个不同命名空间的块导出同名函数 → G13 报冲突。"""
        index = {
            '版本': blocks.BLOCK_INDEX_VERSION,
            '生成时间': '2026-08-10T00:00:00',
            '块': [
                {'名称': '求和', '命名空间': '', '领域': ['数据'],
                 '层级': 0, '描述': 'x', '输入': [], '输出': {},
                 '导出': ['汇总'], '稳定性': 'stable'},
                {'名称': '求和', '命名空间': '社区', '领域': ['数据'],
                 '层级': 0, '描述': 'y', '输入': [], '输出': {},
                 '导出': ['汇总'], '稳定性': 'stable'},
            ],
        }
        冲突 = check_export_globally_unique(index)
        assert len(冲突) == 1
        名, 块列 = 冲突[0]
        assert 名 == '汇总'
        assert len(块列) == 2

    def test_不同导出名无冲突(self):
        """两个不同命名空间的块导出名不同 → G13 通过。"""
        index = {
            '版本': blocks.BLOCK_INDEX_VERSION,
            '生成时间': '2026-08-10T00:00:00',
            '块': [
                {'名称': '求和', '命名空间': '', '领域': ['数据'],
                 '层级': 0, '描述': 'x', '输入': [], '输出': {},
                 '导出': ['汇总'], '稳定性': 'stable'},
                {'名称': '方差', '命名空间': '社区', '领域': ['数据'],
                 '层级': 0, '描述': 'y', '输入': [], '输出': {},
                 '导出': ['方差值'], '稳定性': 'stable'},
            ],
        }
        冲突 = check_export_globally_unique(index)
        assert 冲突 == []


# ---------------------------------------------------------------------------
# 测试：generate_index 输出含命名空间字段
# ---------------------------------------------------------------------------

class TestGenerateIndexNamespace:
    """generate_index 产出的条目应含 `命名空间` 字段。"""

    def test_内置块命名空间为空串(self, tmp_path):
        """单根 generate_index 产出的条目 '命名空间' == ''。"""
        _write_block(str(tmp_path / '求和'),
                     _minimal_data(名称='求和', 稳定性='stable'))
        idx = generate_index(root=str(tmp_path),
                             timestamp='2026-08-10T00:00:00')
        条目 = idx['块'][0]
        assert NAMESPACE_KEY in 条目
        assert 条目[NAMESPACE_KEY] == BUILTIN_NAMESPACE

    def test_多根生成带命名空间(self, tmp_path):
        """roots=[内置, 第三方] generate_index 产出含非空命名空间。"""
        builtin = tmp_path / 'b'
        _write_block(str(builtin / '数据' / '求和'),
                     _minimal_data(名称='求和', 稳定性='stable'))
        third = tmp_path / 't'
        _write_block(str(third / '社区' / '数据' / '方差'),
                     _minimal_data(名称='方差', 稳定性='stable'))

        idx = generate_index(roots=[str(builtin), str(third)],
                             timestamp='2026-08-10T00:00:00')
        条目们 = {e['名称']: e for e in idx['块']}
        assert 条目们['求和'][NAMESPACE_KEY] == ''
        assert 条目们['方差'][NAMESPACE_KEY] == '社区'


# ---------------------------------------------------------------------------
# 测试：Retriever 能检索到第三方块
# ---------------------------------------------------------------------------

class TestRetrieverThirdParty:
    """Retriever 从含命名空间的索引条目里能正常检索。"""

    def test_检索到第三方块(self):
        """TF-IDF 启发式能命中第三方块。"""
        块列表 = [
            {'名称': '求和', '命名空间': '', '领域': ['数据'],
             '层级': 0, '描述': '对数值列表求和，返回总和',
             '稳定性': 'stable'},
            {'名称': '方差', '命名空间': '社区', '领域': ['数据'],
             '层级': 0, '描述': '计算数值列表的方差和标准差',
             '稳定性': 'stable'},
        ]
        r = Retriever(块列表)
        hits = r.retrieve('方差', top=2)
        assert len(hits) > 0
        names = [h.name for h in hits]
        assert '方差' in names


# ---------------------------------------------------------------------------
# 测试：extra_roots 环境变量
# ---------------------------------------------------------------------------

class TestExtraRoots:
    """extra_roots() 从 JIKUAI_PKG_ROOTS 环境变量读取路径。"""

    def test_空环境变量返回空列表(self, monkeypatch):
        monkeypatch.delenv(PKG_ROOTS_ENV, raising=False)
        assert extra_roots() == []

    def test_有效路径被返回(self, tmp_path, monkeypatch):
        real_dir = str(tmp_path / 'blocks')
        os.makedirs(real_dir, exist_ok=True)
        monkeypatch.setenv(PKG_ROOTS_ENV, real_dir)
        roots = extra_roots()
        assert os.path.abspath(real_dir) in roots

    def test_不存在的路径被过滤(self, monkeypatch):
        monkeypatch.setenv(PKG_ROOTS_ENV, '/绝对不存在的路径/xyz')
        assert extra_roots() == []

    def test_多路径去重(self, tmp_path, monkeypatch):
        real_dir = str(tmp_path / 'blocks')
        os.makedirs(real_dir, exist_ok=True)
        sep = os.pathsep
        monkeypatch.setenv(PKG_ROOTS_ENV, real_dir + sep + real_dir)
        roots = extra_roots()
        assert len(roots) == 1
