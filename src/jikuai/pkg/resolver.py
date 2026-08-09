# -*- coding: utf-8 -*-
"""极快包管理 - 依赖解析（M8-5）。

解析器把「根清单 → 完整依赖树」这一步做出来，产出一个**安装计划**
（每个包配一个已抓取的源码目录）。MVP 的解析策略刻意简单：

- 广度优先遍历依赖图，同名包**首次遇到即锁定**，后续再出现时只做
  约束**一致性校验**，命中冲突就报错——不做 npm 那种「嵌套多副本」
  或 pip 那种「回溯求解」。扁平单副本对中文脚本生态足够，且行为可预测。
- 环检测靠遍历栈；命中环给出完整链路，而不是栈溢出。
- 只有「注册表」来源需要在多版本里挑，MVP 未接注册表，所以这里
  路径/仓库来源各自只有一个确定版本，冲突判定就是「版本是否落在
  所有出现过的约束交集内」。
"""

from typing import Dict, List, Optional, Tuple

from . import semver
from .lockfile import LockedPackage, Lockfile
from .manifest import Dependency, Manifest
from .sources import FetchedSource, SourceError, resolve_source, compute_checksum

__all__ = ['ResolveError', 'ResolvedNode', 'resolve']


class ResolveError(Exception):
    """依赖解析失败：版本冲突、循环依赖、来源抓取失败等。"""


class ResolvedNode:
    """解析树里的一个节点：一次抓取 + 它的直接依赖名。"""

    __slots__ = ('name', 'version', 'source', 'dep', 'direct_deps', 'checksum')

    def __init__(self, name: str, version: str, source: FetchedSource,
                 dep: Dependency, direct_deps: List[str]):
        self.name = name
        self.version = version
        self.source = source
        self.dep = dep
        self.direct_deps = sorted(direct_deps)
        self.checksum: Optional[str] = None

    def to_locked(self) -> LockedPackage:
        return LockedPackage(
            name=self.name, version=self.version, source=self.dep.kind,
            constraint=self.dep.constraint, path=self.dep.path,
            repo=self.dep.repo, tag=self.dep.tag,
            checksum=self.checksum, deps=self.direct_deps,
        )


def _check_constraint(dep: Dependency, version: str) -> None:
    """若依赖带版本约束，校验实际抓到的版本是否满足。"""
    if dep.constraint is None:
        return
    if not semver.matches(version, dep.constraint):
        raise ResolveError(
            f'依赖 {dep.name} 要求版本 {dep.constraint}，'
            f'但抓到的是 {version}')


def resolve(root: Manifest, include_dev: bool = False,
            cleanup: Optional[List[FetchedSource]] = None
            ) -> Tuple[List[ResolvedNode], List[str]]:
    """解析根清单的完整依赖树。

    返回 `(节点列表, 顶层依赖名列表)`。抓取产生的临时目录会追加到
    `cleanup`（若提供），交由调用方在安装完成后统一清理。

    冲突策略：同名包第二次出现时，要求「已锁定版本」满足「新约束」，
    否则报冲突。这不做版本回退，但对单副本扁平树是安全且可解释的。
    """
    resolved: Dict[str, ResolvedNode] = {}
    # 队列项：(依赖声明, 该依赖来源清单所在目录, 遍历链路)
    queue: List[Tuple[Dependency, str, Tuple[str, ...]]] = []

    top_names: List[str] = []
    for name, dep in sorted(root.dependencies(include_dev=include_dev).items()):
        top_names.append(name)
        queue.append((dep, root.root, (root.name,)))

    while queue:
        dep, base_dir, chain = queue.pop(0)

        if dep.name in chain:
            cyc = ' -> '.join(chain + (dep.name,))
            raise ResolveError(f'检测到循环依赖：{cyc}')

        if dep.name in resolved:
            # 已锁定：只校验新约束与既定版本是否相容，不重新抓取
            _check_constraint(dep, resolved[dep.name].version)
            continue

        try:
            source = resolve_source(dep, base_dir)
        except SourceError as e:
            raise ResolveError(str(e)) from None
        if cleanup is not None and source.ephemeral:
            cleanup.append(source)

        version = source.manifest.version
        _check_constraint(dep, version)

        sub_deps = source.manifest.dependencies(include_dev=False)
        node = ResolvedNode(dep.name, version, source, dep,
                            list(sub_deps.keys()))
        resolved[dep.name] = node

        child_chain = chain + (dep.name,)
        for sub_name, sub_dep in sorted(sub_deps.items()):
            queue.append((sub_dep, source.root, child_chain))

    return list(resolved.values()), top_names
