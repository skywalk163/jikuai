# -*- coding: utf-8 -*-
"""v0.27.0 W148 · 规划器通道协议（规划上下文包 / 回填响应）的常量与校验器断言。

正本：`docs/ADR-41-规划器与NL层.md` §3/§4。本文件钉三件事：

1. **新协议的键集是严格白名单**——多一个键就报错。规划器的输入来自 LLM，
   宽松键集等于给幻觉字段开门。
2. **`输入槽`/`输出类型` 真的在候选里**。这两个字段是本轮的技术核心：
   v0.26.0 W145 那 124 步实参全靠人手写，直接根因就是「选响应」候选不带输入槽，
   LLM 只能猜。若哪天有人为了「精简协议」把它们删掉，这里当场红。
3. **`validate_candidate` 与 `validate_context_candidate` 字段口径同源**。两者键集
   不同但逐字段规则必须一致，靠共用 `_check_candidate_fields` 保证；这里用同一个
   坏字段喂两边、断言报同样的错，防将来有人复制粘贴出两套漂移的规则。

本层**只管形状**。ADR-41 §4 那五条硬规则（实参长度等于输入槽数、块名白名单、
分歧点必须选一条…）要拿上下文包做参照，落 `tools/ai-bridge/planner.py`（W157），
不在 schema 层——它拿不到候选清单，也不该拿。

零第三方依赖，纯标准库 + pytest。
"""

import os
import sys

import pytest

_HERE = os.path.abspath(os.path.dirname(__file__))
_SRC = os.path.normpath(os.path.join(_HERE, '..', 'src'))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.service import schema  # noqa: E402


# ---------------------------------------------------------------------------
# 构造：一份合法的上下文包与回填响应（其余用例在此基础上做单点破坏）
# ---------------------------------------------------------------------------

def _候选(名称='产量汇总', 导出名='量汇', 输入槽=None, 输出类型='元组'):
    if 输入槽 is None:
        输入槽 = [schema.make_slot('赵表', '列表'),
                  schema.make_slot('赵维度列', '字符串')]
    return schema.make_context_candidate(
        名称=名称, 领域='制造', 层级=1, 导出名=导出名,
        描述='按维度列分组汇总产量，先汇总后相除', 分数=3.14,
        输入槽=输入槽, 输出类型=输出类型, 路径='[启发式]')


def _契约():
    return schema.make_fill_contract(
        必填=['每步 参数 必填，长度等于该块 输入槽 数'],
        禁止=['省略 参数', '协议外字段', '候选清单外的块名'])


def _上下文包(**改):
    包 = schema.make_context_envelope(
        需求='2026年6月各车型的总产量是多少',
        语义命中=[schema.make_semantic_hit(
            业务词='产量', 表='fact_production_actual',
            字段='actual_quantity', 口径说明='实际产量，按 model_id 汇总')],
        候选=[_候选()],
        回填契约=_契约(),
        拒答建议=schema.make_reject_advice(覆盖=True, 理由='语义层已登记「产量」'),
        分歧告警=[schema.make_divergence_warning(
            分歧点='缺陷率',
            两侧块名=['缺陷率', '缺陷汇总'],
            实测差值='2026-06 先汇总后相除 0.050550 vs 行级比率平均 0.032218，差 57%')],
    )
    包.update(改)
    return 包


def _回填(**改):
    信封 = schema.make_filled_envelope(
        需求='2026年6月各车型的总产量是多少',
        方案=schema.make_plan(
            步骤=[schema.make_step('产量汇总', '制造', '量汇',
                                   参数=['赵表', '赵维度列'])],
            需求='2026年6月各车型的总产量是多少',
            共享=[{'名': '赵维度列', '值': 'model_id'}]),
        模型='人工')
    信封.update(改)
    return 信封


# ---------------------------------------------------------------------------
# 1. 正例：make_* 的产物必须自洽
# ---------------------------------------------------------------------------

def test_上下文包正例过校验():
    assert schema.validate_context_envelope(_上下文包()) == []
    schema.ensure_context_envelope(_上下文包())


def test_回填响应正例过校验():
    assert schema.validate_filled_envelope(_回填()) == []
    schema.ensure_filled_envelope(_回填())


def test_分歧告警可省略():
    """`分歧告警` 是可选字段：没命中分歧点时**不出现该键**，而不是给空数组。"""
    包 = _上下文包()
    del 包['分歧告警']
    assert schema.validate_context_envelope(包) == []


def test_零参数块的输入槽是空数组而不是缺字段():
    """零参数块合法，`输入槽` 给 `[]`；`输入槽` 是**必需**字段，不许省。"""
    assert schema.validate_context_candidate(_候选(输入槽=[])) == []
    缺 = _候选()
    del 缺['输入槽']
    errs = schema.validate_context_candidate(缺)
    assert any('输入槽' in e for e in errs), errs


# ---------------------------------------------------------------------------
# 2. 严格键集：多一个键就报错（四个层级各一条反例）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('位置名,构造', [
    ('上下文包顶层', lambda: _上下文包(多余键='x')),
    ('候选', lambda: _上下文包(候选=[dict(_候选(), 多余键='x')])),
    ('语义命中', lambda: _上下文包(语义命中=[dict(
        schema.make_semantic_hit('产量', 't', 'c', '说'), 多余键='x')])),
    ('回填契约', lambda: _上下文包(回填契约=dict(_契约(), 多余键='x'))),
])
def test_上下文包多余字段被拒(位置名, 构造):
    errs = schema.validate_context_envelope(构造())
    assert any('未知字段' in e and '多余键' in e for e in errs), (位置名, errs)


def test_输入槽多余字段被拒():
    坏槽 = dict(schema.make_slot('赵表', '列表'), 多余键='x')
    errs = schema.validate_context_candidate(_候选(输入槽=[坏槽]))
    assert any('未知字段' in e and '多余键' in e for e in errs), errs


def test_回填响应多余字段被拒():
    errs = schema.validate_filled_envelope(_回填(多余键='x'))
    assert any('未知字段' in e and '多余键' in e for e in errs), errs


def test_多余字段抛SchemaError而不是静默通过():
    with pytest.raises(schema.SchemaError):
        schema.ensure_context_envelope(_上下文包(多余键='x'))
    with pytest.raises(schema.SchemaError):
        schema.ensure_filled_envelope(_回填(多余键='x'))


# ---------------------------------------------------------------------------
# 3. 拒答建议：`覆盖` 是布尔，不是分数也不是字符串
# ---------------------------------------------------------------------------

def test_覆盖必须是布尔而不是分数():
    """这条防的是「把分数阈值偷偷塞回来」——四轮实测已证伪分数拒答（ADR-41 §5）。

    若有人把 `覆盖` 改成 0.73 这样的置信度，本断言当场红。
    """
    for 坏值 in (0.73, 1, 0, '是', None):
        包 = _上下文包(拒答建议={'覆盖': 坏值, '理由': '随便'})
        errs = schema.validate_context_envelope(包)
        assert any('覆盖 必须是布尔' in e for e in errs), (坏值, errs)


def test_判为库外时理由不能为空():
    for 空理由 in ('', '   '):
        包 = _上下文包(拒答建议={'覆盖': False, '理由': 空理由})
        errs = schema.validate_context_envelope(包)
        assert any('理由 不能为空' in e for e in errs), (repr(空理由), errs)


def test_判为库外且给了理由是合法的():
    包 = _上下文包(
        候选=[],
        拒答建议=schema.make_reject_advice(
            覆盖=False, 理由='语义层未登记「销量预测」，且候选块无一自报时序预测口径'))
    assert schema.validate_context_envelope(包) == [], \
        '拒答时候选为空数组是合法形状，不是错误'


def test_须显式选一条必须是布尔():
    坏 = dict(schema.make_divergence_warning('缺陷率', ['缺陷率'], '差 57%'),
              须显式选一条='真')
    errs = schema.validate_context_envelope(_上下文包(分歧告警=[坏]))
    assert any('须显式选一条 必须是布尔' in e for e in errs), errs


# ---------------------------------------------------------------------------
# 4. 回填响应：`模型` 是录像与溯源的锚，不许空
# ---------------------------------------------------------------------------

def test_模型不能是空串():
    for 空 in ('', '  '):
        errs = schema.validate_filled_envelope(_回填(模型=空))
        assert any('模型 不能是空串' in e for e in errs), (repr(空), errs)


def test_模型缺失被拒():
    信封 = _回填()
    del 信封['模型']
    errs = schema.validate_filled_envelope(信封)
    assert any('缺少必需字段「模型」' in e for e in errs), errs


def test_嵌套方案的错误会冒泡():
    """回填响应里的 `方案` 走 `validate_plan`，方案层的违约必须冒到顶层。"""
    errs = schema.validate_filled_envelope(
        _回填(方案={'步骤': [], '野字段': 1}))
    assert any('回填响应.方案' in e for e in errs), errs
    assert any('野字段' in e for e in errs), errs


def test_溯源必须是对象():
    errs = schema.validate_filled_envelope(_回填(溯源=['不是对象']))
    assert any('溯源 必须是对象' in e for e in errs), errs


# ---------------------------------------------------------------------------
# 5. 常量结构：`输入槽`/`输出类型` 是在「选响应」候选之上**追加**的
# ---------------------------------------------------------------------------

def test_上下文候选是选响应候选的超集():
    """键集关系写成断言：将来有人改 `CANDIDATE_REQUIRED` 而忘了这边，会红。"""
    assert schema.CONTEXT_CANDIDATE_REQUIRED[:len(schema.CANDIDATE_REQUIRED)] \
        == schema.CANDIDATE_REQUIRED
    assert set(schema.CONTEXT_CANDIDATE_REQUIRED) - set(schema.CANDIDATE_REQUIRED) \
        == {'输入槽', '输出类型'}
    assert schema.CONTEXT_CANDIDATE_OPTIONAL == schema.CANDIDATE_OPTIONAL


def test_上下文候选前七字段与选响应候选逐字节同构():
    """`make_context_candidate` 复用 `make_candidate`，所以共有字段必须完全一致
    （含 `分数` 保留 4 位小数这个口径）——三通道数字要能逐字比对。"""
    共有 = schema.make_candidate('产量汇总', '制造', 1, '量汇',
                                 '按维度列分组汇总产量，先汇总后相除',
                                 3.14, '[启发式]')
    扩展 = _候选()
    for 键 in 共有:
        assert 扩展[键] == 共有[键], 键
    assert 扩展['分数'] == 共有['分数'] == round(3.14, 4)


def test_普通候选里塞输入槽会被拒():
    """反向锁：「选响应」候选**不该**带 `输入槽`。两个通道的候选形状不同是有意的
    （选响应要小、上下文包要全），混用会让 Web/LSP 拿到没准备好的字段。"""
    errs = schema.validate_candidate(_候选())
    assert any('未知字段' in e and '输入槽' in e for e in errs), errs


# ---------------------------------------------------------------------------
# 6. 两个候选校验器的**字段口径同源**（防复制粘贴漂移）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('坏字段,坏值,片段', [
    ('导出名', '', '导出名 不能是空串'),
    ('层级', '1', '层级 必须是整数'),
    ('分数', '高', '分数 必须是数字'),
    ('名称', 123, '名称 必须是字符串'),
])
def test_两个候选校验器对同一坏字段报同样的错(坏字段, 坏值, 片段):
    普通 = schema.make_candidate('产量汇总', '制造', 1, '量汇', '描述', 3.14)
    普通[坏字段] = 坏值
    扩展 = _候选()
    扩展[坏字段] = 坏值

    普通错 = [e for e in schema.validate_candidate(普通, '位') if 片段 in e]
    扩展错 = [e for e in schema.validate_context_candidate(扩展, '位') if 片段 in e]
    assert 普通错, ('普通候选漏判', 坏字段, schema.validate_candidate(普通, '位'))
    assert 普通错 == 扩展错, ('两边口径漂了', 坏字段, 普通错, 扩展错)


def test_输入槽的名与类型都不能是空串():
    for 键 in schema.SLOT_REQUIRED:
        槽 = dict(schema.make_slot('赵表', '列表'))
        槽[键] = ''
        errs = schema.validate_context_candidate(_候选(输入槽=[槽]))
        assert any('不能是空串' in e and 键 in e for e in errs), (键, errs)


# ---------------------------------------------------------------------------
# 7. 导出面：新增名字都在 __all__ 里（W20 起「字段只从常量取」的配套）
# ---------------------------------------------------------------------------

def test_新增常量与函数都在__all__():
    应有 = [
        'SLOT_REQUIRED',
        'CONTEXT_CANDIDATE_REQUIRED', 'CONTEXT_CANDIDATE_OPTIONAL',
        'SEMANTIC_HIT_REQUIRED', 'SEMANTIC_HIT_OPTIONAL',
        'DIVERGENCE_WARNING_REQUIRED',
        'FILL_CONTRACT_REQUIRED', 'REJECT_ADVICE_REQUIRED',
        'CONTEXT_ENVELOPE_REQUIRED', 'CONTEXT_ENVELOPE_OPTIONAL',
        'FILLED_ENVELOPE_REQUIRED', 'FILLED_ENVELOPE_OPTIONAL',
        'make_slot', 'make_context_candidate', 'make_semantic_hit',
        'make_divergence_warning', 'make_fill_contract', 'make_reject_advice',
        'make_context_envelope', 'make_filled_envelope',
        'validate_context_candidate', 'validate_context_envelope',
        'validate_filled_envelope',
        'ensure_context_envelope', 'ensure_filled_envelope',
    ]
    缺 = [名 for 名 in 应有 if 名 not in schema.__all__]
    assert not 缺, 缺
    没实现 = [名 for 名 in 应有 if not hasattr(schema, 名)]
    assert not 没实现, 没实现
