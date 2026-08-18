# -*- coding: utf-8 -*-
"""v0.27.0 W149 · `共享[].类型` 可选键：取值域、非法值不放行、向后兼容。

正本：`docs/ADR-41-规划器与NL层.md` §6、`docs/协议-三通道.md`「2. 方案」。

**为什么加这个键**（根因链，v0.26.0 W145 实测）：`glue.py` 把 `共享` 常量一律入池为
`任意` → `任意` 在 `type_feeds` 双向放行 → 每个字符串常量对每个形参都「类型兼容」→
多候选消解退回「最近产出优先」随便挑 → **静默错绑**（`读表(赵产量列)`），既不落 `?`
占位也不写拒绝理由，运行期才死。声明类型是让类型图真能拒绝错绑的前提。

**本文件最要紧的一条**：`类型` 写错**不许被当作 `任意` 放行**。若非法类型名静默降级，
等于把上面那个坑重挖一遍——错绑照旧发生，只是多了一个看起来有类型的方案。

另钉**向后兼容**：`赛题/chatbi/产出/` 那 15 份真实方案（v0.26.0 W145 人工验收产物，
一份都没有 `类型` 键）必须继续过 `validate_plan`。这是 W149 「旧方案一份都不用改」
这句承诺的**证据**，不是口头保证。

零第三方依赖，纯标准库 + pytest。
"""

import json
import os
import sys

import pytest

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.service import schema           # noqa: E402
from jikuai.pkg.blocks import SCALAR_TYPES, CONTAINER_TYPE_NAMES  # noqa: E402

_产出 = os.path.join(_REPO, '赛题', 'chatbi', '产出')


def _方案(共享):
    return {
        '需求': '测试',
        '共享': 共享,
        '步骤': [{'块': '个税', '领域': '财务', '导出名': '缴税',
                  '参数': ['赵月收']}],
    }


# ---------------------------------------------------------------------------
# 1. 取值域：词表内全放行
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('类型名', sorted(set(SCALAR_TYPES) | set(CONTAINER_TYPE_NAMES)))
def test_词表内的类型名全部放行(类型名):
    errs = schema.validate_plan(_方案([{'名': '赵x', '值': '1', '类型': 类型名}]))
    assert errs == [], (类型名, errs)


def test_词表就是标量加容器裸名():
    """把取值域写成断言：ADR-26 词表变了就该来改这里，而不是让协议悄悄跟着变。"""
    assert SCALAR_TYPES == frozenset({'数', '字符串', '布尔', '函数', '任意'})
    assert CONTAINER_TYPE_NAMES == frozenset({'列表', '字典', '元组'})


# ---------------------------------------------------------------------------
# 2. 非法值一律报错——**不静默降级为 `任意`**（本文件的核心断言）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('坏类型', [
    '整数',          # 像但不是（词表里是 `数`）
    'str',           # 英文
    '列表<数>',      # 泛型写法，词表不收
    '联合',          # 只有结构化形态，裸名无意义，故不在词表
    '表',            # ADR-40 §3.3 的「语言级表」还没立项
    '',              # 空串
])
def test_非法类型名被拒而不是当任意放行(坏类型):
    errs = schema.validate_plan(_方案([{'名': '赵x', '值': '1', '类型': 坏类型}]))
    assert any('类型' in e and '不在 ADR-26 类型词表' in e for e in errs), \
        ('静默放行了非法类型名，等于把静默错绑的坑重挖一遍', 坏类型, errs)


def test_非法类型名抛SchemaError():
    with pytest.raises(schema.SchemaError):
        schema.ensure_plan(_方案([{'名': '赵x', '值': '1', '类型': '整数'}]))


def test_结构化类型对象不被接受():
    """`值` 是个字面量串，声明它是 `列表<数>` 这种细化类型没有意义——细化类型是块
    形参的事。所以 `类型` 只收字符串，给对象直接判「必须是字符串」。"""
    errs = schema.validate_plan(_方案([
        {'名': '赵x', '值': '1', '类型': {'类型': '列表', '元素类型': '数'}}]))
    assert any('类型 必须是字符串' in e for e in errs), errs


def test_类型是数字也被拒():
    errs = schema.validate_plan(_方案([{'名': '赵x', '值': '1', '类型': 1}]))
    assert any('类型 必须是字符串' in e for e in errs), errs


# ---------------------------------------------------------------------------
# 3. 向后兼容：不带 `类型` 合法；`共享` 仍不许有第四个键
# ---------------------------------------------------------------------------

def test_不带类型仍然合法():
    assert schema.validate_plan(_方案([{'名': '赵x', '值': '1'}])) == []


def test_共享缺名或值仍然报错():
    for 缺 in ('名', '值'):
        项 = {'名': '赵x', '值': '1'}
        del 项[缺]
        errs = schema.validate_plan(_方案([项]))
        assert any('缺少必需字段「%s」' % 缺 in e for e in errs), (缺, errs)


def test_共享的第四个键仍被拒():
    """加 `类型` 是**开一个口**，不是把 `共享` 变成宽松字典。"""
    errs = schema.validate_plan(_方案([
        {'名': '赵x', '值': '1', '类型': '数', '注释': '不该有'}]))
    assert any('未知字段' in e and '注释' in e for e in errs), errs


def test_名与值仍须是字符串():
    errs = schema.validate_plan(_方案([{'名': 1, '值': ['x']}]))
    assert any('名 必须是字符串' in e for e in errs), errs
    assert any('值 必须是字符串' in e for e in errs), errs


# ---------------------------------------------------------------------------
# 4. 真实方案回归：15 份 W145 人工验收产物必须继续过校验
# ---------------------------------------------------------------------------

def _收集真实方案():
    """`赛题/chatbi/产出/` 下的 15 份方案（10 公开 + 5 留出）。

    sdist 收 `tests/` 但不收 `赛题/`，所以必须带「目录不存在就 skip」守卫
    （v0.26.0 W130 实测的分发边界）。
    """
    出 = []
    for 根 in (_产出, os.path.join(_产出, '留出')):
        if not os.path.isdir(根):
            continue
        for 名 in sorted(os.listdir(根)):
            if 名.endswith('-方案.json'):
                出.append(os.path.join(根, 名))
    return 出


_真实方案 = _收集真实方案()


@pytest.mark.skipif(not _真实方案, reason='赛题/chatbi/产出/ 不在场（sdist 不收赛题目录）')
def test_十五份真实方案数量为十五():
    assert len(_真实方案) == 15, [os.path.basename(p) for p in _真实方案]


@pytest.mark.skipif(not _真实方案, reason='赛题/chatbi/产出/ 不在场')
@pytest.mark.parametrize('路径', _真实方案, ids=lambda p: os.path.basename(p)[:-8])
def test_真实方案仍过校验(路径):
    with open(路径, 'r', encoding='utf-8') as f:
        方案 = json.load(f)
    errs = schema.validate_plan(方案)
    assert errs == [], (os.path.basename(路径), errs)


@pytest.mark.skipif(not _真实方案, reason='赛题/chatbi/产出/ 不在场')
def test_真实方案一份都没有类型键():
    """确认这 15 份**确实**是「旧形态」样本——若哪天有人给它们补上 `类型`，
    这条回归就不再证明向后兼容了，得换样本而不是删断言。"""
    带类型 = []
    for 路径 in _真实方案:
        with open(路径, 'r', encoding='utf-8') as f:
            方案 = json.load(f)
        for 项 in 方案.get('共享') or []:
            if '类型' in 项:
                带类型.append(os.path.basename(路径))
                break
    assert not 带类型, ('样本已不是旧形态，向后兼容回归失效', 带类型)


@pytest.mark.skipif(not _真实方案, reason='赛题/chatbi/产出/ 不在场')
def test_真实方案的实参全是手写的():
    """W145 「`?` 占位 0 次」不是自动推参的功劳——124 步实参全手写（BACKLOG §12.3
    第 2 条纠正的那个「漂亮数字」）。把它钉成断言：每一步都必须显式带 `参数`。

    这条同时是 W157 校验器第 1 条硬规则的**先验证据**：真实可用的方案本来就步步带参。
    """
    无参 = []
    总步数 = 0
    for 路径 in _真实方案:
        with open(路径, 'r', encoding='utf-8') as f:
            方案 = json.load(f)
        for i, 步 in enumerate(方案.get('步骤') or [], 1):
            总步数 += 1
            if '参数' not in 步:
                无参.append('%s 步骤[%d]' % (os.path.basename(路径), i))
    assert not 无参, 无参
    assert 总步数 == 124, ('W145 记录的是 124 步，实际 %d 步' % 总步数)
