#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""包签名/信任的环境变量与文档同步 CI 门禁 G18（v0.20.0 · W76）。

**为什么**：W74/W75 加了 `JIKUAI_KEY_ROOT` / `JIKUAI_TRUST_ROOT` /
`JIKUAI_TRUSTED_SIGNERS` 三个新环境变量控制签名与信任行为。G17 只锁
CLI 子命令名 ↔ 命令表，环境变量属于用户界面，同样要防止「代码加了但文档
没写」（用户不知道怎么开关白名单）与「文档写了但代码没读」（撤了实现却
留着承诺）两类漂移。

**做法**：
- code 侧：扫描 `keys.py` / `trust.py` / `installer.py` / `sources.py`，
  收所有形如 `JIKUAI_*` 的字符串常量赋值（含模块级 `X_ENV = 'JIKUAI_...'`
  这种间接形式）。
- doc 侧：`docs/包管理.md` 全文里出现的 `JIKUAI_*` token。
- 双向 diff，任一方缺就红。

**不学 G13+ 静默跳过**：G17/G18 是硬门禁，读文件失败 = 门禁自己坏了，报错退出。

用法与退出码同 G17。
"""

import argparse
import ast
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
_DOC_PATH = os.path.join(_REPO_ROOT, 'docs', '包管理.md')
_ENV_TOKEN = re.compile(r'JIKUAI_[A-Z_]+')

#: 扫描范围 = `docs/包管理.md` 会谈到的所有环境变量的定义处。
#: 不止签名/信任三个：`JIKUAI_PATH`（module_loader 模块搜索路径）与
#: `JIKUAI_PKG_ROOTS`（blocks 第三方块根）也在这篇文档里承诺过，纳进来
#: 门禁才闭合——否则「文档写了但代码没引用」这一侧会被这两个常驻误报淹掉，
#: 久了就会有人加 `--quiet` 把门禁哑掉。
#:
#: v0.21.0 W93 追加：`tools/registry-server/server.py` 的 `JIKUAI_REGISTRY_SERVER_*`
#: 也算「面向用户的运维界面」，包管理文档承诺过就要同步——门禁的边界不看
#: 目录属主（src/ vs tools/），看的是文档承诺过没有。
_CODE_PATHS = [
    os.path.join(_REPO_ROOT, 'src', 'jikuai', 'pkg', 'keys.py'),
    os.path.join(_REPO_ROOT, 'src', 'jikuai', 'pkg', 'trust.py'),
    os.path.join(_REPO_ROOT, 'src', 'jikuai', 'pkg', 'sources.py'),
    os.path.join(_REPO_ROOT, 'src', 'jikuai', 'pkg', 'registry.py'),
    os.path.join(_REPO_ROOT, 'src', 'jikuai', 'pkg', 'installer.py'),
    os.path.join(_REPO_ROOT, 'src', 'jikuai', 'pkg', 'backend.py'),
    os.path.join(_REPO_ROOT, 'src', 'jikuai', 'pkg', 'blocks.py'),
    os.path.join(_REPO_ROOT, 'src', 'jikuai', 'module_loader.py'),
    os.path.join(_REPO_ROOT, 'tools', 'registry-server', 'server.py'),
]


def _读文件(path):
    if not os.path.isfile(path):
        print('错误：文件不存在：%s' % path, file=sys.stderr)
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _code环境变量():
    """收源码里所有 `JIKUAI_...` 字符串字面量。"""
    envs = set()
    for path in _CODE_PATHS:
        src = _读文件(path)
        if src is None:
            return None
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            print('错误：%s 语法错：%s' % (path, e), file=sys.stderr)
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for m in _ENV_TOKEN.finditer(node.value):
                    envs.add(m.group(0))
    return envs


def _doc环境变量(doc文本):
    return set(_ENV_TOKEN.findall(doc文本))


def _报告(only_code, only_doc, quiet):
    if not only_code and not only_doc:
        if not quiet:
            print('G18 通过：签名/信任环境变量与文档一致')
        return False
    print('错误：G18 环境变量文档同步失败', file=sys.stderr)
    for x in sorted(only_code):
        print('  - 代码里用了但文档没写：%s' % x, file=sys.stderr)
    for x in sorted(only_doc):
        print('  - 文档写了但代码没引用：%s' % x, file=sys.stderr)
    print('  修复：更新 docs/包管理.md，'
          '或调整 src/jikuai/pkg/{keys,trust,...}.py 的环境变量常量（择一）',
          file=sys.stderr)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='check_pkg_env_doc',
        description='CI 门禁 G18：包签名/信任环境变量 ↔ docs/包管理.md 一致性')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    doc文本 = _读文件(_DOC_PATH)
    code环境变量 = _code环境变量()
    if doc文本 is None or code环境变量 is None:
        return 1

    doc环境变量 = _doc环境变量(doc文本)
    only_code = code环境变量 - doc环境变量
    only_doc = doc环境变量 - code环境变量
    return 1 if _报告(only_code, only_doc, args.quiet) else 0


if __name__ == '__main__':
    sys.exit(main())
