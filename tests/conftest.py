# -*- coding: utf-8 -*-
"""`tests/` 的 pytest 前置钩子 —— 让子进程的覆盖率也能被记到（W97 · v0.22.0 · M25）。

## 为什么需要它

全仓有 **17 个测试文件**用 `subprocess` 起子进程跑被测代码：

- `lsp_helpers.start_lsp_process()` → `python -m jikuai_lsp`（LSP 全部能力都在子进程里）
- `test_pkg_remote_publish_e2e.py` → `tools/registry-server/server.py`
- `test_v0_7_0_tutorial.py` / `test_v0_4_0_examples.py` / `test_v0_14_0_demos.py`
  / `test_blocks_cli.py` 等 → shell out 跑 `.jk` 与 `jk` CLI

coverage.py **默认只记发起它的那个进程**。于是 v0.21.0 W94 那次基线把
`completion.py` 记成 25.8%、`main.py` 记成 49.5% —— 而它们其实在子进程里被
整条打过。照那组数字排「低覆盖区」优先级，正好掉进 W94 想避开的坑
（为覆盖率数字写空测试）。

## 机制（coverage 官方的 subprocess 测量三件套）

1. `pyproject.toml` 的 `[tool.coverage.run] parallel = true` —— 每个进程写
   各自的 `.coverage.<host>.<pid>`，避免互相覆写；事后 `coverage combine` 汇总。
2. **本文件**：父进程在 coverage 下运行时，把 `COVERAGE_PROCESS_START` 写进
   `os.environ`，子进程通过环境继承拿到它。
3. `scripts/coverage_baseline.py`：往 site-packages 装一个 `.pth` 钩子，
   让**每个** Python 启动时都调 `coverage.process_startup()`。该函数在
   `COVERAGE_PROCESS_START` 未设置时是空操作 —— 所以钩子在位也不影响普通运行。

三者缺一不可：只有 (1) 会丢子进程；只有 (2) 子进程没人替它开 coverage；
只有 (3) 子进程不知道读哪份配置。

## 不在 coverage 下跑时

`_覆盖率在跑()` 返回 False，本钩子什么都不做 —— 普通 `pytest` 不受影响，
不引入额外环境变量、不改子进程行为。这一点很重要：`tests/` 下所有子进程
用例都断言过子进程的 stdout/退出码，多塞环境变量有污染输出的风险。
"""

import os

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..'))

#: coverage 配置真源。与 `scripts/coverage_baseline.py` 指的是同一份。
_配置路径 = os.path.join(_REPO, 'pyproject.toml')

#: coverage 官方约定的环境变量名。子进程里的 `coverage.process_startup()`
#: 靠它判断「要不要开 coverage、读哪份配置」。
_START_ENV = 'COVERAGE_PROCESS_START'


def _覆盖率在跑():
    """父进程当前是否运行在 coverage 之下。

    用 `Coverage.current()` 而不是猜环境变量：`coverage run` 不会设置任何
    可靠的标记变量，但它一定会建出一个 current 实例。coverage 没装时
    ImportError，同样返回 False。
    """
    try:
        import coverage
    except ImportError:
        return False
    try:
        return coverage.Coverage.current() is not None
    except Exception:
        # 老版本 coverage 可能没有 current()；宁可不启用也别让会话挂掉
        return False


def _启用子进程覆盖率():
    """把 `COVERAGE_PROCESS_START` 放进环境，供 subprocess 继承。

    只在父进程确实在 coverage 下跑、且调用方没有自己设过时才写 ——
    调用方（例如 CI 脚本）显式设了就尊重它的取值。
    """
    if not _覆盖率在跑():
        return
    if os.environ.get(_START_ENV):
        return
    if os.path.exists(_配置路径):
        os.environ[_START_ENV] = _配置路径


_启用子进程覆盖率()
