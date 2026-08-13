# -*- coding: utf-8 -*-
"""G19 · 安全审计不变量门禁（v0.21.0 M22 W87）。

W86 修复的三条 defense-in-depth 不变量必须**在代码里长在原处**——回归测试能证
明「当前实现是对的」，但删掉常量或换成裸 `resp.read()` 会让测试的反例造不出来
（例如把 `_MAX_MEMBERS` 设成 `10**9`，测试仍可能因造大归档过慢而跳过）。这里
补一层静态断言：常量存在、类型对、值为正、被指定的调用点确实调用。

沿 G16/G17 的思路：新门禁**不**用 `except → 跳过`。解析不到就是它自己坏了，
静默跳过等于形同虚设。

用法：
    python scripts/check_security_invariants.py            # 全绿返回 0
    python scripts/check_security_invariants.py --quiet    # 静默返回码
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, '..'))
SRC_PATH = os.path.join(REPO_ROOT, 'src')
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

# 强制 UTF-8 输出：Windows GBK 会把中文报告吞掉
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass


def _问题(rule, msg):
    return {'规则': rule, '描述': msg}


def _check_block_roots_reader():
    """G19a：`.块根.json` 读侧必须走 `安全块根路径`。

    - `installer` 模块必须有 `安全块根路径` 可调用
    - `read_block_roots_index` 源码里必须出现对 `安全块根路径` 的调用
    - `module_loader._block_root_parents` 源码里必须出现 `commonpath` 校验
      （不 import pkg，只能内联同规则；用 AST 检查关键调用是否在里面）
    """
    problems = []
    from jikuai.pkg import installer
    if not callable(getattr(installer, '安全块根路径', None)):
        problems.append(_问题('G19a', 'installer.安全块根路径 缺失或不可调用'))
    src = _读源码('jikuai/pkg/installer.py')
    if '安全块根路径(' not in src:
        problems.append(_问题('G19a',
            'installer.read_block_roots_index 未走 安全块根路径() —— '
            '读侧路径校验被绕过，M23 开写端后是攻击面'))

    ml_src = _读源码('jikuai/module_loader.py')
    if 'commonpath' not in ml_src or 'base_abs' not in ml_src:
        problems.append(_问题('G19a',
            'module_loader._block_root_parents 缺少 commonpath 前缀归属校验；'
            '不 import pkg 是刻意的（核心加载路径独立），但校验必须内联'))
    return problems


def _check_extract_size_limits():
    """G19b：`_safe_extract_targz` 必须有三条解压上限。"""
    problems = []
    from jikuai.pkg import sources
    需要的 = [
        ('_MAX_MEMBERS', 1),
        ('_MAX_MEMBER_BYTES', 1024),   # 至少 1 KiB，防被改成 1 字节让门禁误绿
        ('_MAX_TOTAL_BYTES', 1024),
    ]
    for 名, 下限 in 需要的:
        v = getattr(sources, 名, None)
        if not isinstance(v, int) or v < 下限:
            problems.append(_问题('G19b',
                f'sources.{名} 缺失或不合理（要求 int 且 ≥ {下限}，实际 {v!r}）'))
    # 三条上限必须在 _safe_extract_targz 里被使用（不只是声明）
    src = _读源码('jikuai/pkg/sources.py')
    func_src = _提取函数(src, '_safe_extract_targz')
    if func_src is None:
        problems.append(_问题('G19b', 'sources._safe_extract_targz 定义缺失'))
    else:
        for token, 释义 in [('_MEMBERS_ENV', '成员数上限'),
                            ('_MEMBER_BYTES_ENV', '单成员上限'),
                            ('_TOTAL_BYTES_ENV', '合计上限')]:
            if token not in func_src:
                problems.append(_问题('G19b',
                    f'_safe_extract_targz 未使用 {token}（{释义} 未生效）'))
    return problems


def _check_http_response_cap():
    """G19c：`HttpBackend._request` 必须做分块读 + 上限。"""
    problems = []
    from jikuai.pkg import backend
    max_resp = getattr(backend, '_MAX_RESPONSE_BYTES', None)
    if not isinstance(max_resp, int) or max_resp < 1024 * 1024:
        problems.append(_问题('G19c',
            f'backend._MAX_RESPONSE_BYTES 缺失或不合理'
            f'（要求 int 且 ≥ 1 MiB，实际 {max_resp!r}）'))

    src = _读源码('jikuai/pkg/backend.py')
    # HttpBackend.__slots__ 必须含 `_max_response`
    if "'_max_response'" not in src:
        problems.append(_问题('G19c',
            "HttpBackend.__slots__ 缺 '_max_response'"))
    # _request 必须**分块读**（`resp.read(<正整数>)`），不是裸 `resp.read()`
    func_src = _提取函数(src, '_request')
    if func_src is None:
        problems.append(_问题('G19c', 'HttpBackend._request 定义缺失'))
    else:
        # 关键：允许 resp.read(65536) 之类的带参调用，不允许纯 resp.read()
        import re
        if re.search(r'resp\.read\(\s*\)', func_src):
            problems.append(_问题('G19c',
                '_request 里出现裸 resp.read() —— 无上限读完整响应，'
                '恶意注册表可 OOM 客户端；应分块读并累计比 _max_response'))
        if 'self._max_response' not in func_src:
            problems.append(_问题('G19c',
                '_request 未引用 self._max_response —— 上限声明了但没用上'))
    return problems


def _读源码(rel_path):
    path = os.path.join(SRC_PATH, rel_path)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _提取函数(源码, 函数名):
    """从 Python 源码里抠出指定函数（含嵌套 def / 方法）的源码文本。"""
    tree = ast.parse(源码)
    lines = 源码.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == 函数名:
                start = node.lineno - 1
                end = node.end_lineno
                return '\n'.join(lines[start:end])
    return None


def build_report():
    problems = []
    problems += _check_block_roots_reader()
    problems += _check_extract_size_limits()
    problems += _check_http_response_cap()
    return {'ok': not problems, '问题': problems}


def main(argv=None):
    """入参约定与 G16/G17 一致：`argv` 是**不含程序名**的参数列表。

    W89 修回归时发现的坑：原实现取 `argv[1:]`（sys.argv 风格），而
    `check_stdlib_contract.py` 按兄弟门禁的惯例传 `["--quiet"]`，切片后成空表，
    `--quiet` 静默失效 —— G19 于是在 `--json` 汇总模式下往 stdout 打了一行中文，
    把 `test_契约脚本_json_输出可解析` 打爆。约定统一成「不含程序名」。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    quiet = '--quiet' in args
    report = build_report()
    if not report['ok']:
        if not quiet:
            print('G19 安全不变量校验失败（%d 处）：' % len(report['问题']))
            for 条 in report['问题']:
                print('  - [%s] %s' % (条['规则'], 条['描述']))
        return 1
    if not quiet:
        print('G19 安全不变量：a/b/c 三条全绿')
    return 0


if __name__ == '__main__':
    sys.exit(main())
