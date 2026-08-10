# -*- coding: utf-8 -*-
"""v0.16.0 W29 · ADR-28 L3 聚合块规范 —— 门禁函数正反用例。

被测函数（`jikuai.pkg.blocks`）：

    load_block_metadata            —— `层级` 上限收紧到 MAX_BLOCK_LEVEL=3
    check_dependency_acyclic       —— 依赖环检测（自环 / 短环 / 长环）
    check_level_consistency        —— L3 层级虚标（声明 L3 却只依赖 L1）
    check_stability_propagation    —— stable L3 依赖 experimental/deprecated L2

用 `tmp_path` 造块，风格对齐 `tests/test_blocks_third_party.py`：
只走 `scan_blocks(root=...)` 单根形态，把命名空间锁在空串，避免第三方
命名空间路径不必要地掺进 L3 判定语义。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.pkg.blocks import (  # noqa: E402
    BLOCK_METADATA_NAME, MAX_BLOCK_LEVEL,
    BlockError, load_block_metadata,
    scan_blocks, build_dependency_graph, find_dependency_cycles,
    check_dependency_acyclic, check_level_consistency,
    check_stability_propagation,
)


# ---------------------------------------------------------------------------
# 造块辅助（对齐 test_blocks_third_party._write_block）
# ---------------------------------------------------------------------------

def _mk(名称, 层级, 领域, 依赖块=None, 稳定性='stable'):
    """构造最小合法块元数据字典。领域可传字符串或列表。"""
    if isinstance(领域, str):
        领域 = [领域]
    data = {
        '名称': 名称,
        '版本': '0.1.0',
        '层级': 层级,
        '领域': 领域,
        '描述': '测试块 %s' % 名称,
        '稳定性': 稳定性,
    }
    if 依赖块 is not None:
        data['依赖块'] = list(依赖块)
    return data


def _write(dir_path, data):
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, BLOCK_METADATA_NAME)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _put(root, 领域, data):
    """把块写到 `<root>/<领域>/<名称>/块.json`。"""
    return _write(os.path.join(str(root), 领域, data['名称']), data)


# ---------------------------------------------------------------------------
# 1. `层级` 上限（ADR-28 §3.3）：`_validate` 层
# ---------------------------------------------------------------------------

class Test层级上限:
    """`层级` 现在必须 ∈ [0, MAX_BLOCK_LEVEL]；越界由 `load_block_metadata` 拒。"""

    def test_层级3合法(self, tmp_path):
        """L3 是本轮开放的新层级，`_validate` 必须放行（后续的 L3 判定另论）。"""
        path = _put(tmp_path, '财务', _mk('报销单', 3, '财务'))
        meta = load_block_metadata(path)
        assert meta.level == 3

    def test_层级4被拒(self, tmp_path):
        """深过 MAX_BLOCK_LEVEL（=3）一律拒，错误里应指向 ADR-28 §3.3。"""
        path = _put(tmp_path, '财务', _mk('虚构L4', 4, '财务'))
        with pytest.raises(BlockError) as exc:
            load_block_metadata(path)
        assert '层级' in str(exc.value)
        assert '%d' % MAX_BLOCK_LEVEL in str(exc.value) or '3' in str(exc.value)

    def test_层级负数仍被拒(self, tmp_path):
        """原有下限（非负整数）不能因为加上限而回归。"""
        path = _put(tmp_path, '财务', _mk('虚构负', -1, '财务'))
        with pytest.raises(BlockError):
            load_block_metadata(path)


# ---------------------------------------------------------------------------
# 2. 依赖环检测（ADR-28 §3.5 / §4.1）
# ---------------------------------------------------------------------------

class Test依赖环检测:
    """`check_dependency_acyclic` 顺着 `依赖块` 建图找环。"""

    def test_无环时空列表(self, tmp_path):
        """典型 L0→L1→L2 单向依赖，报环列表应为空。"""
        _put(tmp_path, '数据', _mk('原子甲', 0, '数据'))
        _put(tmp_path, '数据', _mk('复合乙', 1, '数据', 依赖块=['原子甲']))
        _put(tmp_path, '数据',
             _mk('场景丙', 2, '数据', 依赖块=['复合乙', '原子甲']))
        assert check_dependency_acyclic(root=str(tmp_path)) == []

    def test_短环被检出(self, tmp_path):
        """A→B→A 两节点环：报出的路径首尾闭合、含双方名字。"""
        _put(tmp_path, '数据', _mk('甲块', 1, '数据', 依赖块=['乙块']))
        _put(tmp_path, '数据', _mk('乙块', 1, '数据', 依赖块=['甲块']))
        环 = check_dependency_acyclic(root=str(tmp_path))
        assert len(环) == 1
        路径 = 环[0]
        assert 路径[0] == 路径[-1]                 # 闭合
        assert set(路径[:-1]) == {'甲块', '乙块'}   # 环体节点

    def test_自环被检出(self, tmp_path):
        """A→A 也是环——虽然罕见但语义上完全错乱。"""
        _put(tmp_path, '数据', _mk('自恋', 1, '数据', 依赖块=['自恋']))
        环 = check_dependency_acyclic(root=str(tmp_path))
        assert len(环) == 1
        assert 环[0] == ['自恋', '自恋']

    def test_长环被检出(self, tmp_path):
        """A→B→C→A 三节点环：报错要能指出完整路径供贡献者照着改。"""
        _put(tmp_path, '数据', _mk('甲', 1, '数据', 依赖块=['乙']))
        _put(tmp_path, '数据', _mk('乙', 1, '数据', 依赖块=['丙']))
        _put(tmp_path, '数据', _mk('丙', 1, '数据', 依赖块=['甲']))
        环 = check_dependency_acyclic(root=str(tmp_path))
        assert len(环) == 1
        assert set(环[0][:-1]) == {'甲', '乙', '丙'}
        assert 环[0][0] == 环[0][-1]

    def test_指向图外的依赖不成环(self, tmp_path):
        """`依赖块` 指向未扫进来的块（G11 会另报），环检测应放行。"""
        _put(tmp_path, '数据',
             _mk('孤立', 1, '数据', 依赖块=['不存在的块']))
        assert check_dependency_acyclic(root=str(tmp_path)) == []

    def test_find_dependency_cycles纯图接口(self):
        """底层 `find_dependency_cycles` 直接吃图 dict，与 IO 解耦。"""
        graph = {'A': ['B'], 'B': ['C'], 'C': ['A'], 'D': []}
        环 = find_dependency_cycles(graph)
        assert len(环) == 1
        assert set(环[0][:-1]) == {'A', 'B', 'C'}


# ---------------------------------------------------------------------------
# 3. L3 层级一致性（ADR-28 §3.1）
# ---------------------------------------------------------------------------

class TestL3层级一致性:
    """`check_level_consistency` 校验声明 L3 的块，依赖结构真的够 L3。"""

    def test_声明L3只依赖L1_被拒(self, tmp_path):
        """典型层级虚标：拿 L1 凑数装 L3，两条 L3 判定条件都不满足。"""
        _put(tmp_path, '数据', _mk('L1甲', 1, '数据'))
        _put(tmp_path, '数据', _mk('L1乙', 1, '数据'))
        _put(tmp_path, '数据',
             _mk('假L3', 3, '数据', 依赖块=['L1甲', 'L1乙']))
        问题 = check_level_consistency(root=str(tmp_path))
        assert any('假L3' in p for p in 问题)
        assert any('L3 判定' in p or 'L3' in p for p in 问题)

    def test_依赖两个L2_满足条件1(self, tmp_path):
        """条件 1：>= 2 个 L2+ 聚合块。同领域也算。"""
        _put(tmp_path, '财务', _mk('L2甲', 2, '财务'))
        _put(tmp_path, '财务', _mk('L2乙', 2, '财务'))
        _put(tmp_path, '财务',
             _mk('真L3', 3, '财务', 依赖块=['L2甲', 'L2乙']))
        assert check_level_consistency(root=str(tmp_path)) == []

    def test_一个L2加跨领域_满足条件2(self, tmp_path):
        """条件 2：>= 1 个 L2+ 依赖，且依赖并集覆盖 >= 2 个领域。"""
        _put(tmp_path, '财务', _mk('L2财务', 2, '财务'))
        _put(tmp_path, '历法', _mk('L1历法', 1, '历法'))
        _put(tmp_path, '中文', _mk('L1中文', 1, '中文'))
        # L3 自己放财务目录下，`领域` 声明也是财务，但依赖跨了历法+中文
        _put(tmp_path, '财务',
             _mk('报销单', 3, '财务',
                 依赖块=['L2财务', 'L1历法', 'L1中文']))
        assert check_level_consistency(root=str(tmp_path)) == []

    def test_一个L2但单领域_不够条件2(self, tmp_path):
        """条件 2 要求"跨 >= 2 领域"。全部依赖只覆盖 1 个领域应被拒。"""
        _put(tmp_path, '财务', _mk('L2财务', 2, '财务'))
        _put(tmp_path, '财务', _mk('L1财务', 1, '财务'))
        _put(tmp_path, '财务',
             _mk('单域L3', 3, '财务',
                 依赖块=['L2财务', 'L1财务']))
        问题 = check_level_consistency(root=str(tmp_path))
        assert any('单域L3' in p for p in 问题)

    def test_L2块不受层级判定约束(self, tmp_path):
        """本轮只查 L3；存量 L2 块的层级判定不追溯（ADR-28 §5 已知欠账）。"""
        # 一个只依赖 L0 的 L2 块——真按 L2 判定这也是虚标，但 ADR-28 不管
        _put(tmp_path, '数据', _mk('L0原子', 0, '数据'))
        _put(tmp_path, '数据',
             _mk('宽松L2', 2, '数据', 依赖块=['L0原子']))
        assert check_level_consistency(root=str(tmp_path)) == []

    def test_同一L2写两遍不算两个依赖(self, tmp_path):
        """`依赖块` 重复项按去重计数，不能靠写两遍蹭出"依赖 2 个 L2"。"""
        _put(tmp_path, '财务', _mk('L2甲', 2, '财务'))
        _put(tmp_path, '财务',
             _mk('刷量L3', 3, '财务', 依赖块=['L2甲', 'L2甲']))
        问题 = check_level_consistency(root=str(tmp_path))
        assert any('刷量L3' in p for p in 问题)


# ---------------------------------------------------------------------------
# 4. 稳定性传递（ADR-28 §3.2，扩展 ADR-27 §2.5）
# ---------------------------------------------------------------------------

class Test稳定性传递:
    """`check_stability_propagation`：stable L3 的 L2+ 依赖必须也是 stable。"""

    def test_stable_L3依赖experimental_L2_被拒(self, tmp_path):
        """核心违规形态。"""
        _put(tmp_path, '财务',
             _mk('实验L2', 2, '财务', 稳定性='experimental'))
        _put(tmp_path, '财务', _mk('稳定L2', 2, '财务', 稳定性='stable'))
        _put(tmp_path, '财务',
             _mk('稳定L3', 3, '财务', 稳定性='stable',
                 依赖块=['实验L2', '稳定L2']))
        问题 = check_stability_propagation(root=str(tmp_path))
        assert len(问题) == 1
        assert '稳定L3' in 问题[0] and '实验L2' in 问题[0]

    def test_stable_L3依赖deprecated_L2_也被拒(self, tmp_path):
        """deprecated 比 experimental 更糟，同样应拒。"""
        _put(tmp_path, '财务',
             _mk('弃用L2', 2, '财务', 稳定性='deprecated'))
        _put(tmp_path, '财务', _mk('稳定L2', 2, '财务', 稳定性='stable'))
        _put(tmp_path, '财务',
             _mk('稳定L3', 3, '财务', 稳定性='stable',
                 依赖块=['弃用L2', '稳定L2']))
        问题 = check_stability_propagation(root=str(tmp_path))
        assert any('弃用L2' in p and 'deprecated' in p for p in 问题)

    def test_全stable依赖_通过(self, tmp_path):
        """正例：stable L3 依赖全 stable 的 L2，无违规。"""
        _put(tmp_path, '财务', _mk('稳L2甲', 2, '财务', 稳定性='stable'))
        _put(tmp_path, '财务', _mk('稳L2乙', 2, '财务', 稳定性='stable'))
        _put(tmp_path, '财务',
             _mk('稳L3', 3, '财务', 稳定性='stable',
                 依赖块=['稳L2甲', '稳L2乙']))
        assert check_stability_propagation(root=str(tmp_path)) == []

    def test_experimental_L1依赖不追溯(self, tmp_path):
        """ADR-28 §3.2 明确不管 stable L3 → experimental L1。"""
        _put(tmp_path, '数据', _mk('实验L1', 1, '数据', 稳定性='experimental'))
        _put(tmp_path, '财务', _mk('稳L2甲', 2, '财务', 稳定性='stable'))
        _put(tmp_path, '财务', _mk('稳L2乙', 2, '财务', 稳定性='stable'))
        _put(tmp_path, '财务',
             _mk('稳L3', 3, '财务', 稳定性='stable',
                 依赖块=['稳L2甲', '稳L2乙', '实验L1']))
        assert check_stability_propagation(root=str(tmp_path)) == []

    def test_experimental_L3自身不受本规则约束(self, tmp_path):
        """规则只压 stable L3；experimental L3 依赖谁都随意。"""
        _put(tmp_path, '财务',
             _mk('实L2', 2, '财务', 稳定性='experimental'))
        _put(tmp_path, '财务', _mk('稳L2', 2, '财务', 稳定性='stable'))
        _put(tmp_path, '财务',
             _mk('实L3', 3, '财务', 稳定性='experimental',
                 依赖块=['实L2', '稳L2']))
        assert check_stability_propagation(root=str(tmp_path)) == []


# ---------------------------------------------------------------------------
# 5. 组合端到端：一个"真 L3 + 环" 场景同时命中多门禁
# ---------------------------------------------------------------------------

class Test组合场景:
    """同一份块库同时踩多条门禁，确认三个函数各自独立报错。"""

    def test_L3合法且带环_只报环(self, tmp_path):
        """L3 判定通过，但依赖链里有环——环检测报错，层级判定不额外报。"""
        _put(tmp_path, '财务', _mk('L2甲', 2, '财务', 依赖块=['L2乙']))
        _put(tmp_path, '财务', _mk('L2乙', 2, '财务', 依赖块=['L2甲']))
        _put(tmp_path, '财务',
             _mk('真L3', 3, '财务', 依赖块=['L2甲', 'L2乙']))

        assert len(check_dependency_acyclic(root=str(tmp_path))) == 1
        assert check_level_consistency(root=str(tmp_path)) == []


# ---------------------------------------------------------------------------
# 6. 图构造辅助
# ---------------------------------------------------------------------------

class Test依赖图构造:
    """`build_dependency_graph` 从块列表构 `名称 -> [依赖块]` 图。"""

    def test_保留依赖顺序与图外节点(self, tmp_path):
        """依赖顺序保序；指向未扫入块的边保留（G11 另有对账，本函数不过滤）。"""
        _put(tmp_path, '数据',
             _mk('多依赖', 1, '数据', 依赖块=['乙', '甲', '不存在']))
        _put(tmp_path, '数据', _mk('甲', 0, '数据'))
        _put(tmp_path, '数据', _mk('乙', 0, '数据'))
        graph = build_dependency_graph(scan_blocks(root=str(tmp_path)))
        assert graph['多依赖'] == ['乙', '甲', '不存在']
        assert graph['甲'] == [] and graph['乙'] == []
