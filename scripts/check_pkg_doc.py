#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""包管理文档同步 CI 门禁 G17（v0.19.0 · W62）。

**为什么**：v0.19.0 W61 纠偏发现 `docs/包管理.md` 落后 `src/jikuai/pkg/cli.py`
约七个版本——`发布`/`搜索`/`注册表` 三个在 v0.11.0 就落地的子命令一直没
写进命令表，还把它们记作「MVP 占位/未落地」。G16 只锁了 `docs/协议-三通道.md`
↔ `tools/web/server.py`，没锁包管理文档 ↔ CLI 子命令；补上 G17，之后靠门禁
不靠人工审计。

**做法**（沿用 G16 的双向 diff 思路）：
- doc 侧：`## 命令表` 小节里
  - 每条 "- `jk 包 <名称> ...`" bullet 抽出中文主命令名（`包` 后第一个 token）
  - `中文别名：...` 段（若有）抽出反引号包着的中文别名
  - `英文别名：...` 段抽出反引号包着的英文别名
- code 侧：`src/jikuai/pkg/cli.py` 的 `_ALIASES` dict 键：
  - 含 CJK 字符归中文集
  - 其余归英文集（排除 `-h` / `--help` 这类 switch）
- 中文集、英文集**分别双向 diff**——doc 缺、code 缺、别名遗漏都算红。

**第二层：`密钥` 的子子命令**（v0.22.0 · W103）。顶层锁住之后盲区下移了一层：
`密钥 生成/列表/导出/信任/撤信` 不在 `_ALIASES` 里，G17 看不见。W101 加
`密钥 信任`/`密钥 撤信` 时，它们的英文写法被当成**顶层**别名而误报红，当时靠
「把那段挪出『英文别名：』段落」躲开——那是绕过不是修好，正是 G17 要防的那类
漂移只是深了一层。现在同款双向 diff 补上：
- doc 侧：`- `jk 包 密钥 <名> ...`` bullet 抽中文子子命令名；专属段落
  `密钥子命令英文别名：` 抽英文写法（**与顶层「英文别名：」是两个不同的段落头**，
  段落头按行首锚定匹配，两层不互相污染——`list`/`ls` 两层都有也各归各层）。
- code 侧：`ast` 解析 `_cmd_key` 里 `if/elif sub in (...)`（或 `sub == '...'`）
  分派分支的字符串字面量，同样分中文/英文两集。不用正则抓源码。

**不学 G13+ 静默跳过**：G13+ 的宽容是历史遗留（早期块库没补齐 `依赖块` 时
不该红），G17 是新门禁，解析不了就是它自己坏了——静默跳过等于门禁形同虚设。

用法：
    python scripts/check_pkg_doc.py            扫描 + 比对
    python scripts/check_pkg_doc.py --quiet    只有差异才输出

退出码：0=一致 / 1=有差异或读文件失败。
"""

import argparse
import ast
import os
import re
import sys

# Windows 控制台默认 GBK；照抄 G16 的编码强化以避免中文错误消息乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
_DOC_PATH = os.path.join(_REPO_ROOT, 'docs', '包管理.md')
_CLI_PATH = os.path.join(_REPO_ROOT, 'src', 'jikuai', 'pkg', 'cli.py')

#: 命令表小节标题。定位起点后一直读到下一个 `## ` 为止。
_CMD_SECTION_HEAD = '## 命令表'
_ALIAS_LINE_HEAD_ZH = '中文别名：'
_ALIAS_LINE_HEAD_EN = '英文别名：'

#: `密钥` 子子命令的英文写法专属段落头。**刻意与 `_ALIAS_LINE_HEAD_EN` 不同**：
#: 两层集合各有自己的段落头，段落头又按行首锚定匹配（见 `_取别名段`），所以
#: 「密钥子命令英文别名：」不会被当成顶层「英文别名：」的段落，反之亦然。
_ALIAS_LINE_HEAD_KEY_EN = '密钥子命令英文别名：'

#: `密钥` 在顶层的中文命令名 / 它在 cli.py 里的分派函数名。
_KEY_CMD_ZH = '密钥'
_KEY_DISPATCH_FUNC = '_cmd_key'

#: bullet 抽取：``- `jk 包 <名称> ...` ``。捕获 `包` 后第一段非空白非反引号
#: token，**不要求紧跟闭合反引号**——命令后常带参数（`jk 包 初始化 [名称]`）。
_BULLET_CMD = re.compile(r'^\s*-\s+`jk\s+包\s+([^\s`]+)', re.MULTILINE)

#: 子子命令 bullet：``- `jk 包 密钥 <名称> ...` ``。多吃一层 `密钥`，捕获其后
#: 的 token。这类 bullet 同时被 `_BULLET_CMD` 抓到 `密钥`——那是对的，`密钥`
#: 本身确实是顶层命令。
_BULLET_KEY_SUB = re.compile(
    r'^\s*-\s+`jk\s+包\s+%s\s+([^\s`]+)' % _KEY_CMD_ZH, re.MULTILINE)


#: 反引号内的中文标识符（连续 CJK 字符）。
_TOKEN_ZH = re.compile(r'`([\u4e00-\u9fff]+)`')

#: 反引号内的英文/ASCII 命令 token（含连字符）。
_TOKEN_EN = re.compile(r'`([a-zA-Z][a-zA-Z0-9-]*)`')


def _读文件(path):
    if not os.path.isfile(path):
        print('错误：文件不存在：%s' % path, file=sys.stderr)
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _取小节(text, head):
    """截取 `## <head>` 起、到下一个 `## ` 止的段落文本。找不到返回 None。"""
    start = text.find(head)
    if start < 0:
        return None
    tail = text.find('\n## ', start + len(head))
    return text[start:tail] if tail > 0 else text[start:]


def _取别名段(小节, head):
    """从 `head` 所在行开头截到下一个空行（或小节末），返回段落文本。

    **按行首锚定**而不是 `find(head)`：`密钥子命令英文别名：` 里含有
    `英文别名：` 这个子串，裸 `find` 会让顶层段落头命中子级段落（W101 就是被
    这类混淆坑的），两层集合互相污染。行首锚定后两个段落头彼此不匹配，
    先后顺序也就无关紧要了。
    """
    m = re.search(r'^%s' % re.escape(head), 小节, re.MULTILINE)
    if m is None:
        return ''
    idx = m.start()
    双换 = 小节.find('\n\n', idx)
    return 小节[idx:双换] if 双换 > 0 else 小节[idx:]


def _归类(名字集):
    """按是否含 CJK 字符把命令名分成 (中文集, 英文集)；`-` 开头的 switch 丢掉。"""
    中文集 = set()
    英文集 = set()
    for 名 in 名字集:
        if 名.startswith('-'):
            continue
        if any('\u4e00' <= ch <= '\u9fff' for ch in 名):
            中文集.add(名)
        else:
            英文集.add(名)
    return 中文集, 英文集



def _doc命令(doc文本):
    """从「## 命令表」小节抽两组：中文命令名集 + 英文别名集。"""
    小节 = _取小节(doc文本, _CMD_SECTION_HEAD)
    if 小节 is None:
        raise ValueError('未在 docs/包管理.md 找到「%s」小节'
                         % _CMD_SECTION_HEAD)
    中文集 = set()
    for m in _BULLET_CMD.finditer(小节):
        中文集.add(m.group(1))

    # 「中文别名：」段（可选，用于列 `删除`（同 `移除`）等）
    别名段_zh = _取别名段(小节, _ALIAS_LINE_HEAD_ZH)
    for m in _TOKEN_ZH.finditer(别名段_zh):
        中文集.add(m.group(1))

    # 「英文别名：」段
    别名段_en = _取别名段(小节, _ALIAS_LINE_HEAD_EN)
    英文集 = set()
    if 别名段_en:
        for m in _TOKEN_EN.finditer(别名段_en):
            英文集.add(m.group(1))
    return 中文集, 英文集


def _code命令(cli源码):
    """解析 `pkg/cli.py` 的 `_ALIASES` dict 字面量，返回 (中文集, 英文集)。

    键中含 CJK 字符归中文；其余归英文（排除 `-h` / `--help` 这类 switch）。
    """
    tree = ast.parse(cli源码)
    aliases_node = None
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == '_ALIASES'):
            aliases_node = node.value
            break
    if aliases_node is None or not isinstance(aliases_node, ast.Dict):
        raise ValueError(
            '未在 src/jikuai/pkg/cli.py 找到模块级 `_ALIASES` 字典字面量')
    键集 = set()
    for key_node in aliases_node.keys:
        if not (isinstance(key_node, ast.Constant)
                and isinstance(key_node.value, str)):
            continue
        键集.add(key_node.value)
    # `-h` / `--help` 是 switch，不算子命令名——`_归类` 里统一丢掉
    中文集, 英文集 = _归类(键集)
    if not 中文集 and not 英文集:
        raise ValueError('`_ALIASES` 解析为空——字面量结构可能变了')
    return 中文集, 英文集




def _doc密钥子命令(doc文本):
    """从「## 命令表」小节抽 `密钥` 的子子命令：(中文集, 英文集)。

    中文名走 ``- `jk 包 密钥 <名> ...`` bullet；英文写法走专属段落
    `密钥子命令英文别名：`。两者都只在命令表小节内找，与顶层各走各的段落头。
    """
    小节 = _取小节(doc文本, _CMD_SECTION_HEAD)
    if 小节 is None:
        raise ValueError('未在 docs/包管理.md 找到「%s」小节'
                         % _CMD_SECTION_HEAD)
    中文集 = set()
    for m in _BULLET_KEY_SUB.finditer(小节):
        中文集.add(m.group(1))

    英文集 = set()
    别名段 = _取别名段(小节, _ALIAS_LINE_HEAD_KEY_EN)
    for m in _TOKEN_EN.finditer(别名段):
        英文集.add(m.group(1))
    return 中文集, 英文集


def _分派变量名(func节点):
    """找出 `_cmd_key` 里承接 `args[0]` 的那个局部变量名（现状是 `sub`）。

    不写死 `sub`，但也不放过找不到的情形：找不到说明分派入口的写法变了，
    此时把分支字面量当子命令名收集会漏或串味，宁可报错。
    """
    for node in ast.walk(func节点):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        值 = node.value
        if not (isinstance(值, ast.Subscript)
                and isinstance(值.value, ast.Name)
                and 值.value.id == 'args'):
            continue
        下标 = 值.slice
        if not isinstance(下标, ast.Constant):
            # py3.8 把下标包在 ast.Index 里；这里只关心它裹着的字面量
            下标 = getattr(下标, 'value', 下标)
        if isinstance(下标, ast.Constant) and 下标.value == 0:
            return node.targets[0].id
    return None


def _分支字面量(比较节点, 变量名):
    """`sub in ('信任', 'trust')` / `sub == '信任'` → 里面的字符串字面量。

    只认左侧就是分派变量的 `in` / `==`——`_cmd_key` 里还有 `len(rest) < 2`、
    `剩余 == 0` 这类与子命令名无关的比较，混进来会污染集合。
    """
    if not (isinstance(比较节点.left, ast.Name)
            and 比较节点.left.id == 变量名):
        return
    for op, 右 in zip(比较节点.ops, 比较节点.comparators):
        if not isinstance(op, (ast.In, ast.Eq)):
            continue
        候选 = 右.elts if isinstance(右, (ast.Tuple, ast.List, ast.Set)) else [右]
        for 项 in 候选:
            if isinstance(项, ast.Constant) and isinstance(项.value, str):
                yield 项.value


def _code密钥子命令(cli源码):
    """`ast` 解析 `_cmd_key` 的 if/elif 分派分支，返回 (中文集, 英文集)。

    与 `_code命令` 同一路子（读 AST 而不是正则抓源码）：正则遇到跨行的
    `sub in (...)` 或注释里出现的同形文本就会失手。
    """
    tree = ast.parse(cli源码)
    func = None
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == _KEY_DISPATCH_FUNC):
            func = node
            break
    if func is None:
        raise ValueError('未在 src/jikuai/pkg/cli.py 找到 `%s` 函数——`%s` 的'
                         '子命令分派结构变了'
                         % (_KEY_DISPATCH_FUNC, _KEY_CMD_ZH))
    变量名 = _分派变量名(func)
    if 变量名 is None:
        raise ValueError('未在 `%s` 里找到承接 `args[0]` 的分派变量'
                         % _KEY_DISPATCH_FUNC)
    名字集 = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Compare):
            名字集.update(_分支字面量(node, 变量名))
    中文集, 英文集 = _归类(名字集)
    if not 中文集 and not 英文集:
        raise ValueError('`%s` 里没解析出任何子命令——if/elif 分派结构可能变了'
                         % _KEY_DISPATCH_FUNC)
    return 中文集, 英文集


def _比对(doc中, doc英, code中, code英, doc密中, doc密英, code密中, code密英):
    return {
        '中文命令': (sorted(doc中 - code中), sorted(code中 - doc中)),
        '英文别名': (sorted(doc英 - code英), sorted(code英 - doc英)),
        '密钥子命令(中文)': (sorted(doc密中 - code密中),
                       sorted(code密中 - doc密中)),
        '密钥子命令(英文)': (sorted(doc密英 - code密英),
                       sorted(code密英 - doc密英)),
    }


def _报告(差异, quiet):
    有差异 = any(only_doc or only_code
                for only_doc, only_code in 差异.values())
    if not 有差异:
        if not quiet:
            print('G17 通过：`docs/包管理.md` 命令表与 '
                  '`src/jikuai/pkg/cli.py` 一致（含 `密钥` 子命令）')
        return False
    print('错误：G17 包管理文档同步失败——命令表与 CLI 别名表不一致',
          file=sys.stderr)
    for 维度 in 差异:
        only_doc, only_code = 差异[维度]
        if not only_doc and not only_code:
            continue
        print('  [%s]' % 维度, file=sys.stderr)
        for x in only_doc:
            print('    - 文档写了但代码没有：%s' % x, file=sys.stderr)
        for x in only_code:
            print('    - 代码有但文档没写：%s' % x, file=sys.stderr)
    print('  修复：更新 docs/包管理.md 「命令表」小节'
          '（顶层看 bullet 与「英文别名：」，密钥子命令看 '
          '`jk 包 密钥 ...` bullet 与「密钥子命令英文别名：」），'
          '或调整 src/jikuai/pkg/cli.py 的 `_ALIASES` / `_cmd_key`（择一）',
          file=sys.stderr)
    return True



def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='check_pkg_doc',
        description='CI 门禁 G17：docs/包管理.md ↔ '
                    'src/jikuai/pkg/cli.py 命令表一致性')
    parser.add_argument('--quiet', action='store_true',
                        help='一致时静默，只在有差异时输出')
    parser.add_argument('--doc', default=_DOC_PATH, help='包管理文档路径')
    parser.add_argument('--cli', default=_CLI_PATH, help='CLI 源码路径')
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    doc文本 = _读文件(args.doc)
    cli源码 = _读文件(args.cli)
    if doc文本 is None or cli源码 is None:
        return 1
    try:
        doc中, doc英 = _doc命令(doc文本)
        code中, code英 = _code命令(cli源码)
        doc密中, doc密英 = _doc密钥子命令(doc文本)
        code密中, code密英 = _code密钥子命令(cli源码)
    except ValueError as e:
        print('错误：解析失败：%s' % e, file=sys.stderr)
        return 1
    差异 = _比对(doc中, doc英, code中, code英,
                doc密中, doc密英, code密中, code密英)
    return 1 if _报告(差异, args.quiet) else 0



if __name__ == '__main__':
    sys.exit(main())
