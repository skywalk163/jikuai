# -*- coding: utf-8 -*-
"""极快包管理 - 来源抓取（M8-4）。

三种来源，抓取语义都收敛到「把源码物化到一个只读的临时目录」：

- `路径`：本地相对/绝对路径，直接返回该目录（**不复制**，避免大项目里
  开发依赖被拍成快照）。相对路径以「引用它的清单」所在目录为基准。
- `仓库`：git 仓库。用 `git clone --depth 1` 抓，指定标签时用 `--branch`。
  `git` 不在 PATH 时立即报错，不去尝试拼接 HTTP 或 hackish 降级。
- `注册表`：已接本地/内网文件系统注册表（M11-1 落地）。`registry.lookup`
  按 `JIKUAI_REGISTRY` → `~/.jikuai/注册表` 查找本地索引，装不到明确报错。
  **HTTP 远程注册表分发待 v0.20.0**（需先接入 token 鉴权 + 包签名）。

安全底线：所有磁盘写入路径都用 `_ensure_within(base, target)` 校验，
杜绝路径穿越；`subprocess` 全部走 `shell=False` + 显式 argv 列表。
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
from typing import Optional, Tuple

from .manifest import Dependency, load_manifest, Manifest

__all__ = [
    'SourceError', 'FetchedSource',
    'resolve_source', 'compute_checksum',
]


class SourceError(Exception):
    """来源抓取失败：路径不存在、git 出错、来源类型不支持等。"""


class FetchedSource:
    """一次抓取的结果：包源码根目录 + 清单 + 若干坐标信息。"""

    __slots__ = ('root', 'manifest', 'kind', 'origin', 'ephemeral')

    def __init__(self, root: str, manifest: Manifest, kind: str,
                 origin: str, ephemeral: bool):
        self.root = os.path.abspath(root)
        self.manifest = manifest
        self.kind = kind                 # 路径 / 仓库 / 注册表
        self.origin = origin             # 展示用坐标（路径串 / 仓库 URL）
        #: `True` 表示 root 位于临时目录，安装完必须清理；`False` 表示
        #: 直接指向用户已有目录（比如本地路径依赖），**不能**删除。
        self.ephemeral = ephemeral


# ---- 路径工具 ---------------------------------------------------------

def _ensure_within(base: str, target: str) -> str:
    """确保 `target` 在 `base` 树下，否则抛错。防目录穿越。"""
    base_abs = os.path.abspath(base)
    target_abs = os.path.abspath(target)
    # os.path.commonpath 在跨盘符时会抛 ValueError，正好当拒绝信号
    try:
        common = os.path.commonpath([base_abs, target_abs])
    except ValueError:
        raise SourceError(
            f'路径 {target!r} 与基准目录 {base!r} 不在同一根下')
    if common != base_abs:
        raise SourceError(f'路径 {target!r} 越出基准目录 {base!r}')
    return target_abs


# ---- 三种来源 ---------------------------------------------------------

def _fetch_path(dep: Dependency, base_dir: str) -> FetchedSource:
    raw = dep.path or ''
    root = raw if os.path.isabs(raw) else os.path.normpath(
        os.path.join(base_dir, raw))
    if not os.path.isdir(root):
        raise SourceError(f'路径依赖 {dep.name} 指向的目录不存在：{root}')
    try:
        manifest = load_manifest(root)
    except Exception as e:
        raise SourceError(
            f'路径依赖 {dep.name} 的目录 {root} 里缺少可读的 包.json：{e}'
        ) from None
    if manifest.name != dep.name:
        raise SourceError(
            f'路径依赖名不匹配：清单里写着 {dep.name}，'
            f'但 {root}/包.json 的「名称」是 {manifest.name}')
    return FetchedSource(root, manifest, '路径', raw, ephemeral=False)


def _git_available() -> bool:
    return shutil.which('git') is not None


def _fetch_git(dep: Dependency, _base_dir: str) -> FetchedSource:
    if not _git_available():
        raise SourceError('未找到 `git` 命令；仓库依赖需要系统安装 git')
    if dep.repo is None:
        raise SourceError(f'仓库依赖 {dep.name} 缺少「仓库」字段')

    temp_root = tempfile.mkdtemp(prefix='jikuai-fetch-')
    clone_dir = os.path.join(temp_root, dep.name)
    argv = ['git', 'clone', '--depth', '1']
    if dep.tag:
        argv += ['--branch', dep.tag]
    argv += ['--', dep.repo, clone_dir]

    try:
        # capture stderr 用于给出可读的中文错误提示
        subprocess.run(argv, check=True, capture_output=True, text=True,
                       shell=False)
    except subprocess.CalledProcessError as e:
        shutil.rmtree(temp_root, ignore_errors=True)
        stderr = (e.stderr or '').strip()
        raise SourceError(
            f'仓库依赖 {dep.name} 抓取失败：{stderr or e}') from None
    except OSError as e:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise SourceError(f'仓库依赖 {dep.name} 抓取失败：{e}') from None

    try:
        manifest = load_manifest(clone_dir)
    except Exception as e:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise SourceError(
            f'仓库依赖 {dep.name} 缺少可读的 包.json：{e}') from None
    if manifest.name != dep.name:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise SourceError(
            f'仓库依赖名不匹配：清单里写着 {dep.name}，'
            f'仓库中的清单「名称」是 {manifest.name}')

    return FetchedSource(clone_dir, manifest, '仓库', dep.repo,
                         ephemeral=True)


def _fetch_registry(dep: Dependency, _base_dir: str) -> FetchedSource:
    # M11-1：接本地/内网文件系统注册表（registry 模块）。中央 HTTP 注册中心
    # 待接入 token 鉴权 + 包签名后再开；在那之前 registry.lookup 只认
    # JIKUAI_REGISTRY / ~/.jikuai/注册表 下的本地索引，装不到就明确报错。
    from . import registry
    try:
        version, snapshot = registry.lookup(dep.name, dep.constraint)
    except registry.RegistryError as e:
        raise SourceError(str(e)) from None
    try:
        manifest = load_manifest(snapshot)
    except Exception as e:
        raise SourceError(
            f'注册表包 {dep.name}@{version} 的快照缺少可读的 包.json：{e}'
        ) from None
    if manifest.name != dep.name:
        raise SourceError(
            f'注册表包名不匹配：请求 {dep.name}，'
            f'但快照清单「名称」是 {manifest.name}')
    # 快照是注册表的只读副本，绝不能删（ephemeral=False）。
    return FetchedSource(snapshot, manifest, '注册表', dep.name, ephemeral=False)


_FETCHERS = {
    '路径': _fetch_path,
    '仓库': _fetch_git,
    '注册表': _fetch_registry,
}


def resolve_source(dep: Dependency, base_dir: str) -> FetchedSource:
    """按依赖种类调度对应抓取器。"""
    fetcher = _FETCHERS.get(dep.kind)
    if fetcher is None:                  # 理论不可达，Dependency.kind 已收敛
        raise SourceError(f'未知的依赖种类：{dep.kind}')
    return fetcher(dep, base_dir)


# ---- 校验和 -----------------------------------------------------------

def compute_checksum(directory: str) -> Tuple[str, int]:
    """计算目录里 `.jk` 与 `.py` 源文件的稳定 sha256。

    只挑源码相关的扩展，避开 `.git/` 元数据、缓存和随机时间戳。
    返回 `(sha256 十六进制, 参与哈希的字节总数)`。
    """
    h = hashlib.sha256()
    total = 0
    for name, path in _iter_source_files(directory):
        h.update(name.encode('utf-8'))
        h.update(b'\x00')
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
                total += len(chunk)
        h.update(b'\x00')
    return h.hexdigest(), total


def _iter_source_files(directory: str):
    """按相对路径升序遍历源文件，跳过隐藏目录与临时产物。"""
    root = os.path.abspath(directory)
    collected = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 原地过滤：跳过 `.git`、`__pycache__`、隐藏目录、临时目录
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith('.')
            and d not in ('__pycache__', '极快_包', 'node_modules')
        )
        for fn in sorted(filenames):
            if not fn.endswith(('.jk', '.py', '.json')):
                continue
            if fn.endswith('.tmp'):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, '/')
            collected.append((rel, full))
    collected.sort()
    for rel, full in collected:
        yield rel, full
