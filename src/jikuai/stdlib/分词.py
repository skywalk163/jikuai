# -*- coding: utf-8 -*-
"""极快语言标准库 — 中文分词（内部 Python 实现）。

本文件是 `分词.jk` 的内部实现（ADR-16 §3.3 混合模块）。`.jk` 是唯一对外
门面，本文件不参与模块名解析，只由加载器在加载 `分词.jk` 时隔离导入。

算法
====
正向最大匹配（Forward Maximum Matching, FMM）：
  从左往右，在位置 i 处尝试用词典匹配长度 L = min(MAX_WORD_LEN, 剩余长度)
  的子串，失败则 L 递减，直到 L == 2。命中即切出并前进 L 位。
  全部失败时进入兜底分支（见下）。

兜底分支（口径必须与 docs/标准库.md 一致）
=========================================
B1 空白字符（空格 / 制表 / 换行）：作分隔符，**不产出词项**
B2 半角字母数字：连续的 [0-9A-Za-z] 作为**一个**词项整体产出
   （理由：`JiKuai2026` 逐字切没有信息量，整体切才可用）
B3 其余字符（汉字、全角/半角标点、符号）：**单字成词**，逐个产出
   因此纯标点输入的结果是「每个标点各占一项」

边界条件
========
- `None` / 空字符串 → 返回 `[]`
- 非字符串输入先 `str()` 归一
- 单字词不入库：词典最短条目长度为 2，未命中的单字由 B3 兜底
- 词条最长 8 字（`MAX_WORD_LEN_LIMIT`，ADR-38 §4 性能决策）

词典（ADR-38）
==============
词典是**外部数据文件** `分词词典.txt`（同目录，随包分发），约 5.9 万条，由
`tools/dict/重生成词典.py` 从 jieba `dict.txt` 通用底座 + 现有 565 条必留种子 +
THUOCL 财经/法律 合并生成。来源与授权见 `分词词典来源.md`，校验和见
`分词词典.元信息.json`。文件缺失时**导入即抛异常，不静默降级**。

幂等与无副作用（G12 · AC-M5-07-01/02/03）
=========================================
1. 词典 `WORDS` 是模块级 `frozenset`，`MAX_WORD_LEN` 是模块级 int，
   两者都在导入期一次性构建，之后**只读**。词典文件**只在导入期读一次**，
   `segment()` 运行期一次文件都不碰。
2. `segment()` 内部只使用局部变量，**没有** `global`、没有缓存写入、
   没有对任何模块级容器的修改，也不碰 `sys.modules` / 环境变量 / 文件。
3. 因此：同输入连续 N 次调用必然逐项相等（AC-01）；调用前后全局可观察
   状态无差异（AC-02）；与其他 stdlib 模块交替调用互不影响（AC-03）。
4. 返回值每次都是**新建的 list**，调用方修改它不会污染内部状态。
"""

__all__ = ["分词", "segment", "dictionary_size", "max_word_length",
           "all_words", "dictionary_path"]

import os
import re


# ---------------------------------------------------------------------------
# 词典（外部数据文件，ADR-38 §5）
#
# 词典不再内联在本文件里，改为读同目录的 `分词词典.txt`：
# UTF-8、一行一词、码点升序、无注释。它是 `tools/dict/重生成词典.py` 的产物，
# 来源与授权见 `分词词典来源.md`，机读校验和见 `分词词典.元信息.json`。
#
# 为什么不内联：5.9 万条词内联会让本文件涨到 500 KB 以上，git diff 不可读。
# 为什么不压缩：实测压缩只省约 84 KB，代价是 diff 完全不可读、gzip 需固定
# mtime 才字节可复现、加载还更慢。详见 ADR-38 §5.2。
# ---------------------------------------------------------------------------

#: 词典文件路径。与本文件同目录，随包分发。
DICT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "分词词典.txt")

#: 入库词长范围。上限 8 是性能决策（ADR-38 §4）：MAX_WORD_LEN 是 FMM 内层
#: 递减循环的上界，不截断（词典最长 14 字）比截断到 8 慢约 40%，而超过 8 字的
#: 词条只占 0.15%。8 字以上的长串是短语，不该由 FMM 一口吞掉。
MIN_WORD_LEN = 2
MAX_WORD_LEN_LIMIT = 8

#: 入库条目的机械自检式：长度在 [MIN_WORD_LEN, MAX_WORD_LEN_LIMIT] 且全为汉字。
#: 用编译好的正则而非逐字符 all()——58713 条上实测 150 ms → 52 ms，结果一字不差。
#: 导入期成本是 CLI 冷启动的一部分，值得省。
_合法词 = re.compile("[\u4e00-\u9fff]{%d,%d}\\Z" % (MIN_WORD_LEN, MAX_WORD_LEN_LIMIT))


def _build_dictionary():
    """从 `分词词典.txt` 构建词典 frozenset。

    机械自检：`MIN_WORD_LEN <= 长度 <= MAX_WORD_LEN_LIMIT` 且全为汉字（`_合法词`）。
    单字词一律剔除（由兜底策略 B3 处理），非汉字条目剔除（ASCII 由 B2 处理）。
    不合规条目**静默跳过**——词典规模上下界哨在测试里守着，整体损坏会被那条断言接住。

    **词典文件缺失时直接抛异常，不静默降级到内置小词典**（ADR-38 §8）：
    静默降级会把「词典没打包进去」这种发布事故变成线上悄悄劣化，
    比启动即失败糟得多。

    只在导入期调用一次；`segment()` 运行期不碰文件。
    """
    if not os.path.exists(DICT_FILE):
        raise RuntimeError(
            "分词词典文件缺失：%s\n"
            "它是 tools/dict/重生成词典.py 的产物，应随包分发。"
            "若是从源码树运行，跑 `python tools/dict/重生成词典.py` 重新生成；"
            "若是安装后出现，说明打包清单漏了 stdlib/*.txt。" % DICT_FILE)
    with open(DICT_FILE, "r", encoding="utf-8") as f:
        words = frozenset(w for w in f.read().split() if _合法词.match(w))
    if not words:
        raise RuntimeError("分词词典为空：%s" % DICT_FILE)
    return frozenset(words)


#: 词典。模块级不可变常量，运行期只读（G12 前提）。
WORDS = _build_dictionary()

#: 词典中最长条目的长度。FMM 的窗口上界。
MAX_WORD_LEN = max((len(w) for w in WORDS), default=0)


# ---------------------------------------------------------------------------
# 内部字符判定（纯函数）
# ---------------------------------------------------------------------------

def _is_space(ch):
    """空白字符（B1）。"""
    return ch in " \t\r\n\u3000\v\f"


def _is_ascii_alnum(ch):
    """半角字母或数字（B2）。"""
    return ("0" <= ch <= "9") or ("a" <= ch <= "z") or ("A" <= ch <= "Z")


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def segment(text):
    """正向最大匹配分词。返回**新建**的词项 list。

    纯函数：只读模块级常量 WORDS / MAX_WORD_LEN，不写任何外部状态。
    """
    if text is None:
        return []
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return []

    result = []
    i = 0
    n = len(text)
    max_len = MAX_WORD_LEN
    words = WORDS
    while i < n:
        # --- FMM 主路径：长词优先 ---
        window = max_len if n - i > max_len else n - i
        hit = 0
        while window >= 2:
            if text[i:i + window] in words:
                hit = window
                break
            window -= 1
        if hit:
            result.append(text[i:i + hit])
            i += hit
            continue

        ch = text[i]
        # --- B1 空白：吞掉但不产出 ---
        if _is_space(ch):
            i += 1
            continue
        # --- B2 半角字母数字：整段作为一个词项 ---
        if _is_ascii_alnum(ch):
            j = i + 1
            while j < n and _is_ascii_alnum(text[j]):
                j += 1
            result.append(text[i:j])
            i = j
            continue
        # --- B3 其余（汉字/标点/符号）：单字成词 ---
        result.append(ch)
        i += 1
    return result


def dictionary_size():
    """返回内置词典条数。"""
    return len(WORDS)


def max_word_length():
    """返回词典最长条目长度（FMM 窗口上界）。"""
    return MAX_WORD_LEN


def all_words():
    """返回词典集合（frozenset，不可变，调用方无法污染内部状态）。"""
    return WORDS


def dictionary_path():
    """返回词典数据文件的绝对路径（供测试与打包核查用）。"""
    return DICT_FILE


# ---------------------------------------------------------------------------
# 极快侧门面名（由加载器注入 分词.jk 的模块环境，再经 `导出` 对外可见）
# ---------------------------------------------------------------------------

def 分词(文本):
    """把中文文本切分成词序列（列表）。空输入返回空列表。"""
    return segment(文本)