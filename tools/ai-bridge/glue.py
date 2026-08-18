# -*- coding: utf-8 -*-
"""块粘合合成器 —— 把「选块方案」JSON 合成为可运行极快源码。

配套 `select.py` 与 `协议.md`。选块器负责「选哪些块」，本文件负责「把它们
用极快语法串起来」。

两级能力：

  v0 `synthesize`  —— 模板合成。方案里手写 `参数`，缺省填 `?` 占位。
  v1 `TypeGraph`   —— 类型图驱动的**自动链式**（ADR-26 类型词表 + W3-W4）。
                      给定选块方案，按块的 `输入`/`输出` 类型自动推断
                      `赵果i → 赵果j` 的传参链；推不出的显式拒绝、给理由，
                      不静默硬塞。

命令行::

    python tools/ai-bridge/glue.py 方案.json            # 模板合成（尊重方案里的参数）
    python tools/ai-bridge/glue.py 方案.json --自动链式  # 类型图推断缺失的参数

尽力生成能直接跑的代码；无法自动推断的参数用 `?` 占位并注释「需人工填参」。
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

# 协议校验走 `service.schema`——glue 不重复发明字段规则。
# 注意加载方式：glue.py 不是包，靠上面的 sys.path.insert 把 `src/` 挂上，
# 才能 `from jikuai.service import schema` 成功；`tools/ai-bridge/` 目录
# 本身不做成包也不该做，那样会让「桥接工具」污染主发布包的命名空间。
from jikuai.service import schema  # noqa: E402
from jikuai.service.schema import (  # noqa: E402
    STEP_REQUIRED, STEP_OPTIONAL, PLAN_REQUIRED, PLAN_OPTIONAL,
)

#: 协议字段名一律从 schema 常量取，本文件不写裸字面量（W20 硬门槛）。
#: 整元组解包而不是硬编码下标：协议真加了字段这里会当场 ValueError。
#: `PLAN_REQUIRED` 目前只有一个字段，解包写法保留尾逗号（`_F步骤, = ...`）。
_F块, _F领域, _F导出名 = STEP_REQUIRED
_F参数, _F说明, _F命名空间 = STEP_OPTIONAL
_F步骤, = PLAN_REQUIRED
_F需求, _F共享, _F打印 = PLAN_OPTIONAL

__all__ = ['synthesize', 'result_var', 'TypeGraph', 'type_feeds', 'normalize_type',
           '人读类型', 'strip_surname', 'match_slot_name']


#: 占位符：参数无法推断时写在实参位，配一行注释提示人工补。
_占位符 = '?'


# ---------------------------------------------------------------------------
# 块元数据表（W55 · 集成反馈 P0/P1）—— 让 synthesize 也能拿到入参元数与示例
# ---------------------------------------------------------------------------
#
# TypeGraph 已经在 `--自动链式` 路径下用 `scan_blocks` 把 `输入/输出` 灌进内存。
# 但 P0（`?` 占位数与元数一致）与 P1（从块 `示例` 提取实参）在 `--自动链式`
# 关闭时也要用，所以把 scan 结果抽成一个进程级弱缓存 `_块元数据表(root)`。
# 键用绝对路径（None 用 '<default>' 兜底），生命周期与进程等长——scan_blocks
# 单次 100+ 块耗时可观，重复 synthesize 不该反复扫盘。
#
# 缓存仅在测试或临时 root 变化时可能陈旧。测试要清空可调用 `reset_meta_cache()`。
_块元数据缓存 = {}


def _块元数据表(root=None):
    """返回 {(命名空间, 领域, 名称): {...}} 字典。

    v0.20.0 W73 修：原来按纯 m.name 做键，跨命名空间同名块静默覆盖（第三方
    覆盖内置）。改为 (命名空间, 领域, 名称) 三元键；下游消费点（TypeGraph /
    synthesize）统一走带命名空间的查找。
    """
    key = os.path.abspath(root) if root else '<default>'
    cached = _块元数据缓存.get(key)
    if cached is not None:
        return cached
    from jikuai.pkg import blocks as _blocks_mod
    meta = {}
    for m in _blocks_mod.scan_blocks(root):
        # 领域取首个（多领域块的物理目录只落在一个领域下）
        领域 = m.domains[0] if m.domains else ''
        meta_key = (m.namespace, 领域, m.name)
        meta[meta_key] = {
            '输入': [dict(x) for x in m.inputs],
            '输出': dict(m.output),
            '示例': m.example or '',
        }
    _块元数据缓存[key] = meta
    return meta


def _查块元数据(meta_table, 块名, 命名空间='', 领域=''):
    """从三元键元数据表里查找一个块。

    查找策略（与 synthesize 消费步骤时的信息量匹配）：
    1. 若调用方给了完整三元 (命名空间, 领域, 块名) → 精确命中
    2. 否则遍历表找 name 匹配的条目：优先内置（空命名空间），有多个取首条
    """
    if 命名空间 or 领域:
        return meta_table.get((命名空间, 领域, 块名))
    # 退化查找：优先内置
    候选 = [(k, v) for k, v in meta_table.items() if k[2] == 块名]
    if not 候选:
        return None
    # 内置（空命名空间）优先
    for k, v in 候选:
        if k[0] == '':
            return v
    return 候选[0][1]


def reset_meta_cache():
    """清空块元数据进程级缓存（测试用）。"""
    _块元数据缓存.clear()


def _从示例提取实参(示例文本, 导出名):
    r"""从块 `示例` 里定位第一处 `<导出名>(...)` 调用，返回括号内的原始实参串。

    识别不到、括号不配对、内容为空 均返回 None——由调用方决定回落到占位符。
    刻意用**逐字符括号平衡扫描**而非正则：JiKuai 表达式里允许再嵌 `(`（例如
    `顺排(列 1 2 3)`），正则的 `\((.*?)\)` 会在第一个 `)` 处提前收工，把
    `顺排(列 1 2 3` 当实参吐出去。
    """
    if not 示例文本 or not 导出名:
        return None
    prefix = 导出名 + '('
    idx = 示例文本.find(prefix)
    if idx < 0:
        return None
    start = idx + len(prefix)
    depth = 1
    i = start
    while i < len(示例文本):
        c = 示例文本[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                inner = 示例文本[start:i].strip()
                return inner or None
        i += 1
    return None


def result_var(i):
    """第 i 步（0 基）的结果变量名。

    取 `赵果` + 序号。已验证词法原子：`tokenize('赵果1。')` → 单个 IDENT。
    前缀 `赵` 是块生态约定的「安全字头」——避免与内建动词冲突被切碎。
    """
    return '赵果%d' % (i + 1)


# ---------------------------------------------------------------------------
# 类型系统（ADR-26 类型词表）
# ---------------------------------------------------------------------------

_标量 = frozenset({'数', '字符串', '布尔', '函数', '任意'})
_容器 = frozenset({'列表', '字典', '元组'})
_联合 = '联合'


def normalize_type(t):
    """把类型标注归一成 `(种类, 载荷)` 元组，便于比较。

    - 标量字符串 `'数'` → `('数', None)`
    - 裸容器 `'列表'` → `('列表', ('任意-元素',))`（视为 列表<任意>）
    - 裸 `'字典'` → `('字典', (('任意',), ('任意',)))`
    - 结构化 `{'类型':'列表','元素类型':X}` → `('列表', (normalize(X),))`
    - 结构化 元组/字典/联合 类推
    - 缺省 / None → `('任意', None)`
    """
    if t is None:
        return ('任意', None)
    if isinstance(t, str):
        if t in _标量:
            return (t, None)
        if t == '列表':
            return ('列表', (('任意', None),))
        if t == '字典':
            return ('字典', (('任意', None), ('任意', None)))
        if t == '元组':
            return ('元组', None)          # 长度未知的裸元组
        return ('任意', None)              # 未知字符串保守当 任意
    if isinstance(t, dict):
        kind = t.get('类型')
        if kind == '列表':
            return ('列表', (normalize_type(t.get('元素类型')),))
        if kind == '字典':
            return ('字典', (normalize_type(t.get('键类型')),
                            normalize_type(t.get('值类型'))))
        if kind == '元组':
            return ('元组', tuple(normalize_type(x) for x in (t.get('元数') or [])))
        if kind == _联合:
            return (_联合, tuple(normalize_type(x) for x in (t.get('候选') or [])))
        return ('任意', None)
    return ('任意', None)


def _人读(nt):
    """把归一类型转回人类可读串，用于拒绝理由。"""
    kind, payload = nt
    if kind == '列表':
        return '列表<%s>' % _人读(payload[0])
    if kind == '字典':
        return '字典<%s,%s>' % (_人读(payload[0]), _人读(payload[1]))
    if kind == '元组':
        if payload is None:
            return '元组'
        return '元组[%s]' % ','.join(_人读(x) for x in payload)
    if kind == _联合:
        return '联合(%s)' % '|'.join(_人读(x) for x in payload)
    return kind


#: 公开别名：planner.py 的 `类型串` 要把块元数据里的类型规格转成 ADR-26 人读串，
#: 复用这套渲染而不是各写一份（两份必漂）。`normalize_type` + `人读类型` 是一对。
人读类型 = _人读



def type_feeds(src, dst):
    """判断「产出类型 src 能否喂给形参类型 dst」（自动链式的可喂性）。

    比纯子类型更严：**元组不自动喂列表**——固定形状返回值要人工拆包，
    这正是 `批量统计→升序` 该被拒的原因（WBS W3-W4 DoD）。

    `src` / `dst` 是 `normalize_type` 的输出。返回布尔。
    """
    sk, sp = src
    dk, dp = dst

    # 任意 是顶：形参收任意 → 放行；实参是任意（动态/共享常量）→ 放行
    if dk == '任意' or sk == '任意':
        return True

    # 联合：实参联合要求每个候选都能喂；形参联合要求实参能喂某个候选
    if sk == _联合:
        return all(type_feeds(c, dst) for c in sp)
    if dk == _联合:
        return any(type_feeds(src, c) for c in dp)

    # 元组 → 列表：显式拒绝（需人工拆包），即使元素同质
    if sk == '元组' and dk == '列表':
        return False

    if sk != dk:
        return False

    if sk in _标量:
        return True
    if sk == '列表':
        return type_feeds(sp[0], dp[0])
    if sk == '字典':
        return type_feeds(sp[0], dp[0]) and type_feeds(sp[1], dp[1])
    if sk == '元组':
        if sp is None or dp is None:
            return True                   # 裸元组兜底放行
        if len(sp) != len(dp):
            return False
        return all(type_feeds(a, b) for a, b in zip(sp, dp))
    return False


# ---------------------------------------------------------------------------
# 槽名字面匹配（ADR-30 · W43）—— 同型多候选的歧义消解
# ---------------------------------------------------------------------------

def strip_surname(name):
    """剥掉变量名的百家姓前缀（前 1-2 字），返回语义主体。

    极快标识符必须以百家姓开头（`赵果1`、`赵月薪`），前缀本身无语义只是词法要求。
    做字面匹配要拿掉前缀比对语义部分。前缀去掉 1 字（单字姓，覆盖块生态惯用的
    「赵」等）或 2 字（复姓，罕见但要兜住 `欧阳月薪` 之类）。

    这里不引入 `jikuai.surnames`：`tools/ai-bridge/` 保持零跨包依赖，字面匹配
    只需一个「跳过前缀」的粗略近似——保守取 1 个字符即可覆盖 99% 情形，
    未剥掉的复姓字符最多降低命中率不会误匹配（因为对比是子串关系）。
    """
    if not name:
        return name
    # 保守剥 1 字：覆盖内置块生态里的「赵」前缀
    return name[1:] if len(name) > 1 else name


def match_slot_name(slot名, 候选名列表):
    """在 候选名列表 中找与 slot名 字面最匹配的一项，返回 (命中名, 置信度)。

    三级递进，首命中即停（ADR-30 §2.2）：
      1. 精确匹配（剥前缀后完全相等）    置信度 1.0
      2. 后缀匹配（变量名以形参名结尾）  置信度 0.8
      3. 包含匹配（形参名是变量名子串）  置信度 0.6

    若多个候选命中同一级，视为歧义未消解，返回 (None, 0.0)——
    「宁可留空不硬塞」是不可让步的原则。

    低于 0.6 置信度视为不命中，返回 (None, 0.0)。
    """
    if not slot名 or not 候选名列表:
        return None, 0.0

    # 1) 精确匹配（剥前缀）
    精确 = [n for n in 候选名列表 if strip_surname(n) == slot名]
    if len(精确) == 1:
        return 精确[0], 1.0
    if len(精确) > 1:
        return None, 0.0

    # 2) 后缀匹配（变量名剥前缀后以形参名结尾）
    后缀 = [n for n in 候选名列表 if strip_surname(n).endswith(slot名)]
    if len(后缀) == 1:
        return 后缀[0], 0.8
    if len(后缀) > 1:
        return None, 0.0

    # 3) 包含匹配（形参名是变量名剥前缀后的子串）
    包含 = [n for n in 候选名列表 if slot名 in strip_surname(n)]
    if len(包含) == 1:
        return 包含[0], 0.6
    if len(包含) > 1:
        return None, 0.0

    return None, 0.0



# ---------------------------------------------------------------------------
# 类型图：自动链式传参
# ---------------------------------------------------------------------------

class TypeGraph:
    """类型图驱动的自动链式粘合。

    节点 = 已产出变量（附类型）；边 = 「变量 X 的类型可喂步骤 Y 的第 k 个入参」。
    `plan(steps, 共享)` 按步骤顺序推断每步的实参链，返回三元组：

        (实参方案, 未匹配, 拒绝理由)

    - `实参方案`：`list[list[str] | None]`，每步一份实参名列表；某步整体推不出则为 None
    - `未匹配`：`list[(步序, 入参名, 原因)]`，逐个说清哪个槽没填上
    - `拒绝理由`：`list[str]`，人读的「为什么没链上」——尤其是类型近似但不可喂
      （如 元组[数,数,数,数] 非 列表<数>，需人工拆包），不静默。
    """

    def __init__(self, root=None):
        # 复用 `_块元数据表` 的进程级缓存，避免自动链式与占位路径各扫一遍盘。
        self._meta = _块元数据表(root)

    def _block_inputs(self, 名, 命名空间='', 领域=''):
        info = _查块元数据(self._meta, 名, 命名空间, 领域)
        return info['输入'] if info else []

    def _block_output_type(self, 名, 命名空间='', 领域=''):
        info = _查块元数据(self._meta, 名, 命名空间, 领域)
        if not info or not info['输出']:
            return ('任意', None)
        return normalize_type(info['输出'].get('类型'))

    def plan(self, steps, 共享=None):
        """根据步骤列表与共享变量，推导实参方案。返回 (实参方案, 未匹配, 拒绝理由) 三元组。

        ADR-30（W43）后的多候选消解策略：
          1. 收集所有类型兼容候选
          2. 0 候选 → 未匹配，收集近似拒绝理由（不变）
          3. 1 候选 → 直接用（不变）
          4. >1 候选 → 先做槽名字面匹配（match_slot_name）；
             命中且置信度 ≥ 0.6 → 用匹配项
             未命中或歧义 → **v0.27.0 W152 起分两支，判据是候选是不是共享常量**：
               - 有「步骤产出」候选在场 → 取最近的产出候选。产出之间的先后有语义
                 （数据前向流动），L13 钉板用例走这一支。
               - 候选**全是共享常量** → 落 None + 拒绝理由。共享常量的书写先后
                 不是证据，拿它挑参数就是猜——正是 v0.26.0 W145 静默错绑
                 （`表载入(赵产量列)`）的成因。宁可留空不硬塞。
               注意判据不是「有没有声明类型」：Q_PUB_001 的 5 个共享常量全都合法地是
               `字符串`，声明类型一个都挡不住错绑（W151 实测）。病根是拿书写顺序当证据。
          5. 同一步骤内**同变量不得复用**（D 类实现缺陷修复）：
             若某变量已被本步前面的槽消费，则从后续槽的候选池里剔除，
             不足则该槽落 None，未匹配理由记「同变量已喂前一槽，避免同型静默复用」
        """
        # 候选变量池：共享常量先入池，随后每步产出追加。
        # v0.27.0 W151：共享常量按**声明类型**入池，只有没声明 `类型` 时才退 `任意`。
        # 改前一律入 `任意`，而 `任意` 在 type_feeds 双向放行 ⇒ 每个字符串常量对每个
        # 形参都「类型兼容」⇒ 多候选消解退回「最近产出优先」随便挑，静默错绑
        # （v0.26.0 W145：`读表(赵产量列)`）。声明类型让类型图真能把不匹配的常量挡在
        # 候选池外。写错的类型名在 schema.validate_plan（W149）已拦，到这里的都合法。
        池 = []          # list of (变量名, 归一类型)
        共享名集 = set()  # W152：共享常量名集——它们之间的先后顺序**不是证据**
        for c in (共享 or []):
            if c.get('名'):
                声明 = c.get('类型')
                类型 = normalize_type(声明) if 声明 else ('任意', None)
                池.append((c['名'], 类型))
                共享名集.add(c['名'])

        实参方案 = []
        未匹配 = []
        拒绝理由 = []

        for i, s in enumerate(steps):
            名 = s.get(_F块)
            ns = s.get(_F命名空间) or ''
            域 = s.get(_F领域) or ''
            inputs = self._block_inputs(名, ns, 域)
            这步实参 = []
            这步齐全 = True
            这步已用 = set()   # 本步已被消费的变量名，防同型静默复用

            # 若方案已手写参数，尊重之（类型图只补缺失的），但仍校验可喂性
            手写 = s.get(_F参数)

            for k, slot in enumerate(inputs):
                形参类型 = normalize_type(slot.get('类型'))
                slot名 = slot.get('名', '参数%d' % (k + 1))

                if 手写 is not None and k < len(手写):
                    实参名 = str(手写[k])
                    这步实参.append(实参名)
                    这步已用.add(实参名)
                    continue

                # 收集所有类型兼容候选。扫描序沿用「池倒序 = 最近产出优先」——
                # 这是 v0.14 起的既有口径，评测集 L13（文本合成 的分隔符取最近的
                # `赵分` 而非最早的 `赵文`）是它的钉板用例，不许改。
                # 每个候选记 (变量名, 是否共享常量)——W152 消解要用。
                候选 = []
                近似拒绝 = []
                for 变量名, 变量类型 in reversed(池):
                    if 变量名 in 这步已用:
                        continue           # D 类修复：同步骤同变量不复用
                    if type_feeds(变量类型, 形参类型):
                        候选.append((变量名, 变量名 in 共享名集))
                        continue
                    # 「近似不可喂」：记下具体类型冲突，避免拒绝理由静默。
                    # 元组喂列表单独出提示——固定形状要人工拆包（W3-W4 DoD 硬用例）。
                    if 变量类型[0] == '任意':
                        continue                       # 任意 万能兜底，不算冲突
                    if 变量类型[0] == '元组' and 形参类型[0] == '列表':
                        近似拒绝.append(
                            '%s 的输出是 %s，非 %s；需人工拆包后再喂步骤%d「%s」的入参「%s」'
                            % (变量名, _人读(变量类型), _人读(形参类型),
                               i + 1, 名, slot名))
                    else:
                        近似拒绝.append(
                            '%s 的输出是 %s，无法喂步骤%d「%s」的入参「%s」（形参需 %s）'
                            % (变量名, _人读(变量类型), i + 1, 名,
                               slot名, _人读(形参类型)))

                if not 候选:
                    这步齐全 = False
                    这步实参.append(None)
                    # 区分「同变量已用」与「无类型兼容」两种未匹配原因
                    池中同型 = [v for v, t in 池 if type_feeds(t, 形参类型)]
                    if 池中同型 and all(v in 这步已用 for v in 池中同型):
                        未匹配.append((i + 1, slot名,
                                       '同变量已喂前一槽，避免同型静默复用'))
                    else:
                        未匹配.append((i + 1, slot名, '无类型兼容的已产出变量可喂'))
                    拒绝理由.extend(近似拒绝)
                    continue

                if len(候选) == 1:
                    命中 = 候选[0][0]
                else:
                    # 多候选：先按槽名字面匹配消解（ADR-30）。
                    候选名 = [v for v, _ in 候选]
                    字面命中, 置信度 = match_slot_name(slot名, 候选名)
                    if 字面命中 is not None and 置信度 >= 0.6:
                        命中 = 字面命中
                    else:
                        # 字面消解失败。W152 的判据是**候选是不是共享常量**，
                        # 不是「有没有声明类型」——后者被 W151 落地时的实测证伪：
                        # Q_PUB_001 的 5 个共享常量全都合法地是 `字符串`（CSV 路径、
                        # 列名、起止日期），声明类型一个都挡不住，`表载入.路径` 照旧
                        # 按 recency 错绑到 `赵产量列`。真正的病根是**拿声明先后当证据**。
                        #   - 步骤产出之间的 recency **有语义**：数据前向流动，最近一步
                        #     的产出是自然的续接对象（L13 等既有链式用例走这一支）。
                        #   - 共享常量之间的 recency **没有语义**：谁写在前谁写在后是
                        #     作者的书写顺序，拿它挑参数就是猜。宁可留空不硬塞。
                        产出候选 = [v for v, 是共享 in 候选 if not 是共享]
                        if 产出候选:
                            命中 = 产出候选[0]
                        else:
                            这步齐全 = False
                            这步实参.append(None)
                            未匹配.append((i + 1, slot名,
                                           '多个共享常量都能喂，其先后顺序不是证据'
                                           '（避免静默错绑）'))
                            拒绝理由.append(
                                '步骤%d「%s」的入参「%s」有 %d 个共享常量候选（%s）'
                                '类型都可喂，槽名字面也消解不了——共享常量的书写先后'
                                '不是证据，故拒绝硬塞。请在该步显式写 参数，'
                                '或把常量改名成能与槽名「%s」字面匹配的名字'
                                '（ADR-41 §6）'
                                % (i + 1, 名, slot名, len(候选名),
                                   '、'.join(候选名), slot名))
                            continue

                这步实参.append(命中)
                这步已用.add(命中)

            # 该步产出入池，供后续步骤消费
            池.append((result_var(i), self._block_output_type(名, ns, 域)))
            实参方案.append(这步实参 if 这步齐全 else None)

        return 实参方案, 未匹配, 拒绝理由


def _导入行(steps):
    """生成去重后的 `从 ... 导入 ...` 行。顺序按步骤首次出现，稳定可测。

    v0.19.0 W69：第三方块（步骤带非空 `命名空间`）多插一段——
    `从 blocks.<命名空间>.<领域>.<块> 导入 X`。内置块（无 `命名空间` 或空串）
    仍是两段 `blocks.<领域>.<块>`，一个字节都不变。

    这段不是可选优化：`module_loader` 把「已装块包的块根父目录」挂进搜索路径，
    第三方块的物理布局是 `<块根>/<命名空间>/<领域>/<块>/`，少了命名空间段
    永远解析不到。而失败发生在**使用方**运行时——块作者自己测不出来。

    去重键含命名空间：跨命名空间同名块（`scan_blocks` 明确允许）是两条不同的
    导入行，用 (领域, 块, 导出名) 做键会把后者静默吞掉。
    """
    seen = set()
    lines = []
    for s in steps:
        ns = s.get(_F命名空间) or ''
        key = (ns, s[_F领域], s[_F块], s[_F导出名])
        if key in seen:
            continue
        seen.add(key)
        路径段 = ['blocks'] + ([ns] if ns else []) + [s[_F领域], s[_F块]]
        lines.append('从 %s 导入 %s。' % ('.'.join(路径段), s[_F导出名]))
    return lines


def synthesize(方案, 自动链式=False, root=None, 用示例填参=False):
    """把选块方案（`docs/协议-三通道.md` 定义的 JSON）合成为极快源码字符串。

    参数校验走 `schema.ensure_plan`（三通道协议的唯一真源）：字段形状、`步骤`
    非空、`步骤[i]` 有 `块/领域/导出名` 都由它兜底。协议已锁死，glue 不再
    重复发明校验规则。校验失败会抛 `schema.SchemaError`；`blocks_cli._组装`
    只捕 `ValueError`，为兼容旧调用点这里再原样转成 `ValueError`。

    `自动链式=True` 时，对**缺 `参数`** 的步骤用 `TypeGraph` 按类型推断实参链；
    推不出的仍落 `?` 占位并把拒绝理由写进注释。

    `用示例填参=True`（v0.18.0 · W55 · 集成反馈 P1）：**opt-in**——当步骤既没
    手写 `参数`、又不是自动链式命中，则尝试从块 `示例` 里提取 `<导出名>(...)`
    的实参串直接复用。默认 **关**：这条路径会把「块作者示例值」硬塞给方案，
    在链式上下文里可能覆盖掉「本该接前步 赵果i」的语义。给嵌入式/浏览器等
    「拿到即想跑」的场景一个 opt-in 出口即可，主链（Web/CLI/REPL）不动。

    P0（v0.18.0 · W55 · 集成反馈）：落到 `?` 占位时，占位符个数与块 `输入`
    元数一致；查不到元数据（未知块）才回退到单个 `?`。

    返回：以换行分隔、末尾带单个换行的极快源码。
    """
    try:
        schema.ensure_plan(方案)
    except schema.SchemaError as e:
        raise ValueError(str(e))
    steps = 方案[_F步骤]

    自动实参 = None
    拒绝理由 = []
    if 自动链式:
        图 = TypeGraph(root=root)
        自动实参, _未匹配, 拒绝理由 = 图.plan(steps, 方案.get(_F共享))

    # 元数据表（P0/P1）——占位路径 与 示例填参 都要用；未启用可选路径时
    # 也用于查 `输入` 元数以生成对应个数的 `?`（这是纯正确性问题，无 opt-in）。
    # 惰性：所有步骤都手写了 `参数` 就一个块都不用扫，Web `/api/组` 的常见
    # 路径（前端把参数填齐了才提交）因此零额外开销。
    _元数据 = (_块元数据表(root)
               if any(s.get(_F参数) is None for s in steps) else {})

    lines = ['-- 由 极快 AI 桥接（选块 + 粘合%s）自动合成'
             % ('，类型图链式' if 自动链式 else '')]
    if 方案.get(_F需求):
        lines.append('-- 需求：' + str(方案[_F需求]))
    if 拒绝理由:
        lines.append('--')
        lines.append('-- 类型图未能自动链上的传参（需人工处理）：')
        for r in 拒绝理由:
            lines.append('--   * ' + r)
    lines.append('')

    # 1) 导入
    lines.extend(_导入行(steps))

    # 2) 共享常量 / 输入
    共享 = 方案.get(_F共享) or []
    if 共享:
        lines.append('')
        for item in 共享:
            if not item.get('名') or item.get('值') is None:
                raise ValueError('共享项须含「名」与「值」：%r' % (item,))
            lines.append('定义%s=%s。' % (item['名'], item['值']))

    # 3) 步骤调用
    lines.append('')
    结果变量 = []
    for i, s in enumerate(steps):
        var = result_var(i)
        结果变量.append(var)
        if s.get(_F说明):
            lines.append('-- 步骤 %d：%s' % (i + 1, s[_F说明]))

        参数 = s.get(_F参数)
        块名 = s.get(_F块)
        块ns = s.get(_F命名空间) or ''
        块域 = s.get(_F领域) or ''
        块元 = _查块元数据(_元数据, 块名, 块ns, 块域) or {}
        块输入 = 块元.get('输入') or []
        块示例 = 块元.get('示例') or ''

        if 参数 is not None:
            实参 = ' '.join(str(p) for p in 参数)
        elif 自动实参 is not None and 自动实参[i] is not None:
            实参 = ' '.join(自动实参[i])
        elif 用示例填参 and 块示例:
            # P1（opt-in）：从块作者的示例里取实参串，逐字放回调用位。
            示例实参 = _从示例提取实参(块示例, s[_F导出名])
            if 示例实参:
                lines.append('-- 用块示例填参（%s）：%s'
                             % (s[_F导出名], 示例实参))
                实参 = 示例实参
            else:
                实参 = _占位符生成(块输入, s[_F导出名], lines)
        else:
            实参 = _占位符生成(块输入, s[_F导出名], lines)
        lines.append('定义%s=%s(%s)。' % (var, s[_F导出名], 实参))

    # 4) 打印
    lines.append('')
    打印列表 = 方案.get(_F打印) or 结果变量
    for 名 in 打印列表:
        lines.append('打印 %s。' % 名)

    return '\n'.join(lines).rstrip() + '\n'


def _占位符生成(块输入, 导出名, lines):
    """P0：按 `输入` 元数生成 N 个 `?` 占位，注释里点明参数名。

    - 元数 ≥ 1 且拿得到入参名 → `? ? ...` 空格拼接，与既有实参空格分隔口径一致；
      注释形如 `-- 需人工填参：<导出名> 的入参未指定（下一行 N 个 ? 对应：名1, 名2）`
    - 元数为 0 或元数据缺失 → 单个 `?` + 原文案（旧路径兼容——已有测试与
      `_占位记号 = '需人工填参'` 的 Web 检查都吃这条文案）
    """
    n = len(块输入)
    if n <= 0:
        lines.append('-- 需人工填参：%s 的入参未指定（下一行的 %s 占位）'
                     % (导出名, _占位符))
        return _占位符
    if n == 1:
        # 单参数不带「N 个」，避免噪音；仍带参数名以便人一眼看出要填啥
        名 = 块输入[0].get('名') or '参数1'
        lines.append('-- 需人工填参：%s 的入参未指定（下一行的 %s 占位对应：%s）'
                     % (导出名, _占位符, 名))
        return _占位符
    名单 = [ (x.get('名') or ('参数%d' % (k + 1)))
             for k, x in enumerate(块输入) ]
    lines.append('-- 需人工填参：%s 的入参未指定（下一行 %d 个 %s 对应：%s）'
                 % (导出名, n, _占位符, ', '.join(名单)))
    return ' '.join([_占位符] * n)


def _cli(argv=None):
    p = argparse.ArgumentParser(description='极快块粘合合成器')
    p.add_argument('方案', help='选块方案 JSON 文件路径')
    p.add_argument('--自动链式', '--auto', action='store_true',
                   help='用类型图推断缺 参数 的步骤实参链（ADR-26）')
    p.add_argument('--用示例填参', '--from-example', action='store_true',
                   help='缺 参数 且未自动链上时，从块 `示例` 取实参（v0.18.0）')
    args = p.parse_args(argv)
    with open(args.方案, 'r', encoding='utf-8') as f:
        方案 = json.load(f)
    sys.stdout.write(synthesize(方案, 自动链式=args.自动链式,
                                用示例填参=args.用示例填参))
    return 0


if __name__ == '__main__':
    raise SystemExit(_cli())
