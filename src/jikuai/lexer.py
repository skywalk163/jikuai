# -*- coding: utf-8 -*-
"""极快语言 - 无空格词法分析器（Lexer）。

分词策略（v0.4.0 · ADR-09 方案 X1：类作用域限定白名单）：
  1. 用户定义名白名单严格匹配（`_try_user_def_strict`，**按位置查 ScopeMap**）
  2. 最长关键字/动词匹配（贪心）
  3. 百家姓标识符识别
  4. 中文数字字面量转换
  5. 一般汉字标识符

白名单来源 = 本次源码预扫描（`_prescan_definitions` 构建 `ScopeMap`）∪
外部注入（`external_defs`，REPL 会话级累积，全域可见）。

关键区别（ADR-09 vs ADR-06 方案 A）：
  - **顶层 `定义/函数/类 X`**：scope=[定义点, EOF)，全域可见（与旧行为等价）。
  - **类内 `方法 X` / `自身.X=`**：scope=[类块起点, 类块终点)，**仅该类字符区间内**
    生效；类外恢复内建动词/关键字语义。
  - **成员访问后**（前一 token 为 `.`）：作用域检查松弛为「本次分词全部用户定义
    名」，保证 `实例.成员` 无论跨类都能整体识别（AC-67 / AC-70）。
  - `external_defs`：会话级注入，视作全域可见（scope=[0, EOF)）。

回退开关：环境变量 `JIKUAI_LEGACY_ADR06=1` 时，`ScopeMap.visible_at` 退化为
返回全部用户定义名（等价旧的平坦集合行为），用于紧急兜底。
"""

import os
from dataclasses import dataclass
from typing import Optional

from .tokens import Token, TokenType
from .keywords import (
    ALL_KEYWORDS, VERB_ARITY, ADVERBS, PUNCTUATION,
    CHINESE_DIGITS, CHINESE_UNITS, chinese_to_number
)
from .surnames import is_surname, is_compound_surname, COMPOUND_SURNAMES


# ---------------------------------------------------------------------------
# ADR-09 数据模型：DefEntry / ScopeMap
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DefEntry:
    """一条用户定义名的字符可见区间记录。

    Attributes:
        name:          标识符名字（不含边界）。
        kind:          'class' | 'func' | 'define' | 'method' | 'field' | 'external'。
        scope_start:   源码字符偏移（闭区间起点）。
        scope_end:     源码字符偏移（开区间终点）；`-1` 代表 EOF。
        owner_class:   若属于类内成员（method/field），记录所属类名；否则为 None。

    空区间约定（DEF-02）：`scope_start == scope_end == 0` 表示「**任何位置都不
    可见**」。用于 REPL 注入的类内成员——本次源码里找不到其 owner_class 的类块
    时登记为空区间，使它只能通过 `.成员` 松弛路径命中，不会在顶层夺走内建动词
    语义。
    """
    name: str
    kind: str
    scope_start: int
    scope_end: int
    owner_class: Optional[str] = None


# DEF-02 空区间哨兵：[0, 0) 为空集合，`visible_at` 任何 offset 都不命中。
EMPTY_SCOPE = (0, 0)

# 受类作用域约束的成员类别（不可提升为全域可见）
SCOPED_KINDS = frozenset({'method', 'field'})


class ScopeMap:
    """字符位置 → 可见用户定义名集合。

    ADR-09 语义：一个名字可以有**多条**登记记录（不同类各自登记同名方法），
    `visible_at(offset)` 返回**所有覆盖该 offset 的记录**的名字并集。

    ⚠️ 稳定契约：
      - `all_names()` 返回本次分词内出现过的全部用户定义名（供 `.成员` 松弛路径
        与 `get_user_defs()` 旧契约使用）。
      - `signatures()` 返回 `(name, kind, owner_class)` 三元组集合（供 REPL
        会话级累积，DEF-02 起替代平坦名字集合）。
      - `visible_at(offset)` 若 `JIKUAI_LEGACY_ADR06=1`，则退化为 `all_names()`。
    """

    def __init__(self, legacy: bool = False):
        self._entries: list[DefEntry] = []
        self._legacy = legacy
        self._names_cache: Optional[frozenset[str]] = None

    def add(self, entry: DefEntry) -> None:
        self._entries.append(entry)
        self._names_cache = None

    def add_global(self, name: str, kind: str = 'external',
                   owner_class: Optional[str] = None) -> None:
        """便捷方法：登记一个全域可见的用户定义名。"""
        self.add(DefEntry(
            name=name, kind=kind,
            scope_start=0, scope_end=-1,
            owner_class=owner_class,
        ))

    def add_member_only(self, name: str, kind: str,
                        owner_class: Optional[str] = None) -> None:
        """DEF-02：登记一个「仅成员访问可见」的名字（空区间）。

        用于 REPL 注入的类内 method/field，其 owner_class 的类块不在本次源码中：
        名字仍进入 `all_names()`（`.成员` 松弛路径可命中），但 `visible_at()`
        在任何位置都不返回它——顶层 `打印 长度 列 1 2 3` 因此仍走内建动词。
        """
        start, end = EMPTY_SCOPE
        self.add(DefEntry(
            name=name, kind=kind,
            scope_start=start, scope_end=end,
            owner_class=owner_class,
        ))

    def visible_at(self, offset: int) -> frozenset[str]:
        """返回 offset 处可见的名字集合。"""
        if self._legacy:
            return self.all_names()
        names = set()
        for e in self._entries:
            if offset < e.scope_start:
                continue
            if e.scope_end != -1 and offset >= e.scope_end:
                continue
            names.add(e.name)
        return frozenset(names)

    def all_names(self) -> frozenset[str]:
        """本次分词内出现过的全部用户定义名（不做作用域过滤）。"""
        if self._names_cache is None:
            self._names_cache = frozenset(e.name for e in self._entries)
        return self._names_cache

    def signatures(self) -> frozenset:
        """返回 `(name, kind, owner_class)` 三元组集合（DEF-02 会话级累积契约）。"""
        return frozenset((e.name, e.kind, e.owner_class) for e in self._entries)

    def entries(self) -> list[DefEntry]:
        """暴露内部条目列表（供测试/调试用，返回副本）。"""
        return list(self._entries)


def _is_han(ch):
    """判断是否为 CJK 统一汉字。"""
    cp = ord(ch)
    return 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF


def _is_chinese_number_char(ch):
    return ch in CHINESE_DIGITS or ch in CHINESE_UNITS


# 允许出现在源码中但本身不产生 token 的"合法非 token"字符白名单。
# 其余落到兜底分支的字符一律视为非法字符（LEXER 错误），不再静默吞掉。
_ALLOWED_NON_TOKEN = {
    '\u200b',   # 零宽空格（编辑器易带入）
    '\ufeff',   # BOM
}


class Lexer:
    """极快语言词法分析器。"""

    def __init__(self, source, external_defs=None, class_regions=None):
        self.source = source
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens = []
        # ADR-06 X2（v0.5.0）：可选的「权威类区间」。为 None 时走行文本启发式
        # `_heuristic_class_regions`（与 v0.4.x 字节级等价，258 基线零回归）；
        # 非 None 时用 parser 权威区间替代启发式，由 frontend.compile_source
        # 在两遍分词的 Pass2 注入。格式：[(start_char, end_char), ...]，坐标与
        # `_scan_src` 的字符偏移一致（掩码保持长度不变，故等同原始源码偏移）。
        self._external_class_regions = class_regions
        self._class_regions_cache = None
        # 预计算关键字和动词按长度分组
        all_words = set(ALL_KEYWORDS) | set(VERB_ARITY.keys()) | set(ADVERBS)
        self.max_word_len = max(len(w) for w in all_words) if all_words else 4
        self._word_set = all_words
        # T-06 · ADR-09 回退开关：JIKUAI_LEGACY_ADR06=1 → 退回旧的平坦集合行为。
        self.legacy_adr06 = os.environ.get('JIKUAI_LEGACY_ADR06') == '1'
        # 用户定义标识符（预扫描获取，防止被切分）。
        # external_defs：外部注入的用户定义名（REPL 会话级）。DEF-02 起支持两种
        # 元素形态：`str`（旧契约，全域可见）与 `(name, kind, owner_class)` 三元组
        # （类内 method/field 保持类作用域，不提升为会话全域）。
        # _scan_src：注释与字符串内容被掩码为空格的源码副本（长度/行结构不变），
        # 供预扫描使用，避免注释里的 `-- 定义函数` 把 `函数` 误登记为用户名。
        self._scan_src = self._mask_source()
        # T-01/T-02/T-03：预扫描输出 ScopeMap（含每条 def 的字符区间）
        self.scope_map = self._prescan_definitions()
        if external_defs:
            self._register_external_defs(external_defs)
        # 全量名字集合（供 get_user_defs 契约与 `.成员` 松弛路径使用）
        self._user_defs = set(self.scope_map.all_names())
        # 按长度降序缓存候选名（长名优先，`返回值` 先于 `返回`）——R-A 规则 1
        self._defs_by_len = sorted(self._user_defs, key=len, reverse=True)

    def _register_external_defs(self, external_defs):
        """DEF-02：把外部注入的定义名按作用域登记进 `ScopeMap`。

        元素形态：
          - `str` → 全域可见（`kind='external'`）。保持 v0.3.1 `external_defs`
            旧契约，供直接调用 `tokenize(src, external_defs={'X'})` 的既有测试。
          - `(name, kind, owner_class)` → 按 kind 分流：
            * kind ∈ `SCOPED_KINDS`（method/field）且 owner_class 非空：
              若本次源码中存在同名类块 → 登记到该类的每个字符区间；
              否则 → `add_member_only`（空区间，只能经 `.成员` 松弛命中）。
            * 其他 kind（class/func/define/external）→ 全域可见。

        这条规则是 DEF-02 的核心修复：REPL 上一轮定义的 `方法 长度` 不再被
        提升为会话全域，因此下一轮顶层 `打印 长度 列 1 2 3。` 仍走内建动词。
        """
        regions_by_class = self._class_regions_by_name()
        for item in external_defs:
            if isinstance(item, str):
                self.scope_map.add_global(item, kind='external')
                continue
            try:
                name, kind, owner = item
            except (TypeError, ValueError):
                # 非预期形态：保守地按全域名字处理，不让分词整体失败
                self.scope_map.add_global(str(item), kind='external')
                continue
            if kind in SCOPED_KINDS and owner:
                regions = regions_by_class.get(owner)
                if regions:
                    for start, end in regions:
                        self.scope_map.add(DefEntry(
                            name=name, kind=kind,
                            scope_start=start, scope_end=end,
                            owner_class=owner,
                        ))
                else:
                    self.scope_map.add_member_only(name, kind, owner)
            else:
                self.scope_map.add_global(name, kind=kind or 'external',
                                          owner_class=owner)

    def _class_regions_by_name(self):
        """类名 → 该类的字符区间列表（同名类多次定义时可有多段）。"""
        out = {}
        for start, end in self._class_regions():
            cname = self._class_name_at(start)
            if cname:
                out.setdefault(cname, []).append((start, end))
        return out

    # ------------------------------------------------------------------
    # 预扫描（user_defs 白名单）
    # ------------------------------------------------------------------

    def _mask_source(self):
        """把注释与字符串字面量内容替换为空格，保持索引与换行结构不变。

        预扫描是纯文本扫描，不经过 tokenize，因此必须先屏蔽注释/字符串，
        否则 `-- 定义函数` 这类注释会把关键字本身登记进 user_defs 白名单，
        在「白名单最优先」下直接把关键字降级为 IDENT。
        """
        s = self.source
        out = list(s)
        n = len(s)
        close_map = {'"': '"', "'": "'", '\u201c': '\u201d', '\u2018': '\u2019'}
        i = 0
        while i < n:
            ch = s[i]
            # 行注释：# 或 --
            if ch == '#' or (ch == '-' and i + 1 < n and s[i + 1] == '-'):
                while i < n and s[i] != '\n':
                    out[i] = ' '
                    i += 1
                continue
            # 字符串字面量
            if ch in close_map:
                close = close_map[ch]
                out[i] = ' '
                i += 1
                while i < n and s[i] != close:
                    if s[i] == '\\' and i + 1 < n:
                        out[i] = ' '
                        i += 1
                    if i < n and s[i] != '\n':
                        out[i] = ' '
                    i += 1
                if i < n:
                    out[i] = ' '
                    i += 1
                continue
            i += 1
        return ''.join(out)

    def _prescan_definitions(self):
        """T-02/T-03：预扫描源码，构建并返回 `ScopeMap`（不再是平坦 `set[str]`）。

        覆盖：`定义 X`、`函数 X`、`方法 X`、`类 X`，以及 `类` 块作用域内的
        `自身.X =` 字段赋值名。

        ADR-09 作用域规则：

        | 标记       | kind     | scope_start        | scope_end        | owner_class |
        |------------|----------|--------------------|------------------|-------------|
        | `定义 X`   | define   | 标记字位置         | -1（EOF）        | None        |
        | `函数 X`   | func     | 标记字位置         | -1（EOF）        | None        |
        | `类 X`     | class    | 标记字位置         | -1（EOF）        | None        |
        | `方法 X`   | method   | 所属类块起点       | 所属类块终点     | 类名        |
        | `自身.X =` | field    | 所属类块起点       | 所属类块终点     | 类名        |

        `方法 X` 若落在任何类块之外（语法上非法的写法，但预扫描是纯文本扫描），
        退化为「标记字位置 → EOF」的全域记录，保持保守不误切。

        IDENT 提取规则：从关键字紧邻位置开始（先跳过空白），首字必须是
        汉字/字母/下划线（不能是数字），随后贪婪读取连续的汉字/字母/
        数字/下划线，直到遇到 `(`、`：`、`:`、`=`、空白或换行为止。

        标记字前置边界：标记（`定义`/`函数`/`方法`/`类`）必须位于源码开头
        或紧跟一个非汉字字符，避免 `分类` 这类词内命中产生噪声名字。
        """
        scope_map = ScopeMap(legacy=self.legacy_adr06)
        s = self._scan_src  # 使用掩码源码（注释/字符串内容已替换为空格）
        n = len(s)
        regions = self._class_regions()
        # (keyword, keyword_len, kind)
        markers = [('定义', 2, 'define'), ('函数', 2, 'func'),
                   ('方法', 2, 'method'), ('类', 1, 'class')]
        i = 0
        while i < n:
            matched = None
            for kw, klen, kind in markers:
                if s[i:i + klen] == kw and (i == 0 or not _is_han(s[i - 1])):
                    matched = (kw, klen, kind)
                    break
            if matched is None:
                i += 1
                continue
            kw, klen, kind = matched
            marker_pos = i
            j = i + klen
            # 跳过空白（不含换行会造成歧义，故仅跳过空格/制表符）
            while j < n and s[j] in ' \t':
                j += 1
            # IDENT 首字必须是汉字/字母/下划线（不允许数字起头）
            if j < n and (_is_han(s[j]) or (s[j].isascii() and s[j].isalpha()) or s[j] == '_'):
                name = []
                while j < n:
                    c = s[j]
                    if _is_han(c) or c.isalnum() or c == '_':
                        name.append(c)
                        j += 1
                    else:
                        break
                if name:
                    scope_map.add(self._make_entry(
                        ''.join(name), kind, marker_pos, regions))
            i = j if j > i else i + 1
        # 类内 `自身.X =` 字段：作用域严格限定在所属类块区间
        for region in regions:
            start, end = region
            owner = self._class_name_at(start)
            for name in self._collect_self_fields(start, end):
                scope_map.add(DefEntry(
                    name=name, kind='field',
                    scope_start=start, scope_end=end,
                    owner_class=owner,
                ))
        return scope_map

    def _make_entry(self, name, kind, marker_pos, regions):
        """按 kind 与位置生成 `DefEntry`（ADR-09 作用域规则表见上）。"""
        if kind == 'method':
            region = self._region_of(marker_pos, regions)
            if region is not None:
                start, end = region
                return DefEntry(
                    name=name, kind=kind,
                    scope_start=start, scope_end=end,
                    owner_class=self._class_name_at(start),
                )
        # define / func / class，以及类块外的孤立 `方法 X`：全域可见
        return DefEntry(
            name=name, kind=kind,
            scope_start=marker_pos, scope_end=-1,
            owner_class=None,
        )

    @staticmethod
    def _region_of(pos, regions):
        """返回包含 pos 的最内层类块区间；无则 None。"""
        best = None
        for start, end in regions:
            if start <= pos < end:
                if best is None or start >= best[0]:
                    best = (start, end)
        return best

    def _class_name_at(self, region_start):
        """从类块起点解析类名（`类 X：` / `类 X 继承 Y：`）。解析不到返回 None。"""
        s = self._scan_src
        n = len(s)
        i = region_start
        # 跳过缩进
        while i < n and s[i] in ' \t':
            i += 1
        if not s.startswith('类', i):
            return None
        j = i + 1
        while j < n and s[j] in ' \t':
            j += 1
        name = []
        while j < n and (_is_han(s[j]) or s[j].isalnum() or s[j] == '_'):
            name.append(s[j])
            j += 1
        return ''.join(name) or None

    def _collect_self_fields(self, start, end):
        """R-D：在给定区间 [start, end) 内收集 `自身.X =` 形式的字段赋值名。

        只有紧随 `=`（可含空格/制表符）的成员名才算字段赋值；
        `自身.X 加 1` 这类读取表达式不纳入。

        v0.3.2（D-12）：使用 `_scan_src`（注释/字符串内容已掩码为空格）而非原文，
        避免 `-- 类 X` 这类注释里 `自身.Y = 1` 被误登记，或字符串字面量里出现
        `自身.伪 = 1` 造成的假阳性。
        """
        fields = set()
        s = self._scan_src
        i = start
        while i < end:
            k = s.find('自身.', i, end)
            if k < 0:
                break
            j = k + 3
            name = []
            while j < end and (_is_han(s[j]) or s[j].isalnum() or s[j] == '_'):
                name.append(s[j])
                j += 1
            m = j
            while m < end and s[m] in ' \t':
                m += 1
            if name and m < end and s[m] == '=':
                fields.add(''.join(name))
            i = max(j, k + 3)
        return fields

    def _prescan_self_fields(self):
        """兼容保留：返回所有类块内 `自身.X =` 字段名的平坦集合。

        ADR-09 起字段作用域由 `_prescan_definitions` 直接登记到 `ScopeMap`；
        本方法仅供外部工具/诊断使用，不参与分词判定。
        """
        fields = set()
        for start, end in self._class_regions():
            fields |= self._collect_self_fields(start, end)
        return fields

    def _class_regions(self):
        """类块字符区间 [(start, end), ...]。

        ADR-06 X2（v0.5.0）分流：
          - 若 frontend 注入了权威区间（`_external_class_regions` 非 None），
            直接采用——这是 parser 权威结果，消除行文本启发式的边界歧义。
          - 否则回退到 `_heuristic_class_regions()`（原行文本启发式，
            与 v0.4.x 字节级等价）。

        结果缓存，避免 `_prescan_definitions` / `_class_regions_by_name` /
        `_prescan_self_fields` 三处调用重复计算。
        """
        if self._class_regions_cache is not None:
            return self._class_regions_cache
        if self._external_class_regions is not None:
            regions = list(self._external_class_regions)
        else:
            regions = self._heuristic_class_regions()
        self._class_regions_cache = regions
        return regions

    def _heuristic_class_regions(self):
        """R-D：定位 `类` 块的字符区间列表 [(start, end), ...]。

        判定（纯文本启发式，不依赖 parser）：
          - 起点：某行去掉缩进后以 `类` 开头。
          - 终点：其后第一条「缩进量 <= `类` 行缩进量、且内容仅为 `。`」的行末；
            或遇到同级/更外层的新 `类` 行；都没有则到源码末尾。

        v0.3.2（D-12）：改用 `_scan_src`（注释/字符串内容已掩码为空格，长度与
        换行结构与原文完全一致，因此行偏移可直接用于 `_prescan_self_fields`）。
        这样 `-- 类 X：` 注释行不再被当作类块起点，多行字符串里的 `类 X：` /
        独立 `。` 行也不再干扰区间边界。
        """
        s = self._scan_src
        lines = s.split('\n')
        offsets = []
        p = 0
        for ln in lines:
            offsets.append(p)
            p += len(ln) + 1
        regions = []
        i = 0
        total = len(lines)
        while i < total:
            stripped = lines[i].lstrip()
            if not stripped.startswith('类'):
                i += 1
                continue
            indent = len(lines[i]) - len(stripped)
            end = len(s)
            j = i + 1
            while j < total:
                body = lines[j].strip()
                body_indent = len(lines[j]) - len(lines[j].lstrip())
                if body:
                    if body == '。' and body_indent <= indent:
                        end = offsets[j] + len(lines[j])
                        break
                    if body.startswith('类') and body_indent <= indent:
                        end = offsets[j]
                        break
                j += 1
            regions.append((offsets[i], end))
            i = max(j, i + 1)
        return regions

    def get_user_defs(self):
        """暴露本次 tokenize 收集到的 user_defs（供 REPL 会话级累积）。

        ADR-09 契约保持：返回**全部**用户定义名的平坦 frozenset（不含作用域信息）。
        DEF-02 起 REPL 改走 `get_user_def_signatures()` 携带 kind/owner_class 累积，
        本方法保留为向下兼容契约（v0.3.x 测试直接依赖它）。
        """
        return self.scope_map.all_names()

    def get_user_def_signatures(self):
        """DEF-02：返回 `(name, kind, owner_class)` 三元组集合。

        REPL 会话级 `_session_defs` 使用本方法累积，以便下一次分词时按类归属
        判定是否提升为会话全域（顶层可见）或维持类作用域（仅 `.成员` 可见）。
        """
        return self.scope_map.signatures()

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def tokenize(self):
        """执行词法分析，返回 Token 列表。"""
        while self.pos < len(self.source):
            ch = self.source[self.pos]

            # 跳过空白（非换行）
            if ch in ' \t\r':
                self.pos += 1
                self.col += 1
                continue

            # 换行
            if ch == '\n':
                self.tokens.append(Token(TokenType.NEWLINE, '\n', self.line, self.col))
                self.pos += 1
                self.line += 1
                self.col = 1
                continue

            # 注释：-- 或 # 到行尾
            if ch == '#' or (ch == '-' and self.pos + 1 < len(self.source) and self.source[self.pos + 1] == '-'):
                self._skip_line_comment()
                continue

            # 字符串
            if ch == '"' or ch == "'" or ch == '\u201c' or ch == '\u2018':
                self._read_string(ch)
                continue

            # 人民币金额 ￥
            if ch == '￥' or ch == '¥':
                self._read_money()
                continue

            # ASCII数字
            if ch.isdigit():
                self._read_number()
                continue

            # 标点符号（中文/ASCII）
            if ch in PUNCTUATION:
                self.tokens.append(Token(
                    TokenType[PUNCTUATION[ch]], ch, self.line, self.col
                ))
                self.pos += 1
                self.col += 1
                continue

            # ASCII 标识符（只接受 ASCII 字母/下划线开头）
            if (ch.isascii() and ch.isalpha()) or ch == '_':
                self._read_ascii_ident()
                continue

            # 汉字处理（核心分词逻辑）
            if _is_han(ch):
                self._read_han()
                continue

            # 允许的非 token 字符（BOM、零宽空格等）：静默跳过
            if ch in _ALLOWED_NON_TOKEN:
                self.pos += 1
                self.col += 1
                continue

            # R1: 未识别字符 —— 抛 LEXER 错误，不再静默吞掉
            self._raise_lexer_error(
                f"非法字符：{ch!r}（Unicode U+{ord(ch):04X}）",
                self.line, self.col,
            )

        self.tokens.append(Token(TokenType.EOF, '', self.line, self.col))
        return self.tokens

    def _raise_lexer_error(self, msg, line, col):
        """抛出携带 ErrorInfo 的 JiKuaiError（LEXER 类别）。"""
        from .errors import ErrorInfo, ErrorCategory
        from .evaluator import JiKuaiError
        info = ErrorInfo(
            category=ErrorCategory.LEXER,
            message=msg,
            line=line,
            col=col,
        )
        raise JiKuaiError(info=info)

    def _peek(self, offset=0):
        idx = self.pos + 1 + offset
        return self.source[idx] if idx < len(self.source) else ''

    def _skip_line_comment(self):
        while self.pos < len(self.source) and self.source[self.pos] != '\n':
            self.pos += 1

    def _read_string(self, quote):
        """读取字符串字面量。支持 ASCII 与中文引号。"""
        close_map = {
            '"': '"',
            "'": "'",
            '\u201c': '\u201d',   # " -> "
            '\u2018': '\u2019',   # ' -> '
        }
        close = close_map.get(quote, quote)
        start_line, start_col = self.line, self.col
        self.pos += 1
        self.col += 1
        result = []
        while self.pos < len(self.source):
            c = self.source[self.pos]
            if c == close:
                self.pos += 1
                self.col += 1
                break
            if c == '\\':
                self.pos += 1
                self.col += 1
                esc = self.source[self.pos] if self.pos < len(self.source) else ''
                esc_map = {'n': '\n', 't': '\t', '\\': '\\', '"': '"', "'": "'"}
                result.append(esc_map.get(esc, esc))
            else:
                result.append(c)
            if c == '\n':
                self.line += 1
                self.col = 1
            else:
                self.col += 1
            self.pos += 1
        else:
            # 到达源码末尾仍未闭合 → 抛 LEXER 错误
            self._raise_lexer_error(
                f"未闭合的字符串（起始于第{start_line}行第{start_col}列）",
                start_line, start_col,
            )
        self.tokens.append(Token(TokenType.STRING, ''.join(result), start_line, start_col))

    def _read_money(self):
        """读取人民币金额 ￥123.45"""
        start_col = self.col
        self.pos += 1
        self.col += 1
        num_str = []
        while self.pos < len(self.source) and (self.source[self.pos].isdigit() or self.source[self.pos] == '.'):
            num_str.append(self.source[self.pos])
            self.pos += 1
            self.col += 1
        value = float(''.join(num_str)) if num_str else 0.0
        self.tokens.append(Token(TokenType.MONEY, value, self.line, start_col))

    def _read_number(self):
        """读取数字字面量。"""
        start_col = self.col
        num_str = []
        while self.pos < len(self.source) and (self.source[self.pos].isdigit() or self.source[self.pos] == '.'):
            num_str.append(self.source[self.pos])
            self.pos += 1
            self.col += 1
        text = ''.join(num_str)
        value = float(text) if '.' in text else int(text)
        self.tokens.append(Token(TokenType.NUMBER, value, self.line, start_col))

    def _read_ascii_ident(self):
        """读取 ASCII 标识符。"""
        start_col = self.col
        chars = []
        while self.pos < len(self.source) and self.source[self.pos].isascii() and (
                self.source[self.pos].isalnum() or self.source[self.pos] == '_'):
            chars.append(self.source[self.pos])
            self.pos += 1
            self.col += 1
        self.tokens.append(Token(TokenType.IDENT, ''.join(chars), self.line, start_col))

    # ------------------------------------------------------------------
    # 汉字分词（ADR-06 优先级）
    # ------------------------------------------------------------------

    def _read_han(self):
        """核心：汉字分词。ADR-06 优先级 + ADR-09 作用域限定：

          1. `_try_user_def_strict()`   ← 最高优先（白名单，**按 pos 查 ScopeMap**）
          2. `_try_longest_keyword()`   ← 第二（类外恢复内建动词/关键字语义）
          3. 百家姓标识符
          4. 中文数字
          5. 一般标识符

        DP-4 / R-E：白名单只有 `_try_user_def_strict` 这一条入口路径，
        禁止双路径并存。ADR-09 只改变**候选集合的来源**（平坦集合 → 按位置
        查询的 ScopeMap），不新增分支。

        ADR-10 · v0.4.0：`蟒:` Python 桥前缀在此处最优先短路。`蟒` 单字
        既非关键字也非动词/百家姓，只有紧跟 `:` / `：` 才启用该短路，
        因此不会与既有词法（例如常见词 `蟒蛇`）冲突。
        """
        start_col = self.col

        # 0. ADR-10 · `蟒:` Python 桥前缀（最优先，避免被 `_read_general_ident`
        #    切成两个 token）。产出一个 value 为 `蟒:X` 的 IDENT，parser 侧
        #    在 `_parse_import` 中识别前缀并路由到 `Import(kind='python')`。
        py_prefix = self._try_python_prefix()
        if py_prefix is not None:
            self.tokens.append(Token(TokenType.IDENT, py_prefix,
                                     self.line, start_col))
            self.pos += len(py_prefix)
            self.col += len(py_prefix)
            return

        # 1. 用户定义名白名单（最高优先）
        user_ident = self._try_user_def_strict()
        if user_ident:
            self.tokens.append(Token(TokenType.IDENT, user_ident, self.line, start_col))
            self.pos += len(user_ident)
            self.col += len(user_ident)
            return

        # 2. 最长匹配关键字/动词
        matched = self._try_longest_keyword()
        if matched:
            word, token = matched
            self.tokens.append(token)
            self.pos += len(word)
            self.col += len(word)
            return

        # 3. 百家姓开头 -> 标识符
        ch = self.source[self.pos]
        if is_surname(ch) or (self.pos + 1 < len(self.source) and
                               is_compound_surname(self.source[self.pos:self.pos+2])):
            ident = self._read_surname_ident()
            self.tokens.append(Token(TokenType.IDENT, ident, self.line, start_col))
            return

        # 4. 尝试中文数字
        cn_num = self._try_chinese_number()
        if cn_num is not None:
            self.tokens.append(Token(TokenType.NUMBER, cn_num[1], self.line, start_col))
            self.pos += cn_num[0]
            self.col += cn_num[0]
            return

        # 5. 其他汉字：作为标识符收集直到遇到关键字/动词
        ident = self._read_general_ident()
        self.tokens.append(Token(TokenType.IDENT, ident, self.line, start_col))

    # ------------------------------------------------------------------
    # ADR-10 · `蟒:` Python 桥前缀
    # ------------------------------------------------------------------

    #: `蟒:` 前缀的引导字。选它的理由：单字 `蟒` 不在 `ALL_KEYWORDS` /
    #: `VERB_ARITY` / `ADVERBS` / 百家姓表中的任一集合内，因此不会夺走
    #: 任何既有词法语义。
    PY_PREFIX_CHAR = '蟒'
    #: 允许的分隔符：ASCII `:` 与全角 `：`（两者在 `PUNCTUATION` 里都映射 COLON）
    PY_PREFIX_SEPS = ':：'

    def _try_python_prefix(self):
        """尝试匹配 `蟒:<模块名>`，命中返回整段文本（含前缀），否则 None。

        约束（避免与百家姓/动词/关键字冲突）：
          1. 必须以单字 `蟒` 开头，且**紧邻**（无空格）一个 `:` 或 `：`；
             仅 `蟒` 本身（如 `蟒蛇`）不触发，仍走原有分词路径。
          2. 模块名允许 ASCII 字母/数字/下划线/`.`（`os.path` 形态），
             同时也接受汉字，仅用于给出中文诊断（`py_import` 会因非法字符
             拒收，让 AC-96 得到「找不到 Python 模块」而不是 jk 加载错误）。
          3. 模块名为空（如 `蟒:。`）时不匹配，交由原路径。
        """
        s = self.source
        p = self.pos
        if s[p] != self.PY_PREFIX_CHAR:
            return None
        if p + 1 >= len(s) or s[p + 1] not in self.PY_PREFIX_SEPS:
            return None
        j = p + 2
        # 首字符：ASCII 字母/下划线 或 汉字（后者仅用于错误诊断）
        if j >= len(s):
            return None
        first = s[j]
        if not ((first.isascii() and (first.isalpha() or first == '_'))
                or _is_han(first)):
            return None
        while j < len(s):
            c = s[j]
            if _is_han(c) or (c.isascii() and (c.isalnum() or c == '_' or c == '.')):
                j += 1
            else:
                break
        # 末尾的 `.` 不并入模块名（`蟒:math.` 里的句点归还给 DOT）
        while j > p + 2 and s[j - 1] == '.':
            j -= 1
        return s[p:j]

    def _try_user_def_strict(self):
        """T-04：按 `self.pos` 查 `ScopeMap` 的严格用户定义名匹配。

        规则（R-A · ADR-09 版本）：
          1. 候选集合 = `scope_map.visible_at(self.pos)`（作用域过滤）；
             若前一 token 是 `.`（成员访问），则松弛为「本次分词全部用户定义名」，
             使 `实例.成员` 在类外也能整体识别（AC-67 / AC-70）。
          2. 按名字长度降序尝试（长名优先，`返回值` 先于 `返回`）
          3. `source[pos:pos+len(name)] == name`（完整匹配，不允许前缀命中）
          4. 匹配后的下一个字符必须是"边界"：非汉字/非字母数字/非下划线，
             或 EOF；若下一位置本身起始于一个完整关键字/动词，也视为边界。

        Returns: 完整名字 str 或 None
        """
        s = self.source
        p = self.pos
        # 后缀 `.成员` 松弛路径：跨作用域的成员访问不受类作用域限制
        if self._is_member_access_position():
            candidates = self._defs_by_len
        else:
            visible = self.scope_map.visible_at(p)
            if not visible:
                return None
            candidates = [n for n in self._defs_by_len if n in visible]
        for name in candidates:
            end = p + len(name)
            if s[p:end] != name:
                continue
            if not self._is_def_boundary(end):
                continue
            return name
        return None

    def _is_member_access_position(self):
        """判断当前 `_read_han` 入口的前一个 token 是否为 `.`（DOT）。"""
        return bool(self.tokens) and self.tokens[-1].type == TokenType.DOT

    def _is_def_boundary(self, idx):
        """判断 idx 处是否构成用户定义名的右边界（R-A 规则 3）。"""
        s = self.source
        if idx >= len(s):
            return True
        ch = s[idx]
        if not (_is_han(ch) or ch.isalnum() or ch == '_'):
            return True
        # 汉字/字母数字紧邻：仅当该位置起是一个完整关键字/动词时才算边界
        return self._word_at(idx) is not None

    def _word_at(self, idx):
        """返回 idx 处最长匹配的关键字/动词（不匹配则 None）。"""
        s = self.source
        for length in range(min(self.max_word_len, len(s) - idx), 0, -1):
            candidate = s[idx:idx + length]
            if candidate in self._word_set:
                return candidate
        return None

    def _try_longest_keyword(self):
        """从当前位置尝试最长匹配关键字/动词。"""
        for length in range(min(self.max_word_len, len(self.source) - self.pos), 0, -1):
            candidate = self.source[self.pos:self.pos + length]
            if candidate in self._word_set:
                # 决定 token 类型
                if candidate in ADVERBS:
                    return (candidate, Token(TokenType.ADVERB, candidate, self.line, self.col))
                elif candidate in VERB_ARITY:
                    arity = VERB_ARITY[candidate]
                    return (candidate, Token(TokenType.VERB, candidate, self.line, self.col, arity))
                else:
                    return (candidate, Token(TokenType.KEYWORD, candidate, self.line, self.col))
        return None

    def _read_surname_ident(self):
        """读取百家姓开头的标识符。"""
        chars = []
        # 先处理复姓
        if is_compound_surname(self.source[self.pos:self.pos+2]):
            chars.append(self.source[self.pos:self.pos+2])
            self.pos += 2
            self.col += 2
        else:
            chars.append(self.source[self.pos])
            self.pos += 1
            self.col += 1
        # 继续收集，直到遇到关键字/动词/标点
        while self.pos < len(self.source):
            ch = self.source[self.pos]
            if not _is_han(ch) and not ch.isalnum() and ch != '_':
                break
            if _is_han(ch):
                # 检查从此位置开始是否有关键字/动词匹配
                kw = self._try_longest_keyword()
                if kw:
                    break
            chars.append(ch)
            self.pos += 1
            self.col += 1
        return ''.join(chars)

    def _try_chinese_number(self):
        """尝试从当前位置读取中文数字。返回 (长度, 值) 或 None。"""
        i = self.pos
        while i < len(self.source) and _is_chinese_number_char(self.source[i]):
            i += 1
        if i == self.pos:
            return None
        text = self.source[self.pos:i]
        value = chinese_to_number(text)
        if value is not None:
            return (i - self.pos, value)
        return None

    def _read_general_ident(self):
        """读取一般汉字标识符（非百家姓开头的情况）。"""
        chars = []
        while self.pos < len(self.source):
            ch = self.source[self.pos]
            if not _is_han(ch) and not ch.isalnum() and ch != '_':
                break
            if _is_han(ch) and ch in PUNCTUATION:
                break
            # 检查后续是否有关键字/动词匹配
            if _is_han(ch) and chars:
                kw = self._try_longest_keyword()
                if kw:
                    break
            chars.append(ch)
            self.pos += 1
            self.col += 1
        return ''.join(chars)


def tokenize(source, external_defs=None, class_regions=None):
    """对极快源代码进行词法分析。

    external_defs：可选的外部用户定义名集合（REPL 会话级白名单）。
    class_regions：可选的权威类块字符区间 [(start, end), ...]（ADR-06 X2）。
        为 None 时走行文本启发式（默认，与 v0.4.x 等价）；非 None 时由
        frontend 在两遍分词 Pass2 注入 parser 权威区间。
    """
    return Lexer(source, external_defs, class_regions).tokenize()
