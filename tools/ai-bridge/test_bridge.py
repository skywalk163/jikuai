# -*- coding: utf-8 -*-
"""AI 桥接 v0 的测试。

分三层：
  1. 选块器（`select.py`）——索引读取、打分排序、导出名解析
  2. 粘合器（`glue.py`）——生成的文本形状 + **生成的代码能被极快解析**
  3. 三个 demo 的端到端断言——生成代码真的跑出预期输出

跑法::

    python -m pytest tools/ai-bridge/test_bridge.py -q

注意本目录的 `conftest.py`：它负责在收集用例前把标准库 `select` 钉进
`sys.modules`，否则本目录的同名 `select.py` 会遮蔽它。
"""

import importlib.util
import io
import os
import sys

import pytest

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
_DEMOS = os.path.join(_HERE, 'demos')

for _p in (_SRC, _DEMOS):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _按路径载入(模块名, 文件名):
    """按绝对路径载入桥接模块，绕开 `select` 与标准库的名字冲突。"""
    spec = importlib.util.spec_from_file_location(
        模块名, os.path.join(_HERE, 文件名))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[模块名] = mod
    spec.loader.exec_module(mod)
    return mod


块选择器 = _按路径载入('块选择器', 'select.py')
粘合器 = _按路径载入('块粘合器', 'glue.py')

from jikuai.lexer import tokenize                          # noqa: E402
from jikuai.parser import parse                            # noqa: E402
from jikuai.main import run_source                         # noqa: E402

import demo1_数值统计                                       # noqa: E402
import demo2_文本清洗                                       # noqa: E402
import demo3_中文报表                                       # noqa: E402

#: 索引里应有的块数。随块库扩容会变化，不再硬编码——从索引文件读取。
#: 仅断言「不为空且不减少」（防意外批量丢块），不卡精确数量。
_MIN_块数 = 52


@pytest.fixture(autouse=True)
def _同源极快模块():
    """跨测试隔离：把 `run_source`/`tokenize`/`parse` 每个测试前从当前
    `sys.modules` 重新绑定，保证与 `ModuleLoader` 运行时 import 的
    `jikuai.evaluator` **同源**。

    主套件里 `test_v0_7_0_aot.py::TestIsolation` 会
    `del sys.modules['jikuai.*']` 后 `importlib.reload(jikuai)`，留下新旧
    两份 `jikuai.evaluator` 模块。本文件在 collection 期绑定的 `run_source`
    属于**旧**模块，而块加载走 `ModuleLoader.load()` 里运行时
    `from .evaluator import JiKuaiError, Environment` 拿到的是**新**模块——
    于是 `批量统计.jk` 的 `统览` 里 `定义赵总=…` 触发的
    `Environment.update`（新类）抛出**新** `JiKuaiError`，而
    `_eval_Define`（旧代码）的 `except JiKuaiError`（旧类）抓不到这个兄弟类
    的异常，本该「新建变量」的分支反而炸成 `未定义的标识符，无法赋值：赵总`。

    重新 import 让 `run_source`（及其内部新建的 `Evaluator`）与
    `ModuleLoader` 拿到同一个 evaluator 模块，类身份一致，异常照常被捕获。
    单独跑本文件时 `sys.modules` 未被污染，重新 import 命中缓存、等价于空操作。
    """
    global run_source, tokenize, parse
    import importlib
    run_source = importlib.import_module('jikuai.main').run_source
    tokenize = importlib.import_module('jikuai.lexer').tokenize
    parse = importlib.import_module('jikuai.parser').parse
    yield


@pytest.fixture(scope='module')
def 索引():
    return 块选择器.load_index()


# ---------------------------------------------------------------------------
# 1. 选块器
# ---------------------------------------------------------------------------

def test_索引读到全部块(索引):
    """load_index 能读到 stdlib/blocks/索引.json 的全部块。

    v0.13.0 M2 起块库持续扩容，不再断言精确数量（那会让每次加块都改测试）；
    只卡「不少于 v0.12.0 的 52 块」防意外批量丢块。
    """
    assert len(索引['块']) >= _MIN_块数


def test_每个块都能解析出导出名(索引):
    """52 个块全部能从 .jk 里抽出导出名——粘合代码的前置条件。

    索引本身不含 `导出` 字段（见 select.py 模块 docstring 的方案 A 说明），
    导出名靠 `extract_exports` 从 `.jk` 现读。这条测试守住那条通路。
    """
    缺失 = [b['名称'] for b in 索引['块']
            if 块选择器.resolve_export(b) is None]
    assert 缺失 == [], '这些块解析不出导出名：%s' % 缺失


def test_求和类需求命中求和块(索引):
    """「求和」类需求的 top-3 候选里必须有 `求和` 块，导出名为 `汇总`。"""
    候选 = 块选择器.select_blocks('对一组数字求和', 索引, top=3)
    命中 = [c for c in 候选 if c['名称'] == '求和']
    assert 命中, 'top-3 里没有 求和 块：%s' % [c['名称'] for c in 候选]
    assert 命中[0]['导出名'] == '汇总'
    assert 命中[0]['领域'] == '数据'


def test_农历需求返回中文领域块(索引):
    """「农历」需求的 top-3 全部落在 `中文` 领域，且 `农历` 块排第一。

    用「把公历日期换成农历」这种**信息密度高**的需求；若换成口语化的
    「查一下今天的农历日期」，`今/日` 会误命中 工具 领域块的描述——这正是
    v0 字符匹配的已知短板，见 README「现实检查」。"""
    候选 = 块选择器.select_blocks('把公历日期换成农历', 索引, top=3)
    assert 候选[0]['名称'] == '农历'
    assert 候选[0]['导出名'] == '阴历'
    assert {c['领域'] for c in 候选} == {'中文'}


def test_候选按分数降序且完全无关的块被丢掉(索引):
    """排序确定性 + 过滤：分数必须单调不增，且候选数小于块总数。"""
    候选 = 块选择器.select_blocks('把金额转成中文大写', 索引)
    分数 = [c['分数'] for c in 候选]
    assert 分数 == sorted(分数, reverse=True)
    assert 0 < len(候选) < len(索引['块'])


# ---------------------------------------------------------------------------
# 2. 粘合器
# ---------------------------------------------------------------------------

def test_合成含正确的导入行():
    """ADR-15 §3.7：导入用**目录名**，调用用**导出名**，两者不能混。"""
    源码 = 粘合器.synthesize({
        '需求': '求和',
        '共享': [{'名': '赵料', '值': '列 1 2 3 4 5'}],
        '步骤': [{'块': '求和', '领域': '数据', '导出名': '汇总',
                  '参数': ['赵料']}],
    })
    assert '从 blocks.数据.求和 导入 汇总。' in 源码
    assert '定义赵果1=汇总(赵料)。' in 源码
    assert '打印 赵果1。' in 源码


def test_合成的代码语法合法():
    """所有 demo 方案合成出的代码都必须能被极快 lexer + parser 吃下去。

    这条比「跑通」更早一步：语法错会在 parse 阶段就暴露，不必等到求值。
    """
    方案们 = (demo1_数值统计.方案表()
              + demo2_文本清洗.方案表()
              + demo3_中文报表.方案表())
    for 标签, 方案 in 方案们:
        源码 = 粘合器.synthesize(方案)
        ast = parse(tokenize(源码))
        assert ast is not None, '%s 的合成代码解析失败' % 标签


def test_缺参数时生成占位符与提示注释():
    """诚实降级：推不出参数就写 `?` 并注释「需人工填参」，不假装能跑。"""
    源码 = 粘合器.synthesize({
        '步骤': [{'块': '求和', '领域': '数据', '导出名': '汇总'}],
    })
    assert '需人工填参' in 源码
    assert '定义赵果1=汇总(?)。' in 源码


def test_重复步骤的导入只生成一次():
    """同一个块被用两次（demo3 的形态），导入行去重，调用行不去重。"""
    源码 = 粘合器.synthesize({
        '步骤': [
            {'块': '金额雅写', '领域': '中文', '导出名': '银码', '参数': ['1']},
            {'块': '金额雅写', '领域': '中文', '导出名': '银码', '参数': ['2']},
        ],
    })
    assert 源码.count('从 blocks.中文.金额雅写 导入 银码。') == 1
    assert 源码.count('=银码(') == 2


def test_非法方案被拒():
    """空步骤、缺必填字段都必须抛 ValueError，不能静默产出坏代码。"""
    with pytest.raises(ValueError):
        粘合器.synthesize({'步骤': []})
    with pytest.raises(ValueError):
        粘合器.synthesize({'步骤': [{'块': '求和', '领域': '数据'}]})
    with pytest.raises(ValueError):
        粘合器.synthesize(['不是对象'])


# ---------------------------------------------------------------------------
# 3. 三个 demo 的端到端断言
# ---------------------------------------------------------------------------

def _跑方案(方案):
    """合成 → 执行 → 返回 stdout 文本。"""
    源码 = 粘合器.synthesize(方案)
    old = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        run_source(源码)
    finally:
        sys.stdout = old
    return buf.getvalue()


def test_demo1_端到端():
    """数值统计：求和=1000、均值=200.0；一级块给出四元 [1000, 200.0, 100, 300]。"""
    输出A = _跑方案(demo1_数值统计.方案A())
    assert 输出A.splitlines() == ['1000', '200.0']

    输出B = _跑方案(demo1_数值统计.方案B())
    assert 输出B.strip() == '[1000, 200.0, 100, 300]'


def test_demo2_端到端():
    """文本清洗：去重 + 升序后，四步流水线与一级块结果必须完全一致。"""
    输出A = _跑方案(demo2_文本清洗.方案A())
    输出B = _跑方案(demo2_文本清洗.方案B())
    assert 输出A.strip() == '梨,橙子,苹果,香蕉'
    assert 输出A.strip() == 输出B.strip(), '原子块流水线与一级块结果不一致'


def test_demo3_端到端():
    """中文报表：大写金额的角分与「整」字都要对。"""
    输出A = _跑方案(demo3_中文报表.方案A()).splitlines()
    assert 输出A == ['壹仟贰佰叁拾肆元伍角陆分', '捌万捌仟捌佰捌拾捌元整']

    输出B = _跑方案(demo3_中文报表.方案B())
    assert '壹仟贰佰叁拾肆元伍角陆分' in 输出B
    assert '一千二百三十四' in 输出B          # 汉字数字截断小数，符合底层语义


def test_压缩比在合理区间():
    """压缩比必须落在 (0, 1)——粘合代码比等价 Python 短，但不为零。

    刻意不断言具体数值：等价 Python 是手写的对照物，改动它会让硬编码的
    比率失效。这条只守「方向正确」这个不变量。
    """
    for demo in (demo1_数值统计, demo2_文本清洗, demo3_中文报表):
        for 标签, 方案 in demo.方案表():
            源码 = 粘合器.synthesize(方案)
            比 = len(源码.strip()) / len(demo.等价Python.strip())
            assert 0 < 比 < 1, '%s 的压缩比 %.3f 不在 (0,1)' % (标签, 比)
