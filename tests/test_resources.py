# -*- coding: utf-8 -*-
"""stdlib 资源定位唯一入口的测试（ADR-39 / v0.24.0 W113）。

这些用例守的是 `docs/BACKLOG.md` §10 那次**已经发生过**的事故：
PyPI 上 0.4.1 的 wheel 里零个 stdlib 文件，装完 `导入 数学` 直接报
「找不到模块」，而本机 `pip install -e .` 一切正常。根因是 6 处各写各的
`__file__` 相对回溯。本模块把定位收敛成唯一入口，测试盯住这个唯一性。
"""

import os

from jikuai import resources


def test_stdlib_dir_在包内(monkeypatch):
    """默认分支：stdlib 目录是包内 `jikuai/stdlib`，不再上溯到仓库根。"""
    monkeypatch.delenv(resources.ENV_STDLIB, raising=False)
    pkg_dir = os.path.dirname(os.path.abspath(resources.__file__))
    assert resources.stdlib_dir() == os.path.join(pkg_dir, 'stdlib')


def test_环境变量可覆盖(tmp_path, monkeypatch):
    """`JIKUAI_STDLIB` 指向已存在目录时优先生效。"""
    monkeypatch.setenv(resources.ENV_STDLIB, str(tmp_path))
    assert resources.stdlib_dir() == os.path.abspath(str(tmp_path))


def test_环境变量指向不存在目录时忽略(monkeypatch):
    """配错路径不该让整个运行时崩——回落到包内默认值。

    与 `pkg.blocks.extra_roots()` 对坏路径的处置一致。
    """
    monkeypatch.setenv(resources.ENV_STDLIB, os.path.join('不存在', '的路径'))
    pkg_dir = os.path.dirname(os.path.abspath(resources.__file__))
    assert resources.stdlib_dir() == os.path.join(pkg_dir, 'stdlib')


def test_环境变量为空串时忽略(monkeypatch):
    monkeypatch.setenv(resources.ENV_STDLIB, '')
    pkg_dir = os.path.dirname(os.path.abspath(resources.__file__))
    assert resources.stdlib_dir() == os.path.join(pkg_dir, 'stdlib')


def test_blocks_dir_是stdlib下的blocks(tmp_path, monkeypatch):
    monkeypatch.setenv(resources.ENV_STDLIB, str(tmp_path))
    期望 = os.path.join(os.path.abspath(str(tmp_path)), 'blocks')
    assert resources.blocks_dir() == 期望


def test_stdlib_path_拼多级(tmp_path, monkeypatch):
    monkeypatch.setenv(resources.ENV_STDLIB, str(tmp_path))
    期望 = os.path.join(os.path.abspath(str(tmp_path)), 'blocks', '索引.json')
    assert resources.stdlib_path('blocks', '索引.json') == 期望


def test_stdlib_path_无参数等于stdlib_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(resources.ENV_STDLIB, str(tmp_path))
    assert resources.stdlib_path() == resources.stdlib_dir()


def test_模块只依赖标准库():
    """静态约束：本模块是最底层定位入口，不得依赖本包其它模块或第三方。

    它被 `module_loader` / `evaluator` / `pkg` / `ai` 四路调用，一旦它反向
    依赖上层就会造出导入环。
    """
    with open(resources.__file__, encoding='utf-8') as f:
        源码 = f.read()
    for 行 in 源码.splitlines():
        剥 = 行.strip()
        if 剥.startswith('import ') or 剥.startswith('from '):
            assert 剥 == 'import os', f'意外的导入：{剥}'


def test_无全局可变状态():
    """定位入口必须是纯函数：同样的环境两次调用结果一致，且不缓存。"""
    第一次 = resources.stdlib_dir()
    第二次 = resources.stdlib_dir()
    assert 第一次 == 第二次
    模块级容器 = [
        名 for 名, 值 in vars(resources).items()
        if not 名.startswith('__') and isinstance(值, (list, dict, set))
    ]
    assert 模块级容器 == [], f'出现模块级可变容器：{模块级容器}'
