# -*- coding: utf-8 -*-
"""极快块生态 - 命令行子命令（v0.12.0 · ADR-15 §3.5）。

命令表（中文主名 + 英文别名，风格对齐 `pkg/cli.py`）：

    jk 块 列表 [--领域 X] [--层级 N] [--稳定性 stable]   list
    jk 块 查找 <关键词>                                  search
    jk 块 详情 <块名>                                    show
    jk 块 校验 [块目录]                                  check
    jk 块 索引 [块目录]                                  index
    jk 块 帮助                                           help

设计取舍与 `pkg/cli.py` 完全对齐：这一层只做**参数解析 + 人类可读输出**，
所有业务逻辑都在 `blocks` 模块里。所有错误都收敛成返回码（0 成功，非 0
失败）+ stderr 中文提示，不向上抛裸异常。
"""

import json
import os
import sys
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
  jk 块 选 <需求> [--top N] [--json]
                                 按自然语言需求语义排序返回 top-K 块
  jk 块 详情 <块名>              显示某个块的完整元数据与示例
  jk 块 校验 [块目录]            校验一个块或全部块的合规性
                                 （元数据/主 .jk/导出/词法原子/依赖块 一致性）
  jk 块 索引 [块目录]            重新生成 {BLOCK_INDEX_NAME}
  jk 块 帮助                     显示本帮助

英文别名：list(ls) / search(find) / select(pick) / show(info) /
          check(validate) / index / help

领域白名单：{'/'.join(sorted(ALLOWED_DOMAINS))}
稳定性等级：{'/'.join(sorted(STABILITY_LEVELS))}
"""

#: 中文命令 -> 规范命令名；英文别名一并归一。风格对齐 pkg/cli.py。
_ALIASES = {
    '列表': 'list', 'list': 'list', 'ls': 'list',
    '查找': 'search', '搜索': 'search', 'search': 'search', 'find': 'search',
    '选': 'select', '选块': 'select', 'select': 'select', 'pick': 'select',
    '详情': 'show', '显示': 'show', 'show': 'show', 'info': 'show',
    '校验': 'check', '检查': 'check', 'check': 'check', 'validate': 'check',
    '索引': 'index', 'index': 'index',
    '帮助': 'help', 'help': 'help', '-h': 'help', '--help': 'help',
}


def _err(msg: str) -> int:
    print(f'块生态错误：{msg}', file=sys.stderr)
    return 1


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

    `--向量 <文件>` 是神经路径的唯一入口：运行时不做模型推理（ADR-25 §2 零
    依赖），查询向量必须由调用方预先算好（`tools/ai-bridge/`）。不给向量就走
    启发式——输出里的 `[神经]`/`[启发式]` 标签会说明实际走了哪条。
    """
    需求 = None
    top = 5
    as_json = False
    向量文件 = None
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
        elif a in ('--向量', '--vector'):
            if i + 1 >= len(args):
                return _err('--向量 缺少文件路径')
            向量文件 = args[i + 1]
            i += 2
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

    from ..ai import retrieval
    try:
        hits = retrieval.retrieve(需求, top=top, query_vector=查询向量)
    except retrieval.RetrievalError as e:
        return _err(str(e))

    if as_json:
        print(json.dumps({
            '需求': 需求,
            '候选': [h.as_dict() for h in hits],
        }, ensure_ascii=False, indent=2))
        return 0

    if not hits:
        print(f'没有匹配「{需求}」的块。')
        return 0
    print(f'需求：{需求}    {hits[0].path}')
    for i, h in enumerate(hits, 1):
        print(f'  {i}. {h.name}（{h.domain}）  分数 {h.score:.4f}')
        print(f'     {h.description}')
    return 0


_DISPATCH = {
    'list': _cmd_list,
    'search': _cmd_search,
    'select': _cmd_select,
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
