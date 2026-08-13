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
from . import keys
from . import trust
from .backend import is_remote


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
  jk 包 发布 [--确认] [--分类 X] [--允许覆盖] [--签名 别名]
                               发布当前包到注册表。`JIKUAI_REGISTRY` 决定目标：
                               本地路径 → 落到本地注册表；`https://...` → 走
                               远程发布（ADR-35，强制 --签名，不接受 --允许覆盖）。
                               **默认演练**（只体检不落盘/推送），加 --确认 才真发布
  jk 包 搜索 [关键词]          搜索本地注册表里的包
  jk 包 注册表                 显示注册表根目录与统计
  jk 包 密钥 生成 <别名>       生成 Ed25519 签名密钥对
  jk 包 密钥 列表              列出本机已有的签名密钥
  jk 包 密钥 导出 <别名>       打印 base64 公钥（交给注册表管理员）
  jk 包 密钥 信任 <别名> <公钥> 把一把公钥追加进本地信任库（密钥轮换，ADR-36）
  jk 包 密钥 撤信 <别名> <公钥> 从本地信任库移除一把公钥（密钥泄露时用）
  jk 包 帮助                   显示本帮助


英文别名：init / add / remove(rm) / install(i) / list(ls) / run /
          publish / search / registry / key / help

注册表根目录解析顺序：环境变量 JIKUAI_REGISTRY → ~/.jikuai/注册表
密钥根目录解析顺序：  环境变量 JIKUAI_KEY_ROOT → ~/.jikuai/密钥
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
    '密钥': 'key', 'key': 'key',
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
        # 重解析并裁掉不再需要的包。报告不能丢：移除一个依赖会触发对**其余**
        # 依赖的重装，未签名告警在这条路径上同样该出（v0.20.0 W76 补，W75 漏）。
        报告 = install(manifest)
        uninstall(manifest.root, name)
    except (ManifestError, ResolveError, InstallError) as e:
        return _err(str(e))
    print(f'已移除依赖 {name}')
    _print_install_warnings(报告)
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
    signer = None
    i = 0
    while i < len(args):
        if args[i] in ('--分类', '--category'):
            i += 1
            if i >= len(args):
                return _err('--分类 后面需要跟一个分类名')
            category = args[i]
        elif args[i] in ('--签名', '--sign'):
            i += 1
            if i >= len(args):
                return _err('--签名 后面需要跟一个密钥别名（见 jk 包 密钥 列表）')
            signer = args[i]
        i += 1
    try:
        manifest = load_manifest()
        report = registry.publish(
            manifest, category=category, dry_run=not confirm,
            allow_overwrite=allow_overwrite, signer=signer)
    except (ManifestError, registry.RegistryError) as e:
        return _err(str(e))
    except (ValueError, FileNotFoundError) as e:
        # keys 层的别名不合法 / 私钥缺失
        return _err(str(e))
    for w in report.warnings:
        print(f'  ⚠ {w}')
    远程 = is_remote(registry.registry_root())
    目标名 = '远程注册表' if 远程 else '本地注册表'
    if report.dry_run:
        print(f'[演练] {report.name}@{report.version}（分类：{report.category}）→ {目标名}')
        print(f'  文件数：{report.file_count}  校验和：{report.checksum[:12]}…')
        if report.signature:
            print(f'  签名者：{report.signer}  签名：{report.signature[:12]}…')
        print('  演练完成，未落盘/未推送。确认无误后加 --确认 正式发布。')
    else:
        verb = '已覆盖发布' if report.overwritten else '已发布'
        print(f'{verb} {report.name}@{report.version}（分类：{report.category}）→ {目标名}')
        print(f'  文件数：{report.file_count}  校验和：{report.checksum[:12]}…')
        if report.signature:
            print(f'  签名者：{report.signer}  签名：{report.signature[:12]}…')
        else:
            print('  ⚠ 未签名发布。v0.21.0 起装未签名包会被拒，'
                  '建议加 --签名 <别名>')
        print(f'  {"远端" if 远程 else "快照"}：{report.target}')
    return 0


def _cmd_key(args: List[str]) -> int:
    """`jk 包 密钥 生成/列表/导出`（ADR-33 §2.6）。

    子子命令而不是三个顶层命令：密钥操作是同一件事的三个面，挂在 `密钥`
    下比 `生成密钥`/`列密钥`/`导出密钥` 三个顶层名更好记，也给未来的
    `密钥 删除`/`密钥 信任` 留了位置。

    **try 只包 keys 调用、不包 print**：`UnicodeEncodeError` 是 `ValueError`
    子类，把 print 一起包进去会把「控制台编码写不出」误报成「密钥出错」。
    """
    if not args:
        return _err('密钥 需要一个子命令：生成 / 列表 / 导出')
    sub = args[0]
    rest = args[1:]

    if sub in ('生成', 'generate', 'gen', 'new'):
        if not rest:
            return _err('密钥 生成 需要一个别名，例如：jk 包 密钥 生成 甲')
        try:
            pub = keys.generate_keypair(rest[0])
        except (ValueError, OSError) as e:   # FileExistsError 也是 OSError
            return _err(str(e))
        print(f'已生成密钥对「{rest[0]}」')
        print(f'  密钥根：{keys.key_root()}')
        print(f'  公钥：{pub}')
        print('  注意：私钥请勿提交进版本库、勿外发；泄露后只能换别名重发。')
    elif sub in ('列表', 'list', 'ls'):
        try:
            rows = keys.list_keys()
        except OSError as e:
            return _err(str(e))
        if not rows:
            print(f'密钥根 {keys.key_root()} 下没有任何密钥。'
                  f'用 jk 包 密钥 生成 <别名> 建一个。')
            return 0
        print(f'密钥根：{keys.key_root()}')
        for alias, has_sk, has_pk in rows:
            if has_sk and has_pk:
                kind = '可签名'
            elif has_pk:
                kind = '仅公钥（只能验签）'
            else:
                kind = '仅私钥（公钥缺失，需重新生成）'
            print(f'  {alias}  [{kind}]')
    elif sub in ('导出', 'export'):
        if not rest:
            return _err('密钥 导出 需要一个别名，例如：jk 包 密钥 导出 甲')
        try:
            b64 = keys.export_public_key_b64(rest[0])
        except (ValueError, OSError) as e:
            return _err(str(e))
        print(b64)
    elif sub in ('信任', 'trust'):
        # ADR-36 §2.4：密钥轮换的显式动作——把一把公钥追加进本地信任库。
        # 追加而非替换：旧公钥留着，用旧钥签的老包不受影响。
        if len(rest) < 2:
            return _err('密钥 信任 需要别名与公钥，例如：'
                        'jk 包 密钥 信任 甲 <base64公钥>')
        alias, pub_b64 = rest[0], rest[1]
        try:
            新增 = trust.trust_key(alias, pub_b64)
        except (ValueError, trust.TrustError, OSError) as e:
            return _err(str(e))
        if 新增:
            print(f'已把公钥追加进「{alias}」的信任列表')
            print(f'  信任库根：{trust.trust_root()}')
            print(f'  当前受信公钥数：{len(trust.pinned_keys(alias))}')
            print('  旧公钥保留，用旧钥签的老包不受影响。')
        else:
            print(f'该公钥已在「{alias}」的信任列表里，无需重复添加')
    elif sub in ('撤信', 'untrust', 'revoke'):
        # 密钥泄露时用：从信任列表摘掉一把公钥。撤到一把不剩则回到未建立信任。
        if len(rest) < 2:
            return _err('密钥 撤信 需要别名与公钥，例如：'
                        'jk 包 密钥 撤信 甲 <base64公钥>')
        alias, pub_b64 = rest[0], rest[1]
        try:
            删了 = trust.untrust_key(alias, pub_b64)
        except (ValueError, trust.TrustError, OSError) as e:
            return _err(str(e))
        if 删了:
            剩余 = len(trust.pinned_keys(alias))
            print(f'已从「{alias}」的信任列表移除该公钥')
            print(f'  剩余受信公钥数：{剩余}')
            if 剩余 == 0:
                print('  信任列表已空，下次装该签名者的包会重新走首次信任（TOFU）。')
        else:
            print(f'「{alias}」的信任列表里没有这把公钥')
    else:
        return _err(f'未知的密钥子命令：{sub}'
                    f'（可用：生成 / 列表 / 导出 / 信任 / 撤信）')
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
    _print_install_warnings(report)


def _print_install_warnings(report) -> None:
    """把安装告警打到 stderr（v0.20.0 W75：未签名包过渡期告警）。

    走 stderr 而不是 stdout：告警不属于「装了哪些包」这份可被脚本消费的
    输出，混进去会让 `jk 包 装 | grep` 之类的管道被污染。
    """
    for w in getattr(report, 'warnings', ()):
        print(f'  ⚠ {w}', file=sys.stderr)


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
    'key': _cmd_key,
}


def run(argv: Optional[List[str]] = None) -> int:
    """包管理子命令入口。`argv` 是 `包` 之后的参数列表。"""
    # Windows 控制台默认 GBK，输出里的 `⚠` 会 UnicodeEncodeError——而这条
    # 异常是 ValueError 子类，会被下游的 except 误当成业务错误报出来
    # （v0.20.0 W74 实测：`密钥 生成` 成功却报「包管理错误」）。
    # `blocks_cli` 早就这么做了，这里补齐同一处理。
    from .blocks_cli import _reconfigure_utf8
    _reconfigure_utf8(sys.stdout)
    _reconfigure_utf8(sys.stderr)
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
