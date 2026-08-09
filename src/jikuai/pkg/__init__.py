# -*- coding: utf-8 -*-
"""极快包管理（M8）。

对外公开的稳定 API：

    from jikuai.pkg import load_manifest, install, packages_dir

分层：
    semver     版本号与约束（无依赖）
    manifest   包.json 读写与校验
    lockfile   包.锁 读写
    sources    路径 / 仓库 / 注册表 三种来源的抓取
    resolver   依赖图遍历 → 安装计划
    installer  物化到 极快_包/ 并写锁文件
    cli        命令行外壳（只做参数解析与输出）

依赖方向严格自上而下，`cli` 之外的模块都不打印任何东西，
便于被 LSP / DAP / 测试以库形式复用。
"""

from .manifest import (
    MANIFEST_NAME, Dependency, Manifest, ManifestError,
    find_manifest, load_manifest, save_manifest, new_manifest,
    validate_package_name,
)
from .lockfile import (
    LOCKFILE_NAME, LOCK_VERSION, LockError, LockedPackage, Lockfile,
    load_lockfile, save_lockfile,
)
from .installer import (
    PACKAGES_DIR, InstallError, InstallReport,
    install, uninstall, installed_packages, packages_dir,
)
from .resolver import ResolveError, resolve
from .sources import SourceError
from .registry import (
    RegistryError, PublishReport,
    registry_root, load_index, publish, lookup,
    list_packages, search, unpublish,
)

__all__ = [
    # manifest
    'MANIFEST_NAME', 'Dependency', 'Manifest', 'ManifestError',
    'find_manifest', 'load_manifest', 'save_manifest', 'new_manifest',
    'validate_package_name',
    # lockfile
    'LOCKFILE_NAME', 'LOCK_VERSION', 'LockError', 'LockedPackage',
    'Lockfile', 'load_lockfile', 'save_lockfile',
    # installer
    'PACKAGES_DIR', 'InstallError', 'InstallReport',
    'install', 'uninstall', 'installed_packages', 'packages_dir',
    # resolver / sources
    'ResolveError', 'resolve', 'SourceError',
    # registry (M11-1)
    'RegistryError', 'PublishReport',
    'registry_root', 'load_index', 'publish', 'lookup',
    'list_packages', 'search', 'unpublish',
]
