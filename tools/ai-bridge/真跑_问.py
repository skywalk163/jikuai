# -*- coding: utf-8 -*-
"""`jk 块 问` 真端点真跑台架（v0.29.0 W182）。

## 它补的是哪一段

v0.28.0 收口时 `jk 块 问 --模型 <端点>` 的挂账原文是「**从未真跑过一次真实端点**」
（`docs/BACKLOG.md` §12.4）。此前覆盖它的 `tests/test_v0_27_0_w158_w159_规划CLI.py`
把端点起在**同一个解释器**里，CLI 也是同进程调 `blocks_cli.run`——socket、子进程
编码、Bearer 头全在一个进程里打转。本台架把两头都拆成**独立进程**：

    参考回填端点.py（进程 A） ←── 真 HTTP ──→ blocks_cli 引导（进程 B）

## 它证明什么，不证明什么

**证明**：契约与传输。独立进程 + 真 socket + 真 `Authorization` 头 + 真
`validate_filled` + 真 `组`/`跑` 这条链路通不通，以及各条拒绝路径在跨进程下的
退出码对不对。

**不证明**「某个真实 LLM 能回填对」。端点是 `参考回填端点.py`，机械造填、
`模型` 字段如实写 `参考端点·…`。**跑绿本台架不等于可以把 §12.4 那条挂账改成
「模型端点已验证」**——它只让「契约端点从未真跑过」这半边落地。真模型那半边要
另接适配层（BACKLOG 里另记）。

## 为什么不进 CI

ADR-41 §8 的口径：CI 只回放录像，真跑只在本机人工跑。本台架要起两个子进程、
占端口、跑真数据集，放进 CI 会把「网络/端口/时序抖动」混进回归信号里
（v0.28.0 那条 `WinError 10053` 抖动就是先例）。**但它的产物要归档**（W183），
归档件本身进版本库、可复核。

## 用法

    python tools/ai-bridge/真跑_问.py                    # 跑全部用例，只报数
    python tools/ai-bridge/真跑_问.py --出 tools/ai-bridge/真跑记录
    python tools/ai-bridge/真跑_问.py --用例 录像正例

退出码：全部用例判定与期望一致 → 0；有不一致 → 1。

**产出物一律由 Python 自己写文件**（`--出`），不许走 shell 重定向：Windows 控制台
按 GBK 转码，`单车电耗均值` 这类中文列名落盘就坏了（v0.28.0 W173 的教训）。
"""

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.abspath(os.path.dirname(__file__))
# 本目录挪到 sys.path 末尾，理由同 `参考回填端点.py`（`select.py` 遮蔽标准库）。
sys.path[:] = ([p for p in sys.path if os.path.abspath(p) != _HERE]
               + [p for p in sys.path if os.path.abspath(p) == _HERE])
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.service import schema  # noqa: E402
from jikuai.service.schema import (  # noqa: E402
    RUN_ENVELOPE_REQUIRED, RESULT_REQUIRED,
)

_R源码, _R执行结果 = RUN_ENVELOPE_REQUIRED
_Fstdout, _Fstderr, _F返回值, _F耗时毫秒 = RESULT_REQUIRED

端点脚本 = os.path.join(_HERE, '参考回填端点.py')
默认出目录 = os.path.join(_HERE, '真跑记录')

#: 客户端引导。**不装包也能跑**：`jk` 是 console_script，本机未 `pip install -e .`
#: 时不在 PATH 上；这里直接把 `blocks_cli.run` 当入口，走的是同一份代码。
_客户端引导 = ('import sys\n'
              'from jikuai.pkg import blocks_cli\n'
              'sys.exit(blocks_cli.run(sys.argv[1:]))\n')

#: 鉴权环境变量（客户端与端点共读，见 `参考回填端点.py` 模块文档）。
ENV令牌 = 'JIKUAI_PLANNER_TOKEN'

#: 退出码约定（`blocks_cli`）：0 成功 / 1 输入或校验被拒 / 2 执行期报错。
判定表 = {0: '通过', 1: '拒', 2: '执行报错'}

#: 用例表。`期望判定` 是**人拍板的期望**，不是把实测抄回来——口径同
#: `规划录像/清单.json` 的 `期望判定`。
用例表: List[Dict[str, Any]] = [
    {
        '名': '录像正例',
        '问句': '2026年6月各车型的总产量是多少？按产量从高到低排序。',
        '模式': '录像',
        'top': 8,
        '令牌': '',
        '期望判定': '通过',
        '期望stdout片段': ['M0'],
        '说明': '默认 top=8 就能承载 Q_PUB_001 的三个块（骨架块走 W174 语义旁路进来），'
                '所以这条不需要调大 top——真跑一次把「承载得下」变成实测而不是推断。',
    },
    {
        '名': '标量正例',
        '问句': '6月按班次看有多少天',
        '模式': '标量',
        'top': 8,
        '令牌': '',
        '期望判定': '通过',
        '期望stdout片段': [],
        '说明': '不碰数据集的一条：证明链路本身通，与 CSV 读得到读不到无关。',
    },
    {
        '名': '幻觉反例',
        '问句': '2026年6月各车型的总产量是多少？按产量从高到低排序。',
        '模式': '幻觉',
        'top': 8,
        '令牌': '',
        '期望判定': '拒',
        '期望stderr片段': ['校验拒绝', '白名单'],
        '说明': '端点响应一律当不可信输入。此前这条只在同进程 mock 上证过。',
    },
    {
        '名': '鉴权不匹配反例',
        '问句': '6月按班次看有多少天',
        '模式': '标量',
        'top': 8,
        '令牌': 'endpoint-only-token',   # 只给端点，不给客户端；**必须纯 ASCII**
        '仅端点令牌': True,
        '期望判定': '拒',
        '期望stderr片段': ['HTTP 401'],
        '说明': '端点要 Bearer、客户端没发 → 401。这条在同进程 fixture 里也能测，'
                '但跨进程才顺带证明「令牌不会靠共享内存漏过去」。',
    },
    {
        '名': '库外问句反例',
        '问句': '把这段话转成繁体再算个SHA256',
        '模式': '标量',
        'top': 8,
        '令牌': '',
        '期望判定': '拒',
        '期望stderr片段': ['拒答'],
        '说明': '拒答要在**调端点之前**就停。真跑下端点进程根本收不到请求。',
    },
]


def _子进程环境(令牌: str = '') -> Dict[str, str]:
    """子进程环境。**必须显式给 UTF-8 两个变量**（v0.28.0 W164 的教训）：

    Windows 上子进程默认按 GBK 写 stdout，父进程按 UTF-8 解就抛
    `UnicodeDecodeError`，报错看着像被测代码坏了。
    """
    环境 = dict(os.environ)
    环境['PYTHONUTF8'] = '1'
    环境['PYTHONIOENCODING'] = 'utf-8'
    既有 = 环境.get('PYTHONPATH') or ''
    环境['PYTHONPATH'] = _SRC + (os.pathsep + 既有 if 既有 else '')
    环境.pop(ENV令牌, None)
    if 令牌:
        环境[ENV令牌] = 令牌
    return 环境


def 起端点(模式: str, 令牌: str = '', 超时: float = 30.0):
    """起 `参考回填端点.py` 子进程，返回 `(进程, url)`。读不到 URL 就抛 RuntimeError。"""
    进程 = subprocess.Popen(
        [sys.executable, '-u', 端点脚本, '--模式', 模式],
        cwd=_REPO, env=_子进程环境(令牌),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding='utf-8', errors='replace')
    截止 = time.monotonic() + 超时
    while time.monotonic() < 截止:
        行 = 进程.stdout.readline()
        if not 行:
            if 进程.poll() is not None:
                错 = 进程.stderr.read()
                raise RuntimeError('端点进程启动就退了：%s' % 错.strip())
            continue
        行 = 行.strip()
        if 行.startswith('端点='):
            return 进程, 行.split('=', 1)[1]
    进程.kill()
    raise RuntimeError('端点在 %.0fs 内没报出 URL' % 超时)


def 跑问(url: str, 问句: str, top: int = 8, 令牌: str = '',
        严格: bool = False, 超时: float = 180.0):
    """真跑一次 `jk 块 问 <问句> --模型 <url> --json`，返回 `(退出码, out, err)`。"""
    argv = ['问', 问句, '--模型', url, '--top', str(top), '--json']
    if 严格:
        argv.append('--严格')
    完成 = subprocess.run(
        [sys.executable, '-c', _客户端引导] + argv,
        cwd=_REPO, env=_子进程环境(令牌),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding='utf-8', errors='replace', timeout=超时)
    return 完成.returncode, 完成.stdout, 完成.stderr


def 一次真跑(用例: Dict[str, Any]) -> Dict[str, Any]:
    """跑一条用例，出一份**可归档的记录**（判定 + 现场，不做任何修饰）。"""
    仅端点令牌 = bool(用例.get('仅端点令牌'))
    端点令牌 = 用例.get('令牌') or ''
    客户端令牌 = '' if 仅端点令牌 else 端点令牌
    进程, url = 起端点(用例['模式'], 端点令牌)
    端点尾声 = ''
    try:
        码, out, err = 跑问(url, 用例['问句'], 用例.get('top', 8), 客户端令牌)
    finally:
        进程.terminate()
        try:
            _, 端点错 = 进程.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            进程.kill()
            _, 端点错 = 进程.communicate()
        端点尾声 = (端点错 or '').strip()
    判定 = 判定表.get(码, '未知退出码 %s' % 码)
    记录: Dict[str, Any] = {
        '用例': 用例['名'],
        '问句': 用例['问句'],
        '端点模式': 用例['模式'],
        'top': 用例.get('top', 8),
        '鉴权': ('端点要求且客户端未给' if 仅端点令牌
                 else ('两侧一致' if 端点令牌 else '未启用')),
        '退出码': 码,
        '判定': 判定,
        '期望判定': 用例['期望判定'],
        '一致': 判定 == 用例['期望判定'],
        'stderr': err.strip(),
        '说明': 用例.get('说明', ''),
    }
    if 'Traceback' in 端点尾声:
        # 端点自己崩了要在记录里看得见。首跑就栽在这上面：`select.py` 遮蔽标准库，
        # 端点绑得上、URL 印出来了，`serve_forever` 一进 selectors 就当场退，
        # 客户端只看到 `WinError 10061 连接被拒绝`——不记端点 stderr 就查不到病根。
        记录['端点异常'] = 端点尾声[-800:]
    缺 = []
    for 片段 in (用例.get('期望stdout片段') or []):
        if 片段 not in out:
            缺.append('stdout 缺 %r' % 片段)
    for 片段 in (用例.get('期望stderr片段') or []):
        if 片段 not in err:
            缺.append('stderr 缺 %r' % 片段)
    记录['片段缺失'] = 缺
    if 缺:
        记录['一致'] = False
    if 码 == 0:
        try:
            信封 = json.loads(out)
        except ValueError as e:
            记录['一致'] = False
            记录['信封问题'] = '退 0 但 stdout 不是 JSON：%s' % e
        else:
            问题 = schema.validate_run_envelope(信封)
            记录['信封问题'] = 问题 or '（合法跑响应）'
            记录['执行stdout'] = (信封.get(_R执行结果) or {}).get(_Fstdout, '')
            记录['源码行数'] = len((信封.get(_R源码) or '').splitlines())
            if 问题:
                记录['一致'] = False
    return 记录


def 写记录(记录表: List[Dict[str, Any]], 出目录: str) -> str:
    """把记录逐条落盘 + 出一份清单。**Python 自己写文件，不走重定向。**"""
    os.makedirs(出目录, exist_ok=True)
    for 条 in 记录表:
        名 = os.path.join(出目录, '%s.json' % 条['用例'])
        with open(名, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(条, f, ensure_ascii=False, indent=2)
            f.write('\n')
    清单 = {
        '版本': '1.0.0',
        '说明': ('`jk 块 问` 真端点真跑记录（v0.29.0 W182/W183）。端点是 '
                 '`参考回填端点.py`（机械造填的**对拍端点**，不是模型），所以这批记录'
                 '证明的是「契约 + 传输 + 校验 + 组 + 跑」在**独立进程 + 真 socket** '
                 '下走得通，**不证明任何真实 LLM 能回填对**。这批记录刻意**不进** '
                 '`规划录像/`：那边的 `白名单可承载率` 分母按录像 id 建，混进来会换'
                 '分母、让 v0.28.0 W174 报的 0.0% → 60.0% 跨轮次没法比。'),
        '一致数': sum(1 for 条 in 记录表 if 条['一致']),
        '总数': len(记录表),
        '记录': [{'用例': 条['用例'], '文件': '%s.json' % 条['用例'],
                  '判定': 条['判定'], '期望判定': 条['期望判定'],
                  '一致': 条['一致']} for 条 in 记录表],
    }
    路径 = os.path.join(出目录, '清单.json')
    with open(路径, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(清单, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return 路径


def 主(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description='`jk 块 问` 真端点真跑台架')
    p.add_argument('--出', default='', help='归档目录；不给就只报数不落盘')
    p.add_argument('--用例', default='', help='只跑指定名字的用例')
    a = p.parse_args(argv)
    待跑 = [u for u in 用例表 if not a.用例 or u['名'] == a.用例]
    if not 待跑:
        print('没有名为 %r 的用例。可选：%s'
              % (a.用例, '、'.join(u['名'] for u in 用例表)), file=sys.stderr)
        return 1
    记录表 = []
    for 用例 in 待跑:
        条 = 一次真跑(用例)
        记录表.append(条)
        print('%-14s 退出码=%s 判定=%s 期望=%s %s'
              % (条['用例'], 条['退出码'], 条['判定'], 条['期望判定'],
                 'OK' if 条['一致'] else '不一致 %s' % (条['片段缺失'] or '')))
    if a.出:
        print('清单已落盘：%s' % 写记录(记录表, a.出))
    不一致 = [条 for 条 in 记录表 if not 条['一致']]
    print('一致 %d / %d' % (len(记录表) - len(不一致), len(记录表)))
    return 1 if 不一致 else 0


if __name__ == '__main__':
    sys.exit(主())
