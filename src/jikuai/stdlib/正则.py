# -*- coding: utf-8 -*-
"""极快语言标准库 — 中文正则（内部 Python 实现）。

本文件是 `正则.jk` 的内部实现（ADR-16 §3.3 混合模块）。`.jk` 是唯一对外
门面，本文件不参与模块名解析，只由加载器在加载 `正则.jk` 时隔离导入。

设计取舍
========
1. 底层引擎复用 Python 标准库 `re`，只在其上做薄封装：避免自研 NFA/DFA，
   保证性能与稳定性，代价是把 `re` 的语法子集当作事实契约。
2. 对使用者只声明「支持子集」：字面量、字符类 `[]`（含中文范围）、量词
   `* + ? {n} {n,} {n,m}`、分组 `()`、或 `|`。反向引用、断言等超出子集
   的语法由 `re` 底层报错，我们包装成清晰错误消息回吐。
3. 中文别名 `\汉` 在编译前预处理为 `[\u4e00-\u9fff]`，纯字符串替换，不
   引入解析层。文档单独声明这条规则。
4. `编译` 返回一个 dict 包装：`{"源": 原模式, "_编译对象": re.Pattern}`。
   之所以不直接返回 `re.Pattern`，是为了让 `.jk` 侧的 MemberAccess（字典
   语义 `dict.get(attr)`）能取到 `.源` 字段，同时兼容后续把 dict 作为
   参数再传给 `匹配/搜索/替代`。
5. 无命中不报错：`搜索` 返回空(None)，`匹配` 返回假(False)，`替代` 原样
   返回文本。空/None 输入按字符串空 `""` 处理。
"""

import re

__all__ = ["匹配", "搜索", "替代", "编译",
           "match", "search", "replace", "compile_pattern"]


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

#: 中文汉字别名 → Python re 等价字符类
#: 只做机械替换，不做语法感知（该子集内的模式中不会出现字面量 "\汉"）
_ZH_ALIAS_HAN = r"[\u4e00-\u9fff]"


def _expand_aliases(pattern):
    """展开中文别名。目前只有 `\汉` 一条。"""
    if not isinstance(pattern, str):
        return pattern
    return pattern.replace(r"\汉", _ZH_ALIAS_HAN)


def _wrap_error(exc, pattern):
    """把 re.error 包装成 ValueError，携带清晰的中文消息。"""
    return ValueError(
        "正则表达式语法错误（模式=%r）：%s" % (pattern, exc)
    )


def _to_pattern(mod):
    """把外部传入的「模式」规范化为 re.Pattern。

    - str            → 展开别名后 re.compile
    - dict 且含 "_编译对象" → 直接取用（`编译` 的产出）
    - re.Pattern     → 原样返回（Python 侧直接调用时的便利路径）
    其他类型 → TypeError。
    """
    if isinstance(mod, re.Pattern):
        return mod
    if isinstance(mod, dict) and "_编译对象" in mod:
        obj = mod["_编译对象"]
        if isinstance(obj, re.Pattern):
            return obj
    if isinstance(mod, str):
        expanded = _expand_aliases(mod)
        try:
            return re.compile(expanded)
        except re.error as e:
            raise _wrap_error(e, mod) from None
    raise TypeError("正则模式必须为字符串或 编译() 的返回值")


def _normalize_text(text):
    """None / 非字符串统一转成字符串；None → ""。"""
    if text is None:
        return ""
    if not isinstance(text, str):
        return str(text)
    return text


# ---------------------------------------------------------------------------
# 公共 API（英文别名，供 Python 侧单测直接调用）
# ---------------------------------------------------------------------------

def match(pattern, text):
    """整串匹配：等价 re.fullmatch，返回 bool。"""
    if text is None:
        return False
    p = _to_pattern(pattern)
    return p.fullmatch(_normalize_text(text)) is not None


def search(pattern, text):
    """搜索首个匹配。命中返回 dict，未命中返回 None。

    返回结构：`{"文本": str, "起始": int, "结束": int}`。
    起始与结束采用 Python 半开区间约定（text[起始:结束] == 文本）。
    """
    if text is None:
        return None
    p = _to_pattern(pattern)
    m = p.search(_normalize_text(text))
    if m is None:
        return None
    return {"文本": m.group(0), "起始": m.start(), "结束": m.end()}


def replace(pattern, repl, text):
    """全部替换，返回替换后的字符串。text 为 None 视为空串。"""
    if text is None:
        return ""
    p = _to_pattern(pattern)
    return p.sub(_normalize_text(repl), _normalize_text(text))


def compile_pattern(pattern):
    """编译模式。返回包装 dict：`{"源": 原模式, "_编译对象": re.Pattern}`。

    包装成 dict 的动机见文件头设计取舍 §4。
    """
    p = _to_pattern(pattern)
    return {"源": pattern if isinstance(pattern, str) else p.pattern,
            "_编译对象": p}


# ---------------------------------------------------------------------------
# 极快侧门面名（由加载器注入 正则.jk 的模块环境，再经 `导出` 对外可见）
# 注意：替换动作叫 替代 而非 替换——替换 是内建动词关键字（最长匹配会吞掉前缀），
# 成员访问 模块.属性 要求 . 后为 IDENT，故必须改名（见 正则.jk 注释）。
# ---------------------------------------------------------------------------

def 匹配(模式, 文本):
    """整串匹配。命中返回真，未命中返回假；None 视为空串。"""
    return match(模式, 文本)


def 搜索(模式, 文本):
    """搜索首个匹配。命中返回字典 {文本,起始,结束}，未命中返回空。"""
    return search(模式, 文本)


def 替代(模式, 新文本, 原文):
    """把 `原文` 中所有匹配 `模式` 的子串替换为 `新文本`。"""
    return replace(模式, 新文本, 原文)


def 编译(模式):
    """编译一个模式，返回可重复使用的编译对象（字典包装）。"""
    return compile_pattern(模式)