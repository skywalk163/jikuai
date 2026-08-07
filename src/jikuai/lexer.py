# -*- coding: utf-8 -*-
"""极快语言 - 无空格词法分析器（Lexer）。

分词策略（v0.3.1 · ADR-06 方案 A：白名单最优先）：
  1. 用户定义名白名单严格匹配（`_try_user_def_strict`）
  2. 最长关键字/动词匹配（贪心）
  3. 百家姓标识符识别
  4. 中文数字字面量转换
  5. 一般汉字标识符

白名单来源 = 本次源码预扫描（`_prescan_definitions`）∪ 外部注入
（`external_defs`，REPL 会话级累积）。

⚠️ ADR-06 方案 A 副作用（同次分词全域生效）：
一旦某内建动词名被登记进 user_defs 白名单，该名字在同次分词的**全域范围内**
（包含类定义之外的顶层语句、同类其他方法体内、REPL 同一会话的后续输入）都
失去内建动词语义、被整体识别为 IDENT。规避方式：方法/字段命名避开内建动词名，
或把含动词名的类定义与使用同名动词的代码拆到不同 .jk 文件。
"""

from .tokens import Token, TokenType
from .keywords import (
    ALL_KEYWORDS, VERB_ARITY, ADVERBS, PUNCTUATION,
    CHINESE_DIGITS, CHINESE_UNITS, chinese_to_number
)
from .surnames import is_surname, is_compound_surname, COMPOUND_SURNAMES


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

    def __init__(self, source, external_defs=None):
        self.source = source
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens = []
        # 预计算关键字和动词按长度分组
        all_words = set(ALL_KEYWORDS) | set(VERB_ARITY.keys()) | set(ADVERBS)
        self.max_word_len = max(len(w) for w in all_words) if all_words else 4
        self._word_set = all_words
        # 用户定义标识符（预扫描获取，防止被切分）。
        # external_defs：外部注入的用户定义名集合（REPL 会话级），与 prescan 结果取并集。
        # _scan_src：注释与字符串内容被掩码为空格的源码副本（长度/行结构不变），
        # 供预扫描使用，避免注释里的 `-- 定义函数` 把 `函数` 误登记为用户名。
        self._scan_src = self._mask_source()
        self._user_defs = self._prescan_definitions()
        if external_defs:
            self._user_defs |= set(external_defs)
        # 按长度降序缓存，供 _try_user_def_strict 使用（R-A 规则 1）
        self._defs_by_len = sorted(self._user_defs, key=len, reverse=True)

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
        """预扫描源码中的关键字后紧跟的标识符，收集到 user_defs 白名单。

        覆盖：`定义 X`、`函数 X`、`方法 X`、`类 X`，以及（v0.3.1 扩容）
        `类` 块作用域内的 `自身.X =` 字段赋值名。这样后续 `_read_han`
        的 `_try_user_def_strict` 能优先匹配到完整名字，避免姓氏后跟随的
        动词字符（如 `乘`、`加`）把标识符切成两段。

        IDENT 提取规则：从关键字紧邻位置开始（先跳过空白），首字必须是
        汉字/字母/下划线（不能是数字），随后贪婪读取连续的汉字/字母/
        数字/下划线，直到遇到 `(`、`：`、`:`、`=`、空白或换行为止。

        标记字前置边界：标记（`定义`/`函数`/`方法`/`类`）必须位于源码开头
        或紧跟一个非汉字字符，避免 `分类` 这类词内命中产生噪声名字。
        """
        defs = set()
        s = self._scan_src  # 使用掩码源码（注释/字符串内容已替换为空格）
        # (keyword, keyword_len)
        markers = [('定义', 2), ('函数', 2), ('方法', 2), ('类', 1)]
        i = 0
        n = len(s)
        while i < n:
            matched = None
            for kw, klen in markers:
                if s[i:i + klen] == kw and (i == 0 or not _is_han(s[i - 1])):
                    matched = (kw, klen)
                    break
            if matched is None:
                i += 1
                continue
            kw, klen = matched
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
                    defs.add(''.join(name))
            i = j if j > i else i + 1
        defs |= self._prescan_self_fields()
        return defs

    def _prescan_self_fields(self):
        """R-D：收集 `类` 块作用域内 `自身.X =` 形式的字段赋值名。

        只有紧随 `=`（可含空格/制表符）的成员名才算字段赋值；
        `自身.X 加 1` 这类读取表达式不纳入。类外出现的 `自身.X =` 一律不收集。

        v0.3.2（D-12）：使用 `_scan_src`（注释/字符串内容已掩码为空格）而非原文，
        避免 `-- 类 X` 这类注释里 `自身.Y = 1` 被误登记，或字符串字面量里出现
        `自身.伪 = 1` 造成的假阳性。
        """
        fields = set()
        s = self._scan_src
        for start, end in self._class_regions():
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

    def _class_regions(self):
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
        """暴露本次 tokenize 收集到的 user_defs（供 REPL 会话级累积）。"""
        return frozenset(self._user_defs)

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
        """核心：汉字分词。ADR-06 新优先级：

          1. `_try_user_def_strict()`   ← 最高优先（方案 A 白名单）
          2. `_try_longest_keyword()`   ← 降为第二
          3. 百家姓标识符
          4. 中文数字
          5. 一般标识符

        DP-4 / R-E：原先位于 keyword 之后的 `_try_user_def` 调用点已删除，
        白名单只有这一条路径，禁止双路径并存。
        """
        start_col = self.col

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

    def _try_user_def_strict(self):
        """R-A 严格匹配用户定义名。仅在 `_read_han` 入口（起始位置）触发。

        规则：
          1. 按名字长度降序尝试（长名优先，`返回值` 先于 `返回`）
          2. `source[pos:pos+len(name)] == name`（完整匹配，不允许前缀命中）
          3. 匹配后的下一个字符必须是"边界"：非汉字/非字母数字/非下划线，
             或 EOF；若下一位置本身起始于一个完整关键字/动词，也视为边界
             （使 `自身.次数加1` 这类"名字+动词"紧邻写法仍可切分）。

        ⚠️ 作用范围（QA 实测取证）：白名单命中**不做位置/作用域区分**，
        `self._user_defs` 是整次分词共享的平坦集合。因此一旦某内建动词名进入
        白名单，它在**同次分词的全域**都被识别为 IDENT，具体包括：
          - 类定义之外的顶层语句（如 `打印 长度 郑列` → `名称错误：未定义的标识符：长度`）
          - 同一个类里其他方法的方法体（如 `长度 自身.吴项`）
          - REPL 同一会话的后续输入（`_session_defs` 会话级累积所致）
        括号写法（`长度(郑列)`）**不能**规避：名字已是 IDENT，`FuncCall` 按环境
        变量解析而非 `verbs` 内建表。唯一可行规避是命名避开内建动词名，
        或把定义与使用拆到不同 .jk 文件（不同次分词）。

        Returns: 完整名字 str 或 None
        """
        s = self.source
        p = self.pos
        for name in self._defs_by_len:
            end = p + len(name)
            if s[p:end] != name:
                continue
            if not self._is_def_boundary(end):
                continue
            return name
        return None

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


def tokenize(source, external_defs=None):
    """对极快源代码进行词法分析。

    external_defs：可选的外部用户定义名集合（REPL 会话级白名单）。
    """
    return Lexer(source, external_defs).tokenize()
