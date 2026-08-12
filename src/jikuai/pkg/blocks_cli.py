# -*- coding: utf-8 -*-
"""极快块生态 - 命令行子命令（v0.12.0 · ADR-15 §3.5 / v0.14.0 W9 三段式）。

命令表（中文主名 + 英文别名，风格对齐 `pkg/cli.py`）：

    jk 块 列表 [--领域 X] [--层级 N] [--稳定性 stable]   list
    jk 块 查找 <关键词>                                  search
    jk 块 选 <需求> [--top N] [--json] [--组] [--神经]  select/pick
    jk 块 组 <方案.json | ->                             synthesize/assemble
    jk 块 跑 <方案.json | ->                             run
    jk 块 详情 <块名>                                    show
    jk 块 校验 [块目录]                                  check
    jk 块 索引 [块目录]                                  index
    jk 块 新建 --领域 X --名 <块名> ...                  new/init
    jk 块 帮助                                           help

三段式设计语言：`[需求]→[候选]→[方案]→[源码]→[结果]`
    选：需求文本 → 候选 JSON
    组：方案 JSON → 极快源码
    跑：方案 JSON → 组 → 落临时 .jk → 执行 → 结果

设计取舍与 `pkg/cli.py` 完全对齐：这一层只做**参数解析 + 人类可读输出**，
所有业务逻辑都在 `blocks` 模块里。所有错误都收敛成返回码（0 成功，非 0
失败）+ stderr 中文提示，不向上抛裸异常。
"""

import json
import os
import sys
import tempfile
import time
from typing import List, Optional

from . import blocks
from .blocks import (
    ALLOWED_DOMAINS, BLOCK_INDEX_NAME, BLOCK_METADATA_NAME, STABILITY_LEVELS,
    BlockError, BlockMetadata,
)
from ..service import schema

__all__ = ['main', 'run']

_USAGE = f"""极快块生态 用法：
  jk 块 列表 [--领域 X] [--层级 N] [--稳定性 stable]
                                 列出所有块（可按领域/层级/稳定性过滤）
  jk 块 查找 <关键词>            在名称/描述/领域里做子串搜索
  jk 块 选 <需求> [--top N] [--json] [--组]
                                 按自然语言需求语义排序返回 top-K 块
                                 --组：选完直接过粘合器输出源码
                                 --神经：subprocess 拉 sidecar 生成查询向量走
                                         神经检索（失败自动降级启发式）
                                 --向量 <文件>：查询向量已算好，读文件直用
  jk 块 组 <方案.json | ->       方案 JSON → 极快源码（synthesize）
  jk 块 跑 <方案.json | ->       方案 JSON → 组 → 执行 → 结果（run）
  jk 块 详情 <块名>              显示某个块的完整元数据与示例
  jk 块 校验 [块目录]            校验一个块或全部块的合规性
                                 （元数据/主 .jk/导出/词法原子/依赖块 一致性）
  jk 块 索引 [块目录]            重新生成 {BLOCK_INDEX_NAME}
  jk 块 新建 --领域 X --名 <块名> [--导出 <名>] [--参 赵甲 赵乙]
            [--层级 N] [--稳定性 experimental] [--依赖 甲 乙]
                                 生成合规块脚手架三件套（{BLOCK_METADATA_NAME} /
                                 <块名>.jk / 测试.jk）；块名、导出名与每个形参名
                                 都先过词法原子性预检，不原子当场拒绝；
                                 --依赖 声明聚合的子块（叶名），自动接线 `导入`
  jk 块 帮助                     显示本帮助

三段式：选（需求→候选）→ 组（方案→源码）→ 跑（方案→结果）
  端到端：jk 块 选 "需求" --组 | jk 块 跑 -

英文别名：list(ls) / search(find) / select(pick) / synthesize(assemble) /
          run / show(info) / check(validate) / index / new(init) / help

领域白名单：{'/'.join(sorted(ALLOWED_DOMAINS))}
稳定性等级：{'/'.join(sorted(STABILITY_LEVELS))}
"""

#: 中文命令 -> 规范命令名；英文别名一并归一。风格对齐 pkg/cli.py。
_ALIASES = {
    '列表': 'list', 'list': 'list', 'ls': 'list',
    '查找': 'search', '搜索': 'search', 'search': 'search', 'find': 'search',
    '选': 'select', '选块': 'select', 'select': 'select', 'pick': 'select',
    '组': 'synthesize', '组装': 'synthesize', '粘合': 'synthesize',
    'synthesize': 'synthesize', 'assemble': 'synthesize',
    '跑': 'run', '执行': 'run', 'run': 'run',
    '详情': 'show', '显示': 'show', 'show': 'show', 'info': 'show',
    '校验': 'check', '检查': 'check', 'check': 'check', 'validate': 'check',
    '索引': 'index', 'index': 'index',
    '新建': 'new', '起块': 'new', 'new': 'new', 'init': 'new',
    '帮助': 'help', 'help': 'help', '-h': 'help', '--help': 'help',
}


def _err(msg: str, code: int = 1) -> int:
    """把错误收敛成 stderr 中文提示 + 返回码。默认 1（输入/参数错误）。"""
    print(f'块生态错误：{msg}', file=sys.stderr)
    return code


#: 退出码约定（三段共用，见 docs/CLI-三段式.md）：
#:   0  成功
#:   1  输入错误（参数/JSON/方案字段/块不存在）
#:   2  执行错误（`跑` 阶段：源码含占位符或解释器报错）
_EXIT_OK = 0
_EXIT_INPUT = 1
_EXIT_RUN = 2


def _reconfigure_utf8(stream) -> None:
    """把一条文本流切到 UTF-8，尽力而为不抛异常。

    Windows 默认 stdout 是 GBK，`✓`/`✗` 会 `UnicodeEncodeError`。这里用
    `getattr` 探测 `reconfigure`（Python 3.7+ 才有；pytest 的 capsys 流
    也没有），任何异常都吞掉——把控制台切成 UTF-8 是锦上添花，测试环境
    与旧解释器不能因此崩掉。
    """
    reconfigure = getattr(stream, 'reconfigure', None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding='utf-8')
    except Exception:
        pass



# ---- 参数辅助 ----------------------------------------------------------

def _take_option(args: List[str], names) -> Optional[str]:
    """从 args 里取出 `--名 值` 形式的参数，命中则从 args 原地删除。

    `names` 是一个可迭代的选项名集合（中英同名）。找不到返回 None。
    """
    i = 0
    while i < len(args):
        if args[i] in names:
            if i + 1 >= len(args):
                raise BlockError('%s 后面需要跟一个值' % args[i])
            value = args[i + 1]
            del args[i:i + 2]
            return value
        i += 1
    return None


def _load_all() -> List[BlockMetadata]:
    """扫描内置 `stdlib/blocks/` 下的全部块。任何字段错误都会抛 BlockError。"""
    return blocks.scan_blocks()


def _display_name(block_dir: str) -> str:
    """尽量拿到块名用于展示；元数据坏时退回目录名，保证 CLI 输出可读。"""
    try:
        return blocks.load_block_metadata(block_dir).name
    except BlockError:
        return os.path.basename(os.path.abspath(block_dir).rstrip(os.sep))


# ---- 三段式共用：候选 / 方案 / 结果 -------------------------------------
# 三通道 JSON 协议的**唯一真源**是 `jikuai.service.schema`（见
# `docs/协议-三通道.md`）；本文件只做参数解析与人读输出，构造 JSON 一律
# 走 `schema.make_*`，不再手写字段名字面量。
#
# 读取入参 / 拼中间态方案时也不写裸字符串，键名从 schema 常量解包出来——
# 用整元组解包而不是硬编码下标：协议真加了字段，这里会当场 ValueError，
# 而不是静默读错一个键。
_F需求, _F共享, _F打印 = schema.PLAN_OPTIONAL
_, _F候选 = schema.SELECT_ENVELOPE_REQUIRED
_F步骤 = schema.PLAN_REQUIRED[0]
_F块, _F领域, _F导出名 = schema.STEP_REQUIRED
_F参数, _F说明, _F命名空间 = schema.STEP_OPTIONAL
_F名称, _, _, _F候选导出名, _F描述, _, _ = schema.CANDIDATE_REQUIRED
_Fstdout, _Fstderr, _F返回值, _F耗时毫秒 = schema.RESULT_REQUIRED
_F错误, _F诊断 = schema.RESULT_OPTIONAL

#: `选` 的人读候选行：`  1. 求和（数据）  分数 0.1234`
import re as _re
_人读候选行 = _re.compile(r'^\s*\d+\.\s*(?P<名>[^（(]+)[（(](?P<域>[^）)]+)[）)]\s+分数')
_人读需求行 = _re.compile(r'^需求：(?P<需求>.*?)(?:\s{2,}\[|\s*$)')

#: 粘合器模块缓存（`tools/ai-bridge/glue.py` 不是包，按文件路径加载）。
_GLUE = None


def _repo_root() -> str:
    """仓库根目录（`src/jikuai/pkg` → 上三级），与 `blocks.blocks_root()` 同算法。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, '..', '..', '..'))


def _glue():
    """按文件路径加载 `tools/ai-bridge/glue.py`。

    `tools/ai-bridge` 不是包，也不该是——它是桥接工具而非运行时的一部分。
    比 `sys.path.insert` 干净：用 `importlib` 挂一个带命名空间前缀的模块名
    （`_jikuai_ai_bridge_glue`），既不污染 `sys.path`，也不会和别人的
    `import glue` 撞车。glue.py 自己会把 `src/` 插进 sys.path，那是它的事。
    """
    global _GLUE
    if _GLUE is not None:
        return _GLUE
    import importlib.util
    path = os.path.join(_repo_root(), 'tools', 'ai-bridge', 'glue.py')
    if not os.path.isfile(path):
        raise BlockError('找不到粘合器 %s；组/跑 需要仓库内的 tools/ai-bridge/' % path)
    spec = importlib.util.spec_from_file_location('_jikuai_ai_bridge_glue', path)
    if spec is None or spec.loader is None:      # 理论上不可达，保底不抛裸异常
        raise BlockError('粘合器 %s 无法作为模块加载' % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _GLUE = mod
    return mod


def _块目录(领域: str, 块: str, 命名空间: str = '') -> str:
    """块所在磁盘目录。

    - 内置块（`命名空间` 空串或未传）：`stdlib/blocks/<领域>/<块>/`
    - 第三方块（`命名空间` 非空）：遍历 `extra_roots()`，取第一个存在的
      `<根>/<命名空间>/<领域>/<块>/`；都不存在时返回**首选路径**（让上层
      `os.path.isdir` 汇报统一的「不存在」错误）
    """
    if not 命名空间:
        return os.path.join(blocks.blocks_root(), 领域, 块)
    候选 = None
    for 根 in blocks.extra_roots():
        p = os.path.join(根, 命名空间, 领域, 块)
        if 候选 is None:
            候选 = p
        if os.path.isdir(p):
            return p
    # 都不存在时给一个可诊断的路径（含命名空间）供错误消息使用
    return 候选 or os.path.join('<第三方块根>', 命名空间, 领域, 块)


def _主jk(块目录: str, 块: str) -> Optional[str]:
    """块的主 `.jk`：`<块>.jk` 优先，`main.jk` 兜底（对齐 module_loader 策略 2/3）。"""
    for 候选 in (块 + '.jk', 'main.jk'):
        p = os.path.join(块目录, 候选)
        if os.path.isfile(p):
            return p
    return None


def _推导出名(领域: str, 块: str, 命名空间: str = '') -> str:
    """从块的主 `.jk` 里提取导出名（协议.md：导入用目录名，调用用导出名）。

    多个导出时优先取与块同名的那个，否则取排序首位——要稳定可测。
    第三方块要传 `命名空间`，否则只会去 `stdlib/blocks/` 里找（查不到）。
    """
    目录 = _块目录(领域, 块, 命名空间)
    jk = _主jk(目录, 块)
    if jk is None:
        raise BlockError('块「%s」（领域 %s%s）没有主 .jk，无法确定导出名'
                         % (块, 领域,
                            '，命名空间 %s' % 命名空间 if 命名空间 else ''))
    names = sorted(blocks.extract_exports(jk))
    if not names:
        raise BlockError('块「%s」没有 `导出` 声明，无法调用' % 块)
    return 块 if 块 in names else names[0]


def _读文本(路径: str, 用途: str) -> str:
    """从文件或 stdin（`-`）读一段 UTF-8 文本。"""
    if 路径 == '-':
        try:
            data = sys.stdin.read()
        except (OSError, UnicodeDecodeError) as e:
            raise BlockError('从 stdin 读%s失败：%s' % (用途, e))
        if not data.strip():
            raise BlockError('stdin 没有内容；%s 需要 JSON（上一段忘了加 --json / --组？）' % 用途)
        return data
    try:
        with open(路径, 'r', encoding='utf-8') as f:
            return f.read()
    except OSError as e:
        raise BlockError('读不到%s文件 %s：%s' % (用途, 路径, e))
    except UnicodeDecodeError as e:
        raise BlockError('%s文件 %s 不是合法 UTF-8：%s' % (用途, 路径, e))


def _解析人读候选(text: str) -> Optional[dict]:
    """把 `选` 的**人读**输出解析回候选信封；解析不出返回 None。

    为什么要这一层：DoD 里的管道是 `jk 块 选 "…" | jk 块 组 -`，中间没有
    `--json`。宽容地认这份人读清单，管道才真的能一把接上；解析失败不猜，
    交给调用方报错。人读候选没带 `层级`/`分数`/`路径`——本层只捞名/域，
    真正的候选校验交给下游的 `_校验方案`。
    """
    需求 = None
    候选 = []
    for line in text.splitlines():
        m = _人读需求行.match(line)
        if m and 需求 is None:
            需求 = m.group('需求').strip()
            continue
        m = _人读候选行.match(line)
        if m:
            候选.append({_F名称: m.group('名').strip(),
                       _F领域: m.group('域').strip()})
    if not 候选:
        return None
    return {_F需求: 需求 or '', _F候选: 候选}


def _候选转方案(信封: dict, 步数: int) -> dict:
    """候选 → 方案：取前 `步数` 条候选，按顺序变成 `步骤`。

    候选没有参数信息，所以 `参数` 一律省略，交给 `--自动链式` 的类型图去推；
    推不出的由粘合器落 `?` 占位并写明「需人工填参」——不静默硬塞。

    只把 `需求`/`共享`/`打印` 这几个**方案字段**带过去：`候选`/`降级说明`
    是「选」阶段的产物，不是方案的一部分，留在里面会被
    `schema.ensure_plan`（glue 入口）当未知字段拒掉——协议禁止通道私自加字段。
    """
    候选 = 信封.get(_F候选)
    if not isinstance(候选, list) or not 候选:
        raise BlockError('输入既没有「步骤」也没有非空「候选」，无从组装')
    步骤 = []
    for h in 候选[:步数]:
        if not isinstance(h, dict) or not h.get(_F名称) or not h.get(_F领域):
            raise BlockError('候选项必须含「名称」与「领域」：%r' % (h,))
        步骤.append({
            _F块: h[_F名称],
            _F领域: h[_F领域],
            _F说明: h.get(_F描述) or ('候选 %s' % h[_F名称]),
        })
    return schema.make_plan(
        步骤,
        需求=信封.get(_F需求),
        共享=信封.get(_F共享),
        打印=信封.get(_F打印),
    )


def _校验方案(方案: dict) -> dict:
    """校验并就地补全方案：字段齐全 + 块真实存在 + 导出名可推导。

    返回补全后的方案（不改调用方的对象）。任何问题抛 `BlockError`，由
    `_cmd_*` 转成返回码 1 + 人读提示，不让裸异常栈冒到用户脸上。
    """
    if not isinstance(方案, dict):
        raise BlockError('方案必须是 JSON 对象（得到 %s）' % type(方案).__name__)
    步骤 = 方案.get(_F步骤)
    if 步骤 is None or 步骤 == []:
        raise BlockError('方案缺少非空的「步骤」字段（schema 见 docs/协议-三通道.md）')
    if not isinstance(步骤, list):
        raise BlockError('「步骤」必须是数组（得到 %s）' % type(步骤).__name__)

    新步骤 = []
    for i, s in enumerate(步骤, 1):
        if not isinstance(s, dict):
            raise BlockError('步骤 %d 必须是 JSON 对象（得到 %s）' % (i, type(s).__name__))
        s = dict(s)
        for 字段 in (_F块, _F领域):
            if not s.get(字段):
                raise BlockError('步骤 %d 缺少必填字段「%s」' % (i, 字段))
        块, 领域 = s[_F块], s[_F领域]
        命名空间 = s.get(_F命名空间) or ''
        if 领域 not in ALLOWED_DOMAINS:
            raise BlockError('步骤 %d 的领域 %r 不在白名单（允许：%s）'
                             % (i, 领域, '/'.join(sorted(ALLOWED_DOMAINS))))
        if not os.path.isdir(_块目录(领域, 块, 命名空间)):
            raise BlockError('步骤 %d 的块「%s」不存在：%s 下没有这个目录'
                             '（`jk 块 查找 %s` 看看真名）'
                             % (i, 块,
                                os.path.join('blocks',
                                             *([命名空间] if 命名空间 else []),
                                             领域),
                                块))
        if not s.get(_F导出名):
            s[_F导出名] = _推导出名(领域, 块, 命名空间)
        新步骤.append(s)

    新方案 = dict(方案)
    新方案[_F步骤] = 新步骤
    return 新方案


def _读方案(路径: str, 步数: int) -> dict:
    """读一份「方案」：JSON 方案 / JSON 候选信封 / `选` 的人读候选清单三收。

    返回**已校验补全**的方案 dict。
    """
    text = _读文本(路径, '方案')
    stripped = text.lstrip()
    if stripped.startswith('{') or stripped.startswith('['):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise BlockError('方案不是合法 JSON：%s（第 %d 行第 %d 列）'
                             % (e.msg, e.lineno, e.colno))
    else:
        data = _解析人读候选(text)
        if data is None:
            raise BlockError('输入既不是 JSON 方案，也不是「选」的候选清单；'
                             '试试 `jk 块 选 "需求" --json`')
    if isinstance(data, dict) and data.get(_F步骤) is None and data.get(_F候选):
        data = _候选转方案(data, 步数)
    return _校验方案(data)


def _组装(方案: dict, 自动链式: bool = True) -> str:
    """方案 → 极快源码。参数校验失败一律转 `BlockError`。"""
    glue = _glue()
    try:
        return glue.synthesize(方案, 自动链式=自动链式)
    except ValueError as e:
        raise BlockError('粘合失败：%s' % e)


#: 粘合器给「参数没填上」留的记号——`跑` 见到它就拒绝执行（见 glue.synthesize）。
_占位记号 = '需人工填参'


def _执行源码(源码: str):
    """把源码落**临时** `.jk` 再交给解释器跑，返回 `schema.make_result`。

    为什么落临时文件而不直接 `run_source(源码)`：模块解析要靠 `current_file`
    定位搜索路径（`module_loader._search_paths`），给个真实路径最省事，也让
    报错信息里的文件名可点。临时文件在 finally 里删掉，工作区不留垃圾。

    stdout 与 stderr **同时**拦下（`redirect_stdout`/`redirect_stderr`）：诊断
    是打到 stderr 的，协议要求 `执行结果.stderr` 把它带上，人读模式再把两条流
    原样吐回真终端。异常收敛成 `错误='<类名>：<消息>'`（不带 traceback），带
    位置信息时顺手填 `诊断`。`耗时毫秒` 用 `time.perf_counter`。
    """
    import io
    from contextlib import redirect_stdout, redirect_stderr
    from ..main import run_source

    fd, path = tempfile.mkstemp(prefix='jk_块跑_', suffix='.jk')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(源码)
        缓出, 缓错 = io.StringIO(), io.StringIO()
        错误 = None
        诊断 = None
        结果 = None
        起 = time.perf_counter()
        try:
            with redirect_stdout(缓出), redirect_stderr(缓错):
                结果 = run_source(源码, file=path)
        except Exception as e:                           # noqa: BLE001
            错误 = '%s：%s' % (type(e).__name__, e)
            诊断 = schema.diagnostics_from_error(e)
        耗时 = (time.perf_counter() - 起) * 1000.0
        return schema.make_result(
            stdout=缓出.getvalue(), stderr=缓错.getvalue(),
            返回值='' if 错误 else repr(结果),
            耗时毫秒=耗时, 错误=错误, 诊断=诊断,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---- 各子命令 ----------------------------------------------------------


def _cmd_list(args: List[str]) -> int:
    args = list(args)
    try:
        domain = _take_option(args, ('--领域', '--domain'))
        level_raw = _take_option(args, ('--层级', '--level'))
        stability = _take_option(args, ('--稳定性', '--stability'))
        metas = _load_all()
    except BlockError as e:
        return _err(str(e))

    if level_raw is not None:
        try:
            level = int(level_raw)
        except ValueError:
            return _err('--层级 需要一个整数，得到 %r' % level_raw)
    else:
        level = None

    if domain is not None and domain not in ALLOWED_DOMAINS:
        return _err('未知领域 %r（允许：%s）'
                    % (domain, '/'.join(sorted(ALLOWED_DOMAINS))))
    if stability is not None and stability not in STABILITY_LEVELS:
        return _err('未知稳定性 %r（允许：%s）'
                    % (stability, '/'.join(sorted(STABILITY_LEVELS))))

    def keep(m: BlockMetadata) -> bool:
        if domain is not None and domain not in m.domains:
            return False
        if level is not None and m.level != level:
            return False
        if stability is not None and m.stability != stability:
            return False
        return True

    kept = [m for m in metas if keep(m)]
    if not kept:
        print('没有匹配的块')
        return 0

    print('共 %d 个块：' % len(kept))
    for m in kept:
        domains = '/'.join(m.domains)
        # 格式：  [L0] 求和  [数据]  stable   对数值列表求和，返回总和
        print('  [L%d] %s  [%s]  %s   %s'
              % (m.level, m.name, domains, m.stability, m.description))
    return 0


def _cmd_search(args: List[str]) -> int:
    if not args:
        return _err('用法：jk 块 查找 <关键词>')
    keyword = args[0]
    try:
        metas = _load_all()
    except BlockError as e:
        return _err(str(e))

    lower = keyword.lower()

    def hit(m: BlockMetadata) -> bool:
        # 名称/描述/领域 三个字段做子串匹配；ASCII 走大小写不敏感，
        # 中文字段直接子串——中文没有 case 概念。
        if lower in m.name.lower() or keyword in m.name:
            return True
        if lower in m.description.lower() or keyword in m.description:
            return True
        for d in m.domains:
            if lower in d.lower() or keyword in d:
                return True
        return False

    matched = [m for m in metas if hit(m)]
    if not matched:
        print('没有匹配 %r 的块' % keyword)
        return 0
    print('找到 %d 个块：' % len(matched))
    for m in matched:
        domains = '/'.join(m.domains)
        print('  [L%d] %s  [%s]  %s   %s'
              % (m.level, m.name, domains, m.stability, m.description))
    return 0


def _cmd_show(args: List[str]) -> int:
    if not args:
        return _err('用法：jk 块 详情 <块名>')
    target = args[0]
    try:
        metas = _load_all()
    except BlockError as e:
        return _err(str(e))

    hit = next((m for m in metas if m.name == target), None)
    if hit is None:
        return _err('找不到块 %r' % target)

    print('%s@%s' % (hit.name, hit.version))
    print('  领域：%s' % '/'.join(hit.domains))
    print('  层级：L%d' % hit.level)
    print('  稳定性：%s' % hit.stability)
    print('  描述：%s' % hit.description)
    if hit.inputs:
        print('  输入：')
        for item in hit.inputs:
            print('    - %s：%s' % (item.get('名', ''), item.get('类型', '')))
    else:
        print('  输入：（无）')
    if hit.output:
        print('  输出：%s' % hit.output.get('类型', ''))
    else:
        print('  输出：（无）')
    if hit.dep_blocks:
        print('  依赖块：%s' % '、'.join(hit.dep_blocks))
    else:
        print('  依赖块：（无）')
    if hit.jikuai_requirement:
        print('  极快版本：%s' % hit.jikuai_requirement)
    if hit.example:
        print('  示例：')
        for line in hit.example.split('\n'):
            print('    %s' % line)
    return 0


def _cmd_check(args: List[str]) -> int:
    # 单目录模式：`jk 块 校验 <目录>`；否则校验全部
    if args:
        targets = [args[0]]
    else:
        try:
            targets = [m.root for m in _load_all()]
        except BlockError as e:
            return _err(str(e))
        if not targets:
            print('没有可校验的块')
            return 0

    had_error = False
    for block_dir in targets:
        errors, warnings = blocks.validate_block(block_dir)
        name = _display_name(block_dir)
        if errors:
            had_error = True
            for msg in errors:
                print('✗ %s：%s' % (name, msg))
        else:
            print('✓ %s' % name)
        for w in warnings:
            print('  ⚠ %s' % w)
    return 1 if had_error else 0


def _cmd_index(args: List[str]) -> int:
    root = args[0] if args else None
    try:
        index = blocks.generate_index(root)
        path = blocks.save_index(index, blocks.index_path(root))
    except BlockError as e:
        return _err(str(e))
    print('已生成索引 %s' % path)
    print('  版本：%s' % index['版本'])
    print('  块数：%d' % len(index['块']))
    return 0


def _cmd_select(args: List[str]) -> int:
    """`jk 块 选 <需求>` —— 按自然语言需求语义排序返回 top-K 块。

    与 `查找` 的区别：`查找` 是子串命中（要求用户已经知道块名里的字），`选`
    走 `jikuai.ai.retrieval` 的 TF-IDF + 同义词 + 领域先验，吃的是「我想干什么」
    这种口语需求。

    神经路径有两个入口，都遵守「运行时不做模型推理」（ADR-25 §2 零依赖）：

    * `--向量 <文件>`：查询向量由调用方预先算好，读文件即用。
    * `--神经`：subprocess 拉一次 sidecar（`tools/ai-bridge/embed_query.py`，
      可由 `JIKUAI_AI_EMBED_CMD` 覆盖）自动生成向量。torch 只活在子进程里。

    **默认走启发式**——不给这两个开关就一个子进程都不起，保零依赖体验。
    `--神经` 的任何失败都降级到启发式 + 一行 stderr 提示，返回码仍是 0：
    降级不是失败。输出里的 `[神经]`/`[启发式]` 标签说明实际走了哪条。
    """
    需求 = None
    top = 5
    as_json = False
    as_组 = False
    向量文件 = None
    用神经 = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--top':
            if i + 1 >= len(args):
                return _err('--top 缺少数值')
            try:
                top = int(args[i + 1])
            except ValueError:
                return _err(f'--top 需要整数，得到 {args[i + 1]!r}')
            if top <= 0:
                return _err('--top 必须是正整数')
            i += 2
        elif a == '--json':
            as_json = True
            i += 1
        elif a in ('--组', '--synthesize', '--assemble'):
            as_组 = True
            i += 1
        elif a in ('--向量', '--vector'):
            if i + 1 >= len(args):
                return _err('--向量 缺少文件路径')
            向量文件 = args[i + 1]
            i += 2
        elif a in ('--神经', '--neural'):
            用神经 = True
            i += 1
        elif a.startswith('--'):
            return _err(f'未知选项：{a}')
        else:
            if 需求 is not None:
                return _err('需求只能给一条（含空格请用引号括起来）')
            需求 = a
            i += 1

    if not 需求:
        return _err('缺少需求文本。用法：jk 块 选 <需求> [--top N] [--json]')

    查询向量 = None
    降级说明 = None
    if 向量文件:
        try:
            with open(向量文件, 'r', encoding='utf-8') as f:
                查询向量 = json.load(f)
        except OSError as e:
            return _err(f'读不到向量文件 {向量文件}：{e}')
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return _err(f'向量文件不是合法 UTF-8 JSON：{e}')
        if not isinstance(查询向量, list) or not 查询向量:
            return _err('向量文件必须是非空 JSON 数组')
        if 用神经:
            # 显式文件胜过自动拉取——用户既然给了文件，就是想吃这个向量，
            # 不该被 sidecar 拉出来的（可能维度都对不上）覆盖掉。
            print('提示：`--向量` 与 `--神经` 同时给出，采用 `--向量` 文件；'
                  '`--神经` 忽略。', file=sys.stderr)

    if 用神经 and 查询向量 is None:
        # 神经路径：subprocess 拉一次 sidecar；任何失败都降级到启发式 +
        # stderr 提示，返回码保持 0。ADR-25 §3.1「分层兜底」的运行时实现。
        from ..ai import embed_client
        expected = embed_client.index_dim()
        vec, why = embed_client.fetch_query_vector(需求, expected_dim=expected)
        if vec is None:
            # 降级原因既打 stderr（保留旧交互）也进 JSON `降级说明`（协议字段）。
            # 文案前缀走 `embed_client.DEGRADE_PREFIX` 常量，三处（CLI/Web/REPL）同源。
            from ..ai.embed_client import DEGRADE_PREFIX
            降级说明 = DEGRADE_PREFIX + why
            print(降级说明, file=sys.stderr)
        else:
            查询向量 = vec

    from ..ai import retrieval
    try:
        hits = retrieval.retrieve(需求, top=top, query_vector=查询向量)
    except retrieval.RetrievalError as e:
        # 维度不符是「模型换了没重生成索引」的编程错误，不该静默；但既然走到
        # 这里说明是 `--向量` 显式路径（`--神经` 分支上面已经比对过 dim 了），
        # 报错退出比降级更符合「显式优先」的语义。
        return _err(str(e))

    if as_json:
        信封 = schema.make_select_envelope(
            需求, [schema.candidate_from_hit(h) for h in hits],
            降级说明=降级说明,
        )
        print(json.dumps(信封, ensure_ascii=False, indent=2))
        return 0

    if as_组:
        # --组：选完直接过 校验（补 导出名）+ glue.synthesize 出源码
        try:
            方案 = _校验方案({
                _F需求: 需求,
                _F步骤: [{_F块: h.name, _F领域: h.domain,
                        _F说明: h.description,
                        **({_F命名空间: h.namespace} if h.namespace else {})}
                        for h in hits],
            })
            源码 = _glue().synthesize(方案, 自动链式=True)
        except (BlockError, ValueError) as e:
            # glue.synthesize 的协议校验失败抛的是 ValueError（不是 BlockError），
            # 只捕 BlockError 会让裸异常栈冒到用户脸上。
            return _err(str(e))
        sys.stdout.write(源码)
        return 0

    if not hits:
        print(f'没有匹配「{需求}」的块。')
        return 0
    print(f'需求：{需求}    {hits[0].path}')
    for i, h in enumerate(hits, 1):
        print(f'  {i}. {h.name}（{h.domain}）  分数 {h.score:.4f}')
        print(f'     {h.description}')
    return 0


def _cmd_synthesize(args: List[str]) -> int:
    """`jk 块 组 <方案.json | ->` -- 方案 JSON -> 极快源码。stdout 出源码。"""
    路径 = None
    top = 5
    自动链式 = True
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--top':
            if i + 1 >= len(args):
                return _err('--top 缺少数值')
            try:
                top = int(args[i + 1])
            except ValueError:
                return _err(f'--top 需要整数，得到 {args[i + 1]!r}')
            if top <= 0:
                return _err('--top 必须是正整数')
            i += 2
        elif a in ('--无自动链式', '--no-auto'):
            自动链式 = False
            i += 1
        elif a.startswith('--'):
            return _err(f'未知选项：{a}')
        else:
            if 路径 is not None:
                return _err('组 只接一个方案输入（文件路径或 -）')
            路径 = a
            i += 1
    if 路径 is None:
        return _err('用法：jk 块 组 <方案.json | ->')
    try:
        源码 = _组装(_读方案(路径, top), 自动链式=自动链式)
    except BlockError as e:
        return _err(str(e))
    sys.stdout.write(源码)
    return 0


def _cmd_run(args: List[str]) -> int:
    """`jk 块 跑 <方案.json | ->` -- 方案 -> 组 -> 临时 .jk -> 执行 -> 结果。

    退出码：0 成功 / 1 输入错误 / 2 执行期报错。
    """
    路径 = None
    top = 5
    自动链式 = True
    as_json = False
    show_src = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--top':
            if i + 1 >= len(args):
                return _err('--top 缺少数值')
            try:
                top = int(args[i + 1])
            except ValueError:
                return _err(f'--top 需要整数，得到 {args[i + 1]!r}')
            if top <= 0:
                return _err('--top 必须是正整数')
            i += 2
        elif a in ('--无自动链式', '--no-auto'):
            自动链式 = False
            i += 1
        elif a == '--json':
            as_json = True
            i += 1
        elif a in ('--看源码', '--show-source'):
            show_src = True
            i += 1
        elif a.startswith('--'):
            return _err(f'未知选项：{a}')
        else:
            if 路径 is not None:
                return _err('跑 只接一个方案输入（文件路径或 -）')
            路径 = a
            i += 1
    if 路径 is None:
        return _err('用法：jk 块 跑 <方案.json | ->')
    try:
        方案 = _读方案(路径, top)
        源码 = _组装(方案, 自动链式=自动链式)
    except BlockError as e:
        return _err(str(e))
    if show_src:
        sys.stderr.write(源码 if 源码.endswith('\n') else 源码 + '\n')
        sys.stderr.write('----\n')
    占位提示 = ('方案有步骤的参数未指定（源码里出现「%s」）；'
              '在「步骤」里补 `参数`，或换成类型图能自动链上的组合' % _占位记号)
    if _占位记号 in 源码:
        if as_json:
            执行结果 = schema.make_result(错误=占位提示)
            信封 = schema.make_run_envelope(源码, 执行结果, 需求=方案.get(_F需求))
            print(json.dumps(信封, ensure_ascii=False, indent=2))
            return _EXIT_INPUT
        return _err(占位提示)
    执行结果 = _执行源码(源码)
    if as_json:
        信封 = schema.make_run_envelope(源码, 执行结果, 需求=方案.get(_F需求))
        print(json.dumps(信封, ensure_ascii=False, indent=2))
    else:
        # 人读模式：stdout 直出、stderr 转发到真 stderr（诊断也走这条）；
        # 有 `错误` 时再补一行「执行错误：…」，等价于旧行为里裸异常打的那条。
        sys.stdout.write(执行结果.get(_Fstdout, ''))
        sys.stderr.write(执行结果.get(_Fstderr, ''))
        错误 = 执行结果.get(_F错误)
        if 错误:
            print('执行错误：%s' % 错误, file=sys.stderr)
    return _EXIT_RUN if 执行结果.get(_F错误) else _EXIT_OK


#: 脚手架产出的初始版本号。块生态里 `0.1.0` 是「刚起、还没人用」的约定起点。
_新块版本 = '0.1.0'

#: 描述字段的占位。刻意留成刺眼的 TODO：`_validate` 只要求「非空字符串」，
#: 描述空着会静默过校验，但检索侧（TF-IDF / 向量）吃的就是这段文字——留白
#: 等于把块从 `jk 块 选` 里删掉。
_新块描述占位 = 'TODO: 填写描述'

#: 未标注类型时的兜底。`任意` 在 SCALAR_TYPES 里，能过 `_validate`，但会被
#: G14（`check_type_annotation`）留下精度问题——脚手架故意不猜类型，让 W23
#: 的门禁把「该细化了」这件事顶到贡献者脸上，而不是替他编一个假类型。
_新块类型占位 = '任意'


def _原子性拒绝(角色: str, 名: str, pieces, 补充: str) -> int:
    """把一次词法原子性失败翻译成人读理由 + 返回码 1。

    `pieces` 是 `check_*_atomicity` 的第二个返回值 `[(词形, 文本), ...]`。
    报错里**必须**带切分结果——只说「不原子」用户不知道是哪个字招的祸，
    带上 `赵(IDENT)+次(KEYWORD)` 他立刻看懂是 `次` 撞了关键字。
    格式与 `blocks.validate_block` 的报错保持一致，两处输出可互相对照。
    """
    frag = '+'.join('%s(%s)' % (v, t) for t, v in pieces)
    return _err('%s「%s」非词法原子，切分为 %s；%s' % (角色, 名, frag, 补充))


def _取形参(args: List[str]) -> List[str]:
    """从 args 里就地取出 `--参 赵甲 赵乙 ...` 的变长形参列表。

    与 `_take_option` 的区别：它只吃一个值，`--参` 要吃到下一个 `--` 选项
    （或参数结束）为止。可以给多次，结果按出现顺序拼接。
    """
    形参: List[str] = []
    i = 0
    while i < len(args):
        if args[i] in ('--参', '--params'):
            j = i + 1
            while j < len(args) and not args[j].startswith('--'):
                形参.append(args[j])
                j += 1
            if j == i + 1:
                raise BlockError('%s 后面至少要跟一个形参名（如 --参 赵文 赵数）'
                                 % args[i])
            del args[i:j]
        else:
            i += 1
    return 形参


def _取依赖(args: List[str]) -> List[str]:
    """从 args 里就地取出 `--依赖 甲 乙 ...` 的变长依赖块名列表（W30）。

    形态与 `--参` 完全一致（吃到下一个 `--` 选项或参数结束为止，可给多次），
    刻意不复用 `_取形参` 的实现——两者的**校验口径不同**：形参走
    `check_export_atomicity`（单 IDENT），依赖块名走
    `check_module_segment_atomicity`（单 token 即可，`求和` 这类 VERB 合法，
    因为它是点分路径段）。合成一个函数只会让调用方多传一个开关。

    值是块的**叶名**（`块.json` 的 `名称`），不是点分全名——与 ADR-28 §3.4
    和 G11 的既有对账口径一致。
    """
    依赖: List[str] = []
    i = 0
    while i < len(args):
        if args[i] in ('--依赖', '--deps'):
            j = i + 1
            while j < len(args) and not args[j].startswith('--'):
                依赖.append(args[j])
                j += 1
            if j == i + 1:
                raise BlockError('%s 后面至少要跟一个块名（如 --依赖 工资条 日序）'
                                 % args[i])
            del args[i:j]
        else:
            i += 1
    return 依赖


def _cmd_new(args: List[str]) -> int:
    """`jk 块 新建` —— 一步生成合规块脚手架三件套（v0.15.0 W21 / W30 补 `--依赖`）。

    产出（都落 `stdlib/blocks/<领域>/<块名>/`，UTF-8 + LF）：

        块.json      名称/版本/层级/领域/描述/[输入]/输出/导出/[依赖块]/稳定性
        <块名>.jk    `[从 blocks.X.Y 导入 Z。]` + `函数 <导出名> [接收 …]` 骨架
        测试.jk      `从 blocks.<领域>.<块名> 导入 <导出名>` 的最小冒烟


    `.py` 背衬不生成——需要混合模块（ADR-16 §3.3）的块是少数，多写一个空
    壳只会让 `jk 块 校验` 与人都要多看一个没内容的文件。

    **两套原子性标准，刻意分开**（ADR-15 §3.7 / 交接文档坑 #4、#5）：

    * 块名（= 目录名 = 点分路径段）走 `check_module_segment_atomicity`：
      单个 token 即可，词形不限。所以 `求和`（单 VERB）是合法块名。
    * 导出名与每个形参名走 `check_export_atomicity`：必须是单个 **IDENT**。
      调用方分词时不知道被导入模块的导出名，非 IDENT 名一律被切碎。

    默认导出名 = 块名，于是 `--名 求和`（不给 `--导出`）会在导出名这一关被拒，
    提示去取一个非动词的导出名——这正是「目录名与导出名分离」的既有惯例。

    退出码：0 成功 / 1 参数或预检失败（不产生半个块——所有校验都在建目录前）。
    """
    args = list(args)
    try:
        领域 = _take_option(args, ('--领域', '--domain'))
        块名 = _take_option(args, ('--名', '--name'))
        导出名 = _take_option(args, ('--导出', '--export'))
        层级原文 = _take_option(args, ('--层级', '--level'))
        稳定性 = _take_option(args, ('--稳定性', '--stability'))
        形参 = _取形参(args)
        依赖块 = _取依赖(args)
    except BlockError as e:
        return _err(str(e))

    # 选项全部摘完后还有残留 → 拼错了选项名，早报比生成一个错块好
    if args:
        return _err('无法识别的参数：%s（用法见 `jk 块 帮助`）' % ' '.join(args))

    if not 领域:
        return _err('缺少 --领域。用法：jk 块 新建 --领域 X --名 <块名> '
                    '[--导出 <名>] [--参 赵甲 赵乙]')
    if not 块名:
        return _err('缺少 --名。用法：jk 块 新建 --领域 X --名 <块名> '
                    '[--导出 <名>] [--参 赵甲 赵乙]')
    if 领域 not in ALLOWED_DOMAINS:
        return _err('未知领域 %r（允许：%s）；新增领域要走 ADR-15 §3.6 注册流程'
                    % (领域, '/'.join(sorted(ALLOWED_DOMAINS))))

    if 层级原文 is None:
        层级 = 0
    else:
        try:
            层级 = int(层级原文)
        except ValueError:
            return _err('--层级 需要一个非负整数，得到 %r' % 层级原文)
        if 层级 < 0:
            return _err('--层级 必须是非负整数，得到 %d' % 层级)

    if 稳定性 is None:
        稳定性 = blocks.DEFAULT_STABILITY
    elif 稳定性 not in STABILITY_LEVELS:
        return _err('未知稳定性 %r（允许：%s）'
                    % (稳定性, '/'.join(sorted(STABILITY_LEVELS))))

    # ---- 预检 1：命名（词法原子性）------------------------------------
    # 全部在落盘之前跑完。一条不过就整体退出，工作区一个字节都不动。
    段原子, 段切分 = blocks.check_module_segment_atomicity(块名)
    if not 段原子:
        return _原子性拒绝(
            '块名', 块名, 段切分,
            '它要作为 `从 blocks.%s.%s 导入 …` 的点分路径段，'
            '被切成多个 token 就会 ParseError' % (领域, 块名))

    if 导出名 is None:
        导出名 = 块名
        默认导出 = True
    else:
        默认导出 = False
    出原子, 出切分 = blocks.check_export_atomicity(导出名)
    if not 出原子:
        补充 = ('调用方分词时不知道本块的导出名，非单 IDENT 名必然被切碎；'
              '建议改用 汇总/合计/聚合/整合 这类词')
        if 默认导出:
            补充 = ('默认导出名取的是块名。' + 补充
                  + '——用 `--导出 <名>` 单独指定一个原子导出名即可'
                    '（目录名与导出名分离是既有惯例）')
        return _原子性拒绝('导出名', 导出名, 出切分, 补充)

    for p in 形参:
        参原子, 参切分 = blocks.check_export_atomicity(p)
        if not 参原子:
            return _原子性拒绝(
                '形参名', p, 参切分,
                '变量名首字须是百家姓姓氏且整体是单个 IDENT；'
                '`赵列表`/`赵次` 这类会被内建动词或关键字切碎，'
                '改用 `赵数值`/`赵项表` 这种')

    # 依赖块名走**目录名**那套（单 token 即可）——它是 `blocks.X.<依赖>` 的
    # 点分路径段，不是要在本模块里当变量用的标识符。所以 `求和` 作依赖名合法。
    for d in 依赖块:
        依原子, 依切分 = blocks.check_module_segment_atomicity(d)
        if not 依原子:
            return _原子性拒绝(
                '依赖块名', d, 依切分,
                '它要作为 `从 blocks.<领域>.%s 导入 …` 的点分路径段' % d)
    if len(set(依赖块)) != len(依赖块):
        return _err('--依赖 有重复项：%s（`依赖块` 是集合语义，写两遍不会多算一次'
                    '聚合依赖，见 ADR-28 _tally_deps）' % ' '.join(依赖块))

    # ---- 预检 2：不撞车（目录 / 块名 / 导出名）-------------------------
    目录 = _块目录(领域, 块名)
    if os.path.exists(目录):
        return _err('目标已存在，不覆盖：%s（换个块名，或先自己删掉）' % 目录)

    try:
        既有 = _load_all()
    except BlockError as e:
        # 现有块库本身坏了不该阻断新建——但要说清楚唯一性预检因此没跑全。
        print('提示：扫描现有块失败（%s），块名/导出名唯一性预检跳过；'
              '生成后请务必跑 `jk 块 索引` + `jk 块 校验`。' % e, file=sys.stderr)
        既有 = []
    for m in 既有:
        if m.name == 块名:
            return _err('已有同名块「%s」：%s（块名全局唯一，见 scan_blocks）'
                        % (块名, m.root))

    # ---- 预检 3：依赖块解析（W30）--------------------------------------
    # `依赖块` 装叶名（ADR-28 §3.4），所以在写盘前必须能把每个叶名解析成
    # 「哪个领域 + 导出什么」，否则生成的 `从 blocks.?.X 导入 ?` 是废话。
    # 解析不了就整体拒——G11 会拿 `依赖块` 和 `.jk` 的 `导入` 对账，
    # 让脚手架产一个注定挂门禁的块，比当场报错糟得多。
    依赖表 = []
    for d in 依赖块:
        命中 = [m for m in 既有 if m.name == d]
        if not 命中:
            return _err('依赖块「%s」在块库里找不到（`依赖块` 装的是叶名，'
                        '不是点分全名；跑 `jk 块 列表` 看有哪些）' % d)
        dep = 命中[0]
        dep域 = dep.domains[0] if dep.domains else ''
        dep导出 = sorted(dep.exports)
        if not dep导出:
            # 元数据没声明 `导出` 的老块（W7 前的形态）退回读它的主 `.jk`
            try:
                dep导出 = [_推导出名(dep域, d)]
            except BlockError as e:
                return _err('依赖块「%s」的导出名取不到：%s' % (d, e))
        依赖表.append((d, dep域, dep导出[0], dep.level, dep.stability))


    # G13 导出名全局唯一。真源是索引里的 `导出` 字段；索引不在/没这个字段时
    # 退回扫到的元数据 `导出`——两条都查不到就只能放过，由 CI 的 G13 兜底。
    占用 = {}
    try:
        索引 = blocks.load_index()
    except BlockError:
        索引 = None
    if 索引:
        for entry in 索引.get('块') or []:
            for e in entry.get('导出') or []:
                占用.setdefault(e, entry.get('名称'))
    for m in 既有:
        for e in m.exports:
            占用.setdefault(e, m.name)
    if 导出名 in 占用:
        return _err('导出名「%s」已被块「%s」占用（G13 导出名全局唯一）；'
                    '换一个名字，别指望 CI 放过' % (导出名, 占用[导出名]))

    # ---- 生成三件套 ----------------------------------------------------
    元数据 = {'名称': 块名, '版本': _新块版本, '层级': 层级, '领域': [领域],
            '描述': _新块描述占位}
    if 形参:
        元数据['输入'] = [{'名': (p[1:] if len(p) > 1 else p),
                       '类型': _新块类型占位} for p in 形参]
    元数据['输出'] = {'类型': _新块类型占位}
    元数据['导出'] = [导出名]
    if 依赖块:
        元数据['依赖块'] = list(依赖块)
    元数据['稳定性'] = 稳定性

    # 构造主源码：有依赖时头部加 `从 blocks.X.Y 导入 Z。`
    导入行 = ''
    if 依赖表:
        导入行 = '\n'.join(
            '从 blocks.%s.%s 导入 %s。' % (dep域, dep名, dep导出)
            for dep名, dep域, dep导出, _, _ in 依赖表
        ) + '\n\n'

    签名 = '函数 %s 接收 %s：' % (导出名, ' '.join(形参)) if 形参 \
        else '函数 %s：' % 导出名
    主源码 = ('%s%s\n  -- TODO: 实现\n  返回 空。\n。\n\n导出 %s。\n'
            % (导入行, 签名, 导出名))

    # 测试用 `空` 占位实参：`tests/test_blocks_smoke.py` 会真跑每个 测试.jk，
    # 骨架 `返回 空。` 吃什么参数都不会崩，所以脚手架一落地就是绿的——贡献者
    # 改实现时才需要把占位换成真数据。多参形如 `合计(空 空)`（空格分隔）。
    实参 = ' '.join('空' for _ in 形参)
    测试源码 = ('-- 块 %s.%s 的单元测试。\n\n从 blocks.%s.%s 导入 %s。\n\n'
             '-- TODO: 补充测试用例\n定义赵结果=%s(%s)。\n打印 赵结果。\n'
             % (领域, 块名, 领域, 块名, 导出名, 导出名, 实参))

    try:
        os.makedirs(目录)
        # `newline='\n'` 三个文件都要：Windows 默认把 `\n` 翻成 `\r\n`，
        # 同一条命令在两个平台会产出字节不同的块（理由同 blocks.save_index）。
        with open(os.path.join(目录, BLOCK_METADATA_NAME), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(json.dumps(元数据, ensure_ascii=False, indent=2) + '\n')
        with open(os.path.join(目录, 块名 + '.jk'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(主源码)
        with open(os.path.join(目录, '测试.jk'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(测试源码)
    except OSError as e:
        return _err('写脚手架失败：%s' % e)

    print('✓ 已创建块 %s.%s → %s' % (领域, 块名, 目录))
    print('  下一步：编辑 %s.jk 实现逻辑，再跑 `jk 块 校验 %s`'
          % (块名, 目录))
    return _EXIT_OK


_DISPATCH = {
    'list': _cmd_list,
    'search': _cmd_search,
    'select': _cmd_select,
    'synthesize': _cmd_synthesize,
    'run': _cmd_run,
    'show': _cmd_show,
    'check': _cmd_check,
    'index': _cmd_index,
    'new': _cmd_new,
}


def run(argv: Optional[List[str]] = None) -> int:
    """块生态子命令入口。`argv` 是 `块` 之后的参数列表。"""
    # Windows 默认 stdout 走 GBK，`✓`/`✗` 等符号会 UnicodeEncodeError。
    # 尽力切到 UTF-8；`reconfigure` 在 Python 3.7+ 才有，pytest 捕获流也没有，
    # 用 `getattr` 保底不影响测试。
    _reconfigure_utf8(sys.stdout)
    _reconfigure_utf8(sys.stderr)
    # stdin 也要转 UTF-8：Windows PowerShell 管道 `选 --json | 组 -` 会按 GBK
    # 重编码中文 key，不转的话 `组 -` 收到的方案 JSON 直接解析失败。
    _reconfigure_utf8(sys.stdin)

    argv = list(sys.argv[2:] if argv is None else argv)
    if not argv:
        print(_USAGE)
        return 0
    raw = argv[0]
    command = _ALIASES.get(raw)
    if command is None:
        print(f'未知的块生态命令：{raw}\n', file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return 1
    if command == 'help':
        print(_USAGE)
        return 0
    return _DISPATCH[command](argv[1:])


def main() -> None:
    """独立入口（`python -m jikuai.pkg.blocks_cli`）。"""
    sys.exit(run(sys.argv[1:]))


if __name__ == '__main__':
    main()
