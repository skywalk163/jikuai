# -*- coding: utf-8 -*-
"""覆盖率基线测量编排器（W97 · v0.22.0 · M25）。

一条命令跑出**含子进程**的真实覆盖率：

    python scripts/coverage_baseline.py                 # 测量 + 打印报告
    python scripts/coverage_baseline.py --json 报告.json # 另存 JSON
    python scripts/coverage_baseline.py --保留数据       # 不删 .coverage* 中间件

## 为什么需要这个脚本，而不是直接 `coverage run -m pytest`

全仓 17 个测试文件用 `subprocess` 起子进程跑被测代码（LSP `python -m jikuai_lsp`、
注册表服务端、CLI 跑 `.jk`）。coverage 默认只记发起它的进程，直接
`coverage run -m pytest` 会把这些路径记成未覆盖 —— v0.21.0 W94 那次基线
就因此把 `completion.py` 记成 25.8%、`main.py` 记成 49.5%。

要记到子进程，coverage 要求**每个 Python 启动时**调用
`coverage.process_startup()`。唯一可靠的挂载点是 site-packages 里的 `.pth`
文件（`.pth` 里的 `import` 行在解释器启动阶段执行，早于任何用户代码）。

`.pth` 不能进版本库（它在 Python 环境里，不在仓库里），所以由本脚本
**临时装、用完删**。`process_startup()` 在 `COVERAGE_PROCESS_START` 未设置时
是空操作，因此即便脚本异常退出、`.pth` 残留，也不会影响普通 Python 运行 ——
但 `finally` 仍会清理，别留垃圾在别人的环境里。

三件套的另两件：
- `pyproject.toml` 的 `[tool.coverage.run] parallel = true`
- `tests/conftest.py` 把 `COVERAGE_PROCESS_START` 放进环境供子进程继承

## 退出码

0 = 测量完成（**不代表覆盖率达标** —— 本脚本不做阈值判定，
阈值是 `[tool.coverage.report] fail_under` 的事）。
非 0 = pytest 失败或 coverage 步骤失败，此时报告不可信。
"""

import argparse
import os
import subprocess
import sys
import sysconfig

_HERE = os.path.abspath(os.path.dirname(__file__))
REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
配置路径 = os.path.join(REPO_ROOT, 'pyproject.toml')

#: `.pth` 文件名。前缀 `zzz_` 是刻意的：site-packages 里的 `.pth` 按文件名
#: 排序执行，排在后面能确保 coverage 包本身已可导入。
PTH_NAME = 'zzz_jikuai_coverage_subprocess.pth'

#: `.pth` 内容。必须是**单行**且以 `import ` 开头 —— Python 只把这种行当代码执行，
#: 其余行一律当成要加进 sys.path 的目录。
PTH_BODY = 'import coverage; coverage.process_startup()\n'


def _可写site目录():
    """挑一个可写的 site-packages 放 `.pth`。

    优先用户级（`--user` 那个），因为系统级常常没有写权限（本机
    `C:\\Python314\\Lib\\site-packages` 就不可写，coverage 也是装到用户级的）。
    """
    候选 = []
    try:
        import site
        用户级 = site.getusersitepackages()
        if isinstance(用户级, str):
            候选.append(用户级)
    except Exception:
        pass
    纯净 = sysconfig.get_paths().get('purelib')
    if 纯净:
        候选.append(纯净)
    for d in 候选:
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    # 用户级目录可能还不存在，试着建出来
    for d in 候选:
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except Exception:
            continue
    return None


def _装钩子(site目录):
    路径 = os.path.join(site目录, PTH_NAME)
    with open(路径, 'w', encoding='utf-8', newline='\n') as f:
        f.write(PTH_BODY)
    return 路径


def _卸钩子(路径):
    try:
        if 路径 and os.path.exists(路径):
            os.remove(路径)
    except OSError as e:
        print('警告：`.pth` 钩子删不掉，请手工删除 %s（%s）' % (路径, e),
              file=sys.stderr)


def _跑(参数, 环境=None, 静默=False):
    """在仓库根目录跑一条命令，返回退出码。"""
    if not 静默:
        print('$ %s' % ' '.join(参数))
    return subprocess.call(参数, cwd=REPO_ROOT, env=环境)


def main(argv=None):
    """入参约定与 G16/G17/G19 一致：`argv` 是**不含程序名**的参数列表。"""
    解析器 = argparse.ArgumentParser(
        prog='coverage_baseline',
        description='测量含子进程的覆盖率基线')
    解析器.add_argument('--json', dest='json路径', default=None,
                        help='另存一份 JSON 报告到该路径')
    解析器.add_argument('--保留数据', dest='保留数据', action='store_true',
                        help='测完不删 .coverage* 中间文件（便于自己再出报告）')
    解析器.add_argument('--按覆盖率升序', dest='升序', action='store_true',
                        help='报告按覆盖率从低到高排（找低覆盖区用）')
    args = 解析器.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        import coverage  # noqa: F401
    except ImportError:
        print('未装 coverage。先跑：python -m pip install -e ".[dev]"',
              file=sys.stderr)
        return 2

    site目录 = _可写site目录()
    if site目录 is None:
        print('找不到可写的 site-packages，无法装子进程钩子。\n'
              '没有钩子时子进程覆盖率会全部丢失，测出来的数字不可信，'
              '因此这里直接失败而不是降级。', file=sys.stderr)
        return 2

    钩子路径 = None
    try:
        钩子路径 = _装钩子(site目录)
        print('已装子进程钩子：%s' % 钩子路径)

        环境 = dict(os.environ)
        环境['COVERAGE_PROCESS_START'] = 配置路径
        # 子进程打中文横幅时 Windows 默认 GBK 会炸 UnicodeEncodeError，
        # 那会让服务端/LSP 起不来，表现为测试失败而非覆盖率缺失。
        环境.setdefault('PYTHONIOENCODING', 'utf-8')

        码 = _跑([sys.executable, '-m', 'coverage', 'erase'], 环境)
        if 码 != 0:
            return 码

        测试码 = _跑([sys.executable, '-m', 'coverage', 'run',
                      '-m', 'pytest', 'tests', '-q'], 环境)

        # 无论测试成败都 combine —— 失败时的部分数据也有诊断价值。
        码 = _跑([sys.executable, '-m', 'coverage', 'combine'], 环境)
        if 码 != 0:
            print('coverage combine 失败，报告不可信', file=sys.stderr)
            return 码

        报告参数 = [sys.executable, '-m', 'coverage', 'report']
        if args.升序:
            报告参数.append('--sort=cover')
        _跑(报告参数, 环境)

        if args.json路径:
            _跑([sys.executable, '-m', 'coverage', 'json',
                 '-o', args.json路径], 环境)

        if 测试码 != 0:
            print('\n注意：pytest 退出码 %d —— 有用例失败，'
                  '上面的覆盖率数字只反映实际跑到的部分。' % 测试码,
                  file=sys.stderr)
        return 测试码
    finally:
        _卸钩子(钩子路径)
        if not args.保留数据:
            _跑([sys.executable, '-m', 'coverage', 'erase'], 静默=True)


if __name__ == '__main__':
    sys.exit(main())
