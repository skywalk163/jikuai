# -*- coding: utf-8 -*-
"""jikuai-dap 版本号 · 本包内的单一真源。

W118（v0.24.0）：与主包同号发布。此前这里写死在 `dap/pyproject.toml` 里，
停在 0.7.0 而主包已 0.23.0——三包同仓同步发，版本各走各的只会让用户
猜「哪个 dap 配哪个 jikuai」。G15 门禁把这里纳入投影校验（第六处）。

刻意**不** `from jikuai._version import __version__`：构建 dap 的 wheel 时
主包不一定可导入，构建期不该有跨包运行时依赖（ADR-20 的隔离约束也不允许
本包在导入期硬依赖主包）。改由 G15 静态校验两处字面一致。

也刻意保持成只有一个字面量赋值的小模块——`pyproject` 的
`[tool.setuptools.dynamic] version = {attr = ...}` 能靠静态 AST 分析读出来，
不必真 import 本包（真 import 会连带跑 `__init__.py` 的 `from .adapter import main`）。
"""

__version__ = "0.28.0"

__all__ = ["__version__"]
