# -*- coding: utf-8 -*-
"""极快包管理 - 安装与裁剪（M8-6）。

安装布局
--------
    项目根/
    ├── 包.json          清单（人写）
    ├── 包.锁            锁文件（工具生成，应提交进版本库）
    └── 极快_包/         依赖安装目录（扁平单副本，应写进 .gitignore）
        ├── 甲/
        └── 乙/

为什么是扁平单副本
------------------
`node_modules` 的嵌套多副本能解决版本冲突，代价是路径爆炸和体积。
极快的模块系统按**模块名**解析（`导入 甲`），本身无法表达「在乙的
上下文里 甲 指另一个版本」，所以嵌套副本在语言层面根本无法生效。
既然如此就老实做扁平，冲突在解析期报错而不是偷偷装两份。

写入策略
--------
每个包先拷进 `极快_包/.tmp-<名称>`，成功后再 `os.replace` 到目标名。
中途失败不会留下半个包目录。旧版本目录在替换前移到 `.old-<名称>`
并在最后清理，避免 Windows 上「目录非空无法替换」。
"""

import os
import shutil
from typing import Dict, List, Optional, Tuple

from .lockfile import Lockfile, save_lockfile
from .manifest import Manifest
from .resolver import ResolvedNode, resolve
from .sources import FetchedSource, compute_checksum

__all__ = [
    'PACKAGES_DIR', 'InstallError', 'InstallReport',
    'packages_dir', 'install', 'uninstall', 'installed_packages',
]

#: 依赖安装目录名。与 `包.json` / `包.锁` 同属中文命名族。
PACKAGES_DIR = '极快_包'

#: 拷贝时跳过的目录：版本库元数据、Python 缓存、嵌套依赖目录。
_SKIP_DIRS = frozenset({'.git', '.hg', '.svn', '__pycache__',
                        PACKAGES_DIR, 'node_modules', '.pytest_cache'})


class InstallError(Exception):
    """安装过程失败。"""


class InstallReport:
    """一次安装的结果摘要，供 CLI 渲染。"""

    __slots__ = ('installed', 'unchanged', 'removed', 'lock_path')

    def __init__(self):
        self.installed: List[Tuple[str, str]] = []   # (名称, 版本)
        self.unchanged: List[Tuple[str, str]] = []
        self.removed: List[str] = []
        self.lock_path: Optional[str] = None

    @property
    def total(self) -> int:
        return len(self.installed) + len(self.unchanged)


def packages_dir(project_root: str) -> str:
    """返回项目的依赖安装目录绝对路径（不保证存在）。"""
    return os.path.join(os.path.abspath(project_root), PACKAGES_DIR)


def installed_packages(project_root: str) -> Dict[str, str]:
    """扫描 `极快_包/`，返回 `名称 -> 版本`。目录不存在时返回空字典。"""
    from .manifest import load_manifest, ManifestError

    base = packages_dir(project_root)
    if not os.path.isdir(base):
        return {}
    result: Dict[str, str] = {}
    for entry in sorted(os.listdir(base)):
        if entry.startswith('.'):        # 跳过 .tmp-* / .old-* 残留
            continue
        full = os.path.join(base, entry)
        if not os.path.isdir(full):
            continue
        try:
            result[entry] = load_manifest(full).version
        except ManifestError:
            # 目录里没有可读清单：当作损坏，报未知版本而不是崩掉整个命令
            result[entry] = '未知'
    return result


def _copy_tree(src: str, dst: str) -> None:
    """拷贝源码树，跳过版本库元数据与缓存目录。"""
    def ignore(_dir, names):
        return {n for n in names if n in _SKIP_DIRS or n.endswith('.tmp')}
    shutil.copytree(src, dst, ignore=ignore, symlinks=False)


def _install_one(node: ResolvedNode, base: str) -> str:
    """把单个包物化到 `极快_包/<名称>`。返回目标目录。"""
    target = os.path.join(base, node.name)
    tmp = os.path.join(base, f'.tmp-{node.name}')
    old = os.path.join(base, f'.old-{node.name}')

    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _copy_tree(node.source.root, tmp)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise InstallError(f'拷贝包 {node.name} 失败：{e}') from None

    try:
        if os.path.isdir(target):
            # 先挪走旧目录再落新目录：Windows 上 os.replace 不能覆盖非空目录
            shutil.rmtree(old, ignore_errors=True)
            os.replace(target, old)
        os.replace(tmp, target)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        if os.path.isdir(old) and not os.path.isdir(target):
            os.replace(old, target)      # 回滚
        raise InstallError(f'安装包 {node.name} 失败：{e}') from None
    finally:
        shutil.rmtree(old, ignore_errors=True)

    return target


def _prune(base: str, keep: set) -> List[str]:
    """删除 `极快_包/` 里不在 `keep` 中的包目录，并清理临时残留。"""
    if not os.path.isdir(base):
        return []
    removed = []
    for entry in sorted(os.listdir(base)):
        full = os.path.join(base, entry)
        if not os.path.isdir(full):
            continue
        if entry.startswith('.tmp-') or entry.startswith('.old-'):
            shutil.rmtree(full, ignore_errors=True)
            continue
        if entry.startswith('.'):
            continue
        if entry not in keep:
            shutil.rmtree(full, ignore_errors=True)
            removed.append(entry)
    return removed


def install(root: Manifest, include_dev: bool = False,
            prune: bool = True) -> InstallReport:
    """解析并安装根清单的全部依赖，写回锁文件。

    `prune=True` 时会清掉 `极快_包/` 里已不再被依赖的包，
    使安装目录与清单保持严格一致（对齐 `npm ci` 而非 `npm install`）。
    """
    project_root = root.root
    base = packages_dir(project_root)
    report = InstallReport()

    cleanup: List[FetchedSource] = []
    try:
        nodes, _top = resolve(root, include_dev=include_dev, cleanup=cleanup)

        if nodes:
            os.makedirs(base, exist_ok=True)

        before = installed_packages(project_root)
        lock = Lockfile(path=os.path.join(project_root, '包.锁'))

        for node in sorted(nodes, key=lambda n: n.name):
            digest, _size = compute_checksum(node.source.root)
            node.checksum = f'sha256:{digest}'

            if before.get(node.name) == node.version:
                # 版本相同也重装：路径依赖的源码可能已被就地改过，
                # 只有重拷才能保证 极快_包/ 反映当前源码。
                _install_one(node, base)
                report.unchanged.append((node.name, node.version))
            else:
                _install_one(node, base)
                report.installed.append((node.name, node.version))

            lock.put(node.to_locked())

        if prune:
            report.removed = _prune(base, {n.name for n in nodes})

        report.lock_path = save_lockfile(lock)
        return report
    finally:
        for src in cleanup:
            # 临时克隆目录：删掉它的父级（tempfile.mkdtemp 建的那层）
            shutil.rmtree(os.path.dirname(src.root), ignore_errors=True)


def uninstall(project_root: str, name: str) -> bool:
    """删除已安装的包目录。返回是否真的删掉了东西。"""
    base = packages_dir(project_root)
    target = os.path.join(base, name)
    # 名称已由 validate_package_name 收敛，这里再兜一层：必须是直接子目录
    if os.path.dirname(os.path.abspath(target)) != os.path.abspath(base):
        raise InstallError(f'非法包名：{name}')
    if not os.path.isdir(target):
        return False
    shutil.rmtree(target, ignore_errors=True)
    return True
