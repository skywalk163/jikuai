# -*- coding: utf-8 -*-
"""v0.28.0 W174 · 语义层直取旁路（ADR-41 §9）。

本文件的断言分三类，按「防什么」组织：

1. **钉住证伪结论**：WBS 原意的「一跳只注入口径块」在 `白名单可承载率` 上是 0.0%。
   所以有一条测试专门断言「只做一跳装不下 Q_PUB_009 的方案」——后来者若把旁路简化回
   一跳，那条会红，而不是让指标悄悄退回 0。
2. **钉住边界**：旁路只在有制造域语义命中时启动；`retrieval.retrieve` 永远不吐
   `[语义层]`（检索层一行未改，`jk 块 选` 照旧永不拒答）。
3. **钉住代价**：近边缘负例的兄弟块诱骗率因旁路上升，且上升**只来自一条**
   （`停线汇总`），登记的 §5.5 分歧点对侧块**旁路一条都没带入**。把这两件事写成断言，
   是因为「旁路零成本」是最容易被后来者想当然的一句话。
"""

import importlib.util
import io
import json
import os

_仓库根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_桥目录 = os.path.join(_仓库根, 'tools', 'ai-bridge')


def _加载(模块名, 路径):
    spec = importlib.util.spec_from_file_location(模块名, 路径)
    模块 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(模块)
    return 模块


planner = _加载('_w174用规划器', os.path.join(_桥目录, 'planner.py'))

from jikuai.ai import retrieval  # noqa: E402  （planner 已把 src/ 挂上 sys.path）
from jikuai.service import schema  # noqa: E402

_名称, _领域, _层级, _导出名, _描述, _分数, _路径, _输入槽, _输出类型 = (
    schema.CONTEXT_CANDIDATE_REQUIRED)
_候选键, = (schema.CONTEXT_ENVELOPE_REQUIRED[2],)

#: 一条典型制造域问句：录像 Q_PUB_009 的需求，方案 = 窗口 + 能耗汇总 + 表载入。
#: 选它是因为三块里有两块（`窗口` #23、`表载入` #59）词面永远捞不上来。
_制造问句 = '2026年6月各车间的总电耗是多少'
_Q_PUB_009方案 = ('窗口', '能耗汇总', '表载入')

#: 一条七域之外、语义层登记不到的问句 —— 旁路必须整条不启动。
_域外问句 = '月薪两万个税多少'


def _包(需求, top=8):
    return planner.build_context(需求, top=top)


def _名字集(包):
    return {c[_名称] for c in 包[_候选键]}


def _旁路名字集(包):
    return {c[_名称] for c in 包[_候选键]
            if c[_路径] == retrieval.PATH_SEMANTIC}


# ---------------------------------------------------------------------------
# 一、路径取值与检索层边界
# ---------------------------------------------------------------------------

def test_三个路径取值同源且互不相同():
    取值 = {retrieval.PATH_HEURISTIC, retrieval.PATH_NEURAL,
            retrieval.PATH_SEMANTIC}
    assert len(取值) == 3
    assert retrieval.PATH_SEMANTIC == '[语义层]'


def test_路径取值从ai包顶层也能取到():
    import jikuai.ai as ai
    assert ai.PATH_SEMANTIC is retrieval.PATH_SEMANTIC
    assert 'PATH_SEMANTIC' in ai.__all__


def test_检索层永远不吐语义层路径():
    """旁路整个实现在规划器层。检索器若哪天自己开始吐 `[语义层]`，
    两层的数字就再也分不开了（AGENTS.md §四 反复强调的那件事）。"""
    for 需求 in (_制造问句, _域外问句, '各产线的达成率'):
        for h in retrieval.retrieve(需求, 40):
            assert h.path in (retrieval.PATH_HEURISTIC, retrieval.PATH_NEURAL)


# ---------------------------------------------------------------------------
# 二、旁路启动条件与注入内容
# ---------------------------------------------------------------------------

def test_制造域问句会注入语义层候选():
    包 = _包(_制造问句)
    assert _旁路名字集(包), '有制造域语义命中却一条都没注入'


def test_域外问句旁路整条不启动():
    包 = _包(_域外问句)
    assert _旁路名字集(包) == set()
    # 没有旁路时候选数就是 top，这条顺带钉住「旁路不改词面那一段」
    assert len(包[_候选键]) == 8


def test_通用管道块靠旁路进来而不是靠词面():
    """`表载入`/`窗口` 的词面永远捞不上来（TF-IDF 名次 14-95），它们必须是旁路带入的。

    这条是 W174 的核心收益点：一跳只注入口径块时这两块进不来，承载率因此是 0.0%。
    """
    包 = _包(_制造问句)
    旁 = _旁路名字集(包)
    词面 = {h.name for h in retrieval.retrieve(_制造问句, 8)}
    for 块名 in ('表载入', '窗口'):
        assert 块名 in 旁, '%s 没被旁路带入' % 块名
        assert 块名 not in 词面, '%s 竟然在 top-8 词面候选里，本测试的前提变了' % 块名


def test_录像方案的块在K等于8下全进候选():
    """`白名单可承载率` 的口径：录像方案用到的块全部落进候选。旁路前这里是 0.0%。"""
    名 = _名字集(_包(_制造问句))
    assert set(_Q_PUB_009方案) <= 名, '缺 %s' % sorted(set(_Q_PUB_009方案) - 名)


def test_只做一跳装不下这份方案():
    """**钉住 WBS 原意被证伪**：只按「命中业务词的表 → 描述提到该表的制造域块」注入
    （一跳），`表载入`/`窗口` 一个都进不来 —— 它们的描述里没有任何表名。

    这条不调 `_语义旁路`，而是就地复算那一跳，免得把断言绑到实现的内部函数签名上。
    """
    命中 = planner.语义命中(_制造问句)
    表集 = {h[_表] for h in 命中 if h[_表]} if 命中 else set()
    assert 表集, '这条问句连语义命中都没有，测试前提变了'
    元表 = planner._索引表()
    一跳 = {名 for 名, 条 in 元表.items()
            if '制造' in (条.get('领域') or ())
            and any(表 in (条.get('描述') or '') for 表 in 表集)}
    for 块名 in ('表载入', '窗口'):
        assert 块名 not in 一跳, (
            '%s 竟然能被一跳捞到 —— 若真如此，W174「一跳收益 0.0%%」的结论要重测'
            % 块名)
    assert not set(_Q_PUB_009方案) <= 一跳


_业务词, _表, _字段, _口径说明 = schema.SEMANTIC_HIT_REQUIRED


# ---------------------------------------------------------------------------
# 三、候选形状与去重
# ---------------------------------------------------------------------------

def test_旁路候选是合法上下文包候选():
    包 = _包(_制造问句)
    旁 = [c for c in 包[_候选键] if c[_路径] == retrieval.PATH_SEMANTIC]
    assert 旁
    for c in 旁:
        assert set(c) >= set(schema.CONTEXT_CANDIDATE_REQUIRED)
        assert isinstance(c[_输入槽], list)
        assert c[_输出类型]
        assert c[_导出名]
    # 包整体过自查（build_context 内部已做，这里再断言一次形状没被旁路破坏）
    assert schema.validate_context_envelope(包) == []


def test_旁路候选分数恒为零且不与词面分数比较():
    包 = _包(_制造问句)
    for c in 包[_候选键]:
        if c[_路径] == retrieval.PATH_SEMANTIC:
            assert c[_分数] == 0.0
    词面 = [c for c in 包[_候选键] if c[_路径] == retrieval.PATH_HEURISTIC]
    assert 词面 and all(c[_分数] > 0 for c in 词面), (
        '词面候选分数应为正 —— 若也变成 0，读包的人就分不出两段了')


def test_旁路不重复注入已在词面候选里的块():
    包 = _包(_制造问句)
    名单 = [c[_名称] for c in 包[_候选键]]
    assert len(名单) == len(set(名单)), '候选出现重名：旁路去重失效'
    三元组 = [(c[_名称], c[_领域], c[_导出名]) for c in 包[_候选键]]
    assert len(三元组) == len(set(三元组)), (
        '规则 2 白名单键重复 —— `_候选索引` 会后写覆盖前写')


def test_旁路候选追加在词面候选之后():
    """顺序是设计的一部分：先词面、后旁路。读包的人据此判断「这条是怎么进来的」。"""
    路径序 = [c[_路径] for c in _包(_制造问句)[_候选键]]
    首个旁路 = 路径序.index(retrieval.PATH_SEMANTIC)
    assert all(p != retrieval.PATH_SEMANTIC for p in 路径序[:首个旁路])
    assert all(p == retrieval.PATH_SEMANTIC for p in 路径序[首个旁路:])


# ---------------------------------------------------------------------------
# 四、规则 2 白名单接受旁路带入的块
# ---------------------------------------------------------------------------

def test_回填可以引用旁路带入的块():
    """旁路块必须真的能被回填引用，否则「进了候选」只是好看。"""
    包 = _包(_制造问句)
    候选 = {c[_名称]: c for c in 包[_候选键]}
    读表 = 候选['表载入']
    信封 = {
        schema.FILLED_ENVELOPE_REQUIRED[0]: _制造问句,
        schema.FILLED_ENVELOPE_REQUIRED[1]: {
            schema.PLAN_REQUIRED[0]: [{
                schema.STEP_REQUIRED[0]: 读表[_名称],
                schema.STEP_REQUIRED[1]: 读表[_领域],
                schema.STEP_REQUIRED[2]: 读表[_导出名],
                schema.STEP_OPTIONAL[0]: ['赵路径'],
            }],
            schema.PLAN_OPTIONAL[1]: [{'名': '赵路径', '值': '“x.csv”'}],
        },
        schema.FILLED_ENVELOPE_REQUIRED[2]: '人工·W174 测试',
    }
    理由 = planner.validate_filled(信封, 包)
    assert 理由 == [], '规则 2 把旁路带入的块当成幻觉块名了：%s' % 理由


# ---------------------------------------------------------------------------
# 五、代价：近边缘诱骗率上升，且上升只来自一条
# ---------------------------------------------------------------------------

def _近边缘用例():
    with io.open(os.path.join(_桥目录, '评测集-chatbi-近边缘.json'),
                 'r', encoding='utf-8') as f:
        return json.load(f)['用例']


def test_旁路确实抬高了近边缘兄弟块诱骗率且只抬高一条():
    """实测 0.625 → 0.75（8 条里 5 条 → 6 条）。新增的那条是
    「各产线的停线时长占工作时间的比例」的 `停线汇总`，**只由旁路带入**。

    这条断言在这里不是为了锁死数字，而是为了让「旁路把召回做上去也把像的块带上来」
    这件事有一个会红的看门人：谁把旁路改宽了，这个数会动，就得回来重新看代价。
    """
    带入 = []
    for c in _近边缘用例():
        包 = _包(c['需求'])
        名 = _名字集(包)
        旁 = _旁路名字集(包)
        for b in (c.get('兄弟块') or ()):
            if b in 名 and b in 旁:
                带入.append((c['需求'], b))
    assert [b for _, b in 带入] == ['停线汇总'], (
        '旁路带入的兄弟块清单变了：%s' % 带入)


def test_登记的分歧点对侧块旁路一条都没带入():
    """本轮预留过「复用 `分歧点表` 把对侧块从旁路里剔掉」的方案，实测**不必做**：
    近边缘档里被诱骗的分歧点两侧块（`达成率均值`/`达成率权重`/`单车电耗现成`/
    `单车电耗重算`/`能耗汇总`/`缺陷率`）全是 TF-IDF 词面本来就召回的，旁路一条没加。
    要剔的东西旁路根本没引入 —— 加那段代码等于加一段永不生效的逻辑。
    """
    两侧 = set()
    for 处 in planner.分歧点表:
        两侧.update(处['两侧'])
    assert 两侧, '分歧点表空了？'
    for c in _近边缘用例():
        旁 = _旁路名字集(_包(c['需求']))
        被诱骗的对侧 = 旁 & 两侧 & set(c.get('兄弟块') or ())
        assert not 被诱骗的对侧, (
            '%s：旁路带入了登记的分歧点对侧块 %s —— 那条「不必剔除」的判断要重做'
            % (c['需求'], sorted(被诱骗的对侧)))


def test_远离档拒答率不因旁路改变():
    """旁路只在有制造域语义命中时启动，远离档那 10 条整类能力不在库里，
    `拒答建议.覆盖` 的判据（语义命中非空 + 候选非空）不受影响。"""
    with io.open(os.path.join(_桥目录, '评测集-chatbi-无覆盖.json'),
                 'r', encoding='utf-8') as f:
        用例 = json.load(f)['用例']
    拒 = sum(1 for c in 用例
             if not _包(c['需求'])[schema.CONTEXT_ENVELOPE_REQUIRED[4]]['覆盖'])
    assert 拒 == 5, '远离档拒下条数应仍是 5（10 条中 50.0%），实得 %d' % 拒
