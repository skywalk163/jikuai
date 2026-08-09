# -*- coding: utf-8 -*-
"""三个 demo 的公共脚手架。

**为什么用 importlib 按路径载入而不是 `import select`**

《块选择协议 v0》要求选块器落在 `tools/ai-bridge/select.py`，而 `select` 是
Python 标准库里的 I/O 多路复用模块。一旦把 `tools/ai-bridge/` 插到 `sys.path`
首位，后续任何 `import select`（`selectors` → `subprocess` 在 POSIX 上都会）
都会拿到我们的文件，以极难排查的方式炸掉。

所以：本目录（`demos/`，无标准库同名文件）可以安全进 `sys.path`；
`tools/ai-bridge/` 不进，`select.py` 与 `glue.py` 都用
`importlib.util.spec_from_file_location` 按绝对路径载入，绕开模块名解析。
"""

import importlib.util
import io
import json
import os
import sys

_HERE = os.path.abspath(os.path.dirname(__file__))
#: `tools/ai-bridge/`。**刻意不加进 sys.path**（见模块 docstring）。
BRIDGE_DIR = os.path.normpath(os.path.join(_HERE, '..'))
#: 仓库根。
REPO_ROOT = os.path.normpath(os.path.join(BRIDGE_DIR, '..', '..'))

_SRC = os.path.join(REPO_ROOT, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _按路径载入(模块名, 文件名):
    """按绝对文件路径载入模块，不经过 `sys.path` 查找。"""
    路径 = os.path.join(BRIDGE_DIR, 文件名)
    spec = importlib.util.spec_from_file_location(模块名, 路径)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[模块名] = mod          # 允许模块内部的相对自引用与 pickling
    spec.loader.exec_module(mod)
    return mod


#: 选块器模块（`select.py`），以「块选择器」这个不冲突的名字挂载。
块选择器 = _按路径载入('块选择器', 'select.py')
#: 粘合器模块（`glue.py`）。名字本身不与标准库冲突，但为对称也走同一条路。
粘合器 = _按路径载入('块粘合器', 'glue.py')

load_index = 块选择器.load_index
select_blocks = 块选择器.select_blocks
synthesize = 粘合器.synthesize

from jikuai.main import run_source                       # noqa: E402


def 执行(源码):
    """执行一段极快源码，返回它打印到 stdout 的全部文本。

    用 `run_source` 而不是起子进程：省掉进程启动开销，且异常能直接向上抛，
    demo 一旦跑不通立刻炸在断言之前，不会被静默吞掉。
    """
    old = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        run_source(源码)
    finally:
        sys.stdout = old
    return buf.getvalue()


def 压缩比(粘合源码, 等价Python):
    """token 压缩比的**启发式**估算。

    估算方法（刻意选最保守、最可复现的一种）：
        比率 = len(粘合极快源码.strip()) / len(等价 Python 实现.strip())

    为什么用字符数而不是真 token 数：真 token 数依赖具体分词器（不同模型的
    中文 token 化差异很大），而字符数**任何人都能复算**。中文字符在多数
    BPE 分词器里约 1～2 token/字，英文标识符约 0.25～0.5 token/字符，
    所以本比率对极快（几乎全中文）是**偏悲观**的——真实 token 比率通常
    比这里报的数字更差一点，不要拿这个数当上限吹。

    局限：分母是「我们手写的一份等价 Python」，不是「AI 真实会写的代码」。
    AI 通常还会附带注释、类型标注、错误处理，实际分母更大。所以这个比率
    既不精确也不是唯一解，只是一个可复现的量级参考。
    """
    g = len(粘合源码.strip())
    p = len(等价Python.strip())
    return {'粘合代码字符': g, '等价Python字符': p, '比率': round(g / max(p, 1), 3)}


def 跑一遍(标题, 需求, 方案表, 等价Python, top=5):
    """跑完整流程并打印报告；返回 {标签: {源码, 输出, 压缩比}} 供测试复用。

    `方案表` 是 `[(标签, 方案dict), ...]`——同一个需求往往有「用一级块一步
    搞定」和「用原子块拆几步」两条路，都跑一遍才看得出块生态的分层价值。
    """
    print('=' * 64)
    print(标题)
    print('需求：' + 需求)
    print('=' * 64)

    候选 = select_blocks(需求, load_index(), top=top)
    print('\n[选块结果 top-%d]' % top)
    for c in 候选:
        print('  %-8s [%s] 分数=%-6s 导出名=%-6s %s'
              % (c['名称'], c['领域'], c['分数'], c['导出名'], c['描述']))

    结果 = {}
    for 标签, 方案 in 方案表:
        源码 = synthesize(方案)
        输出 = 执行(源码)
        比 = 压缩比(源码, 等价Python)

        print('\n' + '-' * 64)
        print('[%s] 生成的极快代码' % 标签)
        print('-' * 64)
        print(源码, end='')
        print('-' * 64)
        print('[%s] 实际运行输出' % 标签)
        print(输出, end='')
        print('-' * 64)
        print('[%s] token 压缩比估算：%s' % (标签, json.dumps(比, ensure_ascii=False)))

        结果[标签] = {'源码': 源码, '输出': 输出, '压缩比': 比, '候选': 候选}

    print('\n%s 完成。\n' % 标题)
    return 结果
