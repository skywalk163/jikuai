# -*- coding: utf-8 -*-
"""覆盖率基线测量编排器（W97 · v0.22.0 · M25）。

一条命令跑出**含子进程**的真实覆盖率：

    python scripts/coverage_baseline.py                 # 测量 + 打印报告
    python scripts/coverage_baseline.py --json 报告.json # 另存 JSON
    python scripts/coverage_baseline.py --保留数据       # 不删 .coverage* 中间件
    python scripts/coverage_baseline.py --检查下限       # 门禁模式：卡阈值


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

默认（无 `--检查下限`）：0 = 测量完成，**不代表覆盖率达标** —— 平时看基线不该
被阈值挡住，所以此时连 `fail_under` 都不生效。非 0 = pytest 失败或 coverage
步骤失败，此时报告不可信。

门禁模式（`--检查下限`，CI 用的就是这个）：把两级阈值都变成硬失败 ——
逐文件点阈值（`docs/覆盖率下限.json`）与全局面阈值（`fail_under`）。
优先级：pytest 失败 > 点阈值 > 面阈值。
"""


import argparse
import json
import os
import subprocess
import sys
import sysconfig

_HERE = os.path.abspath(os.path.dirname(__file__))
REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
配置路径 = os.path.join(REPO_ROOT, 'pyproject.toml')

#: 逐文件覆盖率下限（点阈值）的数据文件。W94 纪律「点阈值优先于面阈值」的落地物。
下限文件 = os.path.join(REPO_ROOT, 'docs', '覆盖率下限.json')

#: 覆盖率跑**排除**的测试文件（W163）。这不是「跳过测试」——排除的文件在 CI 的
#: 「运行全部测试」那一步已经**全速跑过一遍**（`.gitea/workflows/ci.yml`），
#: 本脚本是第二遍、只为量 `src/jikuai` 的覆盖率。
#:
#: 为什么要排：这两个文件是「起子进程跑门禁 / 回放录像」型测试，而本脚本往
#: site-packages 装了 `.pth` 钩子让**每个子进程都挂 coverage**，于是嵌套子进程
#: （测试 → `check_planner_contract` → `bench_planner` → 15 份录像）每一层都要付
#: coverage 初始化 + 写 `.coverage.*` 的代价，放大倍率远高于同进程测试。
#: 实测（本机、不带覆盖率）：全套 3086 个测试 265s，其中 ≥10s 的 8 个测试全在
#: 这两个文件里，合计 77s —— 个数占 0.2%、墙钟占 29%。v0.27.0 就是被这一下
#: 顶过了 25 分钟的 job 上限（CI 跑到 56% 超时，一个覆盖率数字都没产出）。
#:
#: 为什么排了不影响结论：它们测的是 `scripts/check_planner_contract.py` 与
#: `tools/ai-bridge/`，**都不在** `docs/覆盖率下限.json` 的点阈值表里（表里全是
#: `src/jikuai/*`）；子进程路过的那些 `src/jikuai` 行另有直接测试覆盖。
#: **加新条目前必须实测点阈值不掉**，别凭「看着像不影响」就往里加 —— 那是这份
#: 清单变成藏污纳垢处的开始。
覆盖率排除 = (
    'tests/test_v0_27_0_w161_G23规划器契约.py',
    'tests/test_v0_27_0_w160_规划录像与bench.py',
)

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


def _读下限():
    """读逐文件下限表。返回 {相对posix路径: 下限百分比}。文件不存在则空表。"""
    if not os.path.exists(下限文件):
        return {}
    with open(下限文件, 'r', encoding='utf-8') as f:
        return json.load(f).get('下限', {})


def _检查逐文件下限(json路径):
    """按 docs/覆盖率下限.json 逐文件核对覆盖率。

    这是 W94「点阈值优先于面阈值」纪律的**执行体**：全局 fail_under 是面阈值，
    掩盖单点回归；这里对点名文件卡各自的地板。返回 (是否全部达标, 违规列表)。

    coverage 的 JSON（`coverage json`）里每个文件的百分比在
    `files[路径].summary.percent_covered`，路径分隔符随平台，统一成 posix 再比。
    """
    下限 = _读下限()
    if not 下限:
        return True, []
    with open(json路径, 'r', encoding='utf-8') as f:
        数据 = json.load(f)
    文件表 = 数据.get('files', {})
    规范化 = {}
    for 路径, 信息 in 文件表.items():
        键 = os.path.relpath(路径, REPO_ROOT) if os.path.isabs(路径) else 路径
        规范化[键.replace(os.sep, '/')] = 信息.get('summary', {}).get(
            'percent_covered', 0.0)
    违规 = []
    for 目标, 地板 in 下限.items():
        实测 = 规范化.get(目标)
        if 实测 is None:
            违规.append((目标, None, 地板))  # 该文件没被测到，等同触底
        elif 实测 + 1e-9 < 地板:
            违规.append((目标, 实测, 地板))
    return (not 违规), 违规


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
    解析器.add_argument('--检查下限', dest='检查下限', action='store_true',
                        help='按 docs/覆盖率下限.json 逐文件卡点阈值，'
                             '任一文件跌破其下限即以非 0 退出（W94 点阈值纪律）')
    解析器.add_argument('--不排除', dest='不排除', action='store_true',
                        help='连 覆盖率排除 里的子进程密集文件一起跑（要 ~2 倍时间，'
                             '只在核对「排除是否真的不影响点阈值」时用）')
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

        测试参数 = [sys.executable, '-m', 'coverage', 'run',
                    '-m', 'pytest', 'tests', '-q']
        if not args.不排除:
            for 文件 in 覆盖率排除:
                测试参数.append('--ignore=' + 文件)
            print('覆盖率跑排除 %d 个子进程密集文件（它们在「运行全部测试」那步已全速跑过，'
                  '这里只是不进覆盖率统计）：%s'
                  % (len(覆盖率排除), '、'.join(覆盖率排除)))
        测试码 = _跑(测试参数, 环境)

        # 无论测试成败都 combine —— 失败时的部分数据也有诊断价值。
        码 = _跑([sys.executable, '-m', 'coverage', 'combine'], 环境)
        if 码 != 0:
            print('coverage combine 失败，报告不可信', file=sys.stderr)
            return 码

        报告参数 = [sys.executable, '-m', 'coverage', 'report']
        if args.升序:
            报告参数.append('--sort=cover')
        # `coverage report` 在总覆盖率低于 pyproject 的 `fail_under` 时以 2 退出。
        # 这个退出码以前被丢掉了 —— 面阈值等于没设。只在门禁模式（--检查下限）
        # 下让它生效：平时看基线不该被阈值挡住。
        报告码 = _跑(报告参数, 环境)
        if not args.检查下限:
            报告码 = 0


        # 逐文件下限要靠 JSON 里的 percent_covered，所以 `--检查下限` 时即便
        # 用户没给 `--json` 也得出一份；这种情况下用临时路径，检查完就删。
        json路径 = args.json路径
        临时json = None
        if args.检查下限 and not json路径:
            临时json = os.path.join(REPO_ROOT, '.coverage_下限检查.json')
            json路径 = 临时json
        if json路径:
            _跑([sys.executable, '-m', 'coverage', 'json',
                 '-o', json路径], 环境)

        下限码 = 0
        if args.检查下限:
            try:
                达标, 违规 = _检查逐文件下限(json路径)
            finally:
                if 临时json and os.path.exists(临时json):
                    os.remove(临时json)
            if 达标:
                print('\n逐文件下限：全部达标（%d 个受保护文件）。'
                      % len(_读下限()))
            else:
                下限码 = 1
                print('\n逐文件下限未达标（W94 点阈值纪律）：', file=sys.stderr)
                for 目标, 实测, 地板 in 违规:
                    if 实测 is None:
                        print('  %s：报告里没有这个文件（改名/删除？），'
                              '下限 %.1f%%' % (目标, 地板), file=sys.stderr)
                    else:
                        print('  %s：%.1f%% < 下限 %.1f%%'
                              % (目标, 实测, 地板), file=sys.stderr)
                print('全局 fail_under 是面阈值，掩盖单点回归；'
                      '要么补测试，要么带理由改 docs/覆盖率下限.json。',
                      file=sys.stderr)

        if 测试码 != 0:
            print('\n注意：pytest 退出码 %d —— 有用例失败，'
                  '上面的覆盖率数字只反映实际跑到的部分。' % 测试码,
                  file=sys.stderr)
        if 报告码 != 0:
            print('\n总覆盖率低于 pyproject 的 fail_under（面阈值）。',
                  file=sys.stderr)
        # 测试失败优先报 —— 用例没跑全时覆盖率数字本身不可信，先修测试。
        return 测试码 or 下限码 or 报告码

    finally:
        _卸钩子(钩子路径)
        if not args.保留数据:
            _跑([sys.executable, '-m', 'coverage', 'erase'], 静默=True)


if __name__ == '__main__':
    sys.exit(main())
