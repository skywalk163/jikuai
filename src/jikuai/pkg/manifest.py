# -*- coding: utf-8 -*-
"""极快包管理 - 包清单 `包.json`（M8-2）。

为什么用 JSON 而不是 TOML
------------------------
`pyproject.toml` 那套更漂亮，但 `tomllib` 是 Python 3.11+ 才进标准库的，
而本项目 `requires-python = ">=3.10"`。为一个清单文件引入 `tomli` 依赖
不值得——极快的核心包至今是**零运行时依赖**，这条底线优先于语法美观。
JSON 在 3.10 就是标准库，且 UTF-8 中文键天然可读。

清单结构
--------
    {
      "名称": "我的包",
      "版本": "0.1.0",
      "描述": "一句话说明",
      "作者": "张三",
      "许可": "MIT",
      "入口": "main.jk",
      "极快版本": ">=0.6.0",
      "依赖": {
        "分词助手": "^1.0.0",
        "本地工具": {"路径": "../本地工具"},
        "远端库":   {"仓库": "https://example.com/x.git", "标签": "v1.2.0"}
      },
      "开发依赖": {"测试助手": "^0.2.0"},
      "脚本": {"测试": "jk tests/main.jk"}
    }
"""

import json
import os
import re
from typing import Dict, Optional

from . import semver

__all__ = [
    'MANIFEST_NAME', 'Manifest', 'ManifestError', 'Dependency',
    'validate_package_name', 'find_manifest', 'load_manifest',
    'save_manifest', 'new_manifest',
]

#: 清单文件名。刻意用中文，与 `包.锁` / `极快_包/` 保持同一命名族。
MANIFEST_NAME = '包.json'

#: 包名白名单：中日韩统一表意文字 + ASCII 字母数字 + `-` `_`，1..64 字。
#: **不允许**点、斜杠、反斜杠、空格——包名会直接拼进安装目录路径，
#: 放宽这里等于开一个目录穿越的口子。
_NAME_RE = re.compile(r'^[\u4e00-\u9fffA-Za-z0-9_\-]{1,64}$')

#: 保留名，避免与内置标准库模块撞名后遮蔽标准库。
_RESERVED_NAMES = frozenset({
    '分词', '排版', '校验', '成语', '正则', '简繁', '历法', '工具',
})


class ManifestError(Exception):
    """清单缺失、格式错误或字段不合法。"""


class Dependency:
    """一条依赖声明。三种来源互斥：注册表版本约束 / 本地路径 / git 仓库。"""

    __slots__ = ('name', 'constraint', 'path', 'repo', 'tag', 'dev')

    def __init__(self, name: str, constraint: Optional[str] = None,
                 path: Optional[str] = None, repo: Optional[str] = None,
                 tag: Optional[str] = None, dev: bool = False):
        self.name = name
        self.constraint = constraint
        self.path = path
        self.repo = repo
        self.tag = tag
        self.dev = dev

    @property
    def kind(self) -> str:
        if self.path is not None:
            return '路径'
        if self.repo is not None:
            return '仓库'
        return '注册表'

    @classmethod
    def from_spec(cls, name: str, spec, dev: bool = False) -> 'Dependency':
        """从清单里的一个 `名称: 规格` 条目构造依赖。"""
        validate_package_name(name)
        if isinstance(spec, str):
            # 字符串规格一律当版本约束，提前校验以便尽早报错
            try:
                semver.parse_constraint(spec)
            except (semver.InvalidConstraint, semver.InvalidVersion) as e:
                raise ManifestError(f'依赖 {name} 的版本约束不合法：{e}') from None
            return cls(name, constraint=spec, dev=dev)

        if not isinstance(spec, dict):
            raise ManifestError(
                f'依赖 {name} 的规格必须是字符串或对象，得到 '
                f'{type(spec).__name__}')

        if '路径' in spec:
            raw = spec['路径']
            if not isinstance(raw, str) or not raw:
                raise ManifestError(f'依赖 {name} 的「路径」必须是非空字符串')
            return cls(name, path=raw, dev=dev)

        if '仓库' in spec:
            repo = spec['仓库']
            if not isinstance(repo, str) or not repo:
                raise ManifestError(f'依赖 {name} 的「仓库」必须是非空字符串')
            tag = spec.get('标签')
            if tag is not None and not isinstance(tag, str):
                raise ManifestError(f'依赖 {name} 的「标签」必须是字符串')
            return cls(name, repo=repo, tag=tag, dev=dev)

        raise ManifestError(
            f'依赖 {name} 必须给出「路径」或「仓库」之一，或直接写版本约束')

    def to_spec(self):
        """序列化回清单里的规格形态（与 `from_spec` 互逆）。"""
        if self.path is not None:
            return {'路径': self.path}
        if self.repo is not None:
            spec = {'仓库': self.repo}
            if self.tag:
                spec['标签'] = self.tag
            return spec
        return self.constraint or '*'

    def __repr__(self):
        return f'<依赖 {self.name} {self.kind}>'


class Manifest:
    """`包.json` 的内存表示。"""

    def __init__(self, data: dict, path: Optional[str] = None):
        self._data = data
        self.path = path

    # ---- 只读字段 ----------------------------------------------------
    @property
    def name(self) -> str:
        return self._data['名称']

    @property
    def version(self) -> str:
        return self._data['版本']

    @property
    def description(self) -> str:
        return self._data.get('描述', '')

    @property
    def entry(self) -> str:
        return self._data.get('入口', 'main.jk')

    @property
    def jikuai_requirement(self) -> Optional[str]:
        return self._data.get('极快版本')

    @property
    def scripts(self) -> Dict[str, str]:
        raw = self._data.get('脚本') or {}
        if not isinstance(raw, dict):
            raise ManifestError('「脚本」必须是对象')
        return {k: v for k, v in raw.items() if isinstance(v, str)}

    @property
    def root(self) -> str:
        """包根目录（清单所在目录）。"""
        if self.path is None:
            raise ManifestError('该清单没有关联文件路径')
        return os.path.dirname(os.path.abspath(self.path))

    # ---- 依赖 --------------------------------------------------------
    def dependencies(self, include_dev: bool = False) -> Dict[str, Dependency]:
        """返回 `名称 -> Dependency`。`include_dev` 为真时并入开发依赖。

        同名依赖同时出现在两组里时以**运行时依赖**为准——运行时才是
        真正会被打包进产物的那份，让开发依赖覆盖它会造成生产环境缺包。
        """
        deps: Dict[str, Dependency] = {}
        if include_dev:
            for name, spec in (self._data.get('开发依赖') or {}).items():
                deps[name] = Dependency.from_spec(name, spec, dev=True)
        for name, spec in (self._data.get('依赖') or {}).items():
            deps[name] = Dependency.from_spec(name, spec, dev=False)
        return deps

    def add_dependency(self, dep: Dependency) -> None:
        """写入（或覆盖）一条依赖。"""
        key = '开发依赖' if dep.dev else '依赖'
        table = self._data.setdefault(key, {})
        if not isinstance(table, dict):
            raise ManifestError(f'「{key}」必须是对象')
        table[dep.name] = dep.to_spec()
        # 同名依赖不允许横跨两张表，否则解析结果取决于读取顺序
        other = '依赖' if dep.dev else '开发依赖'
        if isinstance(self._data.get(other), dict):
            self._data[other].pop(dep.name, None)

    def remove_dependency(self, name: str) -> bool:
        """移除依赖（两张表都查）。返回是否真的移除了东西。"""
        removed = False
        for key in ('依赖', '开发依赖'):
            table = self._data.get(key)
            if isinstance(table, dict) and name in table:
                del table[name]
                removed = True
        return removed

    def to_dict(self) -> dict:
        return self._data


def validate_package_name(name: str) -> str:
    """校验包名。不合法直接抛 `ManifestError`。"""
    if not isinstance(name, str):
        raise ManifestError(f'包名必须是字符串，得到 {type(name).__name__}')
    if not _NAME_RE.match(name):
        raise ManifestError(
            f'包名不合法：{name!r}（只允许中文、字母、数字、下划线、连字符，'
            f'1-64 字，且不含点与路径分隔符）')
    if name in _RESERVED_NAMES:
        raise ManifestError(f'包名 {name!r} 与内置标准库模块重名，请另取一个')
    return name


def _validate(data: dict, path: Optional[str]) -> None:
    where = f'（{path}）' if path else ''
    if not isinstance(data, dict):
        raise ManifestError(f'清单顶层必须是对象{where}')
    for field in ('名称', '版本'):
        if field not in data:
            raise ManifestError(f'清单缺少必填字段「{field}」{where}')
    validate_package_name(data['名称'])
    try:
        semver.parse_version(data['版本'])
    except semver.InvalidVersion as e:
        raise ManifestError(f'清单「版本」不合法{where}：{e}') from None
    if data.get('极快版本') is not None:
        try:
            semver.parse_constraint(data['极快版本'])
        except (semver.InvalidConstraint, semver.InvalidVersion) as e:
            raise ManifestError(f'清单「极快版本」不合法{where}：{e}') from None
    for key in ('依赖', '开发依赖'):
        table = data.get(key)
        if table is not None and not isinstance(table, dict):
            raise ManifestError(f'清单「{key}」必须是对象{where}')


def find_manifest(start: Optional[str] = None) -> Optional[str]:
    """从 `start`（默认当前目录）向上逐级查找 `包.json`，返回绝对路径。

    与 npm 找 `package.json`、Cargo 找 `Cargo.toml` 的行为一致：
    在子目录里执行命令也能定位到项目根。
    """
    here = os.path.abspath(start or os.getcwd())
    if os.path.isfile(here):
        here = os.path.dirname(here)
    while True:
        candidate = os.path.join(here, MANIFEST_NAME)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:          # 抵达文件系统根
            return None
        here = parent


def load_manifest(path: Optional[str] = None) -> Manifest:
    """读取清单。`path` 可以是清单文件或其所在目录；省略则向上查找。"""
    if path is None:
        found = find_manifest()
        if found is None:
            raise ManifestError(
                f'当前目录及其上级都没有 {MANIFEST_NAME}，'
                f'先运行 `jk 包 初始化`')
        path = found
    elif os.path.isdir(path):
        path = os.path.join(path, MANIFEST_NAME)

    if not os.path.isfile(path):
        raise ManifestError(f'找不到清单文件：{path}')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ManifestError(f'清单不是合法 JSON（{path}）：第 {e.lineno} 行 {e.msg}') from None
    except UnicodeDecodeError:
        raise ManifestError(f'清单编码不是 UTF-8：{path}') from None
    _validate(data, path)
    return Manifest(data, path=os.path.abspath(path))


def save_manifest(manifest: Manifest, path: Optional[str] = None) -> str:
    """原子写回清单。返回写入的绝对路径。"""
    target = os.path.abspath(path or manifest.path or MANIFEST_NAME)
    _validate(manifest.to_dict(), target)
    text = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + '\n'
    tmp = target + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, target)          # 原子替换，避免中断留下半个清单
    manifest.path = target
    return target


def new_manifest(name: str, version: str = '0.1.0',
                 description: str = '', entry: str = 'main.jk') -> Manifest:
    """构造一份最小清单（供 `jk 包 初始化` 使用）。"""
    validate_package_name(name)
    semver.parse_version(version)
    data = {
        '名称': name,
        '版本': version,
        '描述': description,
        '入口': entry,
        '依赖': {},
    }
    return Manifest(data)
