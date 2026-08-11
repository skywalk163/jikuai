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


元数据层 / 安装执行层的 import 解耦（v0.18.0 · 集成反馈 4.2）
------------------------------------------------------------
`frontend._collect_import_whitelist()` 在**编译期**就要
`from .pkg.blocks import block_exports`。而 Python 导入子模块必先执行父包
`__init__`，于是「只想跑一个块」的场景被迫把 `sources → resolver → installer
→ registry` 整条安装链也拉起来——`sources` 依赖 git subprocess，在 Pyodide /
嵌入式环境里根本不可用（quye 因此不得不写一个「保留全部公开签名、调用才抛错」
的 `sources.py` 替身）。

因此**安装执行层的名字改成 PEP 562 惰性属性**：`jikuai.pkg.install` 等公开
API 一字不变，但只有真的取用时才 import 对应模块。编译期刚需的
`manifest` / `lockfile` / `blocks` 仍然饿汉加载——它们是纯标准库、无 git。
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
from .blocks import (
    BLOCK_METADATA_NAME, BLOCK_INDEX_NAME, STABILITY_LEVELS,
    BlockMetadata, BlockError,
    load_block_metadata,
    scan_blocks, generate_index,
    check_export_atomicity, check_module_segment_atomicity,
    extract_exports, validate_block,
)

#: 安装执行层的公开名 → 所属模块。取用时才 import（见模块头「import 解耦」）。
#: 注意 `load_index` 刻意映到 `registry`——与既有 `from .registry import load_index`
#: 排在 `from .blocks import ...` 之前的解析结果一致（blocks 的同名函数不在
#: 本包的公开名单里，要用它得走 `jikuai.pkg.blocks.load_index`）。
_LAZY_ATTRS = {
    'PACKAGES_DIR': 'installer',
    'InstallError': 'installer',
    'InstallReport': 'installer',
    'install': 'installer',
    'uninstall': 'installer',
    'installed_packages': 'installer',
    'packages_dir': 'installer',
    'ResolveError': 'resolver',
    'resolve': 'resolver',
    'SourceError': 'sources',
    'RegistryError': 'registry',
    'PublishReport': 'registry',
    'registry_root': 'registry',
    'load_index': 'registry',
    'publish': 'registry',
    'lookup': 'registry',
    'list_packages': 'registry',
    'search': 'registry',
    'unpublish': 'registry',
}


def __getattr__(name):
    """PEP 562 惰性属性：只在真的取用安装执行层时才 import 它。"""
    module_name = _LAZY_ATTRS.get(name)
    if module_name is None:
        raise AttributeError('module %r has no attribute %r' % (__name__, name))
    from importlib import import_module
    value = getattr(import_module('.' + module_name, __name__), name)
    # 缓存进 globals：后续访问直接命中，不再进 __getattr__
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_ATTRS))


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
    # blocks (v0.12.0 · ADR-15)
    'BLOCK_METADATA_NAME', 'BLOCK_INDEX_NAME', 'STABILITY_LEVELS',
    'BlockMetadata', 'BlockError',
    'load_block_metadata',
    'scan_blocks', 'generate_index',
    'check_export_atomicity', 'check_module_segment_atomicity',
    'extract_exports', 'validate_block',
]
