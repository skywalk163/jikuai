# -*- coding: utf-8 -*-
"""规划器 —— 把问句组装成**规划上下文包**，并对 LLM 的回填做硬校验。

正本：`docs/ADR-41-规划器与NL层.md`。本文件是 M28 的核心，两个职责：

  W156 `build_context(需求)`  —— 问句 + 检索候选 + 语义层 + 块元数据 → 受限结构。
                                 关键差别在候选带 `输入槽`/`输出类型`：v0.26.0 W145
                                 实测 LLM 写不出实参、124 步全靠人手写，直接根因就是
                                 「选响应」的候选不告诉你块吃几个参数、每个什么类型
                                 （ADR-41 §3）。上下文包是**使能**，校验器是兜底。
  W157 `validate_filled(回填, 上下文包)` —— ADR-41 §4 的五条硬规则，任一不过即拒，
                                 拒绝理由必须可操作（说清缺哪个槽、该填什么类型）。

**不进 wheel**（ADR-41 §7）：它依赖 `tools/ai-bridge/`，与 `glue.py` 同级——装了
wheel 的用户本来就跑不了 `组`/`跑`，规划器不可能比它的依赖可用性更强。

命令行::

    python tools/ai-bridge/planner.py 上下文 "2026年6月各车型总产量" [--top 8]
    python tools/ai-bridge/planner.py 校验 回填.json 上下文包.json [--严格]
"""

import argparse
import json
import os
import sys

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
# 兄弟模块 `glue.py` 走同目录 import：`tools/ai-bridge/` 刻意不做成包，
# 免得桥接工具污染主发布包的命名空间（理由同 glue.py 头部注释）。
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from jikuai.service import schema  # noqa: E402
from jikuai.service.schema import (  # noqa: E402
    STEP_REQUIRED, STEP_OPTIONAL, PLAN_REQUIRED, PLAN_OPTIONAL,
    SLOT_REQUIRED, CONTEXT_CANDIDATE_REQUIRED, SEMANTIC_HIT_REQUIRED,
    DIVERGENCE_WARNING_REQUIRED,
    CONTEXT_ENVELOPE_REQUIRED, CONTEXT_ENVELOPE_OPTIONAL,
    FILLED_ENVELOPE_REQUIRED,
)
from jikuai.ai import retrieval  # noqa: E402
import glue  # noqa: E402

#: 协议字段名一律从 schema 常量取，本文件不写裸字面量（W20 硬门槛）。
#: 整元组解包而不是硬编码下标：协议真加了字段这里会当场 ValueError。
_F块, _F领域, _F导出名 = STEP_REQUIRED
_F参数, _F说明, _F命名空间 = STEP_OPTIONAL
_F步骤, = PLAN_REQUIRED
_F需求, _F共享, _F打印 = PLAN_OPTIONAL
_F槽名, _F槽类型 = SLOT_REQUIRED
(_F名称, _F候选领域, _F层级, _F候选导出名, _F描述, _F分数, _F路径,
 _F输入槽, _F输出类型) = CONTEXT_CANDIDATE_REQUIRED
_F业务词, _F表, _F字段, _F口径说明 = SEMANTIC_HIT_REQUIRED
_F分歧点, _F两侧块名, _F实测差值, _F须显式选一条 = DIVERGENCE_WARNING_REQUIRED
_C需求, _C语义命中, _C候选, _C回填契约, _C拒答建议 = CONTEXT_ENVELOPE_REQUIRED
_C分歧告警, = CONTEXT_ENVELOPE_OPTIONAL
_R需求, _R方案, _R模型 = FILLED_ENVELOPE_REQUIRED

#: `共享[]` 的键。schema 把这个键集写在 `validate_plan` 里没抽成模块常量，
#: 故这里与 `glue.py:392` 同法直读——两处一致，改协议时一起改。
_S共享名 = '名'
_S共享类型 = '类型'

#: `索引.json` 条目的键。**这不是协议字段**，是块索引的落盘格式（真源
#: `pkg.blocks` 的索引写入侧），所以不从 schema 常量取——把两套键混成一套，
#: 将来任一侧变了都会牵连另一侧。
_I名称, _I领域, _I层级, _I描述 = '名称', '领域', '层级', '描述'
_I输入, _I输出, _I导出 = '输入', '输出', '导出'
_I类型, _I槽名 = '类型', '名'
_I块表 = '块'

#: `制造/语义层.json` 的键。注意 `口径备注` ≠ 协议的 `口径说明`——文件先写的，
#: 协议后定的，这里做映射而不是去改任一侧的既有命名。
_Y条目, _Y业务词, _Y同义词 = '条目', '业务词', '同义词'
_Y表, _Y字段, _Y口径备注 = '表', '字段', '口径备注'

__all__ = ['build_context', 'validate_filled', 'ensure_filled', '类型串',
           '分歧点表', '语义命中', '默认索引路径', '默认语义层路径']

#: 顶类型。`严格` 下第 5 条规则要判「槽是不是精确类型」，判据就是它。
_任意 = '任意'


# ---------------------------------------------------------------------------
# 口径分歧点表（ADR-40 §5 四处，与 G22 `check_manufacturing_contract.py`
# 的 `分歧点表` 一一对应）
# ---------------------------------------------------------------------------
# 为什么规划器要自己存一份而不是 import 那个门禁脚本：门禁跑在 CI、按目录名扫块
# 并断言「两侧都在且互相点名」；规划器跑在请求路径上、要的是「问句命中了哪处口径」
# 外加一句能贴给 LLM 的人读差值。共用一份数据结构会让门禁被迫携带触发词、
# 让规划器被迫携带「必含」断言词，两边都变形。**但两份表的条目数与块名必须一致**
# ——W157 有一条测试直接比对这两张表的块名集合。
#
# `两侧` 只有一个块名的那处（缺陷率）不是笔误：ADR-40 §5.3 的另一侧口径
# （行级比率再平均）**不存在对应块**，因为它是错的。规则 3 对它退化成
# 「命中这个口径就必须显式选 缺陷率，不许自己另写一套行级比率」。
分歧点表 = (
    {
        '名': '平均达成率（ADR-40 §5.1）',
        '两侧': ('达成率权重', '达成率均值'),
        '差值': '产量加权 sum(actual_quantity)/sum(planned_quantity) '
                'vs 逐行 achievement_rate 的算术平均。本数据集两者只差万分之几，'
                '**数值接近不等于口径等价**——换一份产量分布不均的数据就会分开；'
                'ADR-40 §5.1 决议默认走加权口径',
        '触发词': ('达成率', '完成率', '计划达成', '产量达成', '达成情况'),
    },
    {
        '名': '单车电耗（ADR-40 §5.2）',
        '两侧': ('单车电耗现成', '单车电耗重算'),
        '差值': '直接用现成比率列 energy_per_vehicle vs 关联产量表重算 '
                'sum(electricity_kwh)/sum(actual_quantity)。现成列是**已经算好的比率**，'
                '跨行求和或求算术平均都会算出错数（语义层 `现成比率列` 只有两列为 true，'
                '这是其中一列）',
        '触发词': ('单车电耗', '单台电耗', '单车能耗', '每台电耗', '单车耗电'),
    },
    {
        '名': '缺陷率（ADR-40 §5.3）',
        '两侧': ('缺陷率',),
        '差值': '先分别汇总再相除 0.050550 vs 先算行级比率再平均 0.032218，'
                '相差 57%（2026-06 实测）。后者没有对应块也不会有——两表粒度不同'
                '（缺陷表多工序/缺陷类型/严重度三维，2992 行 vs 2896 行），'
                '行级比率根本对不上。命中这个口径就用 `缺陷率` 块，别自己写比率',
        '触发词': ('缺陷率', '不良率', '缺陷比率', '千台缺陷率'),
    },
    {
        '名': '时序对比参照系（ADR-40 §5.4）',
        '两侧': ('窗间对比', '基线偏离'),
        '差值': '跟上一个平级窗口比（对称）vs 跟自身长期基线比（不对称）。'
                'Q_PUB_004 实测：L002 单车电耗 151.8007 → 191.0953，相对自身基线 +25.9% '
                '排第一，但在横向绝对量榜上只排第 4（L006 的 256.5050 才是最高）。'
                '**两个榜结论不同不是 bug，是两个问题**；「相比平时」「异常升高」要的是'
                '基线偏离，拿上一个窗口当基线会把一次性波动当成常态',
        '触发词': ('相比平时', '相较平时', '异常', '升高', '基线', '偏离',
                   '环比', '同比', '相比上', '比上个月', '比上月', '是否上升'),
    },
)


# ---------------------------------------------------------------------------
# 路径与读取
# ---------------------------------------------------------------------------

def _blocks_root():
    from jikuai.pkg.blocks import blocks_root
    return blocks_root()


def 默认索引路径():
    """内置块索引 `索引.json` 的路径。"""
    return os.path.join(_blocks_root(), '索引.json')


def 默认语义层路径():
    """制造域语义注册表 `制造/语义层.json` 的路径。"""
    return os.path.join(_blocks_root(), '制造', '语义层.json')


def _读json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _索引表(索引路径=None):
    """块名 → `索引.json` 条目。读不到就返回空表。

    读不到不在这里炸（与 `schema._load_index` 同一口径）：索引缺失/损坏是 G11/G12
    门禁的职责，不该把一次 `规划` 请求打挂。代价是候选的 `输入槽` 会是空数组——
    那是**如实**的「不知道」，比编一组槽名让 LLM 照着填要好。
    """
    try:
        data = _读json(索引路径 or 默认索引路径())
    except (OSError, ValueError):
        return {}
    条目表 = data.get(_I块表)
    if not isinstance(条目表, list):
        return {}
    表 = {}
    for 条目 in 条目表:
        名 = 条目.get(_I名称)
        if isinstance(名, str):
            表[名] = 条目
    return 表


# ---------------------------------------------------------------------------
# 类型串
# ---------------------------------------------------------------------------

def 类型串(t):
    """块元数据里的类型规格 → ADR-26 人读类型串（`列表<字典<字符串,任意>>`）。

    复用 `glue.normalize_type` + `glue.人读类型`，不另写一份渲染：粘合器的拒绝理由
    和上下文包的 `输入槽.类型` 必须是同一种写法，否则 LLM 读到的类型名与它照着填完
    被粘合器拒时看到的类型名对不上。缺省/不认识的一律落 `任意`（保守，不猜）。
    """
    return glue.人读类型(glue.normalize_type(t))


# ---------------------------------------------------------------------------
# 语义命中（W156）
# ---------------------------------------------------------------------------

def 语义命中(需求, 语义层路径=None):
    """问句 → 命中的语义层条目列表（协议 `语义命中` 形状）。

    判定是**字面包含**：业务词或其任一同义词作为子串出现在问句里。为什么不做分词/
    模糊匹配——本层是「把中文业务词锚定到真实列名」的映射，宁可漏不可错：多召回一个
    无关列会让 LLM 挑错列（`achievement_rate` 与 `actual_quantity` 这种一挑错就静默
    出错数），漏掉一个则最多退化成 `拒答建议.覆盖=False` 让人复核。

    「产量」是「计划产量」的子串，所以问「计划产量」会同时命中两条。这是**故意**不去
    重的：两条的 `口径说明` 摆在一起，正好把「计划 vs 实际」这个坑显式化。
    行序 = 文件条目序，确定可复现。
    """
    try:
        data = _读json(语义层路径 or 默认语义层路径())
    except (OSError, ValueError):
        return []
    条目表 = data.get(_Y条目)
    if not isinstance(条目表, list):
        return []
    命中 = []
    for 条目 in 条目表:
        词 = 条目.get(_Y业务词)
        if not isinstance(词, str) or not 词:
            continue
        别名 = [x for x in (条目.get(_Y同义词) or []) if isinstance(x, str)]
        if not any(t in 需求 for t in [词] + 别名):
            continue
        命中.append(schema.make_semantic_hit(
            业务词=词,
            表=条目.get(_Y表) or '',
            字段=条目.get(_Y字段) or '',
            # 文件里叫 `口径备注`，协议里叫 `口径说明`——映射在这一行，别处不再出现。
            口径说明=条目.get(_Y口径备注) or ''))
    return 命中


def _分歧告警(需求):
    """问句命中的口径分歧点 → `分歧告警` 列表（空则不出这个键）。"""
    出 = []
    for 处 in 分歧点表:
        if any(t in 需求 for t in 处['触发词']):
            出.append(schema.make_divergence_warning(
                分歧点=处['名'], 两侧块名=处['两侧'], 实测差值=处['差值']))
    return 出


# ---------------------------------------------------------------------------
# 候选（W156）
# ---------------------------------------------------------------------------

def _块导出名(条目, 块名):
    """块名 → 主 `导出名`。tie-break 与 `schema.export_table` 逐字同源：
    与块同名的优先，否则取排序首位。查不到退回块名（索引过期时的唯一降级点）。"""
    导出 = 条目.get(_I导出)
    名单 = sorted(n for n in (导出 or []) if isinstance(n, str) and n)
    if not 名单:
        return 块名
    return 块名 if 块名 in 名单 else 名单[0]


def _候选(hit, 元表):
    """`retrieval.Hit` + 索引条目 → 一条**上下文包候选**（带 `输入槽`/`输出类型`）。"""
    条目 = 元表.get(hit.name) or {}
    槽表 = []
    for slot in (条目.get(_I输入) or []):
        if not isinstance(slot, dict):
            continue
        槽表.append(schema.make_slot(
            名=slot.get(_I槽名) or '', 类型=类型串(slot.get(_I类型))))
    输出 = 条目.get(_I输出)
    输出类型 = 类型串((输出 or {}).get(_I类型) if isinstance(输出, dict) else None)
    try:
        层级 = int(条目.get(_I层级, 0))
    except (TypeError, ValueError):
        层级 = 0
    命名空间 = getattr(hit, 'namespace', '') or ''
    return schema.make_context_candidate(
        名称=hit.name, 领域=hit.domain, 层级=层级,
        导出名=_块导出名(条目, hit.name), 描述=hit.description, 分数=hit.score,
        输入槽=槽表, 输出类型=输出类型, 路径=getattr(hit, 'path', '') or '',
        命名空间=命名空间 or None)


# ---------------------------------------------------------------------------
# 回填契约与拒答建议（W156）
# ---------------------------------------------------------------------------

def _回填契约():
    """给 LLM 的回填格式说明。文案里的字段名全部由 schema 常量拼出。"""
    必填 = [
        '%s[].%s —— 每步都要写，长度必须等于该候选 %s 的个数。省略它会让粘合器按'
        '类型图自动推链，v0.26.0 W145 实测那会**静默错绑**（`读表(赵产量列)`），'
        '既不落 `?` 占位也不写拒绝理由，运行期才死'
        % (_F步骤, _F参数, _F输入槽),
        '每步的 %s / %s / %s —— 三个都要，且必须逐字取自本包 %s 里的同一条候选'
        % (_F块, _F领域, _F导出名, _C候选),
        '%s —— 回填来源标识（人手填「人工」，端到端填端点名）。录像回放与溯源靠它'
        '区分「换模型后结果变了」和「链路本身变了」' % (_R模型,),
    ]
    禁止 = [
        '协议之外的多余键：%s / %s 的键集都是白名单，多一个就拒' % (_F步骤, _R方案),
        '幻觉块名：%s 里不存在的 %s / %s / %s 组合' % (_C候选, _F块, _F领域, _F导出名),
        '同一处口径分歧点的两侧块同时出现；命中了该口径却两侧都不选，同样拒',
        '绕开口径块自己另算一套（例如手写行级比率代替 `缺陷率` 块）——'
        '那种代码跑得通、结果是错的，比报错糟得多',
    ]
    return schema.make_fill_contract(必填, 禁止)


#: 已知缺口，恒登记进 `拒答建议.理由` 供人复核（ADR-41 §5 要求把缺口写在明面上）。
#: 为什么不顺手补上：检索层本轮**一行不改**，改了就得重跑调优集 + 留出集两套数字，
#: 那是另一件事（AGENTS.md 第六节）。
_已知缺口 = (
    '已知缺口（本轮不修，供人复核）：检索层 `_DOMAIN_KEYWORDS` 没登记「制造」域，'
    '制造块拿不到 1.5× 领域先验加分，排序上天生吃亏。另：覆盖判定是**词表覆盖**，'
    '不是分数阈值——四轮实测已证伪分数拒答（AUC 0.52-0.67，能拒掉 94% 无覆盖需求的'
    '阈值会同时误杀 64% 真命中），检索层永远返回 top-K 且不拒答。'
)

#: 覆盖判定的**边界**，同样恒登记：语义层只登记制造域 42 个业务词，所以七个域里
#: 另外六个域的问句一律被判「未覆盖」。那是本轮的作用域边界，不是「库里没有」的结论。
_覆盖边界 = (
    '判定边界：语义层只登记**制造域**业务词（42 条），因此财务/数据/中文/历法/'
    '网络/工具六个域的问句在这里一律判为未覆盖——这是本轮规划器的作用域边界，'
    '不等于块库没有对应能力，那六个域请照旧走 `jk 块 选`。'
)


def _拒答建议(语义命中表, 候选表):
    """词表覆盖判定 → `拒答建议`。**没有任何分数参与**。"""
    覆盖 = bool(语义命中表) and bool(候选表)
    if 覆盖:
        理由 = ('判为库内能力：语义层命中 %d 个业务词，且检索给出 %d 条候选。%s%s'
                % (len(语义命中表), len(候选表), _覆盖边界, _已知缺口))
    else:
        原因 = []
        if not 语义命中表:
            原因.append('语义层无业务词命中（问句没落到任何业务词或其同义词上）')
        if not 候选表:
            原因.append('检索没给出候选')
        理由 = ('判为库外能力：%s。%s%s'
                % ('；'.join(原因), _覆盖边界, _已知缺口))
    return schema.make_reject_advice(覆盖, 理由)


# ---------------------------------------------------------------------------
# W156 · 上下文包构造器
# ---------------------------------------------------------------------------

def build_context(需求, top=8, 索引路径=None, 语义层路径=None,
                  query_vector=None):
    """问句 → **规划上下文包**（`jk 块 规划` 的出口）。

    Args:
        需求: 自然语言问句（中文）。
        top: 候选数上限。默认 8 而不是 `选` 的 5——上下文包的候选带槽信息，
            多给两三条的 token 代价换 LLM 少猜一次口径，划得来。
        索引路径 / 语义层路径: 测试与第三方块库用；缺省走内置块库。
        query_vector: 给了才走神经检索路径（口径同 `retrieval.retrieve`）。

    Returns:
        `schema.make_context_envelope` 的产物。**出门前自查**一遍
        `validate_context_envelope`，不通过直接抛——规划器吐一个自己都不合法的包
        比不吐更糟（W157 的校验器会拿它当参照，参照错了后面全错）。
    """
    hits = retrieval.retrieve(需求, top, query_vector)
    元表 = _索引表(索引路径)
    候选表 = [_候选(h, 元表) for h in hits]
    命中表 = 语义命中(需求, 语义层路径)
    告警表 = _分歧告警(需求)
    信封 = schema.make_context_envelope(
        需求=需求, 语义命中=命中表, 候选=候选表,
        回填契约=_回填契约(), 拒答建议=_拒答建议(命中表, 候选表),
        分歧告警=告警表 or None)
    errs = schema.validate_context_envelope(信封)
    if errs:
        raise schema.SchemaError(
            '规划器自查未过（这是规划器自己的 bug，不是调用方的）：%s'
            % '；'.join(errs))
    return 信封


# ---------------------------------------------------------------------------
# W157 · 回填校验器：ADR-41 §4 五条硬规则
# ---------------------------------------------------------------------------

def _候选索引(上下文包):
    """上下文包 → `(块, 领域, 导出名)` → 候选。白名单的真源。"""
    表 = {}
    for c in (上下文包.get(_C候选) or []):
        if not isinstance(c, dict):
            continue
        键 = (c.get(_F名称), c.get(_F候选领域), c.get(_F候选导出名))
        表[键] = c
    return 表


def _规则1与2(步骤表, 候选表):
    """规则 1（`参数` 必填且长度 = 输入槽数）与规则 2（块名白名单）。

    两条合在一个循环里不是图省事：规则 1 要知道该块的槽数，而槽数只能从规则 2 认下的
    那条候选上取。块名不在白名单时槽数无从得知，此时只报规则 2，不硬编一个槽数去报
    「长度不对」——那种理由不可操作（ADR-41 §4 末句）。
    """
    理由 = []
    for i, 步 in enumerate(步骤表, 1):
        位置 = '步骤%d' % i
        if not isinstance(步, dict):
            理由.append('%s 不是对象，无法校验' % 位置)
            continue
        键 = (步.get(_F块), 步.get(_F领域), 步.get(_F导出名))
        候选 = 候选表.get(键)
        if 候选 is None:
            理由.append(
                '%s 的 %s=%r / %s=%r / %s=%r 不在上下文包的 %s 里（白名单）。'
                '可选组合：%s' % (
                    位置, _F块, 键[0], _F领域, 键[1], _F导出名, 键[2], _C候选,
                    '、'.join('%s/%s/%s' % k for k in sorted(
                        候选表, key=lambda x: tuple(str(y) for y in x)))
                    or '（上下文包没给候选——覆盖判定为 False 时本就该拒答'
                       '而不是回填方案）'))
            if _F参数 not in 步:
                理由.append('%s 缺 %s（不论块名对不对，实参都不许省）'
                            % (位置, _F参数))
            continue

        槽表 = 候选.get(_F输入槽) or []
        名 = 键[0]
        if _F参数 not in 步:
            理由.append(
                '%s「%s」缺 %s。该块有 %d 个输入槽：%s。省略 %s 会让粘合器按类型图'
                '自动推链，v0.26.0 W145 实测那会静默错绑且不落 `?` 占位，'
                '运行期才死——所以这里根本不许省'
                % (位置, 名, _F参数, len(槽表), _槽清单(槽表), _F参数))
            continue
        实参 = 步.get(_F参数)
        if not isinstance(实参, list):
            理由.append('%s「%s」的 %s 必须是数组' % (位置, 名, _F参数))
            continue
        if len(实参) != len(槽表):
            理由.append(
                '%s「%s」的 %s 给了 %d 个，该块要 %d 个：%s'
                % (位置, 名, _F参数, len(实参), len(槽表), _槽清单(槽表)))
    return 理由


def _槽清单(槽表):
    """`输入槽` → 人读串 `路径:字符串, 维度列:列表<字符串>`。拒绝理由要可操作。"""
    if not 槽表:
        return '（零参数）'
    return ', '.join('%s:%s' % (s.get(_F槽名), s.get(_F槽类型)) for s in 槽表)


def _规则3(上下文包, 步骤表):
    """规则 3：分歧点两侧同时出现 → 拒；命中该口径而一侧都没选 → 拒。

    判据取自上下文包的 `分歧告警`，不在这里重新匹配问句——告警在场本身就等价于
    「问句命中了这处口径」，两处各判一次必然漂。
    """
    理由 = []
    选中块 = {步.get(_F块) for 步 in 步骤表 if isinstance(步, dict)}
    for 警 in (上下文包.get(_C分歧告警) or []):
        if not isinstance(警, dict):
            continue
        两侧 = [b for b in (警.get(_F两侧块名) or []) if isinstance(b, str)]
        分歧点 = 警.get(_F分歧点)
        差值 = 警.get(_F实测差值)
        命中 = [b for b in 两侧 if b in 选中块]
        if len(两侧) >= 2 and len(命中) >= 2:
            理由.append(
                '「%s」的两侧口径块 %s 同时出现在方案里。这是两个不同的答案，'
                '不是两个步骤——必须显式选一条。实测差异：%s'
                % (分歧点, ' 与 '.join(命中), 差值))
        elif not 命中:
            if len(两侧) >= 2:
                理由.append(
                    '问句命中口径分歧点「%s」，但方案里 %s 一个都没选。'
                    '必须显式挑一条口径。实测差异：%s'
                    % (分歧点, ' / '.join(两侧), 差值))
            else:
                理由.append(
                    '问句命中口径分歧点「%s」，但方案里没用 `%s` 块。'
                    '这一处只有一条正确口径，另一侧没有对应块也不会有——'
                    '不许自己另算。实测差异：%s'
                    % (分歧点, 两侧[0] if 两侧 else '?', 差值))
    return 理由


def _规则5(方案, 步骤表, 候选表):
    """规则 5（`--严格`）：无声明类型的共享常量不许喂精确形参。

    配 ADR-41 §6 的粘合器治根：`共享` 没写 `类型` 就入池为 `任意`，而 `任意` 在
    `type_feeds` 里双向放行，于是每个字符串常量对每个形参都「类型兼容」。W151 实测
    声明类型**挡不住** Q_PUB_001 那种全是 `字符串` 的错绑（真病根是拿书写顺序当证据，
    W152 已治），所以这一条不是万能药——它管的是另一半：常量压根没声明类型时，
    别让它悄悄喂进一个精确槽。默认关，`--严格` 才开。
    """
    理由 = []
    无声明 = set()
    for 项 in (方案.get(_F共享) or []):
        if not isinstance(项, dict):
            continue
        名 = 项.get(_S共享名)
        if isinstance(名, str) and 名 and not 项.get(_S共享类型):
            无声明.add(名)
    if not 无声明:
        return 理由
    for i, 步 in enumerate(步骤表, 1):
        if not isinstance(步, dict):
            continue
        候选 = 候选表.get((步.get(_F块), 步.get(_F领域), 步.get(_F导出名)))
        if 候选 is None:
            continue
        槽表 = 候选.get(_F输入槽) or []
        实参 = 步.get(_F参数)
        if not isinstance(实参, list):
            continue
        for k, 实 in enumerate(实参):
            if k >= len(槽表) or 实 not in 无声明:
                continue
            槽 = 槽表[k]
            槽类型 = 槽.get(_F槽类型)
            if 槽类型 == _任意:
                continue
            理由.append(
                '严格模式：步骤%d「%s」第 %d 个实参「%s」是没声明 %s 的共享常量，'
                '却喂给精确形参 %s:%s。请给该常量补 %s（落 ADR-26 类型词表），'
                '否则它在类型图里是 `%s`、对任何形参都「兼容」'
                % (i, 步.get(_F块), k + 1, 实, _S共享类型,
                   槽.get(_F槽名), 槽类型, _S共享类型, _任意))
    return 理由


def validate_filled(回填, 上下文包, 严格=False):
    """ADR-41 §4 的五条硬规则。返回**中文拒绝理由列表**，空列表 = 通过。

    返回 list 而不是 `(通过?, 理由)` 二元组：全仓的校验器（`schema.validate_*`、
    `glue` 的拒绝理由）都是这个约定，`通过 = not validate_filled(...)` 一行可得，
    多一个布尔只是多一处可以写反的地方。ADR-41 §4 已同步这一句。

    五条：
      1. 每步 `参数` 必填，长度 = 该块输入槽数（`_规则1与2`）。
      2. `块`/`领域`/`导出名` 必须来自上下文包 `候选`（同上，白名单）。
      3. 分歧点两侧同时出现 → 拒；命中该口径而一侧都没选 → 拒（`_规则3`）。
      4. 产物必过 `schema.validate_filled_envelope`（内含 `validate_plan`）。
      5. `严格` 下无声明类型的共享常量不许喂精确形参（`_规则5`）。

    第 4 条**先跑**：形状不对时后面四条的取值全不可信（`步骤` 可能不是数组），
    此时直接回形状错误，不叠一堆派生噪音。
    """
    if not isinstance(上下文包, dict):
        return ['上下文包不是对象，无法当参照做校验']
    形状 = schema.validate_filled_envelope(回填)
    if 形状:
        return 形状

    方案 = 回填.get(_R方案) or {}
    步骤表 = 方案.get(_F步骤) or []
    候选表 = _候选索引(上下文包)

    理由 = []
    理由.extend(_规则1与2(步骤表, 候选表))
    理由.extend(_规则3(上下文包, 步骤表))
    if 严格:
        理由.extend(_规则5(方案, 步骤表, 候选表))
    return 理由


def ensure_filled(回填, 上下文包, 严格=False):
    """校验回填，不通过抛 `schema.SchemaError`；通过则原样返回。"""
    理由 = validate_filled(回填, 上下文包, 严格)
    if 理由:
        raise schema.SchemaError('；'.join(理由))
    return 回填


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def _cmd上下文(args):
    包 = build_context(args.需求, top=args.top)
    print(json.dumps(包, ensure_ascii=False, indent=2))
    return 0


def _cmd校验(args):
    理由 = validate_filled(_读json(args.回填), _读json(args.上下文包),
                           严格=args.严格)
    if not 理由:
        print('通过')
        return 0
    for r in 理由:
        print('拒：%s' % r, file=sys.stderr)
    return 1


def main(argv=None):
    p = argparse.ArgumentParser(
        description='规划器：上下文包构造 + 回填硬校验（ADR-41）')
    subs = p.add_subparsers(dest='子命令')
    a = subs.add_parser('上下文', help='问句 → 规划上下文包 JSON')
    a.add_argument('需求')
    a.add_argument('--top', type=int, default=8)
    a.set_defaults(func=_cmd上下文)
    b = subs.add_parser('校验', help='回填响应 + 上下文包 → 拒绝理由')
    b.add_argument('回填')
    b.add_argument('上下文包')
    b.add_argument('--严格', action='store_true')
    b.set_defaults(func=_cmd校验)
    args = p.parse_args(argv)
    if not getattr(args, 'func', None):
        p.print_help()
        return 1
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
