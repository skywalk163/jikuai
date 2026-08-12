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

import json
import os
import shutil
from typing import Dict, List, Optional, Tuple

from .lockfile import Lockfile, save_lockfile
from .manifest import Manifest, ManifestError, load_manifest
from .resolver import ResolvedNode, resolve
from .sources import FetchedSource, compute_checksum
from . import trust

__all__ = [
    'PACKAGES_DIR', 'BLOCK_ROOTS_INDEX', 'InstallError', 'InstallReport',
    'packages_dir', 'install', 'uninstall', 'installed_packages',
    'read_block_roots_index',
]

#: 依赖安装目录名。与 `包.json` / `包.锁` 同属中文命名族。
PACKAGES_DIR = '极快_包'

#: 已装块根索引文件名（ADR-32 §2.3）。装完后由 installer 维护，
#: 供 `extra_roots()`（发现）与 `module_loader._search_paths()`（执行）双侧读取。
BLOCK_ROOTS_INDEX = '.块根.json'
BLOCK_ROOTS_INDEX_VERSION = 1

#: 拷贝时跳过的目录：版本库元数据、Python 缓存、嵌套依赖目录。
_SKIP_DIRS = frozenset({'.git', '.hg', '.svn', '__pycache__',
                        PACKAGES_DIR, 'node_modules', '.pytest_cache'})


class InstallError(Exception):
    """安装过程失败。"""


class InstallReport:
    """一次安装的结果摘要，供 CLI 渲染。"""

    __slots__ = ('installed', 'unchanged', 'removed', 'lock_path', 'warnings')

    def __init__(self):
        self.installed: List[Tuple[str, str]] = []   # (名称, 版本)
        self.unchanged: List[Tuple[str, str]] = []
        self.removed: List[str] = []
        self.lock_path: Optional[str] = None
        #: 非致命告警，由 CLI 打到 stderr（v0.20.0 W75：未签名包过渡期告警）。
        #: 走报告而不是在这里直接 print —— installer 是库，I/O 归 CLI，
        #: 否则 LSP/DAP 以库形式复用时会往用户终端乱吐。
        self.warnings: List[str] = []

    @property
    def total(self) -> int:
        return len(self.installed) + len(self.unchanged)


def packages_dir(project_root: str) -> str:
    """返回项目的依赖安装目录绝对路径（不保证存在）。"""
    return os.path.join(os.path.abspath(project_root), PACKAGES_DIR)


def installed_packages(project_root: str) -> Dict[str, str]:
    """扫描 `极快_包/`，返回 `名称 -> 版本`。目录不存在时返回空字典。"""
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


def _收集块根(base: str, names) -> List[dict]:
    """遍历给定包名，收集其 `包.json` 声明且实际存在的块根，返回索引条目。

    每条 `{"包": 名称, "路径": 相对极快_包的posix路径}`。路径按 `/` 分隔存放
    （跨平台可读、可提交）；不存在的块根跳过（包声明了 `块` 但没带上对应目录
    时不该让整个安装失败）。
    """
    条目: List[dict] = []
    for name in sorted(names):
        pkg_dir = os.path.join(base, name)
        manifest_path = os.path.join(pkg_dir, '包.json')
        if not os.path.isfile(manifest_path):
            continue
        try:
            roots = load_manifest(pkg_dir).block_roots
        except ManifestError:
            continue
        for rel in roots:
            块根 = os.path.normpath(os.path.join(pkg_dir, rel))
            # 复用 manifest 的逃逸校验后这里再兜一层：必须落在包目录内
            if os.path.commonpath([os.path.abspath(pkg_dir),
                                   os.path.abspath(块根)]) \
                    != os.path.abspath(pkg_dir):
                continue
            if not os.path.isdir(块根):
                continue
            相对 = os.path.relpath(块根, base).replace(os.sep, '/')
            条目.append({'包': name, '路径': 相对})
    return 条目


def _写块根索引(base: str, names) -> None:
    """按当前有效包集**重建**块根索引 `极快_包/.块根.json`（ADR-32 §2.3）。

    重建而非增量改——`names` 是本次安装解析出的全量包集（`_prune` 的 keep），
    是「当前有效块包」的真相源，据它重写可保证索引与磁盘一致、无卸载残留。
    没有任何块根时删掉索引文件（让「没有块包」= 没有文件，语义干净）。
    """
    index_path = os.path.join(base, BLOCK_ROOTS_INDEX)
    条目 = _收集块根(base, names)
    if not 条目:
        if os.path.isfile(index_path):
            os.remove(index_path)
        return
    data = {'索引版本': BLOCK_ROOTS_INDEX_VERSION, '块根': 条目}
    text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    tmp = index_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, index_path)


def read_block_roots_index(base: str) -> List[str]:
    """读 `极快_包/.块根.json`，返回块根**绝对路径**列表（ADR-32 §2.3）。

    供 `blocks.extra_roots()`（发现侧，直接用这些路径）与
    `module_loader._search_paths()`（执行侧，取每条的 dirname）共用。

    `base` 是 `极快_包/` 目录。文件不存在返回空；版本不匹配拒读（返回空并
    不报错——门禁不该因为一个可选索引文件挡住 `导入`，与 `包.锁` 版本拒读
    的强硬程度区别对待）；只保留实际存在的目录。
    """
    index_path = os.path.join(base, BLOCK_ROOTS_INDEX)
    if not os.path.isfile(index_path):
        return []
    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) \
            or data.get('索引版本') != BLOCK_ROOTS_INDEX_VERSION:
        return []
    结果: List[str] = []
    见过 = set()
    for 条 in data.get('块根') or []:
        if not isinstance(条, dict):
            continue
        rel = 条.get('路径')
        if not isinstance(rel, str) or not rel:
            continue
        abs_p = os.path.normpath(os.path.join(base, rel))
        if abs_p in 见过:
            continue
        见过.add(abs_p)
        if os.path.isdir(abs_p):
            结果.append(abs_p)
    return 结果


def _verify_registry_signature(node: ResolvedNode,
                               warnings: List[str]) -> None:
    """校验一个注册表来源包的完整性与签名（v0.20.0 W75，ADR-33 §2.7）。

    三道检查，前两道**硬拒**（抛 InstallError），第三道过渡期只告警：

    1. **校验和比对**：索引里记的 `校验和` 与本地重算的必须一致。v0.19.0
       之前 installer 只算不比（没有 verify-on-read），快照被就地改一个字节
       都装得进来——这道检查独立于签名，是包完整性的地板。
    2. **签名验证**：有签名就必须验得过（TOFU 公钥 + 白名单，见 trust.py）。
       验不过说明包内容或签名被动过，拒装。
    3. **未签名**：v0.20.0 Warn 但放行，v0.21.0 起拒装。理由见 ADR-33 §2.7
       ——注册表里现在全是 v0.19.0 及之前发的未签包，一上线就硬拒等于把既有
       生态一次性打死；但从第一天就 Warn，静默放行会让所有人以为不签也没事。

    非注册表来源（路径 / 仓库）直接返回：它们没有索引条目，没有签名可验。
    """
    src = node.source
    if src.kind != '注册表':
        return

    coord = f'{node.name}@{node.version}'

    # 1. 校验和比对
    if src.expected_checksum and src.expected_checksum != node.checksum:
        raise InstallError(
            f'{coord} 完整性校验失败：注册表索引记的校验和是 '
            f'{src.expected_checksum}，本地重算得到 {node.checksum}。'
            f'快照可能被改过或索引损坏，拒装')

    # 2 / 3. 签名
    if not (src.signer and src.signature):
        warnings.append(
            f'{coord} 未签名（注册表索引里没有「签名者」/「签名」字段）。'
            f'v0.21.0 起将拒装未签名包，请联系包作者用 '
            f'`jk 包 发布 --签名 <别名>` 重发')
        return

    from . import registry
    # v0.20.0 M20：per-dependency override 下注册表定位符可能与全局不同，
    # 验签要从包实际来源的注册表查公钥，否则 TOFU 会去错误的注册表找。
    reg_locator = getattr(src, 'registry_locator', '') or registry.registry_root()
    try:
        trust.verify_signature(src.signer, src.signature, node.checksum,
                               reg_locator)
    except trust.TrustError as e:
        raise InstallError(f'{coord} 签名校验失败：{e}') from None


def install(root: Manifest, include_dev: bool = False,
            prune: bool = True) -> InstallReport:
    """解析并安装根清单的全部依赖，写回锁文件。

    `prune=True` 时会清掉 `极快_包/` 里已不再被依赖的包，
    使安装目录与清单保持严格一致（对齐 `npm ci` 而非 `npm install`）。
    装完按当前有效包集重建块根索引（ADR-32 §2.3），让携带块的第三方包
    能被 `scan_blocks`（发现）与 `导入`（执行）双侧看见。
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

            # v0.20.0 W75（ADR-33）：装包端验签。仅对注册表来源做，路径/仓库
            # 来源没有索引条目也就没有签名可验，对它们发告警是噪声。
            _verify_registry_signature(node, report.warnings)

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
        # 按当前有效包集重建块根索引（ADR-32 §2.3）——放在 prune 之后，
        # keep 集合即当前磁盘上应有的包，据它重写避免卸载残留。
        if os.path.isdir(base):
            _写块根索引(base, {n.name for n in nodes})
        return report
    finally:
        for src in cleanup:
            # 临时克隆目录：删掉它的父级（tempfile.mkdtemp 建的那层）
            shutil.rmtree(os.path.dirname(src.root), ignore_errors=True)


def uninstall(project_root: str, name: str) -> bool:
    """删除已安装的包目录。返回是否真的删掉了东西。

    删除后重建块根索引——如果被卸载的包携带块，它的块根条目要同步消失
    （ADR-32 §2.3「重建而非增量改」纪律）。
    """
    base = packages_dir(project_root)
    target = os.path.join(base, name)
    # 名称已由 validate_package_name 收敛，这里再兜一层：必须是直接子目录
    if os.path.dirname(os.path.abspath(target)) != os.path.abspath(base):
        raise InstallError(f'非法包名：{name}')
    if not os.path.isdir(target):
        return False
    shutil.rmtree(target, ignore_errors=True)
    # 重建索引：扫当前剩余包
    剩余 = {entry for entry in os.listdir(base)
            if not entry.startswith('.') and os.path.isdir(os.path.join(base, entry))}
    _写块根索引(base, 剩余)
    return True
