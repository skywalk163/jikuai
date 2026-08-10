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
from typing import List, Optional

from . import blocks
from .blocks import (
    ALLOWED_DOMAINS, BLOCK_INDEX_NAME, STABILITY_LEVELS,
    BlockError, BlockMetadata,
)

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
  jk 块 帮助                     显示本帮助

三段式：选（需求→候选）→ 组（方案→源码）→ 跑（方案→结果）
  端到端：jk 块 选 "需求" --组 | jk 块 跑 -

英文别名：list(ls) / search(find) / select(pick) / synthesize(assemble) /
          run / show(info) / check(validate) / index / help

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
# 一份 schema 走三段（W12 会跨 CLI/LSP/Web 复用）：
#
#   {
#     "需求": "把一批数字求和再算平均",     -- 选/组/跑 都只当注释用
#     "候选": [{名称,领域,描述,分数,路径}],  -- 选 产出
#     "共享": [{"名":"赵料","值":"列 1 2 3"}],
#     "步骤": [{块,领域,导出名,说明,参数}],  -- 方案主体（协议.md）
#     "打印": ["赵果1"],
#     "源码": "-- 由 极快 AI 桥接…",         -- 组 产出
#     "结果": ["6"]                          -- 跑 产出（程序打印的每一行）
#   }
#
# `步骤` 是「方案」的判据：有它就是方案，没有但有 `候选` 就先把候选提成方案。

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


def _块目录(领域: str, 块: str) -> str:
    """块在 `stdlib/blocks/` 下的目录——与 `从 blocks.<领域>.<块> 导入` 同构。"""
    return os.path.join(blocks.blocks_root(), 领域, 块)


def _主jk(块目录: str, 块: str) -> Optional[str]:
    """块的主 `.jk`：`<块>.jk` 优先，`main.jk` 兜底（对齐 module_loader 策略 2/3）。"""
    for 候选 in (块 + '.jk', 'main.jk'):
        p = os.path.join(块目录, 候选)
        if os.path.isfile(p):
            return p
    return None


def _推导出名(领域: str, 块: str) -> str:
    """从块的主 `.jk` 里提取导出名（协议.md：导入用目录名，调用用导出名）。

    多个导出时优先取与块同名的那个，否则取排序首位——要稳定可测。
    """
    目录 = _块目录(领域, 块)
    jk = _主jk(目录, 块)
    if jk is None:
        raise BlockError('块「%s」（领域 %s）没有主 .jk，无法确定导出名' % (块, 领域))
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
    交给调用方报错。
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
            候选.append({'名称': m.group('名').strip(), '领域': m.group('域').strip()})
    if not 候选:
        return None
    return {'需求': 需求 or '', '候选': 候选}


def _候选转方案(信封: dict, 步数: int) -> dict:
    """候选 → 方案：取前 `步数` 条候选，按顺序变成 `步骤`。

    候选没有参数信息，所以 `参数` 一律省略，交给 `--自动链式` 的类型图去推；
    推不出的由粘合器落 `?` 占位并写明「需人工填参」——不静默硬塞。
    """
    候选 = 信封.get('候选')
    if not isinstance(候选, list) or not 候选:
        raise BlockError('输入既没有「步骤」也没有非空「候选」，无从组装')
    步骤 = []
    for h in 候选[:步数]:
        if not isinstance(h, dict) or not h.get('名称') or not h.get('领域'):
            raise BlockError('候选项必须含「名称」与「领域」：%r' % (h,))
        步骤.append({
            '块': h['名称'],
            '领域': h['领域'],
            '说明': h.get('描述') or ('候选 %s' % h['名称']),
        })
    方案 = dict(信封)
    方案['步骤'] = 步骤
    return 方案


def _校验方案(方案: dict) -> dict:
    """校验并就地补全方案：字段齐全 + 块真实存在 + 导出名可推导。

    返回补全后的方案（不改调用方的对象）。任何问题抛 `BlockError`，由
    `_cmd_*` 转成返回码 1 + 人读提示，不让裸异常栈冒到用户脸上。
    """
    if not isinstance(方案, dict):
        raise BlockError('方案必须是 JSON 对象（得到 %s）' % type(方案).__name__)
    步骤 = 方案.get('步骤')
    if 步骤 is None or 步骤 == []:
        raise BlockError('方案缺少非空的「步骤」字段（schema 见 tools/ai-bridge/协议.md）')
    if not isinstance(步骤, list):
        raise BlockError('「步骤」必须是数组（得到 %s）' % type(步骤).__name__)

    新步骤 = []
    for i, s in enumerate(步骤, 1):
        if not isinstance(s, dict):
            raise BlockError('步骤 %d 必须是 JSON 对象（得到 %s）' % (i, type(s).__name__))
        s = dict(s)
        for 字段 in ('块', '领域'):
            if not s.get(字段):
                raise BlockError('步骤 %d 缺少必填字段「%s」' % (i, 字段))
        块, 领域 = s['块'], s['领域']
        if 领域 not in ALLOWED_DOMAINS:
            raise BlockError('步骤 %d 的领域 %r 不在白名单（允许：%s）'
                             % (i, 领域, '/'.join(sorted(ALLOWED_DOMAINS))))
        if not os.path.isdir(_块目录(领域, 块)):
            raise BlockError('步骤 %d 的块「%s」不存在：%s 下没有这个目录'
                             '（`jk 块 查找 %s` 看看真名）'
                             % (i, 块, os.path.join('blocks', 领域), 块))
        if not s.get('导出名'):
            s['导出名'] = _推导出名(领域, 块)
        新步骤.append(s)

    新方案 = dict(方案)
    新方案['步骤'] = 新步骤
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
    if isinstance(data, dict) and data.get('步骤') is None and data.get('候选'):
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
    """把源码落**临时** `.jk` 再交给解释器跑，返回 `(结果, 打印出的文本)`。

    为什么落临时文件而不直接 `run_source(源码)`：模块解析要靠 `current_file`
    定位搜索路径（`module_loader._search_paths`），给个真实路径最省事，也让
    报错信息里的文件名可点。临时文件在 finally 里删掉，工作区不留垃圾。

    程序的打印用 `redirect_stdout` 收进内存——`--json` 要把它塞进 `结果`
    字段，人读模式再原样吐回真 stdout，两条路输出一致。
    """
    import io
    from contextlib import redirect_stdout
    from ..main import run_source

    fd, path = tempfile.mkstemp(prefix='jk_块跑_', suffix='.jk')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(源码)
        buf = io.StringIO()
        with redirect_stdout(buf):
            结果 = run_source(源码, file=path)
        return 结果, buf.getvalue()
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
            print(f'神经检索不可用，降级到启发式：{why}', file=sys.stderr)
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
        print(json.dumps({
            '需求': 需求,
            '候选': [h.as_dict() for h in hits],
        }, ensure_ascii=False, indent=2))
        return 0

    if as_组:
        # --组：选完直接过 校验（补 导出名）+ glue.synthesize 出源码
        try:
            方案 = _校验方案({
                '需求': 需求,
                '步骤': [{'块': h.name, '领域': h.domain,
                        '说明': h.description} for h in hits],
            })
            源码 = _glue().synthesize(方案, 自动链式=True)
        except BlockError as e:
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
    if _占位记号 in 源码:
        return _err('方案有步骤的参数未指定（源码里出现「%s」）；'
                    '在「步骤」里补 `参数`，或换成类型图能自动链上的组合'
                    % _占位记号)
    try:
        结果, 打印文本 = _执行源码(源码)
    except Exception as e:                            # noqa: BLE001
        错误 = '%s：%s' % (type(e).__name__, e)
        if as_json:
            print(json.dumps({'需求': 方案.get('需求', ''), '错误': 错误,
                              '源码': 源码}, ensure_ascii=False, indent=2))
        else:
            print('执行错误：%s' % 错误, file=sys.stderr)
        return _EXIT_RUN
    if as_json:
        print(json.dumps({
            '需求': 方案.get('需求', ''),
            '源码': 源码,
            '结果': 打印文本.splitlines(),
            '返回值': repr(结果),
        }, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(打印文本)
    return 0


_DISPATCH = {
    'list': _cmd_list,
    'search': _cmd_search,
    'select': _cmd_select,
    'synthesize': _cmd_synthesize,
    'run': _cmd_run,
    'show': _cmd_show,
    'check': _cmd_check,
    'index': _cmd_index,
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
