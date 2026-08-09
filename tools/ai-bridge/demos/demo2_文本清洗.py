# -*- coding: utf-8 -*-
"""Demo 2：文本清洗 —— 需求「把多行文本去重并排序」。

链路：需求文本 → select_blocks → 选块方案 → glue.synthesize → run_source。

两条路径都跑：
  A) 四个原子块串成流水线：文本切分(切片) → 去重(精简) → 升序(顺排) → 文本合成(缝合)
  B) 一级块一步到位：数据.文本清洗(净化)

A 是本套 demo 里**唯一真正链式传参**的例子：每一步用 `参数: ["赵果N"]`
把上一步的结果变量喂进去。v0 的 glue 不会自动推断这个链条，靠方案显式写。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from _公用 import 跑一遍                                   # noqa: E402

需求 = '把多行文本去重并排序'

#: 输入样本：有重复（苹果×2、香蕉×2）且乱序，去重+升序后结果可验证。
_样本 = '"香蕉,苹果,苹果,橙子,香蕉,梨"'
_分隔 = '","'

#: 「传统 AI 从零写」的等价 Python 实现，作为压缩比的分母。
#: 去重用手写循环而不是 `dict.fromkeys`——保留首次出现顺序这件事，AI
#: 通常会显式写出来（而且极快的 `去重` 块也是这个语义）。
等价Python = '''\
def split_text(text, sep):
    """按分隔符切分为列表。"""
    return text.split(sep)


def dedupe(items):
    """去重，保留首次出现顺序。"""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def ascending(items):
    """升序排序（不改动入参）。"""
    return sorted(items)


def join_text(items, sep):
    """用分隔符合成字符串。"""
    result = ""
    for i, item in enumerate(items):
        if i > 0:
            result += sep
        result += item
    return result


def clean(text, sep):
    """切分 → 去重 → 升序 → 合成。"""
    return join_text(ascending(dedupe(split_text(text, sep))), sep)


raw = "香蕉,苹果,苹果,橙子,香蕉,梨"
print(clean(raw, ","))
'''


def 方案A():
    """四个原子块串成流水线，逐步用 赵果N 传参。"""
    return {
        '需求': 需求,
        '共享': [
            {'名': '赵料', '值': _样本},
            {'名': '赵隔', '值': _分隔},
        ],
        '步骤': [
            {'块': '文本切分', '领域': '数据', '导出名': '切片',
             '说明': '按逗号切成列表', '参数': ['赵料', '赵隔']},
            {'块': '去重', '领域': '数据', '导出名': '精简',
             '说明': '去重，保留首次出现顺序', '参数': ['赵果1']},
            {'块': '升序', '领域': '数据', '导出名': '顺排',
             '说明': '升序排序', '参数': ['赵果2']},
            {'块': '文本合成', '领域': '数据', '导出名': '缝合',
             '说明': '用逗号合回字符串', '参数': ['赵果3', '赵隔']},
        ],
        # 中间三步的结果没必要打印，只看流水线末端
        '打印': ['赵果4'],
    }


def 方案B():
    """一级块一步到位：文本清洗 内部已聚合上面四步。"""
    return {
        '需求': 需求,
        '共享': [
            {'名': '赵料', '值': _样本},
            {'名': '赵隔', '值': _分隔},
        ],
        '步骤': [
            {'块': '文本清洗', '领域': '数据', '导出名': '净化',
             '说明': '切分→去重→升序→合成，一步完成', '参数': ['赵料', '赵隔']},
        ],
    }


def 方案表():
    return [('A · 四步流水线', 方案A()), ('B · 一级块一步到位', 方案B())]


def run():
    return 跑一遍('Demo 2：文本清洗', 需求, 方案表(), 等价Python)


if __name__ == '__main__':
    run()
