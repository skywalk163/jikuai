# -*- coding: utf-8 -*-
"""极快包管理 - 锁文件 `包.锁`（M8-3）。

锁文件的唯一职责：让「同一份清单」在任何机器、任何时间都装出**完全一样**
的依赖树。因此这里最重要的性质是**确定性**——条目按包名排序、JSON 固定
缩进、不写入时间戳/机器名等易变字段。带时间戳的锁文件会让每次
`jk 包 装` 都产生无意义的 git diff，这是 npm 早年踩过的坑。

结构
----
    {
      "锁版本": 1,
      "包": [
        {"名称": "甲", "版本": "1.2.0", "来源": "注册表",
         "约束": "^1.0.0", "校验和": "sha256:...", "依赖": ["乙"]},
        {"名称": "乙", "版本": "0.1.0", "来源": "路径", "路径": "../乙"}
      ]
    }
"""

import json
import os
from typing import Dict, List, Optional

__all__ = [
    'LOCKFILE_NAME', 'LOCK_VERSION', 'LockError',
    'LockedPackage', 'Lockfile', 'load_lockfile', 'save_lockfile',
]

LOCKFILE_NAME = '包.锁'

#: 锁文件格式版本。结构不兼容变更时 +1，旧版直接拒读而不是猜测语义。
LOCK_VERSION = 1


class LockError(Exception):
    """锁文件缺失、格式错误或版本不兼容。"""


class LockedPackage:
    """锁定的一个包：名称 + 精确版本 + 来源坐标。"""

    __slots__ = ('name', 'version', 'source', 'constraint', 'path',
                 'repo', 'tag', 'checksum', 'deps')

    def __init__(self, name: str, version: str, source: str = '注册表',
                 constraint: Optional[str] = None, path: Optional[str] = None,
                 repo: Optional[str] = None, tag: Optional[str] = None,
                 checksum: Optional[str] = None,
                 deps: Optional[List[str]] = None):
        self.name = name
        self.version = version
        self.source = source
        self.constraint = constraint
        self.path = path
        self.repo = repo
        self.tag = tag
        self.checksum = checksum
        self.deps = sorted(deps or [])

    def to_dict(self) -> dict:
        """序列化。可选字段为空时**不写键**，保持锁文件紧凑且稳定。"""
        d = {'名称': self.name, '版本': self.version, '来源': self.source}
        if self.constraint:
            d['约束'] = self.constraint
        if self.path:
            d['路径'] = self.path
        if self.repo:
            d['仓库'] = self.repo
        if self.tag:
            d['标签'] = self.tag
        if self.checksum:
            d['校验和'] = self.checksum
        if self.deps:
            d['依赖'] = self.deps
        return d

    @classmethod
    def from_dict(cls, d: dict) -> 'LockedPackage':
        if not isinstance(d, dict):
            raise LockError(f'锁文件条目必须是对象，得到 {type(d).__name__}')
        for field in ('名称', '版本'):
            if field not in d:
                raise LockError(f'锁文件条目缺少「{field}」：{d!r}')
        deps = d.get('依赖') or []
        if not isinstance(deps, list):
            raise LockError(f'锁文件条目 {d["名称"]} 的「依赖」必须是数组')
        return cls(
            name=d['名称'], version=d['版本'],
            source=d.get('来源', '注册表'), constraint=d.get('约束'),
            path=d.get('路径'), repo=d.get('仓库'), tag=d.get('标签'),
            checksum=d.get('校验和'), deps=list(deps),
        )

    def __repr__(self):
        return f'<锁定 {self.name}@{self.version} 来源={self.source}>'


class Lockfile:
    """锁文件的内存表示。按包名索引，序列化时按名排序。"""

    def __init__(self, packages: Optional[List[LockedPackage]] = None,
                 path: Optional[str] = None):
        self._packages: Dict[str, LockedPackage] = {}
        for p in packages or []:
            self._packages[p.name] = p
        self.path = path

    def __len__(self):
        return len(self._packages)

    def __contains__(self, name):
        return name in self._packages

    def __iter__(self):
        """按包名排序迭代，保证任何遍历顺序都是确定的。"""
        for name in sorted(self._packages):
            yield self._packages[name]

    def get(self, name: str) -> Optional[LockedPackage]:
        return self._packages.get(name)

    def put(self, pkg: LockedPackage) -> None:
        self._packages[pkg.name] = pkg

    def remove(self, name: str) -> bool:
        return self._packages.pop(name, None) is not None

    def names(self) -> List[str]:
        return sorted(self._packages)

    def to_dict(self) -> dict:
        return {
            '锁版本': LOCK_VERSION,
            '包': [p.to_dict() for p in self],      # __iter__ 已排序
        }


def load_lockfile(path: str) -> Lockfile:
    """读取锁文件。文件不存在时返回**空锁**（首次安装的正常路径）。"""
    if os.path.isdir(path):
        path = os.path.join(path, LOCKFILE_NAME)
    if not os.path.isfile(path):
        return Lockfile(path=os.path.abspath(path))
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise LockError(
            f'锁文件不是合法 JSON（{path}）：第 {e.lineno} 行 {e.msg}；'
            f'可以删掉它重新 `jk 包 装`') from None

    if not isinstance(data, dict):
        raise LockError(f'锁文件顶层必须是对象：{path}')
    version = data.get('锁版本')
    if version != LOCK_VERSION:
        raise LockError(
            f'锁文件版本 {version!r} 与当前支持的 {LOCK_VERSION} 不兼容'
            f'（{path}）；删掉它重新 `jk 包 装`')
    entries = data.get('包') or []
    if not isinstance(entries, list):
        raise LockError(f'锁文件「包」必须是数组：{path}')
    return Lockfile([LockedPackage.from_dict(e) for e in entries],
                    path=os.path.abspath(path))


def save_lockfile(lock: Lockfile, path: Optional[str] = None) -> str:
    """原子写回锁文件。返回写入的绝对路径。"""
    target = os.path.abspath(path or lock.path or LOCKFILE_NAME)
    if os.path.isdir(target):
        target = os.path.join(target, LOCKFILE_NAME)
    text = json.dumps(lock.to_dict(), ensure_ascii=False, indent=2) + '\n'
    tmp = target + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, target)
    lock.path = target
    return target
