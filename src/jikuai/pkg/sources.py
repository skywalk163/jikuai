# -*- coding: utf-8 -*-
"""极快包管理 - 来源抓取（M8-4）。

三种来源，抓取语义都收敛到「把源码物化到一个只读的临时目录」：

- `路径`：本地相对/绝对路径，直接返回该目录（**不复制**，避免大项目里
  开发依赖被拍成快照）。相对路径以「引用它的清单」所在目录为基准。
- `仓库`：git 仓库。用 `git clone --depth 1` 抓，指定标签时用 `--branch`。
  `git` 不在 PATH 时立即报错，不去尝试拼接 HTTP 或 hackish 降级。
- `注册表`：本地/内网文件系统注册表（M11-1）或**远程 HTTP 注册表**
  （v0.20.0 M20 / ADR-34）。定位符优先取依赖自带的 `registry_url`
  （per-dependency override），否则用全局 `JIKUAI_REGISTRY` → `~/.jikuai/注册表`。
  远程走 `GET <base>/包/<名>/<版本>.tar.gz` 下载 + 安全解压到临时目录；
  本地维持「返回只读快照目录」语义。装不到明确报错。

安全底线：所有磁盘写入路径都用 `_ensure_within(base, target)` 校验，
杜绝路径穿越；`subprocess` 全部走 `shell=False` + 显式 argv 列表。
"""

import hashlib
import os
import shutil
import subprocess
import tarfile
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

    __slots__ = ('root', 'manifest', 'kind', 'origin', 'ephemeral',
                 'signer', 'signature', 'expected_checksum',
                 'registry_locator')

    def __init__(self, root: str, manifest: Manifest, kind: str,
                 origin: str, ephemeral: bool,
                 signer: str = '', signature: str = '',
                 expected_checksum: str = '',
                 registry_locator: str = ''):
        self.root = os.path.abspath(root)
        self.manifest = manifest
        self.kind = kind                 # 路径 / 仓库 / 注册表
        self.origin = origin             # 展示用坐标（路径串 / 仓库 URL）
        #: `True` 表示 root 位于临时目录，安装完必须清理；`False` 表示
        #: 直接指向用户已有目录（比如本地路径依赖），**不能**删除。
        self.ephemeral = ephemeral
        #: 以下三项只有 `注册表` 来源会填（v0.20.0 W75，ADR-33）。
        #: 路径/仓库来源没有索引条目，也就没有签名可验——装包端据此
        #: 只对注册表来源做验签，不会对本地路径依赖发无意义的告警。
        self.signer = signer                        # 签名者别名，未签名为空
        self.signature = signature                  # base64 的 64 字节签名
        self.expected_checksum = expected_checksum  # 索引里记的 `sha256:<hex>`
        #: v0.20.0 M20：这个包来自哪个注册表定位符（本地路径或 URL）。
        #: 装包端用这个查公钥——per-dependency override 下不能拿全局
        #: JIKUAI_REGISTRY 去查，会拿到错包或空。
        self.registry_locator = registry_locator


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


#: 解压上限（v0.21.0 W86 安全审计）。远程注册表是**外部可控输入**，
#: 而 `_safe_extract_targz` 此前只校验成员路径安全、不限体量——一个几 KB 的
#: tar.gz 可以解压出几十 GB（经典 tar bomb），撑爆客户端磁盘；成员数无上限时
#: `getmembers()` 自身就能吃满内存。三条上限按「正常极快包的量级」定：
#: 一个包是若干 .jk/.py/.json 源文件，几百个成员、单文件几 MB 已经很宽松。
#: 环境变量覆盖是给「确实有大资源包」的用户的逃生门，不是默认路径。
_MAX_MEMBERS = 4096
_MAX_MEMBER_BYTES = 64 * 1024 * 1024        # 单成员 64 MiB
_MAX_TOTAL_BYTES = 256 * 1024 * 1024        # 解压后合计 256 MiB
_MEMBERS_ENV = 'JIKUAI_PKG_MAX_MEMBERS'
_MEMBER_BYTES_ENV = 'JIKUAI_PKG_MAX_MEMBER_BYTES'
_TOTAL_BYTES_ENV = 'JIKUAI_PKG_MAX_TOTAL_BYTES'


def _limit(env_name: str, default: int) -> int:
    """读一条上限的环境变量覆盖。非正整数/非数字一律回落默认值。

    刻意不报错：上限是安全网，配错了应当退回**更安全**的默认值，
    而不是让 `导入` 因为一个环境变量拼写错误而挂掉。
    """
    raw = os.environ.get(env_name)
    if not raw:
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _safe_extract_targz(data: bytes, dest_dir: str) -> None:
    """把 tar.gz 字节流安全解压到 `dest_dir`（ADR-34 §2.4）。

    拒绝任何逃逸出 `dest_dir` 的成员：绝对路径、含 `..` 段、软/硬链接、
    设备节点。Python 3.12+ 有内置 `data_filter`，3.10/3.11 手写等价校验
    （项目 `requires-python >= 3.10`）。历史上 Windows 的 tar 路径处理踩过
    坑，这里宁可拒绝可疑归档也不冒解压到目录外的风险。

    **v0.21.0 W86 追加体量上限**（成员数 / 单成员大小 / 合计解压大小）。
    路径安全挡的是「写到哪」，体量上限挡的是「写多少」——tar bomb 的每个
    成员路径都合法，只靠路径校验拦不住。上限在**解压前**按 tar 头部声明的
    `size` 累加判定，不需要真的写盘才发现超标。
    """
    dest_abs = os.path.abspath(dest_dir)
    max_members = _limit(_MEMBERS_ENV, _MAX_MEMBERS)
    max_member_bytes = _limit(_MEMBER_BYTES_ENV, _MAX_MEMBER_BYTES)
    max_total_bytes = _limit(_TOTAL_BYTES_ENV, _MAX_TOTAL_BYTES)
    import io
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tf:
        members = tf.getmembers()
        if len(members) > max_members:
            raise SourceError(
                f'快照归档成员数 {len(members)} 超过上限 {max_members}，'
                f'拒绝解压（疑似解压炸弹；确需放宽设 {_MEMBERS_ENV}）')
        total = 0
        for m in members:
            if m.islnk() or m.issym():
                raise SourceError(f'快照归档含链接成员，拒绝解压：{m.name}')
            if m.isdev():
                raise SourceError(f'快照归档含设备节点，拒绝解压：{m.name}')
            if m.size > max_member_bytes:
                raise SourceError(
                    f'快照归档成员 {m.name} 声明大小 {m.size} 字节，'
                    f'超过单成员上限 {max_member_bytes}，拒绝解压'
                    f'（确需放宽设 {_MEMBER_BYTES_ENV}）')
            total += m.size
            if total > max_total_bytes:
                raise SourceError(
                    f'快照归档解压后合计超过上限 {max_total_bytes} 字节，'
                    f'拒绝解压（疑似解压炸弹；确需放宽设 {_TOTAL_BYTES_ENV}）')
            name = m.name.replace('\\', '/')
            if name.startswith('/') or os.path.isabs(name):
                raise SourceError(f'快照归档含绝对路径成员：{m.name}')
            target = os.path.abspath(os.path.join(dest_abs, name))
            try:
                common = os.path.commonpath([dest_abs, target])
            except ValueError:
                raise SourceError(f'快照归档成员越出解压目录：{m.name}') from None
            if common != dest_abs:
                raise SourceError(f'快照归档成员越出解压目录：{m.name}')
        # 校验通过后统一解压。3.12+ 再叠一层官方过滤器兜底。
        try:
            tf.extractall(dest_abs, filter='data')
        except TypeError:
            tf.extractall(dest_abs)


def _fetch_registry_remote(dep: Dependency, backend, locator: str) -> FetchedSource:
    """从远程 HTTP 注册表抓取：选版 → 下 tar.gz → 安全解压到临时目录。"""
    from . import registry
    version, detail = registry.lookup_entry(dep.name, dep.constraint,
                                            root=locator)
    raw = backend.read_bytes(registry.archive_rel(dep.name, version))
    if raw is None:
        raise SourceError(
            f'远程注册表 {locator} 里 {dep.name}@{version} 缺少快照归档；'
            f'注册表未按 tar.gz 分发（管理员需用 v0.20.0+ 发布并静态托管）')

    temp_root = tempfile.mkdtemp(prefix='jikuai-fetch-')
    try:
        _safe_extract_targz(raw, temp_root)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
    # 归档内成员以 `<版本>/...` 为根（见 registry._archive_snapshot）；
    # 解压后包根就是那唯一的顶层目录。
    entries = [e for e in os.listdir(temp_root)
               if os.path.isdir(os.path.join(temp_root, e))]
    if len(entries) == 1:
        pkg_root = os.path.join(temp_root, entries[0])
    else:
        pkg_root = temp_root
    try:
        manifest = load_manifest(pkg_root)
    except Exception as e:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise SourceError(
            f'远程注册表包 {dep.name}@{version} 的快照缺少可读的 包.json：{e}'
        ) from None
    if manifest.name != dep.name:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise SourceError(
            f'远程注册表包名不匹配：请求 {dep.name}，'
            f'但快照清单「名称」是 {manifest.name}')
    signer = str(detail.get('签名者') or '')
    signature = str(detail.get('签名') or '')
    expected = str(detail.get('校验和') or '')
    # 远程快照落在临时目录，安装完必须清理（ephemeral=True）。校验和/验签
    # 由 installer 复用 M19 的三道检查完成——归档本身不签名。
    return FetchedSource(pkg_root, manifest, '注册表', locator,
                         ephemeral=True, signer=signer,
                         signature=signature, expected_checksum=expected,
                         registry_locator=locator)


def _fetch_registry(dep: Dependency, _base_dir: str) -> FetchedSource:
    # v0.20.0 M20（ADR-34）：注册表定位符可以是本地路径或 https:// URL。
    # 优先级：依赖自带 registry_url（per-dependency override）> 全局
    # JIKUAI_REGISTRY / ~/.jikuai/注册表。远程走 tar.gz 下载 + 安全解压，
    # 本地维持原来的「返回只读快照目录」语义。
    from . import registry
    from . import backend as _backend
    locator = dep.registry_url or registry.registry_root()

    if _backend.is_remote(locator):
        try:
            b = _backend.get_backend(locator)
        except _backend.BackendError as e:
            raise SourceError(str(e)) from None
        try:
            return _fetch_registry_remote(dep, b, locator)
        except registry.RegistryError as e:
            raise SourceError(str(e)) from None
        except _backend.BackendError as e:
            raise SourceError(str(e)) from None

    # 本地注册表：沿用 M11-1 路径，返回不可删的只读快照目录。
    try:
        version, snapshot = registry.lookup(dep.name, dep.constraint,
                                            root=locator)
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
    # v0.20.0 W75：顺手把索引条目里的签名字段带出来，交给 installer 验签。
    # 索引损坏导致读不到不在这里拦——装包端会因「无签名」走 Warn/拒装分支，
    # 抓取阶段就抛错反而会让用户以为包不存在。
    try:
        signer, signature, expected = registry.lookup_signature(
            dep.name, version, root=locator)
    except registry.RegistryError:
        signer, signature, expected = '', '', ''
    # 快照是注册表的只读副本，绝不能删（ephemeral=False）。
    return FetchedSource(snapshot, manifest, '注册表', dep.name,
                         ephemeral=False, signer=signer,
                         signature=signature, expected_checksum=expected,
                         registry_locator=locator)


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
