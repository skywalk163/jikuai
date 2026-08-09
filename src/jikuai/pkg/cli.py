# -*- coding: utf-8 -*-
"""极快包管理 - 命令行子命令（M8-7）。

命令表（中文主名 + 英文别名，对齐 pip / npm / cargo 习惯）：

    jk 包 初始化 [名称]         init   —— 在当前目录生成 包.json
    jk 包 添加 <名称> <来源>    add    —— 写入依赖并安装
    jk 包 移除 <名称>          remove —— 删除依赖并卸载
    jk 包 装                   install—— 按清单/锁文件安装全部依赖
    jk 包 列表                 list   —— 列出已安装的包
    jk 包 运行 <脚本名>         run    —— 执行清单「脚本」表里的命令

设计取舍：这一层只做**参数解析 + 人类可读输出**，所有业务逻辑都在
manifest/resolver/installer 里，保证 CLI 可以被测试替换、也可被 LSP/DAP
以库形式复用。所有错误都收敛成返回码（0 成功，非 0 失败）+ stderr 中文
提示，不向上抛裸异常。
"""

import os
import sys
from typing import List, Optional

from .manifest import (
    MANIFEST_NAME, Dependency, ManifestError,
    load_manifest, save_manifest, new_manifest, validate_package_name,
)
from .lockfile import LOCKFILE_NAME
from .installer import (
    PACKAGES_DIR, InstallError, install, uninstall, installed_packages,
)
from .resolver import ResolveError
from . import registry
from . import semver

__all__ = ['main', 'run']

_USAGE = f"""极快包管理 用法：
  jk 包 初始化 [名称]          在当前目录创建 {MANIFEST_NAME}
  jk 包 添加 <名称> [来源]      添加依赖并安装
                               来源可省略（默认 *）、版本约束（^1.0.0）、
                               路径（--路径 ../某目录）、
                               仓库（--仓库 URL [--标签 v1.0.0]）
  jk 包 移除 <名称>            移除依赖并卸载
  jk 包 装 [--含开发]          按清单安装全部依赖
  jk 包 列表                   列出已安装的包
  jk 包 运行 <脚本名>          执行清单「脚本」里的命令
  jk 包 发布 [--确认] [--分类 X] [--允许覆盖]
                               发布当前包到本地注册表。
                               **默认演练**（只体检不落盘），加 --确认 才真发布
  jk 包 搜索 [关键词]          搜索本地注册表里的包
  jk 包 注册表                 显示注册表根目录与统计
  jk 包 帮助                   显示本帮助

英文别名：init / add / remove(rm) / install(i) / list(ls) / run /
          publish / search / registry / help

注册表根目录解析顺序：环境变量 JIKUAI_REGISTRY → ~/.jikuai/注册表
"""

#: 中文命令 -> 规范命令名；英文别名一并归一。
_ALIASES = {
    '初始化': 'init', 'init': 'init',
    '添加': 'add', 'add': 'add',
    '移除': 'remove', '删除': 'remove', 'remove': 'remove', 'rm': 'remove',
    '装': 'install', '安装': 'install', 'install': 'install', 'i': 'install',
    '列表': 'list', 'list': 'list', 'ls': 'list',
    '运行': 'run', 'run': 'run',
    '发布': 'publish', 'publish': 'publish',
    '搜索': 'search', 'search': 'search',
    '注册表': 'registry', 'registry': 'registry',
    '帮助': 'help', 'help': 'help', '-h': 'help', '--help': 'help',
}


def _err(msg: str) -> int:
    print(f'包管理错误：{msg}', file=sys.stderr)
    return 1


def _parse_add_source(rest: List[str]):
    """从 `添加` 的剩余参数里解析出一个 Dependency 的来源部分。

    返回 `(constraint, path, repo, tag)`，四者按来源种类互斥填充。
    支持三种写法：
      添加 甲 ^1.0.0
      添加 甲 --路径 ../甲
      添加 甲 --仓库 https://x.git --标签 v1.0.0
    """
    constraint = path = repo = tag = None
    i = 0
    positional = []
    while i < len(rest):
        tok = rest[i]
        if tok in ('--路径', '--path'):
            i += 1
            if i >= len(rest):
                raise ManifestError('--路径 后面需要跟一个目录')
            path = rest[i]
        elif tok in ('--仓库', '--repo', '--git'):
            i += 1
            if i >= len(rest):
                raise ManifestError('--仓库 后面需要跟一个 URL')
            repo = rest[i]
        elif tok in ('--标签', '--tag'):
            i += 1
            if i >= len(rest):
                raise ManifestError('--标签 后面需要跟一个标签名')
            tag = rest[i]
        else:
            positional.append(tok)
        i += 1

    if positional:
        constraint = positional[0]

    # 校验来源互斥
    kinds = sum(x is not None for x in (constraint, path, repo))
    if kinds > 1:
        raise ManifestError('版本约束 / 路径 / 仓库 三种来源只能给一个')
    if tag is not None and repo is None:
        raise ManifestError('--标签 只能与 --仓库 一起用')
    if constraint is not None:
        semver.parse_constraint(constraint)   # 提前校验
    return constraint, path, repo, tag


# ---- 各子命令 ---------------------------------------------------------

def _cmd_init(args: List[str]) -> int:
    cwd = os.getcwd()
    target = os.path.join(cwd, MANIFEST_NAME)
    if os.path.exists(target):
        return _err(f'当前目录已存在 {MANIFEST_NAME}，不覆盖')
    name = args[0] if args else os.path.basename(os.path.abspath(cwd))
    try:
        validate_package_name(name)
        manifest = new_manifest(name)
        path = save_manifest(manifest, target)
    except ManifestError as e:
        return _err(str(e))
    print(f'已创建 {path}')
    print(f'  名称：{manifest.name}')
    print(f'  版本：{manifest.version}')
    print(f'  入口：{manifest.entry}')
    _ensure_gitignore(cwd)
    return 0


def _ensure_gitignore(project_root: str) -> None:
    """把 极快_包/ 追加进 .gitignore（若存在 git 仓库且尚未忽略）。"""
    gi = os.path.join(project_root, '.gitignore')
    entry = f'{PACKAGES_DIR}/'
    try:
        existing = ''
        if os.path.isfile(gi):
            with open(gi, 'r', encoding='utf-8') as f:
                existing = f.read()
            if entry in existing.split():
                return
        with open(gi, 'a', encoding='utf-8', newline='\n') as f:
            if existing and not existing.endswith('\n'):
                f.write('\n')
            f.write(f'{entry}\n')
    except OSError:
        pass          # .gitignore 维护是锦上添花，失败不影响主流程


def _cmd_add(args: List[str]) -> int:
    if not args:
        return _err('用法：jk 包 添加 <名称> [来源]')
    name = args[0]
    try:
        validate_package_name(name)
        constraint, path, repo, tag = _parse_add_source(args[1:])
        manifest = load_manifest()
        dep = Dependency(name, constraint=constraint, path=path,
                         repo=repo, tag=tag)
        manifest.add_dependency(dep)
        report = install(manifest)          # 先装，装成功再落盘清单
        save_manifest(manifest)
    except (ManifestError, ResolveError, InstallError,
            semver.InvalidConstraint, semver.InvalidVersion) as e:
        return _err(str(e))
    print(f'已添加依赖 {name}（{dep.kind}）')
    _print_install_report(report)
    return 0


def _cmd_remove(args: List[str]) -> int:
    if not args:
        return _err('用法：jk 包 移除 <名称>')
    name = args[0]
    try:
        manifest = load_manifest()
        removed = manifest.remove_dependency(name)
        if not removed:
            return _err(f'清单里没有依赖 {name}')
        save_manifest(manifest)
        install(manifest)                   # 重解析并裁掉不再需要的包
        uninstall(manifest.root, name)
    except (ManifestError, ResolveError, InstallError) as e:
        return _err(str(e))
    print(f'已移除依赖 {name}')
    return 0


def _cmd_install(args: List[str]) -> int:
    include_dev = ('--含开发' in args or '--dev' in args)
    try:
        manifest = load_manifest()
        report = install(manifest, include_dev=include_dev)
    except (ManifestError, ResolveError, InstallError) as e:
        return _err(str(e))
    if report.total == 0 and not report.removed:
        print('没有依赖需要安装')
        return 0
    print(f'已安装 {report.total} 个包（含开发依赖）' if include_dev
          else f'已安装 {report.total} 个包')
    _print_install_report(report)
    return 0


def _cmd_list(_args: List[str]) -> int:
    try:
        manifest = load_manifest()
    except ManifestError as e:
        return _err(str(e))
    installed = installed_packages(manifest.root)
    declared = manifest.dependencies(include_dev=True)
    print(f'{manifest.name}@{manifest.version} 的依赖：')
    if not declared:
        print('  （无）')
        return 0
    for name in sorted(declared):
        dep = declared[name]
        ver = installed.get(name)
        mark = '开发' if dep.dev else '运行'
        if ver is None:
            status = '未安装'
        elif ver == '未知':
            status = '已安装(版本未知)'
        else:
            status = f'v{ver}'
        print(f'  [{mark}] {name}  {dep.to_spec()!r}  → {status}')
    return 0


def _cmd_run(args: List[str]) -> int:
    if not args:
        return _err('用法：jk 包 运行 <脚本名>')
    script_name = args[0]
    try:
        manifest = load_manifest()
    except ManifestError as e:
        return _err(str(e))
    scripts = manifest.scripts
    if script_name not in scripts:
        avail = '、'.join(sorted(scripts)) or '（无）'
        return _err(f'清单「脚本」里没有 {script_name}；可用脚本：{avail}')

    import subprocess
    command = scripts[script_name]
    print(f'> {command}')
    # 脚本命令是清单作者自己写的、可信内容，用 shell 执行以支持管道等写法；
    # 工作目录切到项目根，让脚本里的相对路径符合直觉。
    try:
        completed = subprocess.run(command, shell=True, cwd=manifest.root)
    except OSError as e:
        return _err(f'脚本执行失败：{e}')
    return completed.returncode


def _cmd_publish(args: List[str]) -> int:
    confirm = ('--确认' in args or '--confirm' in args)
    allow_overwrite = ('--允许覆盖' in args or '--overwrite' in args)
    category = None
    i = 0
    while i < len(args):
        if args[i] in ('--分类', '--category'):
            i += 1
            if i >= len(args):
                return _err('--分类 后面需要跟一个分类名')
            category = args[i]
        i += 1
    try:
        manifest = load_manifest()
        report = registry.publish(
            manifest, category=category,
            dry_run=not confirm, allow_overwrite=allow_overwrite)
    except (ManifestError, registry.RegistryError) as e:
        return _err(str(e))
    for w in report.warnings:
        print(f'  ⚠ {w}')
    if report.dry_run:
        print(f'[演练] {report.name}@{report.version}（分类：{report.category}）')
        print(f'  文件数：{report.file_count}  校验和：{report.checksum[:12]}…')
        print('  演练完成，未落盘。确认无误后加 --确认 正式发布。')
    else:
        verb = '已覆盖发布' if report.overwritten else '已发布'
        print(f'{verb} {report.name}@{report.version}（分类：{report.category}）')
        print(f'  文件数：{report.file_count}  校验和：{report.checksum[:12]}…')
        print(f'  快照：{report.target}')
    return 0


def _cmd_search(args: List[str]) -> int:
    keyword = args[0] if args else ''
    try:
        results = registry.search(keyword)
    except registry.RegistryError as e:
        return _err(str(e))
    if not results:
        where = f'包含 {keyword!r} 的' if keyword else ''
        print(f'注册表里没有{where}包')
        return 0
    print(f'找到 {len(results)} 个包：')
    for r in results:
        desc = r['描述'] or '（无描述）'
        print(f"  {r['名称']}@{r['最新版本']}  [{r['分类']}]  {desc}")
    return 0


def _cmd_registry(_args: List[str]) -> int:
    try:
        index = registry.load_index()
    except registry.RegistryError as e:
        return _err(str(e))
    root = registry.registry_root()
    stats = index.get('统计', {})
    print(f'注册表根目录：{root}')
    print(f"  总包数：{stats.get('总包数', 0)}")
    print(f"  总版本数：{stats.get('总版本数', 0)}")
    return 0


def _print_install_report(report) -> None:
    for name, ver in report.installed:
        print(f'  + {name}@{ver}')
    for name, ver in report.unchanged:
        print(f'  = {name}@{ver}')
    for name in report.removed:
        print(f'  - {name}（已移除）')
    if report.lock_path:
        print(f'  锁文件：{os.path.basename(report.lock_path)}')


_DISPATCH = {
    'init': _cmd_init,
    'add': _cmd_add,
    'remove': _cmd_remove,
    'install': _cmd_install,
    'list': _cmd_list,
    'run': _cmd_run,
    'publish': _cmd_publish,
    'search': _cmd_search,
    'registry': _cmd_registry,
}


def run(argv: Optional[List[str]] = None) -> int:
    """包管理子命令入口。`argv` 是 `包` 之后的参数列表。"""
    argv = list(sys.argv[2:] if argv is None else argv)
    if not argv:
        print(_USAGE)
        return 0
    raw = argv[0]
    command = _ALIASES.get(raw)
    if command is None:
        print(f'未知的包管理命令：{raw}\n', file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return 1
    if command == 'help':
        print(_USAGE)
        return 0
    return _DISPATCH[command](argv[1:])


def main() -> None:
    """独立入口（`python -m jikuai.pkg`）。"""
    sys.exit(run(sys.argv[1:]))


if __name__ == '__main__':
    main()
