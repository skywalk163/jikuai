# -*- coding: utf-8 -*-
"""生成 `stdlib/blocks/索引.json` —— 块生态的 AI 桥接与 CLI 检索索引（ADR-15 §3.4）。

用法：
    python scripts/generate_block_index.py            正常生成，覆盖写入索引
    python scripts/generate_block_index.py --check    只比对，不写盘（CI 门禁）
    python scripts/generate_block_index.py --quiet    只输出错误，正常路径静默

退出码：
    0   一切正常（或 --check 且索引已是最新）
    1   --check 模式下索引与磁盘不一致，或校验/IO 出错

实现要点：
- 扫描与校验的实际逻辑在 `src/jikuai/pkg/blocks.py`，本脚本只做参数解析、
  路径调整、退出码与人类可读输出。这样同一份逻辑既能被 CI 命令行调用，
  也能被 `jk 块 索引` 子命令、LSP、测试以库形式复用。
- `--check` 与 `--quiet` 可以叠加：CI 里典型的写法是 `--check --quiet`，
  失败时才有输出。
"""

import argparse
import os
import sys

# Windows 控制台默认 GBK，脚本输出含中文；强制 UTF-8 免得被 subprocess 捕获报错。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass

# 让脚本可以在**未安装**极快包的仓库里独立运行：把 `src/` 加进 sys.path。
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
_SRC_PATH = os.path.join(_REPO_ROOT, 'src')
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)

from jikuai.pkg.blocks import (   # noqa: E402
    BLOCK_INDEX_NAME, BlockError,
    blocks_root, generate_index, index_differs, index_path,
    load_index, render_index, save_index,
)


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog='generate_block_index',
        description='扫描 stdlib/blocks/ 生成 %s（ADR-15 §3.4）' % BLOCK_INDEX_NAME,
    )
    parser.add_argument(
        '--check', action='store_true',
        help='只校验现有索引是否与扫描结果一致，不写盘；不一致退出码 1',
    )
    parser.add_argument(
        '--quiet', action='store_true',
        help='正常路径静默，只在错误或差异时输出',
    )
    parser.add_argument(
        '--root', default=None,
        help='块目录根（默认使用内置 stdlib/blocks/）',
    )
    parser.add_argument(
        '--output', default=None,
        help='索引输出路径（默认 <root>/%s）' % BLOCK_INDEX_NAME,
    )
    parser.add_argument(
        '--with-examples', dest='with_examples', action='store_true',
        help='生成「胖索引」：每条追加 `示例` 字段（浏览器/离线包等在乎 I/O '
             '次数、不在乎 token 的下游用；默认关，见 blocks.to_index_entry。'
             '**不与 --check 组合**：仓库内 stdlib 索引固定走 lean 形态）',
    )
    return parser.parse_args(argv)


def _rel(path):
    """尝试转成相对仓库根的路径，方便日志阅读；失败退回绝对路径。"""
    try:
        return os.path.relpath(path, _REPO_ROOT)
    except ValueError:
        return path


def _emit(msg, quiet):
    if not quiet:
        print(msg)


def main(argv=None):
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    root = os.path.abspath(args.root) if args.root else blocks_root()
    target = os.path.abspath(args.output) if args.output else index_path(root)

    if not os.path.isdir(root):
        print('错误：块目录不存在：%s' % root, file=sys.stderr)
        return 1

    if args.with_examples and args.check:
        print('错误：--with-examples 与 --check 不能同时使用（仓库内 stdlib 索引固定 lean）',
              file=sys.stderr)
        return 1

    try:
        fresh = generate_index(root, 含示例=args.with_examples)
    except BlockError as e:
        print('错误：扫描块元数据失败：%s' % e, file=sys.stderr)
        return 1

    block_count = len(fresh['块'])

    if args.check:
        try:
            existing = load_index(target)
        except BlockError as e:
            print('错误：读取现有索引失败：%s' % e, file=sys.stderr)
            return 1
        if existing is None:
            print('错误：索引尚未生成：%s（运行 `python scripts/generate_block_index.py`）'
                  % _rel(target), file=sys.stderr)
            return 1
        if index_differs(existing, fresh):
            print('错误：索引 %s 已过期，与 stdlib/blocks/ 扫描结果不一致。'
                  '请重新运行 `python scripts/generate_block_index.py` 后提交。'
                  % _rel(target), file=sys.stderr)
            return 1
        _emit('索引最新（共 %d 个块）：%s' % (block_count, _rel(target)), args.quiet)
        return 0

    # 块列表没变就不落盘。否则每次运行都刷新「生成时间」，索引会在每个
    # commit 里无意义地翻动一行，也会让 `--check` 的语义与生成行为脱节。
    try:
        existing = load_index(target)
    except BlockError:
        existing = None      # 现有索引坏了，直接重写覆盖
    if existing is not None and not index_differs(existing, fresh):
        _emit('找到 %d 个块，索引已是最新，未改动：%s'
              % (block_count, _rel(target)), args.quiet)
        return 0

    try:
        written = save_index(fresh, target)
    except OSError as e:
        print('错误：写入索引失败：%s' % e, file=sys.stderr)
        return 1

    _emit('找到 %d 个块，写入 %s' % (block_count, _rel(written)), args.quiet)
    return 0


if __name__ == '__main__':
    sys.exit(main())
