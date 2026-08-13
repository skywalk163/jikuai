# -*- coding: utf-8 -*-
"""极快语言 · 版本号单一真源。

放在独立小模块而非 `__init__.py`，是为了避免与 `main.py` 的循环导入
（`__init__.py` 从 `.main` 导入 CLI 入口，如果 `main.py` 反过来
`from jikuai import __version__` 就会在 `__init__.py` 未走完时读到未定义符号）。

W25（v0.16.0）起这里是**唯一真源**：
- `pyproject.toml` 通过 `[tool.setuptools.dynamic]` 引用它
- `src/jikuai/__init__.py`、`src/jikuai/main.py` 都从这里导入
- G15 门禁校验 `pyproject` / `CHANGELOG` / `editors/vscode/package.json` /
  `editors/vscode/CHANGELOG.md` 四处投影与真源一致，任何一处改动其它不同步 CI 都红
  （第四处「扩展 CHANGELOG」是 W60 / v0.18.0 补入的——v0.17.0 与 v0.18.0
  连续两轮漏更它，此前只有一个独立 pytest 兜着，跑门禁的人当场看不到红）

`BLOCK_INDEX_VERSION`（`src/jikuai/pkg/blocks.py`）刻意不并入这里 ——
索引格式版本与语言版本解耦，是 v0.12.0 起的既定设计。
"""

__version__ = "0.21.0"

__all__ = ["__version__"]
