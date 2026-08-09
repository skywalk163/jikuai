# -*- coding: utf-8 -*-
"""Demo 3：中文报表 —— 需求「把金额转成中文大写」。

链路：需求文本 → select_blocks → 选块方案 → glue.synthesize → run_source。

两条路径都跑：
  A) 原子块：中文.金额雅写(银码) → 「壹贰叁…元角分整」字符串
  B) 一级块：中文.金额报表(账单) → [数值, 中文大写金额, 汉字数字]

这个 demo 的压缩比最夸张——中文大写金额转换是「规则密集、边界多」的活儿
（零的省略与保留、角分处理、万/亿进位单位），AI 从零写至少几十行且极易
在边界上出错。块生态里它是一个已被测试覆盖的调用。

注意底层语义差异（来自内建动词，非本 demo 引入）：`大写金额` 保留角分，
`汉字数字` 内部先 `int()` 截断，所以 账单(1234.56) 第三元不含小数部分。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from _公用 import 跑一遍                                   # noqa: E402

需求 = '把金额转成中文大写'

#: 两个样本：带角分的小额 + 万级整数（检验进位单位与「整」字）。
_小额 = '1234.56'
_大额 = '88888'

#: 「传统 AI 从零写」的等价 Python 实现，作为压缩比的分母。
#: 这份实现是**简化版**（未处理亿以上、未处理负数），已经这么长了——
#: 真要做对，篇幅还要翻倍。这正是块生态在「中国特色规则」上的价值所在。
等价Python = '''\
CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
CN_UNITS = ["", "拾", "佰", "仟"]
CN_SECTIONS = ["", "万", "亿"]


def _section_to_cn(section):
    """把 4 位以内的整数段转成中文大写，处理段内零的合并。"""
    out = ""
    zero_pending = False
    for i, ch in enumerate(reversed(str(section).zfill(4))):
        pos = 3 - i
        digit = int(ch)
        if digit == 0:
            zero_pending = bool(out)
        else:
            if zero_pending:
                out = CN_DIGITS[0] + out
                zero_pending = False
            out = CN_DIGITS[digit] + CN_UNITS[pos] + out
    return out


def to_chinese_amount(value):
    """把数值转成中文大写金额（壹贰叁肆…元角分整）。"""
    if value < 0:
        return "负" + to_chinese_amount(-value)
    cents = int(round(value * 100))
    yuan, rest = divmod(cents, 100)
    jiao, fen = divmod(rest, 10)

    if yuan == 0:
        head = CN_DIGITS[0]
    else:
        head = ""
        idx = 0
        while yuan > 0:
            yuan, section = divmod(yuan, 10000)
            if section:
                head = _section_to_cn(section) + CN_SECTIONS[idx] + head
            elif head:
                head = CN_DIGITS[0] + head
            idx += 1
        head = head.lstrip(CN_DIGITS[0])

    tail = "元"
    if jiao == 0 and fen == 0:
        tail += "整"
    else:
        if jiao:
            tail += CN_DIGITS[jiao] + "角"
        elif fen:
            tail += CN_DIGITS[0]
        if fen:
            tail += CN_DIGITS[fen] + "分"
        else:
            tail += "整"
    return head + tail


print(to_chinese_amount(1234.56))
print(to_chinese_amount(88888))
'''


def 方案A():
    """原子块：金额雅写 分别转两个样本。"""
    return {
        '需求': 需求,
        '步骤': [
            {'块': '金额雅写', '领域': '中文', '导出名': '银码',
             '说明': '带角分的小额 → 大写金额', '参数': [_小额]},
            {'块': '金额雅写', '领域': '中文', '导出名': '银码',
             '说明': '万级整数 → 大写金额（应带「整」）', '参数': [_大额]},
        ],
    }


def 方案B():
    """一级块：金额报表 一次给出 [数值, 大写金额, 汉字数字]。"""
    return {
        '需求': 需求,
        '步骤': [
            {'块': '金额报表', '领域': '中文', '导出名': '账单',
             '说明': '小额 → 三元报表', '参数': [_小额]},
            {'块': '金额报表', '领域': '中文', '导出名': '账单',
             '说明': '大额 → 三元报表', '参数': [_大额]},
        ],
    }


def 方案表():
    return [('A · 金额雅写原子块', 方案A()), ('B · 金额报表一级块', 方案B())]


def run():
    return 跑一遍('Demo 3：中文报表', 需求, 方案表(), 等价Python)


if __name__ == '__main__':
    run()
