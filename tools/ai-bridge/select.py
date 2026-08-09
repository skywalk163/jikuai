# -*- coding: utf-8 -*-
"""块选择器 v0 —— 本地关键词匹配（**不外接任何大模型 API**）。

定位见 `README.md`：把 AI 的职责从「生成几百行代码」降级为「从索引里选块」。
本文件实现的就是「选块」这一步的**本地下位替代**：用字符重叠打分代替语义
理解。够用来把《块选择协议 v0》（`协议.md`）跑通，不够用来处理同义改写。

命令行::

    python tools/ai-bridge/select.py "对一组数字求和"
    python tools/ai-bridge/select.py "农历" --top 3

作为库（注意本文件名与 Python 标准库 `select` 同名，别直接
`sys.path.insert(0, ...)` + `import select`，用 importlib 按路径载入）::

    import importlib.util
    spec = importlib.util.spec_from_file_location('块选择器', '.../select.py')
    ...

为什么导出名不从索引读
----------------------
`stdlib/blocks/索引.json` 的条目只有 `名称`（= 目录名），没有 `导出`。而
ADR-15 §3.7 规定「导入用目录名、调用用导出名」，生成粘合代码必须拿到导出名。
扩展索引结构要改 `pkg/blocks.py` 的 `generate_index`，超出本工具写域，所以
这里走方案 (A)：复用现成的 `jikuai.pkg.blocks.extract_exports`，直接从
`stdlib/blocks/<领域>/<块名>/<块名>.jk` 里正则抽 `导出 X`。
"""

import argparse
import json
import os
import sys

#: 仓库根 = 本文件上两级（tools/ai-bridge → tools → 仓库根）。
#: 用 `__file__` 推而不用 `os.getcwd()`：CLI 与 pytest 的 cwd 不一定是仓库根。
_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg.blocks import BLOCK_INDEX_NAME, blocks_root, extract_exports  # noqa: E402

__all__ = ['load_index', 'select_blocks', 'index_path', 'resolve_export']

#: `名称` 完整出现在需求文本里时的加分。最强信号——「求和」这类块名本身
#: 就是用户会用的词，命中即基本确定。
_权重_块名 = 8.0

#: 领域词（数据/中文/网络/工具）出现在需求里时的加分。中等信号。
_权重_领域 = 3.0

#: 描述里每个命中字符的加分。弱信号，只用来在同分候选间排序，
#: 权重刻意压得很低——否则长描述的块会仅因为「字多」而胜出。
_权重_描述字 = 0.3


def index_path():
    """`stdlib/blocks/索引.json` 的绝对路径。"""
    return os.path.join(blocks_root(), BLOCK_INDEX_NAME)


def load_index(path=None):
    """读块索引，返回解析后的字典（含 `版本` / `生成时间` / `块`）。"""
    target = path or index_path()
    with open(target, 'r', encoding='utf-8') as f:
        return json.load(f)


def _切字(text):
    """把文本切成「有意义的字符」列表：保留中日韩汉字与字母数字，丢标点空白。

    不接词典分词器：极快块名多是 2 字词（求和/均值/去重/升序），字级重叠
    已经能把它们区分开，引入分词依赖只会让这个参考实现变重。
    """
    return [c for c in text
            if ('\u4e00' <= c <= '\u9fff') or c.isalnum()]


def resolve_export(block, root=None):
    """定位块目录的主 `.jk` 并抽出导出名；找不到返回 `None`。

    极快现有 52 个块都是**单导出**；万一某块有多个导出，取字典序首个并把
    完整集合留给调用方自己判断（本函数只回一个名字，够生成粘合代码）。
    """
    base = root or blocks_root()
    name = block['名称']
    for domain in block.get('领域') or []:
        jk = os.path.join(base, domain, name, name + '.jk')
        if not os.path.isfile(jk):
            jk = os.path.join(base, domain, name, 'main.jk')
        if os.path.isfile(jk):
            exports = extract_exports(jk)
            if exports:
                return sorted(exports)[0]
    return None


def _打分(query, query_chars, block):
    """给一个块打分。分数无绝对含义，只用于同一次查询内部排序。

    三层信号叠加：
      1. 字符重叠数——需求里有多少字出现在「名称+描述+领域」里
      2. 块名 / 领域词完整出现的奖励
      3. 描述命中密度（低权重）
    """
    name = block.get('名称', '')
    desc = block.get('描述', '')
    domains = block.get('领域') or []

    干草堆 = set(_切字(name + desc + ''.join(domains)))
    score = float(len(set(query_chars) & 干草堆))

    if name and name in query:
        score += _权重_块名
    for d in domains:
        if d and d in query:
            score += _权重_领域

    描述字 = set(_切字(desc))
    score += sum(1 for c in query_chars if c in 描述字) * _权重_描述字
    return score


def select_blocks(需求文本, index, top=None):
    """按关键词重叠给索引里的块打分，返回候选列表。

    返回项形如::

        {'名称': '求和', '领域': '数据', '导出名': '汇总',
         '描述': '对数值列表求和，返回总和', '分数': 11.9}

    排序：分数降序；同分按名称升序（保证输出确定，便于测试与 diff）。
    `top` 给定时截断。分数 ≤ 0 的块（与需求毫无字符重叠）直接丢弃。
    """
    query_chars = _切字(需求文本)
    root = blocks_root()
    候选 = []
    for block in index.get('块') or []:
        score = _打分(需求文本, query_chars, block)
        if score <= 0:
            continue
        候选.append({
            '名称': block['名称'],
            '领域': (block.get('领域') or ['?'])[0],
            '导出名': resolve_export(block, root) or '?',
            '描述': block.get('描述', ''),
            '分数': round(score, 2),
        })
    候选.sort(key=lambda c: (-c['分数'], c['名称']))
    return 候选[:top] if top is not None else 候选


def _cli(argv=None):
    p = argparse.ArgumentParser(
        description='极快块选择器 v0（本地关键词匹配，不外接大模型）')
    p.add_argument('需求', help='自然语言需求文本')
    p.add_argument('--top', type=int, default=5, help='候选数上限（默认 5）')
    args = p.parse_args(argv)

    index = load_index()
    print(json.dumps({
        '需求': args.需求,
        '块总数': len(index.get('块') or []),
        '候选': select_blocks(args.需求, index, top=args.top),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(_cli())
