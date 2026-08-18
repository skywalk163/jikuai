# -*- coding: utf-8 -*-
"""v0.26.0 W131 · ADR-15 §7.1 / ADR-40 §7 —— 注册 `制造` 领域。

领域白名单（`jikuai.pkg.blocks.ALLOWED_DOMAINS`）从六域扩到七域。本文件盯住
两件事，缺一不可：

1. **新域真进来了**：`制造` 在白名单里，且带 `领域: ["制造"]` 的合法块能过
   `load_block_metadata` 的校验。
2. **白名单还在守**：总数恰好 7、原有六域一个不少、非法域仍被拒。
   只测第 1 条会把「白名单被整体放开」这种改法放过去——那等于门禁没了
   （v0.22.0 的主教训：守卫绿 ≠ 守卫在守）。

造块风格对齐 `tests/test_blocks_l3_schema.py`：`tmp_path` 下写最小 `块.json`，
不碰真实 `stdlib/blocks/`。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.pkg.blocks import (  # noqa: E402
    ALLOWED_DOMAINS, BLOCK_METADATA_NAME,
    BlockError, load_block_metadata, scan_blocks,
)


#: v0.13.0 M2 之后、W131 之前的既有六域。这份清单是硬编码的**期望值**，
#: 不从 ALLOWED_DOMAINS 推导——否则删域时测试会跟着一起「自愈」。
既有六域 = ('数据', '中文', '网络', '工具', '财务', '历法')


def _mk(名称, 领域, 层级=0, 稳定性='stable'):
    """构造最小合法块元数据字典。领域可传字符串或列表。"""
    if isinstance(领域, str):
        领域 = [领域]
    return {
        '名称': 名称,
        '版本': '0.1.0',
        '层级': 层级,
        '领域': 领域,
        '描述': '测试块 %s' % 名称,
        '稳定性': 稳定性,
    }


def _put(root, 领域, data):
    """把块写到 `<root>/<领域>/<名称>/块.json`，返回 `块.json` 路径。"""
    dir_path = os.path.join(str(root), 领域, data['名称'])
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, BLOCK_METADATA_NAME)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# 1. 白名单本身：七域，含制造，不丢旧域
# ---------------------------------------------------------------------------

class Test领域白名单:
    def test_制造已注册(self):
        assert '制造' in ALLOWED_DOMAINS

    def test_总数为七(self):
        """W131 只加一个域。数字写死，多加或少加都要有人来改这一行。"""
        assert len(ALLOWED_DOMAINS) == 7, sorted(ALLOWED_DOMAINS)

    @pytest.mark.parametrize('域', 既有六域)
    def test_既有六域一个不丢(self, 域):
        assert 域 in ALLOWED_DOMAINS

    def test_白名单恰好是七域(self):
        """逐项对齐，防止「加了制造同时悄悄改掉别的域名」。"""
        assert sorted(ALLOWED_DOMAINS) == sorted(既有六域 + ('制造',))


# ---------------------------------------------------------------------------
# 2. 校验链路：制造域块能过，非法域仍被拒
# ---------------------------------------------------------------------------

class Test制造域块校验:
    def test_制造域块可加载(self, tmp_path):
        path = _put(tmp_path, '制造', _mk('产量汇总', '制造'))
        meta = load_block_metadata(path)
        assert meta.domains == ['制造']
        assert meta.name == '产量汇总'

    def test_制造域块可被扫描到(self, tmp_path):
        """`scan_blocks` 走的是同一条校验路径，域越界会在扫描期就炸。"""
        _put(tmp_path, '制造', _mk('缺陷率', '制造'))
        found = scan_blocks(str(tmp_path))
        assert [b.name for b in found] == ['缺陷率']
        assert found[0].domains == ['制造']

    def test_制造与数据可共存于同一块库(self, tmp_path):
        """ADR-15 §7.1：两域并列而非合并，扫描时互不干扰。"""
        _put(tmp_path, '制造', _mk('达成率加权', '制造'))
        _put(tmp_path, '数据', _mk('合计', '数据'))
        域集合 = {d for b in scan_blocks(str(tmp_path)) for d in b.domains}
        assert 域集合 == {'制造', '数据'}


class Test非法领域仍被拒:
    """白名单是「加了一项」而不是「被放开」——这组用例是它的证据。"""

    def test_不存在的域被拒(self, tmp_path):
        assert '不存在的域' not in ALLOWED_DOMAINS
        path = _put(tmp_path, '不存在的域', _mk('随便', '不存在的域'))
        with pytest.raises(BlockError) as ctx:
            load_block_metadata(path)
        assert '领域' in str(ctx.value)

    @pytest.mark.parametrize('域', ['制造业', '製造', '生产'])
    def test_近似写法也被拒(self, 域):
        """只有 `制造` 这一个词形进了白名单，近似写法不许蒙混过关。"""
        assert 域 not in ALLOWED_DOMAINS


# ---------------------------------------------------------------------------
# 3. 目录骨架：空的 制造/ 目录不该被当成块
# ---------------------------------------------------------------------------

def test_空制造目录扫不出块(tmp_path):
    """W131 只建目录骨架、不落块。G11 扫不到块就没有块，不该红。"""
    os.makedirs(os.path.join(str(tmp_path), '制造'), exist_ok=True)
    assert scan_blocks(str(tmp_path)) == []


def test_真实块库里制造目录存在():
    """`src/jikuai/stdlib/blocks/制造/` 骨架已建（`.gitkeep` 占位）。

    W131 建目录时此处曾断言「制造域暂无块」，W133-W137 引擎层块落地后该前提
    作废，改为断言「制造域已有块，且每块都能通过校验」。
    """
    from jikuai.pkg import blocks as _blocks
    制造目录 = os.path.join(_blocks.blocks_root(), '制造')
    assert os.path.isdir(制造目录), 制造目录
    assert os.path.isfile(os.path.join(制造目录, '.gitkeep'))
    # W133 起制造域开始落引擎层块：目录里应能扫到块，且每块领域含「制造」
    制造块 = [b for b in scan_blocks(_blocks.blocks_root()) if '制造' in b.domains]
    assert 制造块, '制造域应已有引擎层块（W133-W137）'
    for b in 制造块:
        assert '制造' in b.domains
