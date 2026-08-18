# -*- coding: utf-8 -*-
"""极快语言 · 三通道统一 JSON 协议（v0.15.0 W20）。

CLI（`jk 块 …`）、LSP（`workspace/executeCommand`）、Web（`tools/web/server.py`）
三条通道对外吐的 JSON 必须来自同一份定义。任何通道自行发明字段一律拒绝
——这是 v0.15.0 的硬门槛。

三类结构：

    候选     {名称, 领域, 层级, 描述, 分数, 路径}
             用于 `选` 的返回、LSP executeCommand 结果、Web 候选卡片

    方案     {需求?, 共享?, 步骤: [{块, 领域, 导出名, 参数?, 说明?}], 打印?}
             三边 `组` / `跑` 共用的输入

    执行结果 {stdout, stderr, 返回值, 耗时毫秒, 错误?}
             三边 `跑` 共用的输出

v0.27.0 W148 追加**规划器通道**（`jk 块 规划` / `jk 块 问`，正本 ADR-41）：

    规划上下文包 {需求, 语义命中, 候选, 回填契约, 拒答建议, 分歧告警?}
                 候选比「选响应」候选多 `输入槽`/`输出类型` —— 没有这两项 LLM
                 就只能猜实参，这是 v0.26.0 W145 静默错绑的直接根因

    回填响应     {需求, 方案, 模型, 溯源?}
                 LLM 回填后的形状。本层只校验**形状**；ADR-41 §4 那五条硬规则
                 要拿上下文包做参照，落 `tools/ai-bridge/planner.py`

实现约束：只用标准库。不引 jsonschema——校验规则简单到不值得一个依赖，
且 `src/jikuai/` 运行时保持零第三方依赖是既有约定。
"""

import json
import os
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    'SchemaError',
    'CANDIDATE_REQUIRED', 'CANDIDATE_OPTIONAL',
    'PLAN_REQUIRED', 'PLAN_OPTIONAL', 'STEP_REQUIRED', 'STEP_OPTIONAL',
    'RESULT_REQUIRED', 'RESULT_OPTIONAL',
    'DIAGNOSTIC_REQUIRED', 'DIAGNOSTIC_OPTIONAL', 'DIAGNOSTIC_LEVELS',
    'SELECT_ENVELOPE_REQUIRED', 'SELECT_ENVELOPE_OPTIONAL',
    'RUN_ENVELOPE_REQUIRED', 'RUN_ENVELOPE_OPTIONAL',
    'SAVED_PLAN_REQUIRED', 'SAVED_PLAN_LIST_FIELDS', 'SAVED_PLAN_LIST_ENVELOPE',
    'SLOT_REQUIRED',
    'CONTEXT_CANDIDATE_REQUIRED', 'CONTEXT_CANDIDATE_OPTIONAL',
    'SEMANTIC_HIT_REQUIRED', 'SEMANTIC_HIT_OPTIONAL',
    'DIVERGENCE_WARNING_REQUIRED',
    'FILL_CONTRACT_REQUIRED', 'REJECT_ADVICE_REQUIRED',
    'CONTEXT_ENVELOPE_REQUIRED', 'CONTEXT_ENVELOPE_OPTIONAL',
    'FILLED_ENVELOPE_REQUIRED', 'FILLED_ENVELOPE_OPTIONAL',
    'make_candidate', 'make_step', 'make_plan', 'make_result',
    'make_select_envelope', 'make_run_envelope',
    'make_saved_plan', 'make_saved_plan_summary', 'make_saved_plan_list',
    'make_slot', 'make_context_candidate', 'make_semantic_hit',
    'make_divergence_warning', 'make_fill_contract', 'make_reject_advice',
    'make_context_envelope', 'make_filled_envelope',
    'candidate_from_hit', 'level_table', 'export_table', 'diagnostics_from_error',
    'validate_candidate', 'validate_plan', 'validate_result',
    'validate_select_envelope', 'validate_run_envelope',
    'validate_context_candidate', 'validate_context_envelope',
    'validate_filled_envelope',
    'ensure_candidate', 'ensure_plan', 'ensure_result',
    'ensure_select_envelope', 'ensure_run_envelope',
    'ensure_context_envelope', 'ensure_filled_envelope',
]


class SchemaError(ValueError):
    """协议校验失败。中文消息直接面向用户，可原样打到 stderr / HTTP body。"""


# ---- 字段清单（三通道唯一真源）---------------------------------------

#: `候选` 必需字段。`层级`/`导出名` 来自 `索引.json`，不是启发式猜的，见
#: `level_table` / `export_table`。
#:
#: **v0.17.0 Breaking Change**：新增 `导出名`（W37）。历史缺陷：目录名 `名称`
#: 与调用用的 `导出名` 允许不同（`个税` 块导出 `缴税`），但候选只带 `名称`，
#: 命令面板/CLI 就块插入的 `从 blocks.<域>.<块> 导入 <名称>。` 在两者不一致的
#: 块上是**错的**。补齐后 `buildImportStatement` 一律用 `导出名`，兜底分支删除。
CANDIDATE_REQUIRED = ('名称', '领域', '层级', '导出名', '描述', '分数', '路径')
#: `候选` 可选字段。`命名空间` 为 W22 第三方块预留（内置块为空串）。
CANDIDATE_OPTIONAL = ('命名空间',)

#: `方案` 字段。`步骤` 是方案的判据——有它才算方案。
PLAN_REQUIRED = ('步骤',)
PLAN_OPTIONAL = ('需求', '共享', '打印')

#: `方案.步骤[i]` 字段。`导出名` 是调用名，`块` 是导入用的目录名，两者可不同。
STEP_REQUIRED = ('块', '领域', '导出名')
STEP_OPTIONAL = ('参数', '说明', '命名空间')

#: `执行结果` 字段。`错误` 只在失败时出现，成功时**不出现**（而非空串）。
#: `诊断` 只在有位置信息时出现，供 Web 编辑框把 行/列 高亮出来（W19）。
RESULT_REQUIRED = ('stdout', 'stderr', '返回值', '耗时毫秒')
RESULT_OPTIONAL = ('错误', '诊断')

#: `诊断` 条目字段。`行`/`列` 是 **1-based 码点**口径，与
#: `diagnostics.model.Position` 一致（不是 UTF-16、不是字节）。
#: LSP 那侧要的 0-based UTF-16 由 `service/position.py` 负责换算，本层不掺和。
#: `级别` 取值域是 `DIAGNOSTIC_LEVELS`，与 `diagnostics.model.Severity` 同源——
#: 前端 `app.js` 的 `级别类()`/`归并诊断()` 按严重度「错误/警告/提示」分档，
#: 后端塞分类名（如「运行错误」）过来会全部掉到红色档，警告/提示分支形同虚设。
DIAGNOSTIC_REQUIRED = ('行', '列', '级别', '消息')
DIAGNOSTIC_OPTIONAL = ('代码',)

#: `级别` 白名单。**必须**与 `src/jikuai/diagnostics/model.py` 的 `Severity`
#: Literal 三档同源；前端 `app.js` 的 `归并诊断()` 也按这三档分色。分类信息
#: （`ErrorCategory.value`，如「运行错误」「已知限制」）不进这里——那是**分类**
#: 不是**严重度**，混用会让前端警告/提示分支成死代码（v0.15.0 复核轮教训）。
DIAGNOSTIC_LEVELS = frozenset({'错误', '警告', '提示'})

#: `选` 的响应信封（三通道共用）：CLI `jk 块 选 --json`、LSP `极快.选块`、
#: Web `POST /api/选` 都吐这个形状。
#: `降级说明` —— 请求了神经检索但拿不到查询向量时的降级原因。它是协议的正式
#: 可选字段而不是某个通道的私货：用户主动勾了「神经」却拿到启发式结果，必须
#: 被告知；藏进服务端日志等于骗人，塞进 HTTP 头则只有 Web 能用。
SELECT_ENVELOPE_REQUIRED = ('需求', '候选')
SELECT_ENVELOPE_OPTIONAL = ('降级说明',)

#: `跑` 的响应信封（v0.15.0 W20 新增）：CLI `jk 块 跑 --json` 与
#: Web `POST /api/跑` 共用。`源码` 是必需的——`跑` 的结果没有源码就没法复现，
#: 前端也要靠它把出错行高亮出来（配合 `执行结果.诊断`）。`需求` 只是注释，
#: 方案里没写就不出现。
#: 为什么它值得一份常量而 `组响应`（只有 `源码` 一个字段）不值得：`跑响应`
#: 跨两个通道、含嵌套的 `执行结果`，字段名手写两遍必然漂。
RUN_ENVELOPE_REQUIRED = ('源码', '执行结果')
RUN_ENVELOPE_OPTIONAL = ('需求',)

#: `已存方案` 存档项字段（v0.16.0 W31）。`id` / `标题` / `时间戳` 是列表
#: 与详情共用的元数据，`方案` 只在详情里出现（列表 endpoint 不返回方案本体，
#: 省流量也免得把用户的整批共享量甩进列表 payload）。
#: 落盘 JSON 的键名 = 这里的常量，前后端两侧都从本文件取，Web 层零字面量。
SAVED_PLAN_REQUIRED = ('id', '标题', '时间戳', '方案')
#: `已存方案.列` 的条目字段（不含 `方案` 本体，只回元数据）。
SAVED_PLAN_LIST_FIELDS = ('id', '标题', '时间戳')
#: `已存方案.列` 的响应信封。给它一个信封而不是裸数组：后面要加 `总数`
#: / `上限` 之类的元信息时不必再做一次破坏性契约变更。
SAVED_PLAN_LIST_ENVELOPE = ('方案列表',)


# ---- 规划器通道（v0.27.0 W148 · M28）--------------------------------
# 规划器把「问句 + 检索候选 + 语义层 + 块元数据」组装成 LLM 能照着回填的受限结构
# （规划上下文包），LLM 回填出 `方案`，再过 `validate_filled_envelope` 兜底。
# 正本：docs/ADR-41-规划器与NL层.md。为什么这套字段是新协议而不是复用「选响应」：
# 「选响应」的候选**不带输入槽**，这正是 v0.26.0 W145 里 LLM 写不出实参、124 步
# 全靠人手写的直接根因（ADR-41 §3）。

#: 块的一个**输入槽**：`名` 是形参名，`类型` 是 ADR-26 类型词表里的归一类型。
#: 上下文包把它喂给 LLM，是把「实参写死」从事后校验前移到信息供给——LLM 知道
#: 每个槽的名与类型，才有可能一次填对；`validate_filled` 是兜底，不是唯一防线。
SLOT_REQUIRED = ('名', '类型')

#: 规划上下文包里的**候选**：在「选响应」候选（`CANDIDATE_REQUIRED`）之外补
#: `输入槽`（数组，每项 `SLOT_REQUIRED`）与 `输出类型`。这两项是新增信息，缺了
#: LLM 就只能猜实参。`命名空间` 沿用可选语义（第三方块才有）。
CONTEXT_CANDIDATE_REQUIRED = CANDIDATE_REQUIRED + ('输入槽', '输出类型')
CONTEXT_CANDIDATE_OPTIONAL = CANDIDATE_OPTIONAL

#: **语义命中**：问句命中的一条业务词及其锚定的表/字段与口径说明，读自
#: `制造/语义层.json` 的 42 条业务词。`口径说明` 让 LLM 判断「这个词该落哪个口径块」。
SEMANTIC_HIT_REQUIRED = ('业务词', '表', '字段', '口径说明')
SEMANTIC_HIT_OPTIONAL = ()

#: **分歧告警**：命中了一处口径分歧点（ADR-40 §5）时给出，逼 LLM 显式选一条。
#: `两侧块名` 是数组，`实测差值` 是人读字符串（如「缺陷率 0.050550 vs 0.032218，
#: 差 57%」），`须显式选一条` 恒 True——它存在本身就是「这里不许含糊」的信号。
DIVERGENCE_WARNING_REQUIRED = ('分歧点', '两侧块名', '实测差值', '须显式选一条')

#: **回填契约**：给 LLM 的回填格式说明。`目标` 固定是「方案」，`必填` 列出回填时
#: 一个都不能省的字段（尤其 `步骤[].参数`——省略会触发粘合器静默错绑，ADR-41 §4）。
#: `禁止` 列出会被拒的做法（多余字段、幻觉块名等）。
FILL_CONTRACT_REQUIRED = ('目标', '必填', '禁止')

#: **拒答建议**：`覆盖` 是布尔——语义层业务词是否登记且有候选块自报对应口径；
#: 为 False 时 `理由` 说明为什么判为库外能力。**这不是分数阈值**（检索层永远返回
#: top-K，四轮实测已证伪分数拒答，ADR-41 §5），是词表覆盖判定。`理由` 也用来登记
#: 已知缺口（如「制造域无领域先验加分」）供人复核。
REJECT_ADVICE_REQUIRED = ('覆盖', '理由')

#: **规划上下文包**信封（`jk 块 规划` 的出口）。`候选` 每项 `CONTEXT_CANDIDATE_*`；
#: `语义命中`/`分歧告警` 是数组（可空）；`回填契约`/`拒答建议` 各一个对象。
CONTEXT_ENVELOPE_REQUIRED = ('需求', '语义命中', '候选', '回填契约', '拒答建议')
CONTEXT_ENVELOPE_OPTIONAL = ('分歧告警',)

#: **回填响应**信封（LLM 回填后、过校验器前的形状）。`方案` 是 LLM 产出的
#: `PLAN_*` 结构；`模型` 是回填来源标识（人手填为 `'人工'`，端到端为端点名），
#: 供录像（ADR-41 §8）与溯源用。`溯源` 可选，端到端时带上每个数字的来源块名。
FILLED_ENVELOPE_REQUIRED = ('需求', '方案', '模型')
FILLED_ENVELOPE_OPTIONAL = ('溯源',)


# ---- 构造器 -----------------------------------------------------------

def make_candidate(名称: str, 领域: str, 层级: int, 导出名: str, 描述: str,
                   分数: float, 路径: str = '',
                   命名空间: Optional[str] = None) -> Dict[str, Any]:
    """构造一条 `候选`。`分数` 统一保留 4 位小数——三通道数字要能逐字比对。

    `导出名` 是**必需**位置参数（v0.17.0 W37）：调用方必须显式给出，
    没有默认值也不拿 `名称` 兜底。理由——兜底会在目录名≠导出名的块上静默
    产出错误的 `导入` 语句，正是 W37 要修的缺陷；缺值就该在构造点炸。
    """
    候选 = {
        '名称': 名称,
        '领域': 领域,
        '层级': int(层级),
        '导出名': 导出名,
        '描述': 描述,
        '分数': round(float(分数), 4),
        '路径': 路径,
    }
    if 命名空间 is not None:
        候选['命名空间'] = 命名空间
    return 候选


def make_step(块: str, 领域: str, 导出名: str,
              参数: Optional[Sequence[str]] = None,
              说明: Optional[str] = None,
              命名空间: Optional[str] = None) -> Dict[str, Any]:
    """构造一条 `步骤`。`参数` 省略即交给粘合器 `--自动链式` 的类型图去推。

    `命名空间`（v0.19.0 W69）：块的来源包名，内置块**不传**（或传空串）。
    粘合器据此在导入路径里插一段——`从 blocks.<命名空间>.<领域>.<块> 导入 X`。
    与 `候选.命名空间` 同构：`None` 时不写键，旧方案的字典形状一字不变。
    """
    步骤 = {'块': 块, '领域': 领域, '导出名': 导出名}
    if 参数 is not None:
        步骤['参数'] = list(参数)
    if 说明 is not None:
        步骤['说明'] = 说明
    if 命名空间 is not None:
        步骤['命名空间'] = 命名空间
    return 步骤


def make_plan(步骤: Sequence[Dict[str, Any]],
              需求: Optional[str] = None,
              共享: Optional[Sequence[Dict[str, str]]] = None,
              打印: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """构造一份 `方案`。"""
    方案: Dict[str, Any] = {'步骤': list(步骤)}
    if 需求 is not None:
        方案['需求'] = 需求
    if 共享 is not None:
        方案['共享'] = list(共享)
    if 打印 is not None:
        方案['打印'] = list(打印)
    return 方案


def make_result(stdout: str = '', stderr: str = '', 返回值: str = '',
                耗时毫秒: float = 0.0,
                错误: Optional[str] = None,
                诊断: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """构造一份 `执行结果`。`返回值` 是 `repr()` 后的字符串，不是原始对象
    ——原始对象无法保证可 JSON 序列化，跨通道传只会在边界上炸。

    `诊断` 是可选的结构化位置信息，供 Web 编辑框把 行/列 高亮出来（W19）；
    没有位置信息（例如运行期异常）时省略。
    """
    结果 = {
        'stdout': stdout,
        'stderr': stderr,
        '返回值': 返回值,
        '耗时毫秒': round(float(耗时毫秒), 3),
    }
    if 错误 is not None:
        结果['错误'] = 错误
    if 诊断 is not None:
        结果['诊断'] = list(诊断)
    return 结果


def diagnostics_from_error(exc: Any) -> Optional[List[Dict[str, Any]]]:
    """从极快异常对象提取结构化 `诊断` 列表；拿不到位置信息返回 None。

    用鸭子类型读 `exc.info`（`errors.ErrorInfo`），不 import diagnostics/errors
    ——schema 是纯标准库层，不能反向依赖上层模块。

    契约（与前端 `app.js` 的 `级别类()`/`归并诊断()` 对齐）：

    - `级别` 固定填 `'错误'`。抛出来的 `JiKuaiError` 本质就是错误，不是警告/提示；
      前端按**严重度**三档分色，塞分类名（`ErrorCategory.value` 如「运行错误」）
      过来只会全掉红色档，让警告/提示分支成死代码——v0.15.0 复核轮的教训。
    - 分类信息不丢：能读到 `info.category.value` 就当前缀塞进 `消息`，形如
      `'[运行错误] 未定义的变量 赵x'`；读不到就不加前缀。

    与旧 `_诊断条` 私有实现的差别：那个把 `category.value` 直接填进 `级别`，
    与前端严重度口径不同源。本函数一并修掉，`blocks_cli` / `tools/web/server.py`
    统一改调本函数（不再各写一份）。
    """
    info = getattr(exc, 'info', None)
    if info is None:
        return None
    行 = getattr(info, 'line', None)
    列 = getattr(info, 'col', None)
    if not isinstance(行, int) or not isinstance(列, int):
        return None
    分类 = getattr(getattr(info, 'category', None), 'value', None)
    消息原文 = getattr(info, 'message', str(exc))
    if isinstance(分类, str) and 分类:
        消息 = '[%s] %s' % (分类, 消息原文)
    else:
        消息 = 消息原文
    条 = dict(zip(DIAGNOSTIC_REQUIRED, (行, 列, '错误', 消息)))
    return [条]


def make_select_envelope(需求: str, 候选: Sequence[Dict[str, Any]],
                         降级说明: Optional[str] = None) -> Dict[str, Any]:
    """构造 `选` 的响应信封。三通道（CLI/LSP/Web）唯一出口。"""
    信封: Dict[str, Any] = {'需求': 需求, '候选': list(候选)}
    if 降级说明 is not None:
        信封['降级说明'] = 降级说明
    return 信封


def make_run_envelope(源码: str, 执行结果: Dict[str, Any],
                      需求: Optional[str] = None) -> Dict[str, Any]:
    """构造 `跑` 的响应信封。CLI `jk 块 跑 --json` 与 Web `POST /api/跑` 共用。

    `执行结果` 应是 `make_result` 的产物；这里不重复校验，交给 `ensure_run_envelope`
    在需要时统一把关。
    """
    信封: Dict[str, Any] = {'源码': 源码, '执行结果': 执行结果}
    if 需求 is not None:
        信封['需求'] = 需求
    return 信封


def make_saved_plan(id: str, 标题: str, 时间戳: str,
                    方案: Dict[str, Any]) -> Dict[str, Any]:
    """构造一份 `已存方案` 落盘项（v0.16.0 W31）。

    `id` 是 hex 串（`uuid4().hex`），`时间戳` 是 ISO 8601 UTC 字符串。
    键名一律从 `SAVED_PLAN_REQUIRED` 常量取。
    """
    return dict(zip(SAVED_PLAN_REQUIRED, (id, 标题, 时间戳, 方案)))


def make_saved_plan_summary(id: str, 标题: str, 时间戳: str) -> Dict[str, Any]:
    """构造 `已存方案.列` 条目（仅元数据，不含方案本体）。"""
    return dict(zip(SAVED_PLAN_LIST_FIELDS, (id, 标题, 时间戳)))


def make_saved_plan_list(条目: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """构造 `已存方案.列` 的响应信封。"""
    return dict(zip(SAVED_PLAN_LIST_ENVELOPE, (list(条目),)))


# ---- 规划器通道构造器（v0.27.0 W148）--------------------------------

def make_slot(名: str, 类型: str) -> Dict[str, str]:
    """构造一个块**输入槽**。`类型` 应落 ADR-26 类型词表，本层不校验取值域
    （那是 W149 `共享.类型` 与块元数据的事），只保证键名来自 `SLOT_REQUIRED`。"""
    return dict(zip(SLOT_REQUIRED, (名, 类型)))


def make_context_candidate(名称: str, 领域: str, 层级: int, 导出名: str, 描述: str,
                           分数: float, 输入槽: Sequence[Dict[str, str]],
                           输出类型: str, 路径: str = '',
                           命名空间: Optional[str] = None) -> Dict[str, Any]:
    """构造一条**规划上下文包候选**：在 `make_candidate` 基础上补 `输入槽`/`输出类型`。

    复用 `make_candidate` 造前七个字段，保证与「选响应」候选逐字节同构（数字口径、
    键序都一致），再补两个新字段。`输入槽` 每项应是 `make_slot` 的产物。
    """
    候选 = make_candidate(名称, 领域, 层级, 导出名, 描述, 分数, 路径, 命名空间)
    候选['输入槽'] = [dict(s) for s in 输入槽]
    候选['输出类型'] = 输出类型
    return 候选


def make_semantic_hit(业务词: str, 表: str, 字段: str,
                      口径说明: str) -> Dict[str, Any]:
    """构造一条**语义命中**（读自 `制造/语义层.json`）。"""
    return dict(zip(SEMANTIC_HIT_REQUIRED, (业务词, 表, 字段, 口径说明)))


def make_divergence_warning(分歧点: str, 两侧块名: Sequence[str],
                            实测差值: str,
                            须显式选一条: bool = True) -> Dict[str, Any]:
    """构造一条**分歧告警**（命中 ADR-40 §5 口径分歧点时）。

    `须显式选一条` 默认 True——它存在本身就是「这里不许含糊」的信号，留参数只为
    极少数「两侧口径在本数据集恒等值、可任选」的边界情形显式标 False。
    """
    return dict(zip(DIVERGENCE_WARNING_REQUIRED,
                    (分歧点, list(两侧块名), 实测差值, bool(须显式选一条))))


def make_fill_contract(必填: Sequence[str], 禁止: Sequence[str],
                       目标: str = '方案') -> Dict[str, Any]:
    """构造**回填契约**（给 LLM 的回填格式说明）。"""
    return dict(zip(FILL_CONTRACT_REQUIRED, (目标, list(必填), list(禁止))))


def make_reject_advice(覆盖: bool, 理由: str) -> Dict[str, Any]:
    """构造**拒答建议**。`覆盖`=True 表示库内有对应能力；False 表示判为库外
    （词表覆盖判定，非分数阈值，见 ADR-41 §5）。"""
    return dict(zip(REJECT_ADVICE_REQUIRED, (bool(覆盖), 理由)))


def make_context_envelope(需求: str, 语义命中: Sequence[Dict[str, Any]],
                          候选: Sequence[Dict[str, Any]],
                          回填契约: Dict[str, Any],
                          拒答建议: Dict[str, Any],
                          分歧告警: Optional[Sequence[Dict[str, Any]]] = None
                          ) -> Dict[str, Any]:
    """构造**规划上下文包**信封（`jk 块 规划` 的出口）。

    `分歧告警` 为 None 时不写键（旧形状无关，这是新协议；但沿用「可选字段缺省
    即不出现」的既有约定，形状最小化）。
    """
    信封: Dict[str, Any] = {
        '需求': 需求,
        '语义命中': [dict(h) for h in 语义命中],
        '候选': list(候选),
        '回填契约': dict(回填契约),
        '拒答建议': dict(拒答建议),
    }
    if 分歧告警 is not None:
        信封['分歧告警'] = [dict(w) for w in 分歧告警]
    return 信封


def make_filled_envelope(需求: str, 方案: Dict[str, Any], 模型: str,
                         溯源: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构造**回填响应**信封（LLM 回填后、过校验器前）。

    `模型` 是回填来源标识：人手填 `'人工'`，端到端填端点名。它进录像（ADR-41 §8）
    与溯源，不可省。
    """
    信封: Dict[str, Any] = {'需求': 需求, '方案': 方案, '模型': 模型}
    if 溯源 is not None:
        信封['溯源'] = 溯源
    return 信封


# ---- Hit → 候选 -------------------------------------------------------

_LEVELS: Optional[Dict[str, int]] = None
_EXPORTS: Optional[Dict[str, str]] = None


def _load_index(index_path: Optional[str]):
    """读 `索引.json` 的 `块` 数组。读不到/坏了返回空列表。

    索引缺失或损坏不在这里炸：那是 G12 门禁的职责，不该把一条 `选` 请求打挂。
    """
    if index_path is None:
        from ..pkg.blocks import blocks_root
        index_path = os.path.join(blocks_root(), '索引.json')
    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    条目表 = data.get('块')
    return 条目表 if isinstance(条目表, list) else []


def level_table(index_path: Optional[str] = None) -> Dict[str, int]:
    """块名 → `层级` 映射，读自 `索引.json`。进程级缓存。

    `ai.retrieval.Hit` 只承载检索决策需要的字段，不带 `层级`；而协议要求
    候选带 `层级`。这里从索引取真实值，而不是给个默认值糊过去。
    """
    global _LEVELS
    默认索引 = index_path is None
    if 默认索引 and _LEVELS is not None:
        return _LEVELS
    table: Dict[str, int] = {}
    for 条目 in _load_index(index_path):
        名称 = 条目.get('名称')
        if isinstance(名称, str):
            try:
                table[名称] = int(条目.get('层级', 0))
            except (TypeError, ValueError):
                table[名称] = 0
    if 默认索引:
        _LEVELS = table
    return table


def export_table(index_path: Optional[str] = None) -> Dict[str, str]:
    """块名 → **主 `导出名`** 映射，读自 `索引.json` 的 `导出` 字段（v0.17.0 W37）。

    为什么必须有这张表：块的目录名（`名称`，`导入` 用）与调用名（`导出名`）
    允许不同——`个税` 块导出 `缴税`。候选只带 `名称` 时，命令面板拼出的
    `从 blocks.财务.个税 导入 个税。` 是错的（正确是 `导入 缴税`）。

    取值**不做启发式推断**，一律来自索引，与 W20 给 `层级` 定的规矩同源。
    一个块声明多个 `导出` 时的**确定性择一**（不是猜）：与块同名的优先，
    否则取排序首位——和 `blocks_cli._推导出名` 同一套 tie-break，
    保证 CLI 人读路径与 JSON 协议路径选出同一个名字。

    索引里查不到该块（索引过期）时本表不含它，由 `candidate_from_hit`
    统一处理降级——见那里的说明。
    """
    global _EXPORTS
    默认索引 = index_path is None
    if 默认索引 and _EXPORTS is not None:
        return _EXPORTS
    table: Dict[str, str] = {}
    for 条目 in _load_index(index_path):
        名称 = 条目.get('名称')
        导出 = 条目.get('导出')
        if not isinstance(名称, str) or not isinstance(导出, list):
            continue
        names = sorted(n for n in 导出 if isinstance(n, str) and n)
        if not names:
            continue
        table[名称] = 名称 if 名称 in names else names[0]
    if 默认索引:
        _EXPORTS = table
    return table


def candidate_from_hit(hit: Any, 层级: Optional[int] = None,
                       导出名: Optional[str] = None) -> Dict[str, Any]:
    """把 `ai.retrieval.Hit` 转成协议 `候选`。

    `层级` 不传则查 `level_table()`；查不到落 0（块不在索引里说明索引过期，
    这是 G12 门禁的事，不该在这里把整条请求打挂）。

    `导出名` 不传则查 `export_table()`；查不到退回 `hit.name`。这个降级
    **只有一处、就在这里**，且只在索引过期时才可能触发（索引里的块必带非空
    `导出`——`blocks._validate` 与 G13 全局唯一都在管）。v0.16.0 的错误做法
    是把同样的兜底写在 `extension.ts` 的客户端侧且**无条件生效**，于是目录名
    ≠导出名的块被静默拼错；W37 把兜底收归真源并绑定到「索引过期」这一个成因。
    """
    if 层级 is None:
        层级 = level_table().get(hit.name, 0)
    if 导出名 is None:
        导出名 = export_table().get(hit.name) or hit.name
    # v0.19.0 W69：命名空间从 `Hit` 直通候选。`getattr` 兜底是给「自造 hit-like
    # 对象」的调用方（测试/桥接脚本）留的活口，不是给真 `Hit` 的——它必有该字段。
    命名空间 = getattr(hit, 'namespace', '') or ''
    return make_candidate(
        名称=hit.name, 领域=hit.domain, 层级=层级, 导出名=导出名,
        描述=hit.description, 分数=hit.score,
        路径=getattr(hit, 'path', '') or '',
        # 内置块传 None 而不是空串：`候选` 字典里干脆不出现这个键，
        # 旧响应逐字节不变（三通道数字要能逐字比对，形状也一样）。
        命名空间=命名空间 or None,
    )


# ---- 校验器 -----------------------------------------------------------

def _check_keys(obj: Any, required, optional, 位置: str) -> List[str]:
    """字段存在性 + 未知字段检查。返回中文错误列表（空列表 = 通过）。"""
    if not isinstance(obj, dict):
        return ['%s 必须是对象，实际是 %s' % (位置, type(obj).__name__)]
    errs = []
    for 名 in required:
        if 名 not in obj:
            errs.append('%s 缺少必需字段「%s」' % (位置, 名))
    allowed = set(required) | set(optional)
    for 名 in sorted(obj):
        if 名 not in allowed:
            errs.append('%s 出现未知字段「%s」（协议只认 %s）'
                        % (位置, 名, '/'.join(sorted(allowed))))
    return errs


def _check_str(obj: dict, 名: str, 位置: str, errs: List[str]) -> None:
    if 名 in obj and not isinstance(obj[名], str):
        errs.append('%s.%s 必须是字符串' % (位置, 名))


#: `共享[].类型` 的合法取值集，进程级缓存。延迟加载理由与 `_load_index` 同源：
#: schema 是纯标准库层，不在 import 时反向依赖 `pkg.blocks`。
_SHARED_TYPE_VOCAB: Optional[frozenset] = None


def _shared_type_vocab() -> frozenset:
    """ADR-26 类型词表里**可作共享常量声明类型**的那部分。

    只收标量（数/字符串/布尔/函数/任意）与容器裸名（列表/字典/元组），**不收结构化
    类型对象**：`共享[].值` 是一个字面量串，声明它是 `列表<数>` 这种细化类型没有
    意义——粘合器拿到的仍然是那个串。要细化类型的是块的形参，不是共享常量。

    取不到词表（极端裁剪安装）时返回空集：那种情况下本校验退化为「只查是字符串」，
    宁可放行也不要把一条 `组` 请求打挂——真正的类型把关在 `glue.py`（W151）。
    """
    global _SHARED_TYPE_VOCAB
    if _SHARED_TYPE_VOCAB is not None:
        return _SHARED_TYPE_VOCAB
    try:
        from ..pkg.blocks import SCALAR_TYPES, CONTAINER_TYPE_NAMES
        _SHARED_TYPE_VOCAB = frozenset(SCALAR_TYPES) | frozenset(CONTAINER_TYPE_NAMES)
    except ImportError:
        _SHARED_TYPE_VOCAB = frozenset()
    return _SHARED_TYPE_VOCAB


def _check_shared_type(项: dict, 位置: str, errs: List[str]) -> None:
    """校验 `共享[].类型`（v0.27.0 W149 新增的可选键）。

    **非法值一律报错，不静默降级为 `任意`。** 静默降级正是本轮要修的病根：v0.26.0
    W145 实测，`共享` 常量被无条件当 `任意` 入池后，`任意` 在 `type_feeds` 双向放行，
    于是每个字符串常量对每个形参都「类型兼容」，粘合器按「最近产出优先」**静默错绑**
    （`读表(赵产量列)`），既不落 `?` 占位也不写拒绝理由，运行期才死。
    写错类型名却被当成 `任意` 放行，等于把同一个坑重挖一遍。
    """
    if '类型' not in 项:
        return                      # 不声明是合法的：旧方案一份都不用改
    值 = 项['类型']
    if not isinstance(值, str):
        return                      # 类型错误已由 _check_str 报过，不重复
    词表 = _shared_type_vocab()
    if 词表 and 值 not in 词表:
        errs.append('%s.类型 「%s」不在 ADR-26 类型词表里（允许：%s）；'
                    '写错的类型名不会被当作「任意」放行——那正是静默错绑的成因'
                    % (位置, 值, '/'.join(sorted(词表))))


def _check_candidate_fields(obj: dict, 位置: str, errs: List[str]) -> None:
    """候选的**逐字段**类型/取值检查（不含键集）。

    抽出来是给 `validate_candidate` 与 `validate_context_candidate` 共用：两者键集
    不同（后者多 `输入槽`/`输出类型`），字段口径必须完全一致——分成两份手写必然漂。
    """
    for 名 in ('名称', '领域', '导出名', '描述', '路径', '命名空间'):
        _check_str(obj, 名, 位置, errs)
    if '导出名' in obj and isinstance(obj['导出名'], str) and not obj['导出名']:
        errs.append('%s.导出名 不能是空串（插入的 `导入` 语句会缺调用名）' % 位置)
    if '层级' in obj and (isinstance(obj['层级'], bool)
                         or not isinstance(obj['层级'], int)):
        errs.append('%s.层级 必须是整数' % 位置)
    if '分数' in obj and not isinstance(obj['分数'], (int, float)):
        errs.append('%s.分数 必须是数字' % 位置)


def validate_candidate(obj: Any, 位置: str = '候选') -> List[str]:
    """校验一条 `候选`，返回中文错误列表。"""
    errs = _check_keys(obj, CANDIDATE_REQUIRED, CANDIDATE_OPTIONAL, 位置)
    if not isinstance(obj, dict):
        return errs
    _check_candidate_fields(obj, 位置, errs)
    return errs


def validate_plan(obj: Any, 位置: str = '方案') -> List[str]:
    """校验一份 `方案`（含每条 `步骤`），返回中文错误列表。"""
    errs = _check_keys(obj, PLAN_REQUIRED, PLAN_OPTIONAL, 位置)
    if not isinstance(obj, dict):
        return errs
    _check_str(obj, '需求', 位置, errs)
    步骤 = obj.get('步骤')
    if 步骤 is not None:
        if not isinstance(步骤, list) or not 步骤:
            errs.append('%s.步骤 必须是非空数组' % 位置)
        else:
            for i, 步 in enumerate(步骤, 1):
                where = '%s.步骤[%d]' % (位置, i)
                errs.extend(_check_keys(步, STEP_REQUIRED, STEP_OPTIONAL, where))
                if isinstance(步, dict):
                    for 名 in ('块', '领域', '导出名', '说明', '命名空间'):
                        _check_str(步, 名, where, errs)
                    if '参数' in 步 and not isinstance(步['参数'], list):
                        errs.append('%s.参数 必须是数组' % where)
    共享 = obj.get('共享')
    if 共享 is not None:
        if not isinstance(共享, list):
            errs.append('%s.共享 必须是数组' % 位置)
        else:
            for i, 项 in enumerate(共享, 1):
                where = '%s.共享[%d]' % (位置, i)
                errs.extend(_check_keys(项, ('名', '值'), ('类型',), where))
                if isinstance(项, dict):
                    for 名 in ('名', '值', '类型'):
                        _check_str(项, 名, where, errs)
                    _check_shared_type(项, where, errs)
    打印 = obj.get('打印')
    if 打印 is not None and not isinstance(打印, list):
        errs.append('%s.打印 必须是数组' % 位置)
    return errs


def validate_result(obj: Any, 位置: str = '执行结果') -> List[str]:
    """校验一份 `执行结果`，返回中文错误列表。"""
    errs = _check_keys(obj, RESULT_REQUIRED, RESULT_OPTIONAL, 位置)
    if not isinstance(obj, dict):
        return errs
    for 名 in ('stdout', 'stderr', '返回值', '错误'):
        _check_str(obj, 名, 位置, errs)
    if '耗时毫秒' in obj and (isinstance(obj['耗时毫秒'], bool)
                            or not isinstance(obj['耗时毫秒'], (int, float))):
        errs.append('%s.耗时毫秒 必须是数字' % 位置)
    诊断 = obj.get('诊断')
    if 诊断 is not None:
        if not isinstance(诊断, list):
            errs.append('%s.诊断 必须是数组' % 位置)
        else:
            for i, 条 in enumerate(诊断, 1):
                where = '%s.诊断[%d]' % (位置, i)
                errs.extend(_check_keys(条, DIAGNOSTIC_REQUIRED,
                                        DIAGNOSTIC_OPTIONAL, where))
                if isinstance(条, dict):
                    for 名 in ('级别', '消息', '代码'):
                        _check_str(条, 名, where, errs)
                    if isinstance(条.get('级别'), str) and 条['级别'] not in DIAGNOSTIC_LEVELS:
                        errs.append('%s.级别 「%s」不在白名单（允许：%s）'
                                    % (where, 条['级别'],
                                       '/'.join(sorted(DIAGNOSTIC_LEVELS))))
                    for 名 in ('行', '列'):
                        if 名 in 条 and (isinstance(条[名], bool)
                                       or not isinstance(条[名], int)):
                            errs.append('%s.%s 必须是整数（1-based 码点）'
                                        % (where, 名))
    return errs


def validate_select_envelope(obj: Any, 位置: str = '选响应') -> List[str]:
    """校验 `选` 的响应信封（含每条候选），返回中文错误列表。"""
    errs = _check_keys(obj, SELECT_ENVELOPE_REQUIRED,
                       SELECT_ENVELOPE_OPTIONAL, 位置)
    if not isinstance(obj, dict):
        return errs
    _check_str(obj, '需求', 位置, errs)
    _check_str(obj, '降级说明', 位置, errs)
    候选 = obj.get('候选')
    if 候选 is not None:
        if not isinstance(候选, list):
            errs.append('%s.候选 必须是数组' % 位置)
        else:
            for i, c in enumerate(候选, 1):
                errs.extend(validate_candidate(c, '%s.候选[%d]' % (位置, i)))
    return errs


def validate_run_envelope(obj: Any, 位置: str = '跑响应') -> List[str]:
    """校验 `跑` 的响应信封（含嵌套 `执行结果`），返回中文错误列表。"""
    errs = _check_keys(obj, RUN_ENVELOPE_REQUIRED, RUN_ENVELOPE_OPTIONAL, 位置)
    if not isinstance(obj, dict):
        return errs
    _check_str(obj, '需求', 位置, errs)
    _check_str(obj, '源码', 位置, errs)
    if '执行结果' in obj:
        errs.extend(validate_result(obj['执行结果'], '%s.执行结果' % 位置))
    return errs


def validate_context_candidate(obj: Any, 位置: str = '上下文候选') -> List[str]:
    """校验一条**规划上下文包候选**（候选 + `输入槽` + `输出类型`）。

    与 `validate_candidate` 共用 `_check_candidate_fields`，只在键集与两个新字段上
    分叉。`输入槽` 必须是数组（**可以是空数组**——零参数块合法），每项键集严格等于
    `SLOT_REQUIRED`。
    """
    errs = _check_keys(obj, CONTEXT_CANDIDATE_REQUIRED,
                       CONTEXT_CANDIDATE_OPTIONAL, 位置)
    if not isinstance(obj, dict):
        return errs
    _check_candidate_fields(obj, 位置, errs)
    _check_str(obj, '输出类型', 位置, errs)
    槽表 = obj.get('输入槽')
    if 槽表 is not None:
        if not isinstance(槽表, list):
            errs.append('%s.输入槽 必须是数组' % 位置)
        else:
            for i, 槽 in enumerate(槽表, 1):
                where = '%s.输入槽[%d]' % (位置, i)
                errs.extend(_check_keys(槽, SLOT_REQUIRED, (), where))
                if isinstance(槽, dict):
                    for 名 in SLOT_REQUIRED:
                        _check_str(槽, 名, where, errs)
                        if isinstance(槽.get(名), str) and not 槽[名]:
                            errs.append('%s.%s 不能是空串（LLM 靠它写实参）'
                                        % (where, 名))
    return errs


def validate_context_envelope(obj: Any, 位置: str = '规划上下文包') -> List[str]:
    """校验**规划上下文包**信封（含每条候选/语义命中/分歧告警）。

    `候选` 允许为空数组——`拒答建议.覆盖=False` 时本就该是空的；空候选不是错误，
    错误是「拒答建议说没覆盖却还塞了候选」这类自相矛盾，那一条在此**不查**：
    规划器自己保证一致性，校验器只管形状（把语义判断塞进 schema 层会让它反向依赖
    检索/语义层，违背本模块「纯标准库、不反向依赖上层」的约定）。
    """
    errs = _check_keys(obj, CONTEXT_ENVELOPE_REQUIRED,
                       CONTEXT_ENVELOPE_OPTIONAL, 位置)
    if not isinstance(obj, dict):
        return errs
    _check_str(obj, '需求', 位置, errs)

    候选 = obj.get('候选')
    if 候选 is not None:
        if not isinstance(候选, list):
            errs.append('%s.候选 必须是数组' % 位置)
        else:
            for i, c in enumerate(候选, 1):
                errs.extend(validate_context_candidate(
                    c, '%s.候选[%d]' % (位置, i)))

    命中 = obj.get('语义命中')
    if 命中 is not None:
        if not isinstance(命中, list):
            errs.append('%s.语义命中 必须是数组' % 位置)
        else:
            for i, h in enumerate(命中, 1):
                where = '%s.语义命中[%d]' % (位置, i)
                errs.extend(_check_keys(h, SEMANTIC_HIT_REQUIRED,
                                        SEMANTIC_HIT_OPTIONAL, where))
                if isinstance(h, dict):
                    for 名 in SEMANTIC_HIT_REQUIRED:
                        _check_str(h, 名, where, errs)

    告警 = obj.get('分歧告警')
    if 告警 is not None:
        if not isinstance(告警, list):
            errs.append('%s.分歧告警 必须是数组' % 位置)
        else:
            for i, w in enumerate(告警, 1):
                where = '%s.分歧告警[%d]' % (位置, i)
                errs.extend(_check_keys(w, DIVERGENCE_WARNING_REQUIRED, (), where))
                if isinstance(w, dict):
                    for 名 in ('分歧点', '实测差值'):
                        _check_str(w, 名, where, errs)
                    两侧 = w.get('两侧块名')
                    if 两侧 is not None and not isinstance(两侧, list):
                        errs.append('%s.两侧块名 必须是数组' % where)
                    if '须显式选一条' in w and not isinstance(w['须显式选一条'], bool):
                        errs.append('%s.须显式选一条 必须是布尔' % where)

    契约 = obj.get('回填契约')
    if 契约 is not None:
        where = '%s.回填契约' % 位置
        errs.extend(_check_keys(契约, FILL_CONTRACT_REQUIRED, (), where))
        if isinstance(契约, dict):
            _check_str(契约, '目标', where, errs)
            for 名 in ('必填', '禁止'):
                if 名 in 契约 and not isinstance(契约[名], list):
                    errs.append('%s.%s 必须是数组' % (where, 名))

    建议 = obj.get('拒答建议')
    if 建议 is not None:
        where = '%s.拒答建议' % 位置
        errs.extend(_check_keys(建议, REJECT_ADVICE_REQUIRED, (), where))
        if isinstance(建议, dict):
            _check_str(建议, '理由', where, errs)
            if '覆盖' in 建议 and not isinstance(建议['覆盖'], bool):
                errs.append('%s.覆盖 必须是布尔（不是分数，也不是字符串）' % where)
            if 建议.get('覆盖') is False and not (建议.get('理由') or '').strip():
                errs.append('%s.覆盖=False 时 理由 不能为空——判为库外能力必须说清'
                            '为什么，否则调用方无从复核' % where)
    return errs


def validate_filled_envelope(obj: Any, 位置: str = '回填响应') -> List[str]:
    """校验**回填响应**信封（含嵌套 `方案`）。

    这里只做**形状**校验。ADR-41 §4 那五条硬规则（实参必填且长度等于输入槽数、
    块名白名单、分歧点必须选一条…）要拿上下文包做参照，落在
    `tools/ai-bridge/planner.py` 的 `validate_filled`，不在本层——schema 层拿不到
    候选清单，也不该拿。
    """
    errs = _check_keys(obj, FILLED_ENVELOPE_REQUIRED,
                       FILLED_ENVELOPE_OPTIONAL, 位置)
    if not isinstance(obj, dict):
        return errs
    _check_str(obj, '需求', 位置, errs)
    _check_str(obj, '模型', 位置, errs)
    if isinstance(obj.get('模型'), str) and not obj['模型'].strip():
        errs.append('%s.模型 不能是空串（录像与溯源要靠它标明回填来源）' % 位置)
    if '方案' in obj:
        errs.extend(validate_plan(obj['方案'], '%s.方案' % 位置))
    溯源 = obj.get('溯源')
    if 溯源 is not None and not isinstance(溯源, dict):
        errs.append('%s.溯源 必须是对象' % 位置)
    return errs


def _ensure(errs: List[str], obj):
    if errs:
        raise SchemaError('；'.join(errs))
    return obj


def ensure_candidate(obj: Any, 位置: str = '候选'):
    """校验 `候选`，不通过抛 `SchemaError`；通过则原样返回。"""
    return _ensure(validate_candidate(obj, 位置), obj)


def ensure_plan(obj: Any, 位置: str = '方案'):
    """校验 `方案`，不通过抛 `SchemaError`；通过则原样返回。"""
    return _ensure(validate_plan(obj, 位置), obj)


def ensure_result(obj: Any, 位置: str = '执行结果'):
    """校验 `执行结果`，不通过抛 `SchemaError`；通过则原样返回。"""
    return _ensure(validate_result(obj, 位置), obj)


def ensure_select_envelope(obj: Any, 位置: str = '选响应'):
    """校验 `选` 的响应信封，不通过抛 `SchemaError`；通过则原样返回。"""
    return _ensure(validate_select_envelope(obj, 位置), obj)


def ensure_run_envelope(obj: Any, 位置: str = '跑响应'):
    """校验 `跑` 的响应信封，不通过抛 `SchemaError`；通过则原样返回。"""
    return _ensure(validate_run_envelope(obj, 位置), obj)


def ensure_context_envelope(obj: Any, 位置: str = '规划上下文包'):
    """校验**规划上下文包**，不通过抛 `SchemaError`；通过则原样返回。"""
    return _ensure(validate_context_envelope(obj, 位置), obj)


def ensure_filled_envelope(obj: Any, 位置: str = '回填响应'):
    """校验**回填响应**的形状，不通过抛 `SchemaError`；通过则原样返回。

    注意：这只是形状闸门。ADR-41 §4 的五条硬规则在 `planner.validate_filled`，
    过了本函数**不等于**这份回填可以拿去 `组`。
    """
    return _ensure(validate_filled_envelope(obj, 位置), obj)
