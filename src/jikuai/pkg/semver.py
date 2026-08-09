# -*- coding: utf-8 -*-
"""极快包管理 - 语义化版本与约束匹配（M8-1）。

设计取舍
--------
- 只实现 `主.次.修订[-预发布]` 三段式，不支持 `+构建元数据` 的排序语义
  （SemVer 规范本身也规定构建元数据不参与优先级比较，这里直接剥离）。
- 约束语法对齐 pip / npm / Cargo 的**交集**，避免自创方言：
  `^`（兼容主版本）、`~`（兼容次版本）、`>= > <= < ==`、`*`（任意）。
  多个约束用 `,` 连接表示**逻辑与**，与 pip 的 `>=1.0,<2.0` 一致。
- 预发布版本遵循 npm/Cargo 规则：**不被范围约束隐式命中**，
  必须由约束自身显式写出预发布号才匹配。避免 `^1.0.0` 意外装到
  `2.0.0-rc1` 这类半成品上。
"""

import re
from typing import List, Optional, Tuple

__all__ = [
    'Version', 'InvalidVersion', 'InvalidConstraint',
    'parse_version', 'parse_constraint', 'matches', 'max_satisfying',
]


class InvalidVersion(ValueError):
    """版本号字面量不合法。"""


class InvalidConstraint(ValueError):
    """版本约束字面量不合法。"""


#: `1.2.3`、`1.2.3-rc.1`、`1.2.3+build`（构建元数据被剥离）
_VERSION_RE = re.compile(
    r'^\s*v?(\d+)\.(\d+)\.(\d+)'
    r'(?:-([0-9A-Za-z.\-]+))?'
    r'(?:\+[0-9A-Za-z.\-]+)?\s*$'
)

#: `^1.2.3` / `>=1.0.0` / `==1.2.3` / `1.2.3` / `*`
_CONSTRAINT_RE = re.compile(r'^\s*(\^|~|>=|<=|>|<|==|=)?\s*(.+?)\s*$')

_OPERATORS = ('^', '~', '>=', '<=', '>', '<', '==')


class Version:
    """不可变的三段式语义化版本。

    比较语义遵循 SemVer 11：主/次/修订按数值比较；有预发布号的版本
    **小于**同数字的正式版；预发布号逐段比较，数字段按数值、
    非数字段按 ASCII，数字段小于非数字段。
    """

    __slots__ = ('major', 'minor', 'patch', 'prerelease', '_raw')

    def __init__(self, major: int, minor: int, patch: int,
                 prerelease: Optional[str] = None, raw: Optional[str] = None):
        self.major = major
        self.minor = minor
        self.patch = patch
        self.prerelease = prerelease or None
        self._raw = raw or self._render()

    def _render(self) -> str:
        base = f'{self.major}.{self.minor}.{self.patch}'
        return f'{base}-{self.prerelease}' if self.prerelease else base

    @property
    def is_prerelease(self) -> bool:
        return self.prerelease is not None

    def _prerelease_key(self) -> Tuple:
        """把预发布号编成可比较的元组。正式版用哨兵保证大于任何预发布。

        每个元素都统一成 `(kind, value)` 二元组，避免释放版 `(1,)`
        与预发布 `((0,None),...)` 混着比时出现 `int vs tuple` 类型错误。
        """
        if self.prerelease is None:
            return ((1,),)              # 正式版永远最大（单元素占位）
        parts: List[Tuple[int, object]] = [(0, None)]
        for seg in self.prerelease.split('.'):
            if seg.isdigit():
                parts.append((0, int(seg)))      # 数字段 < 非数字段
            else:
                parts.append((1, seg))
        return tuple(parts)

    def _key(self) -> Tuple:
        return (self.major, self.minor, self.patch, self._prerelease_key())

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __le__(self, other):
        return self == other or self < other

    def __gt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return other < self

    def __ge__(self, other):
        return self == other or other < self

    def __hash__(self):
        return hash(self._key())

    def __str__(self):
        return self._raw

    def __repr__(self):
        return f'<版本 {self._raw}>'


def parse_version(text: str) -> Version:
    """解析版本号字面量。允许可选的 `v` 前缀（对齐 git tag 习惯）。"""
    if not isinstance(text, str):
        raise InvalidVersion(f'版本号必须是字符串，得到 {type(text).__name__}')
    m = _VERSION_RE.match(text)
    if not m:
        raise InvalidVersion(
            f'版本号格式不合法：{text!r}（应形如 1.2.3 或 1.2.3-rc.1）')
    major, minor, patch, pre = m.groups()
    normalized = f'{int(major)}.{int(minor)}.{int(patch)}'
    if pre:
        normalized += f'-{pre}'
    return Version(int(major), int(minor), int(patch), pre, normalized)


def _upper_bound_caret(v: Version) -> Version:
    """`^` 的上界：允许不改变「最左侧非零段」的升级（对齐 Cargo/npm）。"""
    if v.major > 0:
        return Version(v.major + 1, 0, 0)
    if v.minor > 0:
        return Version(0, v.minor + 1, 0)
    return Version(0, 0, v.patch + 1)


def _upper_bound_tilde(v: Version) -> Version:
    """`~` 的上界：锁定主+次，只允许修订号升级。"""
    return Version(v.major, v.minor + 1, 0)


def parse_constraint(text: str) -> List[Tuple[str, Optional[Version]]]:
    """把约束串解析为 `(运算符, 版本)` 子句列表，语义为逻辑与。

    `*` / 空串解析为 `[('*', None)]`（任意版本）。
    `^` / `~` 会**展开**成 `>=` 与 `<` 两个子句，便于统一求交。
    """
    if text is None:
        raise InvalidConstraint('版本约束不能为空')
    raw = str(text).strip()
    if raw in ('', '*', '任意'):
        return [('*', None)]

    clauses: List[Tuple[str, Optional[Version]]] = []
    for piece in raw.split(','):
        piece = piece.strip()
        if not piece:
            continue
        m = _CONSTRAINT_RE.match(piece)
        if not m:
            raise InvalidConstraint(f'版本约束格式不合法：{piece!r}')
        op, ver_text = m.group(1), m.group(2)
        op = '==' if op in (None, '=') else op
        version = parse_version(ver_text)
        if op == '^':
            clauses.append(('>=', version))
            clauses.append(('<', _upper_bound_caret(version)))
        elif op == '~':
            clauses.append(('>=', version))
            clauses.append(('<', _upper_bound_tilde(version)))
        else:
            clauses.append((op, version))
    if not clauses:
        raise InvalidConstraint(f'版本约束格式不合法：{raw!r}')
    return clauses


def _constraint_mentions_prerelease(
        clauses: List[Tuple[str, Optional[Version]]]) -> bool:
    return any(v is not None and v.is_prerelease for _, v in clauses)


def matches(version, constraint) -> bool:
    """判断 `version` 是否满足 `constraint`。

    参数都接受字符串或已解析对象。预发布版本只在约束**显式提及**
    预发布号时才可能命中（npm / Cargo 规则），避免误装半成品。
    """
    v = version if isinstance(version, Version) else parse_version(version)
    clauses = (constraint if isinstance(constraint, list)
               else parse_constraint(constraint))

    if clauses == [('*', None)]:
        return not v.is_prerelease

    if v.is_prerelease and not _constraint_mentions_prerelease(clauses):
        return False

    for op, target in clauses:
        if op == '*':
            continue
        if op == '==' and not v == target:
            return False
        if op == '>=' and not v >= target:
            return False
        if op == '>' and not v > target:
            return False
        if op == '<=' and not v <= target:
            return False
        if op == '<' and not v < target:
            return False
    return True


def max_satisfying(versions, constraint) -> Optional[Version]:
    """返回满足约束的**最大**版本；无解返回 None。"""
    parsed = [v if isinstance(v, Version) else parse_version(v)
              for v in versions]
    ok = [v for v in parsed if matches(v, constraint)]
    return max(ok) if ok else None
