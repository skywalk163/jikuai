# -*- coding: utf-8 -*-
"""Demo 1：数值统计 —— 需求「对一组销售额求和并算平均」。

链路：需求文本 → select_blocks → 选块方案 → glue.synthesize → run_source。

两条路径都跑：
  A) 原子块拼装：数据.求和(汇总) + 数据.均值(均数)
  B) 一级块一步到位：数据.批量统计(统览) → [总和, 均值, 最小, 最大]

B 比 A 更省，正是块生态「层级」设计要证明的事：**高层块把粘合成本也吃掉了**。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from _公用 import 跑一遍                                   # noqa: E402

需求 = '对一组销售额求和并算平均'

#: 销售额样本。写在这里而不是塞进方案，是为了让 A/B 两个方案共用同一份输入。
_样本 = '列 100 200 150 300 250'

#: 「传统 AI 从零写」的等价 Python 实现，作为压缩比的分母。
#: 刻意不用 `sum()` / `statistics.mean()` 一行流——块生态的对照物应该是
#: 「AI 真的会输出的那种带函数定义与空列表防御的代码」，而不是极简单行。
等价Python = '''\
def total_sales(values):
    """对销售额列表求和。"""
    result = 0
    for v in values:
        result += v
    return result


def average_sales(values):
    """对销售额列表求算术平均值。"""
    if not values:
        return 0
    return total_sales(values) / len(values)


def summarize(values):
    """返回 [总和, 均值, 最小, 最大]。"""
    if not values:
        return [0, 0, None, None]
    return [
        total_sales(values),
        average_sales(values),
        min(values),
        max(values),
    ]


sales = [100, 200, 150, 300, 250]
print(total_sales(sales))
print(average_sales(sales))
print(summarize(sales))
'''


def 方案A():
    """原子块拼装：求和 + 均值，两步各自吃同一份输入。"""
    return {
        '需求': 需求,
        '共享': [{'名': '赵料', '值': _样本}],
        '步骤': [
            {'块': '求和', '领域': '数据', '导出名': '汇总',
             '说明': '对销售额列表求和', '参数': ['赵料']},
            {'块': '均值', '领域': '数据', '导出名': '均数',
             '说明': '对同一列表求算术平均', '参数': ['赵料']},
        ],
    }


def 方案B():
    """一级块一步到位：批量统计 直接给出四元统计。"""
    return {
        '需求': 需求,
        '共享': [{'名': '赵料', '值': _样本}],
        '步骤': [
            {'块': '批量统计', '领域': '数据', '导出名': '统览',
             '说明': '一步得到 [总和, 均值, 最小, 最大]', '参数': ['赵料']},
        ],
    }


def 方案表():
    return [('A · 原子块拼装', 方案A()), ('B · 一级块一步到位', 方案B())]


def run():
    return 跑一遍('Demo 1：数值统计', 需求, 方案表(), 等价Python)


if __name__ == '__main__':
    run()
