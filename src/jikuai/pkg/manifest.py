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

    __slots__ = ('name', 'constraint', 'path', 'repo', 'tag', 'dev',
                 'registry_url')

    def __init__(self, name: str, constraint: Optional[str] = None,
                 path: Optional[str] = None, repo: Optional[str] = None,
                 tag: Optional[str] = None, dev: bool = False,
                 registry_url: Optional[str] = None):
        self.name = name
        self.constraint = constraint
        self.path = path
        self.repo = repo
        self.tag = tag
        self.dev = dev
        #: per-dependency 注册表覆盖（ADR-34 §2.5）。非空时该依赖从这个
        #: 注册表解析，忽略全局 `JIKUAI_REGISTRY`。它**不是**第四种来源，
        #: 只是「注册表」来源的修饰——远程与本地注册表是同一种依赖。
        self.registry_url = registry_url

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

        if '注册表' in spec:
            # ADR-34 §2.5：per-dependency 注册表覆盖。
            #   {"注册表": "https://reg.example.com", "版本": "^1.2.0"}
            # 不新增 kind——仍是「注册表」来源，只换解析源。
            url = spec['注册表']
            if not isinstance(url, str) or not url:
                raise ManifestError(f'依赖 {name} 的「注册表」必须是非空字符串')
            constraint = spec.get('版本')
            if constraint is not None:
                if not isinstance(constraint, str):
                    raise ManifestError(f'依赖 {name} 的「版本」必须是字符串')
                try:
                    semver.parse_constraint(constraint)
                except (semver.InvalidConstraint, semver.InvalidVersion) as e:
                    raise ManifestError(
                        f'依赖 {name} 的版本约束不合法：{e}') from None
            return cls(name, constraint=constraint, registry_url=url, dev=dev)

        raise ManifestError(
            f'依赖 {name} 必须给出「路径」「仓库」「注册表」之一，或直接写版本约束')

    def to_spec(self):
        """序列化回清单里的规格形态（与 `from_spec` 互逆）。"""
        if self.path is not None:
            return {'路径': self.path}
        if self.repo is not None:
            spec = {'仓库': self.repo}
            if self.tag:
                spec['标签'] = self.tag
            return spec
        if self.registry_url:
            # 只有显式声明过 override 的依赖才输出 dict —— 既有纯字符串依赖
            # 必须原样回写，否则 `包.json` 会被无谓改形态、diff 全是噪声。
            spec = {'注册表': self.registry_url}
            if self.constraint:
                spec['版本'] = self.constraint
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
    def block_roots(self) -> list:
        """包携带的块根相对路径列表（ADR-32 §2.1）。

        `块` 是可选顶层字段；缺失或空 = 普通包，不携带块。每条路径相对
        包根解析，语义与 `JIKUAI_PKG_ROOTS` 每条路径一致（直接指向
        `blocks/` 那一级）。合法性由 `_validate` 保证（字符串列表、每条不
        含 `..` / 不是绝对路径），这里只做读取。
        """
        raw = self._data.get('块')
        if raw is None:
            return []
        return list(raw)


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
    """校验包名。不合法直接抛 `ManifestError`。

    **注意本函数刻意不校验词法原子性。** 包名空间（`_NAME_RE` 允许 `-` / `_` /
    拉丁字母）与「点分模块路径段」空间（lexer 只吃单 token，`-` 直接是非法字符）
    是**两套不兼容的字符集**——`my-pkg` 是完全合法的包名，但永远做不了命名空间。
    只有**携带块**的包才会被当命名空间用，所以原子性检查放在 `_validate_block_roots`
    那一侧（见 v0.19.0 W69 的取舍记录），普通包不受牵连。
    """
    if not isinstance(name, str):
        raise ManifestError(f'包名必须是字符串，得到 {type(name).__name__}')
    if not _NAME_RE.match(name):
        raise ManifestError(
            f'包名不合法：{name!r}（只允许中文、字母、数字、下划线、连字符，'
            f'1-64 字，且不含点与路径分隔符）')
    if name in _RESERVED_NAMES:
        raise ManifestError(f'包名 {name!r} 与内置标准库模块重名，请另取一个')
    return name


def validate_namespace_name(name: str) -> str:
    """校验一个包名能否充当块命名空间（v0.19.0 W69）。

    携带块的包，其包名会作为命名空间进入点分模块路径
    （`从 blocks.<包名>.<领域>.<块> 导入 X`）。parser 的 `_read_module_name()`
    每个 `.` 之后只取**一个** token，所以包名必须词法原子，否则块永远导不进来
    ——而且失败发生在**使用方**，包作者自己测不出来。

    与 `validate_package_name` 分开的理由见后者的 docstring：普通包不需要这条。

    `check_module_segment_atomicity` 内部直接喂 lexer，遇到 `-` 这类**非法字符**
    会抛 `JiKuaiError` 而不是返回 `(False, ...)`，所以这里要兜住转成 `ManifestError`
    ——对调用方来说「lexer 都吃不下」和「切成了多段」是同一类失败。
    """
    from .blocks import check_module_segment_atomicity  # 延迟导入，无循环
    try:
        atomic, pieces = check_module_segment_atomicity(name)
    except Exception:                       # lexer 非法字符（`-` 等）
        raise ManifestError(
            f'包名 {name!r} 不能作为块命名空间：分词器无法处理其中的字符'
            f'（`-` 之类在极快源码里是非法字符）。携带块的包请改用纯中文或'
            f'纯字母的单 token 名字。')
    if not atomic:
        切分 = '+'.join(f'{val}({typ})' for typ, val in pieces)
        raise ManifestError(
            f'包名 {name!r} 不能作为块命名空间（会被分词器切成多段：{切分}）。'
            f'它会出现在 `从 blocks.{name}.<领域>.<块> 导入 X` 里，'
            f'而点分路径每段只能是单个 token——否则块永远导不进来。请换个名字。')
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
    _validate_block_roots(data.get('块'), where, name=data.get('名称', ''))


def _validate_block_roots(raw, where: str, name: str = '') -> None:
    """校验 `块` 字段（ADR-32 §2.1）：字符串列表，每条是包内相对路径。

    安全边界：块根路径来自**第三方包的清单**，是新增的外部输入面。绝对
    路径与 `..` 都会让安装器把块根指到包外，等于开一个目录穿越口子——
    与 `入口` 字段的逃逸防护同一口径，在这里一次拦住。

    v0.19.0 W69：若 `name` 非空且 `raw` 非 None（即包带块），追加校验包名
    能否充当命名空间。这是**唯一的入口**——普通包不受牵连，只有声明了 `块`
    字段的包才被要求包名词法原子。
    """
    if raw is None:
        return
    if not isinstance(raw, list):
        raise ManifestError(f'清单「块」必须是数组{where}')
    # W69：携带块的包，包名必须能当命名空间
    if name:
        validate_namespace_name(name)
    for item in raw:
        if not isinstance(item, str) or not item:
            raise ManifestError(f'清单「块」的每一项必须是非空字符串{where}')
        # 统一按两种分隔符看，避免平台差异放过某种形态
        规整 = item.replace('\\', '/')
        # `os.path.isabs` 在 Windows 上对 `/x`（无盘符）自 3.13 起返回 False，
        # 所以显式查前导分隔符；再加盘符形态（`C:...`）与 POSIX 绝对路径。
        if (规整.startswith('/') or os.path.isabs(item)
                or (len(item) > 1 and item[1] == ':')):
            raise ManifestError(
                f'清单「块」的路径必须是包内相对路径，不允许绝对路径：'
                f'{item!r}{where}')
        段 = [s for s in 规整.split('/') if s]
        if os.pardir in 段:
            raise ManifestError(
                f'清单「块」的路径不允许用 `..` 逃出包目录：{item!r}{where}')
        if not 段:
            raise ManifestError(f'清单「块」的路径不能只由分隔符组成{where}')



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
