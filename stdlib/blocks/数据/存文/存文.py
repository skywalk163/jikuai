# -*- coding: utf-8 -*-
"""数据.存文 的 Python 背衬实现（ADR-16 §3.3 混合模块）。

内建动词 `写入` 在 keywords.py 声明但 evaluator 未实现，运行期调用会抛
「未知动词：写入」。文件写入下沉到本背衬；`.jk` 仅做门面与 `导出`。
"""


def 落盘(路径, 文本):
    """把字符串写入文件（UTF-8），返回写入的字符串。"""
    with open(路径, 'w', encoding='utf-8') as f:
        f.write(文本)
    return 文本
