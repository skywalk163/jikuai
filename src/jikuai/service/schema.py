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
    'make_candidate', 'make_step', 'make_plan', 'make_result',
    'make_select_envelope', 'make_run_envelope',
    'make_saved_plan', 'make_saved_plan_summary', 'make_saved_plan_list',
    'candidate_from_hit', 'level_table', 'diagnostics_from_error',
    'validate_candidate', 'validate_plan', 'validate_result',
    'validate_select_envelope', 'validate_run_envelope',
    'ensure_candidate', 'ensure_plan', 'ensure_result',
    'ensure_select_envelope', 'ensure_run_envelope',
]


class SchemaError(ValueError):
    """协议校验失败。中文消息直接面向用户，可原样打到 stderr / HTTP body。"""


# ---- 字段清单（三通道唯一真源）---------------------------------------

#: `候选` 必需字段。`层级` 来自 `索引.json`，不是启发式猜的，见 `level_table`。
CANDIDATE_REQUIRED = ('名称', '领域', '层级', '描述', '分数', '路径')
#: `候选` 可选字段。`命名空间` 为 W22 第三方块预留（内置块为空串）。
CANDIDATE_OPTIONAL = ('命名空间',)

#: `方案` 字段。`步骤` 是方案的判据——有它才算方案。
PLAN_REQUIRED = ('步骤',)
PLAN_OPTIONAL = ('需求', '共享', '打印')

#: `方案.步骤[i]` 字段。`导出名` 是调用名，`块` 是导入用的目录名，两者可不同。
STEP_REQUIRED = ('块', '领域', '导出名')
STEP_OPTIONAL = ('参数', '说明')

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


# ---- 构造器 -----------------------------------------------------------

def make_candidate(名称: str, 领域: str, 层级: int, 描述: str,
                   分数: float, 路径: str = '',
                   命名空间: Optional[str] = None) -> Dict[str, Any]:
    """构造一条 `候选`。`分数` 统一保留 4 位小数——三通道数字要能逐字比对。"""
    候选 = {
        '名称': 名称,
        '领域': 领域,
        '层级': int(层级),
        '描述': 描述,
        '分数': round(float(分数), 4),
        '路径': 路径,
    }
    if 命名空间 is not None:
        候选['命名空间'] = 命名空间
    return 候选


def make_step(块: str, 领域: str, 导出名: str,
              参数: Optional[Sequence[str]] = None,
              说明: Optional[str] = None) -> Dict[str, Any]:
    """构造一条 `步骤`。`参数` 省略即交给粘合器 `--自动链式` 的类型图去推。"""
    步骤 = {'块': 块, '领域': 领域, '导出名': 导出名}
    if 参数 is not None:
        步骤['参数'] = list(参数)
    if 说明 is not None:
        步骤['说明'] = 说明
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


# ---- Hit → 候选 -------------------------------------------------------

_LEVELS: Optional[Dict[str, int]] = None


def level_table(index_path: Optional[str] = None) -> Dict[str, int]:
    """块名 → `层级` 映射，读自 `索引.json`。进程级缓存。

    `ai.retrieval.Hit` 只承载检索决策需要的字段，不带 `层级`；而协议要求
    候选带 `层级`。这里从索引取真实值，而不是给个默认值糊过去。
    """
    global _LEVELS
    默认索引 = index_path is None
    if 默认索引:
        if _LEVELS is not None:
            return _LEVELS
        from ..pkg.blocks import blocks_root
        index_path = os.path.join(blocks_root(), '索引.json')
    table: Dict[str, int] = {}
    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    for 条目 in (data.get('块') or []):
        名称 = 条目.get('名称')
        if isinstance(名称, str):
            try:
                table[名称] = int(条目.get('层级', 0))
            except (TypeError, ValueError):
                table[名称] = 0
    if 默认索引:
        _LEVELS = table
    return table


def candidate_from_hit(hit: Any, 层级: Optional[int] = None) -> Dict[str, Any]:
    """把 `ai.retrieval.Hit` 转成协议 `候选`。

    `层级` 不传则查 `level_table()`；查不到落 0（块不在索引里说明索引过期，
    这是 G12 门禁的事，不该在这里把整条请求打挂）。
    """
    if 层级 is None:
        层级 = level_table().get(hit.name, 0)
    return make_candidate(
        名称=hit.name, 领域=hit.domain, 层级=层级,
        描述=hit.description, 分数=hit.score,
        路径=getattr(hit, 'path', '') or '',
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


def validate_candidate(obj: Any, 位置: str = '候选') -> List[str]:
    """校验一条 `候选`，返回中文错误列表。"""
    errs = _check_keys(obj, CANDIDATE_REQUIRED, CANDIDATE_OPTIONAL, 位置)
    if not isinstance(obj, dict):
        return errs
    for 名 in ('名称', '领域', '描述', '路径', '命名空间'):
        _check_str(obj, 名, 位置, errs)
    if '层级' in obj and (isinstance(obj['层级'], bool)
                         or not isinstance(obj['层级'], int)):
        errs.append('%s.层级 必须是整数' % 位置)
    if '分数' in obj and not isinstance(obj['分数'], (int, float)):
        errs.append('%s.分数 必须是数字' % 位置)
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
                    for 名 in ('块', '领域', '导出名', '说明'):
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
                errs.extend(_check_keys(项, ('名', '值'), (), where))
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
