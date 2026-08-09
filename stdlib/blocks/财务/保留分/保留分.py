# -*- coding: utf-8 -*-
"""财务.保留分 的 Python 背衬实现（ADR-16 §3.3 混合模块）。

极快没有内建的四舍五入动词（`四舍五入` / `向上取整` / `保留N位` 都不存在），
而财务计算离不开分位处理——利息、税额、月供算完必然是无穷小数。

刻意用 `Decimal` + `ROUND_HALF_UP` 而不是 Python 的 `round()`：
`round()` 走 banker's rounding（四舍六入五成双），`round(2.675, 2)` 得 2.67，
与中国会计惯例（见 evaluator 的 `人民币` 类型同样用 ROUND_HALF_UP）不符。
财务块全部依赖本块做分位收口，避免每块各自造轮子、各自错一遍。
"""

from decimal import Decimal, ROUND_HALF_UP

#: 分位量化模板：两位小数。
_两位 = Decimal('0.01')


def 圆分(数值):
    """四舍五入到分（两位小数），返回 float。

    `ROUND_HALF_UP` = 中国会计惯例的「四舍五入」，与 Python 内建 `round()`
    的 banker's rounding 不同：`圆分(2.675)` 得 2.68，`round(2.675, 2)` 得 2.67。
    """
    return float(Decimal(str(数值)).quantize(_两位, rounding=ROUND_HALF_UP))
