# -*- coding: utf-8 -*-
"""压缩比基准（v0.14.0 W12）—— 「不用块的裸写 token 数 vs 用块的方案 JSON token 数」。

## 两个口径

- **方案 token（用块）**：demo 顶部注释里那段方案 JSON 的 token 数。块生态下，
  这是 AI 需要吐出的**全部**内容——选哪几个块、喂什么参数；剩下的由
  `glue.synthesize` 展开成源码，由块本身提供算法。

- **裸写 token（不用块）**：把 demo 直接依赖与**传递依赖**的所有块源码
  （`<块>.jk` 及其 `.py` 背衬）加上 demo 自身的编排代码，全部计入。
  这才是「块生态不存在时，等价功能必须从头写出来的量」——比如 `工资条`
  背后是 `税单`→`个税`（七级超额累进表）+`增值税`+`保留分`(Decimal 分位)
  +`金额报表`→`金额雅写`(中文大写)+`周岁`(闰年/生日未到判定)。

  同时报一个**参考口径** `同源token`：只数 demo 自身源码（剔除方案 JSON
  注释块）。它是压缩比的**下界**，不作门槛——因为它把「块已经替你写好的
  算法」当成零成本，严重低估裸写量（实测中位数仅 ~1.5x）。

- **压缩比** = 裸写 token / 方案 token。发布门槛：**中位数 ≥ 8x**。

token 用 `tiktoken.get_encoding('cl100k_base')`；未装 tiktoken 时退化为
「UTF-8 字节数 ÷ 3.5」近似并打警告——近似误差对**比值**基本抵消。

用法::

    python tools/ai-bridge/bench_compress.py
    python tools/ai-bridge/bench_compress.py --json
"""

import argparse
import glob
import json
import os
import re
import statistics
import sys

_HERE = os.path.abspath(os.path.dirname(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))

# stdlib 已收进包内（ADR-39 / v0.24.0）。块根一律经 resources 单一入口定位，
# 不再拼旧的仓库根 `stdlib/blocks`——那条路径搬走后不存在，会让依赖闭包全落空。
sys.path.insert(0, os.path.join(_ROOT, 'src'))
from jikuai import resources  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass

demo目录 = os.path.join(_ROOT, 'examples', 'blocks', 'demo')
块根 = resources.blocks_dir()

门槛 = 8.0

方案注释头 = '-- 方案 JSON'

_导入式 = re.compile(r'从\s+blocks\.([^.\s]+)\.([^\s]+?)\s+导入')


# ---------- token ----------

def 取编码器():
    """返回 `(编码器, 是否精确)`；没装 tiktoken 或拉不到词表返回 `(None, False)`。"""
    try:
        import tiktoken
    except ImportError:
        return None, False
    try:
        return tiktoken.get_encoding('cl100k_base'), True
    except Exception:
        # 首次使用需联网下载 BPE 词表；离线环境同样退化
        return None, False


def 数token(文本, 编码器):
    if 编码器 is not None:
        return len(编码器.encode(文本))
    return max(1, int(round(len(文本.encode('utf-8')) / 3.5)))


# ---------- 方案 JSON 抽取 ----------

def 抽方案(源码):
    """从 demo 顶部注释抽方案 JSON。返回 `(方案串, 该注释块的行下标集合)`。

    口径：`-- 方案 JSON` 打头的注释行之后，连续的 `--` 注释行去掉前缀拼起来
    能被 `json.loads` 吃下即为方案。找不到返回 `(None, set())`。
    """
    行表 = 源码.splitlines()
    起 = None
    for i, 行 in enumerate(行表):
        if 行.strip().startswith(方案注释头):
            起 = i
            break
    if 起 is None:
        return None, set()

    片段, 下标 = [], {起}
    for j in range(起 + 1, len(行表)):
        裸 = 行表[j].strip()
        if not 裸.startswith('--'):
            break
        内容 = 裸[2:].strip()
        if not 内容:
            break
        片段.append(内容)
        下标.add(j)

    文本 = ''.join(片段)
    try:
        json.loads(文本)
    except ValueError:
        return None, set()
    return 文本, 下标


def 剔注释(源码, 下标集):
    行表 = 源码.splitlines()
    return '\n'.join(行 for i, 行 in enumerate(行表) if i not in 下标集)


# ---------- 依赖闭包 ----------

def 块源token(领域, 块名, 编码器, 已访, 命中):
    """递归累计某块（含 `.py` 背衬）与其传递依赖块的 token 数。"""
    键 = (领域, 块名)
    if 键 in 已访:
        return 0
    已访.add(键)
    目录 = os.path.join(块根, 领域, 块名)
    门面 = os.path.join(目录, 块名 + '.jk')
    if not os.path.exists(门面):
        return 0
    命中.append('%s.%s' % (领域, 块名))
    with open(门面, 'r', encoding='utf-8') as f:
        源 = f.read()
    合计 = 数token(源, 编码器)
    背衬 = os.path.join(目录, 块名 + '.py')
    if os.path.exists(背衬):
        with open(背衬, 'r', encoding='utf-8') as f:
            合计 += 数token(f.read(), 编码器)
    for 子域, 子名 in _导入式.findall(源):
        合计 += 块源token(子域, 子名, 编码器, 已访, 命中)
    return 合计


# ---------- 核心 ----------

def 跑全量(目录=demo目录):
    编码器, 精确 = 取编码器()
    明细 = []
    for 路径 in sorted(glob.glob(os.path.join(目录, '*.jk'))):
        名 = os.path.basename(路径)
        with open(路径, 'r', encoding='utf-8') as f:
            源码 = f.read()
        方案, 下标集 = 抽方案(源码)
        if 方案 is None:
            明细.append({'demo': 名, '错误': '顶部注释里没找到可解析的方案 JSON'})
            continue
        方案数 = 数token(方案, 编码器)
        同源 = 数token(剔注释(源码, 下标集), 编码器)
        已访, 命中 = set(), []
        依赖 = sum(块源token(域, 块, 编码器, 已访, 命中)
                   for 域, 块 in _导入式.findall(源码))
        裸写 = 同源 + 依赖
        明细.append({
            'demo': 名,
            '方案token': 方案数,
            '同源token': 同源,
            '依赖块token': 依赖,
            '裸写token': 裸写,
            '压缩比': 裸写 / 方案数 if 方案数 else 0.0,
            '同源压缩比': 同源 / 方案数 if 方案数 else 0.0,
            '依赖块': 命中,
        })

    有效 = [d for d in 明细 if '错误' not in d]
    失败 = [d for d in 明细 if '错误' in d]
    比值 = [d['压缩比'] for d in 有效]
    同源比 = [d['同源压缩比'] for d in 有效]
    中位数 = statistics.median(比值) if 比值 else 0.0
    return {
        'demo数': len(明细),
        '有效数': len(有效),
        'token口径': 'tiktoken/cl100k_base' if 精确 else 'UTF-8字节÷3.5（近似）',
        'token精确': 精确,
        '中位数压缩比': 中位数,
        '最低压缩比': min(比值) if 比值 else 0.0,
        '最高压缩比': max(比值) if 比值 else 0.0,
        '同源中位数压缩比': statistics.median(同源比) if 同源比 else 0.0,
        '门槛': 门槛,
        '达标': bool(有效) and not 失败 and 中位数 >= 门槛,
        '明细': 明细,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description='压缩比基准（v0.14.0 W12）')
    p.add_argument('--json', action='store_true', help='输出 JSON 报告')
    p.add_argument('--demo-dir', dest='demo_dir', default=demo目录)
    args = p.parse_args(argv)

    报告 = 跑全量(args.demo_dir)

    if not 报告['token精确']:
        print('[警告] 未装 tiktoken（或拉不到 cl100k_base 词表），'
              '退化为「UTF-8 字节数 ÷ 3.5」近似 token', file=sys.stderr)

    if args.json:
        print(json.dumps(报告, ensure_ascii=False, indent=2))
        return 0 if 报告['达标'] else 1

    print('压缩比基准 · v0.14.0 W12 · %d 个 demo' % 报告['demo数'])
    print('token 口径：%s' % 报告['token口径'])
    print('裸写 = demo 自身编排 + 直接与传递依赖块的全部源码（含 .py 背衬）')
    print('')
    print('%-26s %8s %8s %8s %9s' % ('demo', '方案', '同源', '裸写', '压缩比'))
    print('-' * 64)
    for d in 报告['明细']:
        if '错误' in d:
            print('%-26s %8s %8s %8s %9s   %s'
                  % (d['demo'], '-', '-', '-', '-', d['错误']))
            continue
        print('%-26s %8d %8d %8d %8.1fx'
              % (d['demo'], d['方案token'], d['同源token'],
                 d['裸写token'], d['压缩比']))
    print('-' * 64)
    print('压缩比区间：%.1fx ~ %.1fx' % (报告['最低压缩比'], 报告['最高压缩比']))
    print('中位数压缩比 %.1fx（门槛 ≥%.0fx）' % (报告['中位数压缩比'], 报告['门槛']))
    print('参考：只数 demo 自身源码（不计块实现）中位数仅 %.1fx —— 该口径把'
          '「块已替你写好的算法」当零成本，是压缩比下界，不作门槛。'
          % 报告['同源中位数压缩比'])
    print('')
    print('达标' if 报告['达标']
          else '未达标（中位数 <%.0fx 或有 demo 缺方案 JSON）' % 报告['门槛'])
    return 0 if 报告['达标'] else 1


if __name__ == '__main__':
    raise SystemExit(main())