# -*- coding: utf-8 -*-
"""G20：wheel 内容断言（v0.24.0 W116）。

守的是 BACKLOG §10 那次**已经发生过**的事故：PyPI 0.4.1 的 wheel 里零个
stdlib 文件，装完 `导入 数学` 直接失败，而本机 editable 一切正常。
这条门禁必须真去构建 wheel 再解包看，不能只看 pyproject 声明——
「声明写了」和「文件真在包里」是两件事。

本文件只测纯断言函数 `校验wheel条目`（吃条目名列表，不碰构建），
所以跑得飞快、可进常规回归。真构建那一步在
`python scripts/check_wheel_contents.py` 里，单独跑。
"""

import os
import sys

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SCRIPTS = os.path.join(_REPO, 'scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import check_wheel_contents as G20  # noqa: E402


def test_缺词典即判失败():
    条目 = ['jikuai/__init__.py', 'jikuai/stdlib/blocks/索引.json']
    问题 = G20.校验wheel条目(条目)
    assert any('分词词典.txt' in p for p in 问题)


def test_pyc泄漏即判失败():
    条目 = list(G20.必需条目()) + ['jikuai/stdlib/__pycache__/分词.cpython-311.pyc']
    问题 = G20.校验wheel条目(条目)
    assert any('.pyc' in p for p in 问题)


def test_块json不足112即判失败():
    条目 = list(G20.必需条目())
    问题 = G20.校验wheel条目(条目)
    assert any('112' in p for p in 问题)


def test_块背衬py缺失即判失败():
    """W114 那次差点漏掉的洞：14 个混合模块背衬 .py 全没进 wheel 时，
    原来的三项具名断言 + 块 json 计数**全会绿**。这条就是补那个洞的。"""
    条目 = _满足其它断言的条目()
    # 只有 1 个背衬 .py（那个具名样本），期望 14
    问题 = G20.校验wheel条目(条目)
    assert any('块背衬 .py 有 1 个' in p for p in 问题)


def test_临时测试产物随包发即判失败():
    """W114 实测：9 个块自测写出的 `临时_测试*.txt` 真的进过 wheel。
    根因（自测污染源码树）已在 W115 修掉，但门禁得独立守住——
    「根因修了」不等于「以后不会有别的东西漏进来」。"""
    条目 = _满足其它断言的条目(背衬数=G20.块背衬PY数)
    条目.append('jikuai/stdlib/blocks/数据/存文/临时_测试.txt')
    问题 = G20.校验wheel条目(条目)
    assert any('临时_测试' in p for p in 问题), 问题


def test_干净条目集全绿():
    """反向锚：上面几条都是「该红就红」，这条确认「该绿就绿」——
    否则一个恒真的断言也能让上面全部通过。"""
    assert G20.校验wheel条目(_满足其它断言的条目(背衬数=G20.块背衬PY数)) == []


def test_G20已在静态门禁里留痕():
    """G20 **刻意不**被 `check_stdlib_contract.py` 调用（它要跑 `python -m build`，
    比那边的秒级静态检查慢两个数量级）。但「不调用」和「没人知道它存在」之间只差
    一行提示——G16/G17 用 `test_G17已串进check_stdlib_contract` 守住「串进去了」，
    这条守住「提示还在」。删掉提示，G20 就成了没人跑的死门禁。
    """
    path = os.path.join(_SCRIPTS, 'check_stdlib_contract.py')
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    assert 'G20' in text
    assert 'check_wheel_contents' in text


def _满足其它断言的条目(背衬数=1):
    """造一份除指定维度外全部合规的条目名列表。"""
    条目 = [f'jikuai/stdlib/blocks/领域{i}/块{i}/块{i}.json' for i in range(120)]
    条目 += list(G20.必需条目())
    # 具名样本本身算 1 个背衬 .py，按需补齐到 背衬数
    条目 += [f'jikuai/stdlib/blocks/领域{i}/块{i}/背衬{i}.py'
             for i in range(背衬数 - 1)]
    return 条目
