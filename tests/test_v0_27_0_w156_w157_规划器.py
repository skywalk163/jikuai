# -*- coding: utf-8 -*-
"""v0.27.0 W156-W157 · 规划器：上下文包构造器 + 五条硬规则回填校验器。

正本 `docs/ADR-41-规划器与NL层.md` §3（上下文包）与 §4（五条硬规则）。

两半的测试策略刻意不同：

- **W156 用真块库、真评测集**。上下文包的价值全在「候选带真实输入槽」上，拿造的
  槽去测等于测了个自己写的假货。ADR-41 §3 的根因（W145 那 124 步手写实参）要靠
  「槽名槽类型逐字来自 `索引.json`」这条断言钉住。
- **W157 用手造上下文包**。五条硬规则要覆盖「幻觉块名」「分歧点两侧同时出现」这类
  情形，真检索排出来什么候选不由测试说了算——拿真包测规则等于把断言绑到 TF-IDF
  排序上，块库一改就红一片。手造包让每条规则的反例都精确可控。
"""

import importlib.util
import io
import json
import os

import pytest

_仓库根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_桥目录 = os.path.join(_仓库根, 'tools', 'ai-bridge')


def _加载(模块名, 路径):
    """按路径加载不在包里的模块（`tools/ai-bridge/` 与 `scripts/` 都不是包）。"""
    spec = importlib.util.spec_from_file_location(模块名, 路径)
    模块 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(模块)
    return 模块


planner = _加载('_测试用规划器', os.path.join(_桥目录, 'planner.py'))

from jikuai.service import schema  # noqa: E402  （planner 已把 src/ 挂上 sys.path）


def _读json(路径):
    with io.open(路径, 'r', encoding='utf-8') as f:
        return json.load(f)


def _评测集(文件名):
    return _读json(os.path.join(_桥目录, 文件名))['用例']


调优集 = _评测集('评测集-chatbi.json')
留出集 = _评测集('评测集-chatbi-留出.json')


@pytest.fixture(scope='module')
def 索引表():
    return planner._索引表()


# ===========================================================================
# W156 · 上下文包构造器
# ===========================================================================

@pytest.mark.parametrize('用例', 调优集, ids=[c['id'] for c in 调优集])
def test_调优集十五条都能出合法上下文包(用例):
    """DoD：15 条调优集问句各自产出一个过 schema 校验的上下文包。

    `build_context` 内部已自查过一遍，这里再查一次不是重复——自查是「规划器不许吐
    非法包」的运行期守卫，这条测试是「守卫真的在守」的证据（v0.22.0 主教训）。
    """
    包 = planner.build_context(用例['需求'])
    assert schema.validate_context_envelope(包) == []
    assert 包['需求'] == 用例['需求']
    assert 包['候选'], '检索永远返回 top-K，候选不该为空'


@pytest.mark.parametrize('用例', 留出集, ids=[c['id'] for c in 留出集])
def test_留出集五条都能出合法上下文包(用例):
    """留出集只当裁判：这里只断言「能出合法包」，不看它的命中明细去调触发词。"""
    包 = planner.build_context(用例['需求'])
    assert schema.validate_context_envelope(包) == []


def test_候选带输入槽与输出类型这是本轮的根因修复():
    """ADR-41 §3：「选响应」候选**不带输入槽**，正是 W145 里 LLM 写不出实参的根因。

    所以这条断言不是形状检查，是本轮存在的理由——每条候选都必须带 `输入槽`
    （可以是空数组，零参数块合法）与非空 `输出类型`。
    """
    包 = planner.build_context('2026年6月各车型的总产量是多少？')
    for c in 包['候选']:
        assert '输入槽' in c, '候选缺 输入槽 就退回了「选响应」，本轮白做'
        assert isinstance(c['输入槽'], list)
        assert c['输出类型'], '输出类型 不能是空串——LLM 靠它接链'
        for 槽 in c['输入槽']:
            assert 槽['名'] and 槽['类型']


@pytest.mark.parametrize('用例', 调优集 + 留出集,
                         ids=[c['id'] for c in 调优集 + 留出集])
def test_槽数逐条对齐索引不是编出来的(用例, 索引表):
    """槽信息的唯一真源是 `索引.json` 的 `输入`。

    这条钉的是「别在规划器里给个默认槽数糊过去」：编一组槽名让 LLM 照着填，比不给
    槽信息更坏——LLM 会填得一本正经，粘合器再按真槽数报「长度不对」，人读到的两套
    槽名对不上。
    """
    包 = planner.build_context(用例['需求'])
    for c in 包['候选']:
        条目 = 索引表.get(c['名称'])
        assert 条目 is not None, '候选 %s 不在索引里（索引过期？）' % c['名称']
        期望 = 条目.get('输入') or []
        assert [s['名'] for s in c['输入槽']] == [s.get('名') for s in 期望]
        assert [s['类型'] for s in c['输入槽']] == [
            planner.类型串(s.get('类型')) for s in 期望]


def test_类型串_结构化类型按ADR26写法渲染(索引表):
    """`类型串` 与粘合器共用一套渲染，两边看到的类型名必须一模一样。"""
    表载入 = 索引表['表载入']
    assert [planner.类型串(s['类型']) for s in 表载入['输入']] == ['字符串']
    assert planner.类型串(表载入['输出']['类型']) == '列表<字典<字符串,任意>>'
    窗间对比 = 索引表['窗间对比']
    assert [planner.类型串(s['类型']) for s in 窗间对比['输入']] == [
        '列表<字典<字符串,任意>>', '列表<字典<字符串,任意>>',
        '列表<字符串>', '字符串']
    # 缺省与不认识的一律落顶类型，保守不猜
    assert planner.类型串(None) == '任意'
    assert planner.类型串('没这个类型') == '任意'


def test_索引读不到时拒绝出包而不是给空槽(tmp_path):
    """W157 review C002：索引读不到时若把 `输入槽` 一律落成空数组，`validate_filled`
    的规则 1（`len(实参) == len(槽表)`）会退化成「实参必须是空数组」——`参数: []`
    全部放行，防静默错绑的唯一闸被悄悄卸掉，而 `build_context` 的自查**拦不住**
    （空 `输入槽` 是协议显式允许的合法形状）。故这里必须 fail-closed：宁可不出包。
    """
    缺 = tmp_path / '没有这个索引.json'
    with pytest.raises(schema.SchemaError) as e:
        planner.build_context('2026年6月各车型的总产量', 索引路径=str(缺))
    assert '读不到块索引' in str(e.value)
    assert planner._索引表(str(缺)) is None, '读不到要回 None，不是空表'

    坏 = tmp_path / '坏索引.json'
    坏.write_text('{"不是块": []}', encoding='utf-8')
    assert planner._索引表(str(坏)) is None, '键都不对等于索引损坏，同样不许静默降级'


def test_语义命中把口径备注映射成口径说明():
    """语义层文件里叫 `口径备注`，协议里叫 `口径说明`——映射只在 planner 一处。"""
    命中 = planner.语义命中('2026年6月各车型的总产量')
    词表 = {h['业务词']: h for h in 命中}
    assert '产量' in 词表
    条 = 词表['产量']
    assert 条['表'] == 'fact_production_actual'
    assert 条['字段'] == 'actual_quantity'
    assert '求和' in 条['口径说明'], '口径说明 得真是语义层那段口径备注'
    assert set(条) == set(schema.SEMANTIC_HIT_REQUIRED)


def test_语义命中_同义词也算命中():
    """问句用同义词（「下线量」）时也要锚到同一列，否则 LLM 只能猜列名。"""
    命中 = planner.语义命中('6月各产线的下线量')
    assert 'fact_production_actual' in {h['表'] for h in 命中}
    assert 'actual_quantity' in {h['字段'] for h in 命中}


def test_语义命中_非制造问句为空这是作用域边界():
    """语义层只登记制造域业务词，别的域一条都不该命中——这是边界，不是缺陷。"""
    assert planner.语义命中('月薪两万个税多少') == []


def test_分歧告警_缺陷率问句出告警且须显式选一条():
    """缺陷率那处只有一侧有块，`两侧块名` 只装一个名字且 `须显式选一条` 恒 True。"""
    包 = planner.build_context('2026年6月M003车型缺陷率相对5月是否明显上升？')
    名单 = [w['分歧点'] for w in 包['分歧告警']]
    assert any('缺陷率' in n for n in 名单)
    警 = next(w for w in 包['分歧告警'] if '缺陷率' in w['分歧点'])
    assert 警['须显式选一条'] is True
    # 讲口径分歧要拿缺陷率那 57% 去讲（ADR-41 §5 明确点名）
    assert '0.050550' in 警['实测差值'] and '0.032218' in 警['实测差值']
    assert 警['两侧块名'] == ['缺陷率'], (
        '这一处的另一侧口径（行级比率再平均）没有对应块也不会有——它是错的')


def test_分歧告警_时序对比问句带Q_PUB_004的实测数字():
    包 = planner.build_context('L002产线单车电耗相比平时是否异常升高？')
    警 = next(w for w in 包['分歧告警'] if '时序对比' in w['分歧点'])
    assert 警['两侧块名'] == ['窗间对比', '基线偏离']
    assert '151.8007' in 警['实测差值'] and '191.0953' in 警['实测差值']


def test_分歧告警_无关问句干脆不出这个键():
    """可选字段缺省即不出现（沿用全仓约定，形状最小化）。"""
    包 = planner.build_context('把这张CSV读进来看看有几行')
    assert '分歧告警' not in 包


def test_分歧点表与G22门禁的块名集合一致():
    """两张表各有各的形状（门禁携带断言词，规划器携带触发词），但**块名必须一致**。

    这条是防漂：ADR-40 §5 再加一处分歧点时，只改门禁不改规划器 ⇒ 规划器不会出告警，
    LLM 就会在那处含糊过去；只改规划器不改门禁 ⇒ 双块缺一侧也没人拦。
    """
    门禁 = _加载('_测试用G22',
                 os.path.join(_仓库根, 'scripts',
                              'check_manufacturing_contract.py'))
    def 门禁块名(处):
        if '两侧' in 处:
            return {侧['块'] for 侧 in 处['两侧']}
        return {处['块']}
    期望 = [门禁块名(处) for 处 in 门禁.分歧点表]
    实际 = [set(处['两侧']) for 处 in planner.分歧点表]
    assert len(planner.分歧点表) == len(门禁.分歧点表) == 4
    assert 实际 == 期望


def test_拒答建议_覆盖是布尔且理由登记已知缺口():
    """`覆盖` 必须是布尔（不是分数），`理由` 必须登记制造域先验缺口与「非阈值」口径。"""
    包 = planner.build_context('2026年6月各车型的总产量是多少？')
    建议 = 包['拒答建议']
    assert 建议['覆盖'] is True
    assert isinstance(建议['覆盖'], bool), '不是分数，也不是字符串'
    # ADR-41 §5 要求把缺口写在明面上：制造域拿不到领域先验加分这件事必须登记
    assert '_DOMAIN_KEYWORDS' in 建议['理由']
    assert '制造' in 建议['理由']
    # 也必须点明拒答不是分数阈值——四轮实测已证伪
    assert 'AUC' in 建议['理由']


def test_拒答建议_非制造问句判未覆盖且说清这是作用域边界():
    """「判为未覆盖」在这里**不等于**「块库没这个能力」，理由里必须说清。

    不说清就会变成一条假结论：个税块明明在库里，问句却被规划器判未覆盖——
    那是本轮语义层只登记制造域造成的，属作用域边界。
    """
    包 = planner.build_context('月薪两万个税多少')
    建议 = 包['拒答建议']
    assert 建议['覆盖'] is False
    assert 建议['理由'].strip()
    assert '语义层' in 建议['理由']
    assert '不等于块库没有对应能力' in 建议['理由']


def test_回填契约_必填项点明参数不许省及其根因():
    """契约不光要说「参数必填」，还要带上 W145 静默错绑的根因，否则读者会当它是洁癖。"""
    包 = planner.build_context('2026年6月各车型的总产量是多少？')
    契约 = 包['回填契约']
    assert 契约['目标'] == '方案'
    全文 = '\n'.join(契约['必填'] + 契约['禁止'])
    assert '参数' in 全文 and '输入槽' in 全文
    assert '静默错绑' in 全文, '不写根因，LLM 与人都会以为省略 参数 只是「不够严谨」'
    assert '幻觉块名' in 全文


def test_上下文包_字段名全部来自schema常量():
    """W20：通道里不许出现手写字段名。这条从**产物形状**反查一遍。"""
    包 = planner.build_context('2026年6月各车型的总产量是多少？')
    assert set(包) <= set(schema.CONTEXT_ENVELOPE_REQUIRED) | set(
        schema.CONTEXT_ENVELOPE_OPTIONAL)
    assert set(schema.CONTEXT_ENVELOPE_REQUIRED) <= set(包)
    for c in 包['候选']:
        允许 = set(schema.CONTEXT_CANDIDATE_REQUIRED) | set(
            schema.CONTEXT_CANDIDATE_OPTIONAL)
        assert set(c) <= 允许
    for 槽 in [s for c in 包['候选'] for s in c['输入槽']]:
        assert set(槽) == set(schema.SLOT_REQUIRED)


# ===========================================================================
# W157 · 五条硬规则（手造上下文包，每条规则一类反例）
# ===========================================================================

def _候选(名称, 领域='制造', 导出名=None, 槽=(), 输出类型='列表<字典<字符串,任意>>'):
    return schema.make_context_candidate(
        名称=名称, 领域=领域, 层级=0, 导出名=导出名 or 名称,
        描述='测试候选', 分数=1.0,
        输入槽=[schema.make_slot(名, 类型) for 名, 类型 in 槽],
        输出类型=输出类型)


def _包(候选, 告警=None, 需求='测试需求'):
    return schema.make_context_envelope(
        需求=需求, 语义命中=[], 候选=候选,
        回填契约=schema.make_fill_contract(['参数'], ['多余键']),
        拒答建议=schema.make_reject_advice(True, '测试'),
        分歧告警=告警)


def _回填(步骤, 共享=None, 需求='测试需求', 模型='人工'):
    return schema.make_filled_envelope(
        需求, schema.make_plan(步骤, 需求=需求, 共享=共享), 模型)


@pytest.fixture
def 表载入候选():
    return _候选('表载入', 导出名='读表', 槽=(('路径', '字符串'),))


@pytest.fixture
def 产量汇总候选():
    return _候选('产量汇总', 导出名='计产',
                 槽=(('表', '列表<字典<字符串,任意>>'), ('维度列', '列表<字符串>')))


def test_正例_五条规则全过(表载入候选, 产量汇总候选):
    包 = _包([表载入候选, 产量汇总候选])
    回填 = _回填([
        schema.make_step('表载入', '制造', '读表', 参数=['赵路径']),
        schema.make_step('产量汇总', '制造', '计产', 参数=['赵果1', '赵维度']),
    ], 共享=[{'名': '赵路径', '值': '"a.csv"', '类型': '字符串'},
             {'名': '赵维度', '值': '["model_id"]', '类型': '列表'}])
    assert planner.validate_filled(回填, 包) == []
    assert planner.validate_filled(回填, 包, 严格=True) == []
    assert planner.ensure_filled(回填, 包) is 回填


# --- 反例 1：缺参数（规则 1）------------------------------------------------

def test_反例1_缺参数(表载入候选):
    """省略 `参数` 让粘合器自动推链会**静默错绑**（W145 实测），所以根本不许省。"""
    包 = _包([表载入候选])
    回填 = _回填([schema.make_step('表载入', '制造', '读表')])
    理由 = planner.validate_filled(回填, 包)
    assert 理由, '缺 参数 必须拒'
    合 = '\n'.join(理由)
    assert '缺 参数' in 合
    # 拒绝理由必须可操作：说清该块有几个槽、槽名槽类型分别是什么
    assert '路径:字符串' in 合
    assert '静默错绑' in 合


def test_反例1b_参数长度不等于槽数(产量汇总候选):
    """长度不符时理由要报清「给了几个 / 要几个」并列出缺的那个槽的名与类型。"""
    包 = _包([产量汇总候选])
    回填 = _回填([schema.make_step('产量汇总', '制造', '计产', 参数=['赵表'])])
    理由 = planner.validate_filled(回填, 包)
    合 = '\n'.join(理由)
    assert '给了 1 个' in 合 and '要 2 个' in 合
    assert '维度列:列表<字符串>' in 合


def test_反例1c_零参数块要空数组而不是省略():
    """零参数块的 `输入槽` 合法地是空数组——但 `参数` 仍要写成 `[]`，不许缺键。"""
    包 = _包([_候选('取时钟', 领域='工具', 导出名='现在', 槽=())])
    缺 = _回填([schema.make_step('取时钟', '工具', '现在')])
    assert planner.validate_filled(缺, 包)
    空 = _回填([schema.make_step('取时钟', '工具', '现在', 参数=[])])
    assert planner.validate_filled(空, 包) == []


@pytest.mark.parametrize('坏实参', [{'嵌套': 1}, None, 123, '', '   ', ['a']],
                         ids=['字典', '空值', '整数', '空串', '纯空白', '数组'])
def test_反例1d_实参不是变量名一律拒且严格模式不崩(表载入候选, 坏实参):
    """W157 review C001/C005：`schema.validate_plan` 只校验 `参数` 是数组、不管元素
    类型，所以规则 1 只比长度时 `参数: [{...}]` 会**静默放行**（粘合器渲染出无意义
    实参），而同一份输入在 `严格=True` 下会把校验器本身打挂（`实 not in 无声明` 对
    set 做成员测试，拿到 dict/list 抛 TypeError）。两条同根，一处校验一起堵。
    """
    包 = _包([表载入候选])
    回填 = _回填([schema.make_step('表载入', '制造', '读表', 参数=[坏实参])],
                 共享=[{'名': '赵路径', '值': '"a.csv"'}])
    理由 = planner.validate_filled(回填, 包)
    assert 理由, '非严格模式也必须拒——静默放行比报错糟得多'
    assert any('不是变量名/共享常量名' in r for r in 理由)
    assert any('路径:字符串' in r for r in 理由), '理由要说清该槽要什么'
    # 严格模式必须同样给出理由，而不是抛 TypeError 把校验器打挂
    assert planner.validate_filled(回填, 包, 严格=True)



# --- 反例 2：幻觉块名（规则 2）----------------------------------------------

def test_反例2_幻觉块名(表载入候选):
    """白名单：候选之外的块名一律拒。检索层不拒答，这里是唯一的闸。"""
    包 = _包([表载入候选])
    回填 = _回填([schema.make_step('时序预测', '制造', '预测', 参数=['赵表'])])
    理由 = planner.validate_filled(回填, 包)
    合 = '\n'.join(理由)
    assert '不在上下文包的 候选 里' in 合
    assert '表载入/制造/读表' in 合, '拒绝理由要给出可选组合，否则不可操作'


def test_反例2b_块名对但导出名错也拒(表载入候选):
    """`表载入` 导出的是 `读表`。目录名≠导出名的块拼错导出名，组出来的导入行是死的。"""
    包 = _包([表载入候选])
    回填 = _回填([schema.make_step('表载入', '制造', '表载入', 参数=['赵路径'])])
    assert planner.validate_filled(回填, 包)


def test_反例2c_领域错也拒(表载入候选):
    包 = _包([表载入候选])
    回填 = _回填([schema.make_step('表载入', '数据', '读表', 参数=['赵路径'])])
    assert planner.validate_filled(回填, 包)


# --- 反例 3：口径分歧（规则 3）----------------------------------------------

@pytest.fixture
def 双侧包():
    告警 = [schema.make_divergence_warning(
        '单车电耗（ADR-40 §5.2）', ['单车电耗现成', '单车电耗重算'],
        '用现成比率列 vs 关联产量表重算')]
    return _包([_候选('单车电耗现成', 导出名='车耗现', 槽=(('表', '列表<字典<字符串,任意>>'),)),
                _候选('单车电耗重算', 导出名='车耗算', 槽=(('表', '列表<字典<字符串,任意>>'),))],
               告警=告警)


def test_反例3_分歧点两侧同时出现(双侧包):
    """两侧是两个不同的答案，不是两个步骤。"""
    回填 = _回填([
        schema.make_step('单车电耗现成', '制造', '车耗现', 参数=['赵表']),
        schema.make_step('单车电耗重算', '制造', '车耗算', 参数=['赵表']),
    ])
    理由 = planner.validate_filled(回填, 双侧包)
    合 = '\n'.join(理由)
    assert '同时出现' in 合 and '必须显式选一条' in 合
    assert '单车电耗现成' in 合 and '单车电耗重算' in 合


def test_反例3b_命中口径却两侧都不选(双侧包):
    """告警在场 = 问句命中了这处口径。一侧都不选就是含糊过去。"""
    双侧包['候选'].append(_候选('排序', 导出名='定序', 槽=(('表', '列表<字典<字符串,任意>>'),)))
    回填 = _回填([schema.make_step('排序', '制造', '定序', 参数=['赵表'])])
    理由 = planner.validate_filled(回填, 双侧包)
    合 = '\n'.join(理由)
    assert '一个都没选' in 合
    assert '单车电耗现成 / 单车电耗重算' in 合


def test_反例3c_单侧分歧点必须用那个块():
    """缺陷率那处只有一条正确口径，另一侧没有对应块——不许自己另写行级比率。"""
    告警 = [schema.make_divergence_warning(
        '缺陷率（ADR-40 §5.3）', ['缺陷率'],
        '先汇总后相除 0.050550 vs 行级比率平均 0.032218，差 57%')]
    包 = _包([_候选('缺陷率', 导出名='陷率', 槽=(('缺陷表', '列表<字典<字符串,任意>>'),)),
              _候选('缺陷汇总', 导出名='计陷', 槽=(('表', '列表<字典<字符串,任意>>'),))],
             告警=告警)
    回填 = _回填([schema.make_step('缺陷汇总', '制造', '计陷', 参数=['赵表'])])
    理由 = planner.validate_filled(回填, 包)
    合 = '\n'.join(理由)
    assert '没用 `缺陷率` 块' in 合
    assert '不许自己另算' in 合
    # 选对了就该过
    对 = _回填([schema.make_step('缺陷率', '制造', '陷率', 参数=['赵缺陷表'])])
    assert planner.validate_filled(对, 包) == []


def test_正例3d_无告警时不管选哪个(表载入候选):
    """没命中分歧点就别拿口径规则烦人——规则 3 的触发源只有 `分歧告警`。"""
    包 = _包([表载入候选])
    assert '分歧告警' not in 包
    回填 = _回填([schema.make_step('表载入', '制造', '读表', 参数=['赵路径'])])
    assert planner.validate_filled(回填, 包) == []


# --- 反例 4：协议多余字段（规则 4）------------------------------------------

def test_反例4_协议多余字段(表载入候选):
    """键集是白名单，多一个就拒。形状错误**先回**，不叠派生噪音。"""
    包 = _包([表载入候选])
    回填 = _回填([schema.make_step('表载入', '制造', '读表', 参数=['赵路径'])])
    回填['方案']['步骤'][0]['置信度'] = 0.9
    理由 = planner.validate_filled(回填, 包)
    assert 理由
    assert any('置信度' in r for r in 理由)


def test_反例4b_模型不可省也不可空(表载入候选):
    """录像回放要靠 `模型` 分辨「换模型后结果变了」还是「链路本身变了」。"""
    包 = _包([表载入候选])
    步 = [schema.make_step('表载入', '制造', '读表', 参数=['赵路径'])]
    空模型 = schema.make_filled_envelope('测试需求',
                                         schema.make_plan(步), '   ')
    assert planner.validate_filled(空模型, 包)
    缺模型 = {'需求': '测试需求', '方案': schema.make_plan(步)}
    assert planner.validate_filled(缺模型, 包)


def test_反例4c_形状不对时不叠派生噪音(表载入候选):
    """`步骤` 不是数组时，若还去跑规则 1/2/3 会吐一堆读空对象产生的假理由。"""
    包 = _包([表载入候选])
    回填 = {'需求': '测试需求', '方案': {'步骤': '不是数组'}, '模型': '人工'}
    理由 = planner.validate_filled(回填, 包)
    assert 理由
    assert all('白名单' not in r for r in 理由)


# --- 反例 5：严格模式下裸共享常量喂精确槽（规则 5）--------------------------

def test_反例5_严格下无声明类型的共享常量喂精确槽(产量汇总候选):
    """ADR-41 §6：没声明 `类型` 的共享常量在类型图里是 `任意`，对任何形参都「兼容」。"""
    包 = _包([产量汇总候选])
    回填 = _回填([schema.make_step('产量汇总', '制造', '计产',
                                   参数=['赵表', '赵维度'])],
                 共享=[{'名': '赵表', '值': '[]', '类型': '列表'},
                       {'名': '赵维度', '值': '["model_id"]'}])
    assert planner.validate_filled(回填, 包) == [], '默认不开，这是 --严格 专属'
    理由 = planner.validate_filled(回填, 包, 严格=True)
    合 = '\n'.join(理由)
    assert '赵维度' in 合 and '列表<字符串>' in 合
    assert '补 类型' in 合, '拒绝理由要说清怎么修'


def test_正例5b_严格下裸共享常量喂任意槽放行():
    """槽本身就是 `任意` 时，常量声不声明类型都不改变可喂性——别无谓地拦。"""
    包 = _包([_候选('打印值', 领域='工具', 导出名='示值', 槽=(('值', '任意'),))])
    回填 = _回填([schema.make_step('打印值', '工具', '示值', 参数=['赵啥'])],
                 共享=[{'名': '赵啥', '值': '1'}])
    assert planner.validate_filled(回填, 包, 严格=True) == []


# --- 其它 -------------------------------------------------------------------

def test_ensure_filled_不通过抛SchemaError(表载入候选):
    包 = _包([表载入候选])
    回填 = _回填([schema.make_step('表载入', '制造', '读表')])
    with pytest.raises(schema.SchemaError) as e:
        planner.ensure_filled(回填, 包)
    assert '参数' in str(e.value)


def test_上下文包不是对象时给一句人话而不是崩(表载入候选):
    回填 = _回填([schema.make_step('表载入', '制造', '读表', 参数=['赵路径'])])
    理由 = planner.validate_filled(回填, None)
    assert 理由 == ['上下文包不是对象，无法当参照做校验']


def test_空候选包收到方案时的拒绝理由点明该拒答():
    """`拒答建议.覆盖=False` 时本就该拒答而不是回填方案——理由里要说这句。"""
    包 = _包([])
    回填 = _回填([schema.make_step('表载入', '制造', '读表', 参数=['赵路径'])])
    合 = '\n'.join(planner.validate_filled(回填, 包))
    assert '本就该拒答' in 合
