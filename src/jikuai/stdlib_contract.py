# -*- coding: utf-8 -*-
"""极快语言 · 标准库静态契约工具（T-M4-S01）。

提供不依赖运行时的静态分析工具：
- 从 .jk 源码提取导出声明
- 列举 stdlib 模块
- 判定混合模块
- 定位 stdlib 目录
"""

import os
import re
from typing import Optional


def default_stdlib_dir() -> str:
    """返回包内 stdlib/ 绝对路径。

    W115（v0.24.0 · ADR-39）：改由 `resources.stdlib_dir()` 统一定位，不再
    自己回溯 `__file__`。原实现是「从 src/jikuai/ 上溯两级到仓库根」，只在
    `pip install -e .` 下成立。
    """
    from . import resources
    return resources.stdlib_dir()


def parse_exports(jk_source: str) -> set:
    """从 .jk 源码文本中提取所有 `导出 X Y ...。` 声明的名字。

    处理策略：
    - 忽略 `--` 行注释（注释从 `--` 到行尾）
    - 忽略字符串字面量中的伪导出（简单掩码：替换引号内内容为占位符）
    - 用正则匹配 `导出` 关键字后到 `。` 之间的所有空白分隔 token
    """
    # 步骤 1：掩码字符串（双引号）
    masked = re.sub(r'"[^"]*"', '""', jk_source)
    # 步骤 2：去除行注释
    masked = re.sub(r'--[^\n]*', '', masked)
    # 步骤 3：匹配导出语句：`导出 名1 名2 ... 。`
    exports = set()
    # 导出后跟一个或多个名字，以 。 结束
    pattern = re.compile(r'导出\s+(.+?)\s*。', re.DOTALL)
    for m in pattern.finditer(masked):
        names_str = m.group(1)
        # 按空白分割名字
        names = names_str.split()
        for name in names:
            name = name.strip()
            if name:
                exports.add(name)
    return exports


def declared_exports(module_name: str, stdlib_dir: Optional[str] = None) -> set:
    """读 stdlib/<module_name>.jk 并返回其导出集合。"""
    if stdlib_dir is None:
        stdlib_dir = default_stdlib_dir()
    jk_path = os.path.join(stdlib_dir, module_name + '.jk')
    if not os.path.isfile(jk_path):
        return set()
    with open(jk_path, 'r', encoding='utf-8') as f:
        source = f.read()
    return parse_exports(source)


def list_stdlib_modules(stdlib_dir: Optional[str] = None) -> list:
    """列出 stdlib/ 下所有 .jk 模块名（不含扩展名）。"""
    if stdlib_dir is None:
        stdlib_dir = default_stdlib_dir()
    modules = []
    if not os.path.isdir(stdlib_dir):
        return modules
    for fname in sorted(os.listdir(stdlib_dir)):
        if fname.endswith('.jk'):
            modules.append(fname[:-3])
    return modules


def has_python_backing(module_name: str, stdlib_dir: Optional[str] = None) -> bool:
    """判定是否为混合模块（同名 .py 文件是否存在）。"""
    if stdlib_dir is None:
        stdlib_dir = default_stdlib_dir()
    py_path = os.path.join(stdlib_dir, module_name + '.py')
    return os.path.isfile(py_path)
