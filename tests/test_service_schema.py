# -*- coding: utf-8 -*-
"""三通道统一 JSON 协议校验器测试（v0.15.0 W20）。

校验器正反各覆盖：候选 / 方案 / 执行结果三类结构。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.service import schema


# ---- 候选 -------------------------------------------------------------

def test_候选_合法通过():
    c = schema.make_candidate('求和', '数据', 0, '求和', '把一批数相加', 0.42,
                              'blocks.数据.求和')
    assert schema.validate_candidate(c) == []
    assert schema.ensure_candidate(c) is c


def test_候选_分数保留四位():
    c = schema.make_candidate('求和', '数据', 0, '求和', '描述', 0.123456789)
    assert c['分数'] == 0.1235


def test_候选_缺字段被拒():
    errs = schema.validate_candidate({'名称': 'x'})
    assert any('层级' in e for e in errs)
    assert any('分数' in e for e in errs)


def test_候选_未知字段被拒():
    c = schema.make_candidate('求和', '数据', 0, '求和', '描述', 0.1)
    c['乱入'] = 1
    errs = schema.validate_candidate(c)
    assert any('未知字段' in e and '乱入' in e for e in errs)


def test_候选_类型错误被拒():
    c = schema.make_candidate('求和', '数据', 0, '求和', '描述', 0.1)
    c['层级'] = '0'  # 应为 int
    errs = schema.validate_candidate(c)
    assert any('层级' in e for e in errs)


def test_候选_命名空间可选字段合法():
    c = schema.make_candidate('求和', '数据', 0, '求和', '描述', 0.1, 命名空间='')
    assert schema.validate_candidate(c) == []


# ---- 候选.导出名（v0.17.0 W37 Breaking Change）------------------------

def test_候选_导出名是必需字段():
    """`导出名` 进了 CANDIDATE_REQUIRED——协议层的正向断言。"""
    assert '导出名' in schema.CANDIDATE_REQUIRED


def test_候选_导出名与名称不同也合法():
    """正例：目录名≠导出名（`个税` 块导出 `缴税`）是协议允许的形态。"""
    c = schema.make_candidate('个税', '财务', 0, '缴税', '个税速算', 0.9,
                              'blocks.财务.个税')
    assert schema.validate_candidate(c) == []
    assert c['名称'] == '个税' and c['导出名'] == '缴税'


def test_候选_缺导出名被拒():
    """反例：漏填 `导出名` 必须报错，不许静默放过。"""
    c = schema.make_candidate('个税', '财务', 0, '缴税', '个税速算', 0.9)
    del c['导出名']
    errs = schema.validate_candidate(c)
    assert any('导出名' in e and '缺少' in e for e in errs)


def test_候选_导出名类型错误被拒():
    """反例：`导出名` 必须是字符串。"""
    c = schema.make_candidate('求和', '数据', 0, '求和', '描述', 0.1)
    c['导出名'] = 123
    errs = schema.validate_candidate(c)
    assert any('导出名' in e and '字符串' in e for e in errs)


def test_候选_导出名空串被拒():
    """反例：空串会拼出 `导入 。` 这种残句，必须拒——比字段缺失更阴险。"""
    c = schema.make_candidate('求和', '数据', 0, '', '描述', 0.1)
    errs = schema.validate_candidate(c)
    assert any('导出名' in e and '空串' in e for e in errs)


def test_make_candidate_不给导出名直接炸():
    """`make_candidate` 不为 `导出名` 留默认值：缺值在构造点暴露，不兜底。"""
    with pytest.raises(TypeError):
        schema.make_candidate('求和', '数据', 0, '描述', 0.1)


# ---- 方案 -------------------------------------------------------------


def test_方案_最小合法():
    p = schema.make_plan([schema.make_step('求和', '数据', '求和')])
    assert schema.validate_plan(p) == []
    assert schema.ensure_plan(p) is p


def test_方案_完整字段合法():
    p = schema.make_plan(
        [schema.make_step('求和', '数据', '求和', 参数=['赵料'], 说明='把料求和')],
        需求='求和', 共享=[{'名': '赵料', '值': '列 1 2 3'}], 打印=['赵果1'],
    )
    assert schema.validate_plan(p) == []


def test_方案_步骤为空被拒():
    errs = schema.validate_plan({'步骤': []})
    assert any('非空' in e for e in errs)


def test_方案_缺步骤被拒():
    errs = schema.validate_plan({'需求': '求和'})
    assert any('步骤' in e for e in errs)


def test_方案_步骤缺导出名被拒():
    errs = schema.validate_plan({'步骤': [{'块': '求和', '领域': '数据'}]})
    assert any('导出名' in e for e in errs)


def test_方案_步骤未知字段被拒():
    p = schema.make_plan([schema.make_step('求和', '数据', '求和')])
    p['步骤'][0]['乱入'] = 1
    errs = schema.validate_plan(p)
    assert any('未知字段' in e for e in errs)


def test_方案_ensure_抛错():
    with pytest.raises(schema.SchemaError):
        schema.ensure_plan({'步骤': []})


# ---- 执行结果 ---------------------------------------------------------

def test_结果_合法通过():
    r = schema.make_result(stdout='6\n', stderr='', 返回值='6', 耗时毫秒=1.5)
    assert schema.validate_result(r) == []
    assert '错误' not in r  # 成功时不带 错误 字段


def test_结果_带错误合法():
    r = schema.make_result(错误='RuntimeError: boom')
    assert schema.validate_result(r) == []
    assert r['错误'] == 'RuntimeError: boom'


def test_结果_缺字段被拒():
    errs = schema.validate_result({'stdout': ''})
    assert any('返回值' in e for e in errs)
    assert any('耗时毫秒' in e for e in errs)


def test_结果_耗时类型错误被拒():
    r = schema.make_result()
    r['耗时毫秒'] = '快'
    errs = schema.validate_result(r)
    assert any('耗时毫秒' in e for e in errs)


# ---- Hit → 候选 -------------------------------------------------------

def test_candidate_from_hit_填层级():
    from jikuai.ai.retrieval import Hit
    h = Hit(score=0.5, name='求和', domain='数据', description='把数相加', path='blocks.数据.求和')
    c = schema.candidate_from_hit(h)
    assert schema.validate_candidate(c) == []
    assert c['名称'] == '求和'
    assert isinstance(c['层级'], int)


def test_level_table_含内置块():
    table = schema.level_table()
    assert '求和' in table
    assert isinstance(table['求和'], int)


# ---- export_table / candidate_from_hit 的 导出名（W37）-----------------

def test_export_table_含内置块():
    table = schema.export_table()
    assert '求和' in table
    assert isinstance(table['求和'], str) and table['求和']


def test_export_table_目录名不等于导出名():
    """真实块 `个税`（领域 财务）导出 `缴税`——W37 要修的正是这类块。"""
    table = schema.export_table()
    assert table['个税'] == '缴税'


def test_candidate_from_hit_填导出名_目录名不同():
    """端到端：Hit(个税) → 候选.导出名 == 缴税，而不是回退成「个税」。"""
    from jikuai.ai.retrieval import Hit
    h = Hit(score=0.9, name='个税', domain='财务', description='个税速算',
            path='blocks.财务.个税')
    c = schema.candidate_from_hit(h)
    assert schema.validate_candidate(c) == []
    assert c['名称'] == '个税'
    assert c['导出名'] == '缴税'


def test_candidate_from_hit_索引缺块时退回名称():
    """索引过期（块查不到）时退回 `名称`，且降级只发生在这一处。"""
    from jikuai.ai.retrieval import Hit
    h = Hit(score=0.1, name='压根不存在的块', domain='数据', description='x', path='')
    c = schema.candidate_from_hit(h)
    assert schema.validate_candidate(c) == []
    assert c['导出名'] == '压根不存在的块'


def test_export_table_多导出时确定性择一(tmp_path):
    """多个 `导出` 时：与块同名的优先，否则排序首位——与 `_推导出名` 同 tie-break。"""
    import json as _json
    p = tmp_path / '索引.json'
    p.write_text(_json.dumps({'块': [
        {'名称': '甲', '导出': ['乙', '甲', '丙']},   # 同名优先
        {'名称': '丁', '导出': ['戊', '乙']},         # 无同名 → 排序首位
        {'名称': '己', '导出': []},                   # 空导出 → 不进表
    ]}, ensure_ascii=False), encoding='utf-8')
    table = schema.export_table(str(p))
    assert table['甲'] == '甲'
    assert table['丁'] == sorted(['戊', '乙'])[0]
    assert '己' not in table

