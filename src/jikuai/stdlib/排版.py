# -*- coding: utf-8 -*-
"""极快语言标准库 — 中文排版格式化（内部 Python 实现）。

本文件是 `排版.jk` 的内部实现（ADR-16 §3.3 混合模块）：`.jk` 是唯一对外
门面，本文件不参与模块名解析。

三条规则（口径见 docs/标准库.md，必须与之保持一致）：

R1 中西文间距
    中日韩表意文字与半角英文字母/数字相邻时插入一个半角空格；两侧都处理；
    已有空格不重复插入。

R2 标点规范化（自洽口径，故意收窄以避免误伤）
    R2a 折叠：连续 2 个以上半角空格折叠为 1 个
    R2b 转全角：紧跟在表意文字之后的半角 , . ! ? ; : 转为 ，。！？；：
    R2c 收紧：全角标点两侧紧邻的半角空格一律删除
    **不处理** ASCII 括号 ( )、引号 " '，以及不紧跟表意文字的半角标点
    （如 "3.14"、"a, b" 保持原样）。

R3 幂等性（AC-M4-05-02）
    normalize_text(normalize_text(x)) == normalize_text(x) 恒成立。
    成立理由：R1 只在「无空格的表意文字↔半角字母数字」边界插入空格，
    插入后该边界不再满足条件；R2 的三步各自幂等，且 R1 插入的空格两侧
    都不是全角标点，不会破坏 R2c 的不变量。
"""

import re

__all__ = ['规范化文本', '插入间距', '规范标点',
           'normalize_text', 'insert_spacing', 'normalize_punctuation',
           'punctuation_rules']


# ---------------------------------------------------------------------------
# 字符类判定
# ---------------------------------------------------------------------------

#: 表意文字区段（不含标点、不含全角字母数字）
_CJK_RANGES = (
    (0x3400, 0x4DBF),    # 扩展 A
    (0x4E00, 0x9FFF),    # 基本区
    (0xF900, 0xFAFF),    # 兼容表意文字
    (0x20000, 0x2A6DF),  # 扩展 B
)

#: 半角标点 -> 全角标点（R2b）
_HALF_TO_FULL = {
    ',': '，', '.': '。', '!': '！', '?': '？', ';': '；', ':': '：',
}

#: 参与 R2c「两侧收紧」的全角标点集合
_FULLWIDTH_PUNCT = '，。！？；：、（）〈〉《》「」『』【】〔〕…—～·'

_RE_MULTI_SPACE = re.compile(r' {2,}')
_RE_TIGHTEN = re.compile(r' *([' + re.escape(_FULLWIDTH_PUNCT) + r']) *')


def _is_cjk(ch):
    """判定字符是否为表意文字（不含标点与全角字母数字）。"""
    code = ord(ch)
    for low, high in _CJK_RANGES:
        if low <= code <= high:
            return True
    return False


def _is_ascii_alnum(ch):
    """判定字符是否为半角英文字母或数字。"""
    return ('0' <= ch <= '9') or ('a' <= ch <= 'z') or ('A' <= ch <= 'Z')


# ---------------------------------------------------------------------------
# R1 中西文间距
# ---------------------------------------------------------------------------

def insert_spacing(text):
    """在表意文字与半角字母数字之间插入一个半角空格（R1）。

    两侧都处理；已有空格不重复插入。表外字符原样保留。
    """
    if text is None:
        return ''
    if not isinstance(text, str):
        text = str(text)
    if len(text) < 2:
        return text
    out = [text[0]]
    for i in range(1, len(text)):
        prev, cur = text[i - 1], text[i]
        need = ((_is_cjk(prev) and _is_ascii_alnum(cur))
                or (_is_ascii_alnum(prev) and _is_cjk(cur)))
        if need:
            out.append(' ')
        out.append(cur)
    return ''.join(out)


# ---------------------------------------------------------------------------
# R2 标点规范化
# ---------------------------------------------------------------------------

def normalize_punctuation(text):
    """折叠多余空格 + 半角标点转全角 + 全角标点两侧收紧（R2a/R2b/R2c）。"""
    if text is None:
        return ''
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text

    # R2a 折叠连续半角空格
    text = _RE_MULTI_SPACE.sub(' ', text)

    # R2b 紧跟表意文字的半角标点转全角
    out = []
    for i, ch in enumerate(text):
        if ch in _HALF_TO_FULL and i > 0 and _is_cjk(text[i - 1]):
            out.append(_HALF_TO_FULL[ch])
        else:
            out.append(ch)
    text = ''.join(out)

    # R2c 全角标点两侧紧邻的半角空格删除
    return _RE_TIGHTEN.sub(r'\1', text)


# ---------------------------------------------------------------------------
# 主 API
# ---------------------------------------------------------------------------

def normalize_text(text):
    """中文排版规范化主入口：先规范标点，再插入中西文间距。

    顺序固定为「标点在前、间距在后」，这是幂等性成立的前提（见模块 docstring R3）。
    不做首尾空白裁剪，避免与调用方的模板拼接语义冲突。
    """
    return insert_spacing(normalize_punctuation(text))


def punctuation_rules():
    """返回标点规范化规则的机器可读描述，供文档与契约测试引用。"""
    return {
        'R2a': '连续 2 个以上半角空格折叠为 1 个',
        'R2b': dict(_HALF_TO_FULL),
        'R2c': '全角标点两侧紧邻的半角空格删除',
        '全角标点集合': _FULLWIDTH_PUNCT,
    }


# ---------------------------------------------------------------------------
# 极快侧门面名（由加载器注入 排版.jk 的模块环境，再经 `导出` 对外可见）
# ---------------------------------------------------------------------------

def 规范化文本(文本):
    """中文排版规范化（主 API）。"""
    return normalize_text(文本)


def 插入间距(文本):
    """仅执行中西文间距插入。"""
    return insert_spacing(文本)


def 规范标点(文本):
    """仅执行标点规范化。"""
    return normalize_punctuation(文本)
