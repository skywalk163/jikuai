# -*- coding: utf-8 -*-
"""v0.13.0 W1 · 导入声明反哺 lexer 白名单。

背景（ADR-15 §3.7 / v0.12.0 复盘 §3.1）：极快是免空格分词器，调用方在
`导入` 时无法预知被导入模块的导出名，所以非词法原子的导出名（`块求和`
会被切成 `块` + `求和`）在调用方一侧必然被切碎——v0.12.0 因此被迫改名
11 个块目录。

W1 的通路：Pass1 解析出 `Import` 节点 → 静默解析目标 `.jk` 路径 →
`pkg.blocks.extract_exports` 提取导出名 → 作为 `external_defs` 注入
lexer → Pass2 重新分词并重新解析。
"""

import textwrap

import pytest

from jikuai.evaluator import Evaluator
from jikuai.frontend import compile_source
from jikuai.main import run_source
from jikuai.tokens import TokenType


# 非原子导出名：`块求和` 在调用方会被切成 `块`(IDENT) + `求和`(VERB)
非原子块源码 = textwrap.dedent("""\
    函数 块求和 接收 赵数值：
      返回 求和 赵数值。
    。

    导出 块求和。
    """)


def _写模块(tmp_path, 名字, 源码):
    p = tmp_path / (名字 + '.jk')
    p.write_text(源码, encoding='utf-8')
    return p


def _写调用方(tmp_path, 源码):
    p = tmp_path / '调用方.jk'
    p.write_text(源码, encoding='utf-8')
    return p


def _ident值(tokens):
    return [t.value for t in tokens if t.type == TokenType.IDENT]


# ---------------------------------------------------------------------------
# 核心：非原子导出名在调用方不再被切碎
# ---------------------------------------------------------------------------

def test_非原子导出名在调用方整体识别为IDENT(tmp_path):
    _写模块(tmp_path, '我的块', 非原子块源码)
    src = "从 我的块 导入 块求和。\n打印 块求和(列 1 2 3)。\n"
    caller = _写调用方(tmp_path, src)

    r = compile_source(src, file=str(caller))

    assert r.two_pass is True
    assert r.converged is True
    assert '块求和' in _ident值(r.tokens)
    # AST 也必须是重新解析后的结果：`从...导入` 的名字列表只有一个整体名字
    assert r.ast.body[0].names == ['块求和']


def test_非原子导出名端到端执行(tmp_path, capsys):
    _写模块(tmp_path, '我的块', 非原子块源码)
    src = "从 我的块 导入 块求和。\n打印 块求和(列 1 2 3)。\n"
    caller = _写调用方(tmp_path, src)

    ev = Evaluator()
    ev._current_file = str(caller)
    run_source(src, ev, file=str(caller))

    assert capsys.readouterr().out.strip() == '6'


def test_导入模块整体形式也反哺(tmp_path):
    """`导入 M。` 后用 `M.块求和(...)` —— 成员名同样需要不被切碎。"""
    _写模块(tmp_path, '我的块', 非原子块源码)
    src = "导入 我的块。\n打印 我的块.块求和(列 1 2 3)。\n"
    caller = _写调用方(tmp_path, src)

    r = compile_source(src, file=str(caller))

    assert '块求和' in _ident值(r.tokens)


def test_模块体内的导入也反哺(tmp_path, capsys):
    """L2 聚合 L1 的场景：被加载的模块自己也 `导入` 非原子导出名。"""
    _写模块(tmp_path, '底层块', 非原子块源码)
    _写模块(tmp_path, '中层块', textwrap.dedent("""\
        从 底层块 导入 块求和。

        函数 双倍汇总 接收 赵数值：
          返回 乘 块求和(赵数值) 2。
        。

        导出 双倍汇总。
        """))
    src = "从 中层块 导入 双倍汇总。\n打印 双倍汇总(列 1 2 3)。\n"
    caller = _写调用方(tmp_path, src)

    ev = Evaluator()
    ev._current_file = str(caller)
    run_source(src, ev, file=str(caller))

    assert capsys.readouterr().out.strip() == '12'


# ---------------------------------------------------------------------------
# 回退开关
# ---------------------------------------------------------------------------

def test_开关off时退化为v0_12_0行为(tmp_path, monkeypatch):
    monkeypatch.setenv('JIKUAI_IMPORT_WHITELIST', 'off')
    _写模块(tmp_path, '我的块', 非原子块源码)
    src = "从 我的块 导入 块求和。\n"
    caller = _写调用方(tmp_path, src)

    r = compile_source(src, file=str(caller))

    assert r.two_pass is False
    assert r.ast.body[0].names == ['块', '求和']     # 被切碎，即旧行为


def test_legacy_adr06强制单遍(tmp_path, monkeypatch):
    monkeypatch.setenv('JIKUAI_LEGACY_ADR06', '1')
    _写模块(tmp_path, '我的块', 非原子块源码)
    src = "从 我的块 导入 块求和。\n"
    caller = _写调用方(tmp_path, src)

    r = compile_source(src, file=str(caller))

    assert r.two_pass is False


# ---------------------------------------------------------------------------
# 零回归与失败静默
# ---------------------------------------------------------------------------

def test_无导入无类不走两遍():
    r = compile_source("打印 加 1 2。\n")
    assert r.two_pass is False
    assert r.converged is True


def test_原子导出名不触发Pass2_性能优化():
    """生产块 `blocks.数据.求和` 的导出名 `汇总` 本就原子，不需要白名单救，
    因此白名单为空、不触发 Pass2——这是性能优化的预期行为。"""
    src = "从 blocks.数据.求和 导入 汇总。\n打印 汇总(列 1 2 3)。\n"
    r = compile_source(src)
    assert r.two_pass is False          # 原子名过滤后白名单为空 → 无需 Pass2
    assert r.converged is True
    assert r.ast.body[0].names == ['汇总']


def test_找不到的模块不让编译崩():
    r = compile_source("从 blocks.压根不存在.块 导入 名字。\n")
    assert r.ast is not None
    assert r.two_pass is False          # 白名单为空 → 无需 Pass2


def test_蟒桥导入被跳过():
    """ADR-11：Python 桥必须括号调用，不参与元数与白名单。"""
    r = compile_source("导入 蟒:math。\n打印 math.floor(1.5)。\n")
    assert r.ast is not None
    assert r.two_pass is False


def test_类与导入共存时一次Pass2完成(tmp_path):
    _写模块(tmp_path, '我的块', 非原子块源码)
    src = textwrap.dedent("""\
        从 我的块 导入 块求和。

        类 甲：
            方法 处理 接收 参数：
                返回 块求和(参数)。
        。

        赵实例 是 新建 甲。
        打印 赵实例.处理(列 1 2 3)。
        """)
    caller = _写调用方(tmp_path, src)

    r = compile_source(src, file=str(caller))

    assert r.two_pass is True
    assert '块求和' in _ident值(r.tokens)


def test_动态与空模块名不崩():
    for src in ("导入 。\n", "导入\n"):
        with pytest.raises(Exception):
            # 语法本身非法，应由 parser 报错而非白名单收集阶段崩
            compile_source(src)


# ---------------------------------------------------------------------------
# W4 · _RESOLVE_CACHE 只缓存命中
# ---------------------------------------------------------------------------

def test_W4_未命中不进缓存_后建文件能反哺(tmp_path):
    """长驻进程（LSP/REPL）里先导入不存在的模块，之后创建它，反哺须生效。

    W4 前的实现把 `None` 也写进 `_RESOLVE_CACHE`，负结果没有任何失效手段——
    文件的**出现**无法靠 mtime 指纹发现（没有文件可 stat），于是这个模块在整个
    进程生命周期里永远解析不到。
    """
    from jikuai import frontend

    src = "从 我的块 导入 块求和。\n打印 块求和(列 1 2 3)。\n"
    caller = _写调用方(tmp_path, src)

    frontend._RESOLVE_CACHE.clear()

    # 第一次：`我的块.jk` 还不存在 → 白名单为空，不该触发 Pass2
    r1 = compile_source(src, file=str(caller))
    assert r1.two_pass is False

    # 关键断言：未命中不得留下缓存条目
    assert not frontend._RESOLVE_CACHE

    # 现在把模块创建出来，同一进程内再编译一次
    _写模块(tmp_path, '我的块', 非原子块源码)
    r2 = compile_source(src, file=str(caller))

    assert r2.two_pass is True
    assert '块求和' in _ident值(r2.tokens)


def test_W4_命中仍然缓存(tmp_path):
    """修复不能把缓存收益一起丢掉：命中结果必须留在表里。"""
    from jikuai import frontend

    _写模块(tmp_path, '我的块', 非原子块源码)
    src = "从 我的块 导入 块求和。\n打印 块求和(列 1 2 3)。\n"
    caller = _写调用方(tmp_path, src)

    frontend._RESOLVE_CACHE.clear()
    compile_source(src, file=str(caller))

    assert len(frontend._RESOLVE_CACHE) == 1
    key = next(iter(frontend._RESOLVE_CACHE))
    assert key[0] == '我的块'
    assert frontend._RESOLVE_CACHE[key].endswith('我的块.jk')

