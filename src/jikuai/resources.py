# -*- coding: utf-8 -*-
"""极快语言 · stdlib 资源定位单一入口（ADR-39 · v0.24.0 W113）。

v0.24.0 之前，stdlib 目录靠 6 处各写各的 `__file__` 相对回溯定位（上溯 2 级或
3 级到仓库根），只在 `pip install -e .` 下成立——wheel 里根本没有 stdlib
（`docs/BACKLOG.md` §10：PyPI 上 0.4.1 的 wheel 零个 stdlib 文件，装完
`导入 数学` 直接报「找不到模块」）。

ADR-39 把 `stdlib/` 收进包内 `jikuai/stdlib/`，**本模块是唯一定位入口**。
新增任何需要定位 stdlib 的代码都必须调这里，不许再写 `__file__` 回溯——
留一处旧回溯，就等于留一个「本机 editable 全绿、wheel 里坏掉」的假绿缺口。

**刻意不用 `importlib.resources`**：6 个消费方全都需要真实文件系统目录路径
（往搜索路径塞目录、`spec_from_file_location`、`os.walk` 扫块、`open()`）。
`Traversable` 只在 zipimport 场景有额外价值，而本项目本质上要扫目录、做不到
zip-safe，引入它只增加抽象不增加能力。见 ADR-39 §3。

只 import 标准库；无副作用、无全局可变状态（同 `jikuai` 包既有约定）。
"""

import os

__all__ = ['ENV_STDLIB', 'stdlib_dir', 'blocks_dir', 'stdlib_path']

#: stdlib 根目录覆盖口。开发、装包、测试三种场景都从这里接管。
#: 值必须是已存在的目录，否则忽略并回落到包内默认值——配错路径不该让
#: 整个运行时崩（同 `pkg.blocks.extra_roots()` 对坏路径的处置）。
ENV_STDLIB = 'JIKUAI_STDLIB'


def stdlib_dir() -> str:
    """stdlib 根目录的绝对路径。"""
    override = os.environ.get(ENV_STDLIB, '')
    if override and os.path.isdir(override):
        return os.path.abspath(override)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, 'stdlib')


def blocks_dir() -> str:
    """`stdlib/blocks/` 的绝对路径（块生态根，其下第一级是领域）。"""
    return os.path.join(stdlib_dir(), 'blocks')


def stdlib_path(*parts: str) -> str:
    """拼 stdlib 下的资源路径，如 `stdlib_path('blocks', '索引.json')`。

    无参数时等价于 `stdlib_dir()`。
    """
    return os.path.join(stdlib_dir(), *parts)
