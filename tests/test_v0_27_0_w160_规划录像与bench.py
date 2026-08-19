# -*- coding: utf-8 -*-
"""v0.27.0 W160：规划录像 + `bench_planner.py`。

分三块测：

1. **录像本体**（`tools/ai-bridge/规划录像/`）——形状、与评测集逐字对齐、清单与文件
   互为镜像。录像是回放的输入，输入烂了后面全白测。
2. **bench 的算法**——`评负例` / `_判定一致` 用手造的小输入验，不依赖真块库，
   这样指标算错能被直接抓到，而不是靠肉眼看报表。
3. **回放确定性**（ADR-41 §8）——`--只回放 --门禁` 必须退 0。这条是 W161 G23 的前身：
   实际判定与清单登记不符即红，**不是「全部通过」即绿**（15 份里有 4 份该被拒）。
"""

import importlib.util
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..'))
_AB = os.path.join(_REPO, 'tools', 'ai-bridge')
_录像目录 = os.path.join(_AB, '规划录像')

sys.path.insert(0, os.path.join(_REPO, 'src'))

from jikuai.service import schema  # noqa: E402


def _按路径加载(文件名, 模块名):
    spec = importlib.util.spec_from_file_location(模块名, os.path.join(_AB, 文件名))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[模块名] = mod
    spec.loader.exec_module(mod)
    return mod


_bench = _按路径加载('bench_planner.py', '_t_bench_planner')


def _读(路径):
    with open(路径, 'r', encoding='utf-8') as f:
        return json.load(f)


def _清单():
    return _读(os.path.join(_录像目录, '清单.json'))


def _评测集需求表():
    出 = {}
    for 名 in ('评测集-chatbi.json', '评测集-chatbi-留出.json'):
        for c in _读(os.path.join(_AB, 名))['用例']:
            出[c['id']] = c['需求']
    return 出


class 录像本体(unittest.TestCase):

    def test_清单与目录互为镜像(self):
        """清单登记的文件 == 目录里的录像文件。多一份少一份都算漏登记。"""
        登记 = {条['文件'] for 条 in _清单()['录像']}
        实际 = {n for n in os.listdir(_录像目录)
                if n.endswith('.json') and n != '清单.json'}
        self.assertEqual(登记, 实际)

    def test_每份录像都是合法回填响应(self):
        """键集严格：录像文件里**只能**有 需求/方案/模型/溯源?。

        id、录制时间这些元信息故意只存在清单里——多塞一个键，
        `validate_filled_envelope` 当场报错，回放第一步就过不去。
        """
        for 条 in _清单()['录像']:
            信封 = _读(os.path.join(_录像目录, 条['文件']))
            errs = schema.validate_filled_envelope(信封)
            self.assertEqual(errs, [], '%s: %s' % (条['id'], errs))
            self.assertTrue(信封['模型'].strip(), '%s 模型为空' % 条['id'])

    def test_录像需求与评测集逐字一致(self):
        """回放喂给 `build_context` 的问句必须与评测集同源，否则量的不是同一件事。"""
        需求表 = _评测集需求表()
        for 条 in _清单()['录像']:
            信封 = _读(os.path.join(_录像目录, 条['文件']))
            self.assertEqual(信封['需求'], 需求表[条['id']], 条['id'])
            self.assertEqual(条['需求'], 需求表[条['id']], 条['id'])

    def test_清单的块与步数与方案一致(self):
        """`白名单可承载率` 拿清单的 `块` 当分母，它跟方案脱节就直接算错。"""
        for 条 in _清单()['录像']:
            方案 = _读(os.path.join(_录像目录, 条['文件']))['方案']
            self.assertEqual(条['块'], sorted({s['块'] for s in 方案['步骤']}), 条['id'])
            self.assertEqual(条['步数'], len(方案['步骤']), 条['id'])

    def test_期望判定取值域与拒因非空(self):
        """拒答登记必须带关键词。空 `期望拒因` 等于「随便什么理由都算对」。"""
        拒 = 0
        for 条 in _清单()['录像']:
            self.assertIn(条['期望判定'], ('通过', '拒答'), 条['id'])
            if 条['期望判定'] == '拒答':
                拒 += 1
                self.assertTrue(条['期望拒因'], '%s 拒答却没登记拒因' % 条['id'])
                self.assertTrue((条.get('备注') or '').strip(),
                                '%s 拒答却没写为什么' % 条['id'])
            else:
                self.assertEqual(条['期望拒因'], [], 条['id'])
        self.assertGreater(拒, 0, '一条拒答都没登记：这份清单退化成「全部通过」了')


class bench算法(unittest.TestCase):
    """指标算法用手造输入验——不依赖真块库，算错能被直接抓住。"""

    class 假规划器:
        def __init__(self, 表):
            self.表 = 表

        def build_context(self, 需求, top=8):
            候选, 覆盖 = self.表[需求]
            return {
                '需求': 需求,
                '语义命中': [{'业务词': '产量'}] if 覆盖 else [],
                '候选': [{'名称': n} for n in 候选],
                '拒答建议': {'覆盖': 覆盖, '理由': 'x'},
            }

    def test_负例拒答率与兄弟诱骗率分开算(self):
        """拒答率分母是全档；兄弟块诱骗率分母**只算标了兄弟块的条数**。

        两个分母不同是刻意的（沿用 bench_chatbi.py 的 `有兄弟` 口径）：
        混成一个分母会把没标兄弟块的用例当成「没被诱骗」白送一分。
        """
        pl = self.假规划器({
            'a': (['甲'], False),
            'b': (['乙'], True),
            'c': (['丙'], True),
            'd': (['丁'], True),
        })
        r = _bench.评负例(pl, [
            {'需求': 'a'},                                  # 拒答、无兄弟块
            {'需求': 'b', '兄弟块': ['乙']},                 # 未拒、被诱骗
            {'需求': 'c', '兄弟块': ['戊']},                 # 未拒、未被诱骗
            {'需求': 'd'},                                   # 未拒、无兄弟块
        ])
        self.assertEqual(r['用例数'], 4)
        self.assertEqual(r['拒下条数'], 1)
        self.assertAlmostEqual(r['规划器层拒答率'], 0.25)
        self.assertEqual(r['带兄弟块用例数'], 2)
        self.assertAlmostEqual(r['兄弟块诱骗率'], 0.5)

    def test_无兄弟块的档不出诱骗率键(self):
        """远离档一条兄弟块都没标，报表里就不该出现这个指标（不是出 0）。"""
        pl = self.假规划器({'a': (['甲'], False)})
        r = _bench.评负例(pl, [{'需求': 'a'}])
        self.assertNotIn('兄弟块诱骗率', r)

    def test_上下文包覆盖与白名单可承载分开算(self):
        pl = self.假规划器({'q': (['甲', '乙'], True)})
        r = _bench.评上下文包(pl, [{'id': 'X', '需求': 'q', '期望': ['甲', '丙']}],
                            {'X': ['甲', '乙']}, top=8)
        self.assertAlmostEqual(r['期望块覆盖率'], 0.5)      # 甲中、丙缺
        self.assertAlmostEqual(r['期望块完整命中率'], 0.0)
        self.assertAlmostEqual(r['白名单可承载率'], 1.0)    # 方案的甲乙都在候选里
        self.assertEqual(r['有录像用例数'], 1)

    def test_没录像的用例不进白名单分母(self):
        pl = self.假规划器({'q': (['甲'], True)})
        r = _bench.评上下文包(pl, [{'id': 'X', '需求': 'q', '期望': ['甲']}], {}, top=8)
        self.assertIsNone(r['白名单可承载率'])
        self.assertEqual(r['有录像用例数'], 0)


class 判定一致(unittest.TestCase):
    """`_判定一致` 是回放绿的唯一判据，它自己必须先被反例证明抓得住。"""

    def test_判定不同即不一致(self):
        self.assertFalse(_bench._判定一致(
            {'判定': '通过', '拒因': [], '期望判定': '拒答', '期望拒因': ['甲']}))
        self.assertFalse(_bench._判定一致(
            {'判定': '拒答', '拒因': ['甲不行'], '期望判定': '通过', '期望拒因': []}))

    def test_拒因少一个关键词就不一致(self):
        """拒对了但拒的**不是登记的那件事**，照样算不一致——这是本函数的要点。"""
        self.assertFalse(_bench._判定一致(
            {'判定': '拒答', '拒因': ['甲不行'],
             '期望判定': '拒答', '期望拒因': ['甲', '乙']}))
        self.assertTrue(_bench._判定一致(
            {'判定': '拒答', '拒因': ['甲不行', '乙也不行'],
             '期望判定': '拒答', '期望拒因': ['甲', '乙']}))

    def test_通过时不看拒因(self):
        self.assertTrue(_bench._判定一致(
            {'判定': '通过', '拒因': [], '期望判定': '通过', '期望拒因': []}))


class 导入无副作用(unittest.TestCase):
    """bench 是被 pytest 导入的模块，导入期不许动坏全局状态。

    真踩到过：照抄 `bench_chatbi.py` 的「把脚本目录从 sys.path 删掉」，同一轮里
    `tests/test_glue_type.py` 的 `import bench_glue` 当场 ModuleNotFoundError。
    """

    def test_导入后tools_ai_bridge仍可导入(self):
        """把 `tools/ai-bridge` 放进 sys.path 再重新加载本模块，它必须还在。

        不直接断言「当前 sys.path 含 _AB」——那取决于 conftest 有没有加过，
        单跑本文件时会假红。这里自己造出前置条件，量的才是模块的行为。
        """
        原 = list(sys.path)
        try:
            if _AB not in sys.path:
                sys.path.insert(0, _AB)
            _按路径加载('bench_planner.py', '_t_bench_planner_重载')
            路 = [os.path.abspath(p) for p in sys.path]
            self.assertIn(_AB, 路,
                          'bench_planner 导入期把 tools/ai-bridge 从 sys.path 抹了')
            self.assertEqual(路[-1], _AB, '脚本目录必须挪到末尾，不能压在标准库前面')
        finally:
            sys.path[:] = 原
            sys.modules.pop('_t_bench_planner_重载', None)

    def test_脚本目录不遮蔽标准库select(self):
        """`tools/ai-bridge/select.py` 不能盖住标准库 select。"""
        import select
        self.assertNotEqual(
            os.path.abspath(os.path.dirname(select.__file__ or '')), _AB)


class 回放确定性(unittest.TestCase):
    """ADR-41 §8：CI 只回放，零网络零 API key。这一条真跑全档。"""

    def test_只回放门禁退0(self):
        缓 = io.StringIO()
        旧 = os.getcwd()
        with redirect_stdout(缓):
            码 = _bench.run(['--只回放', '--门禁'])
        self.assertEqual(码, 0, 缓.getvalue()[-2000:])
        self.assertEqual(os.getcwd(), 旧, 'run() 改了工作目录没还原')
        文 = 缓.getvalue()
        self.assertIn('判定一致率 100.0%', 文)

    def test_json模式可解析且不夹带明细(self):
        缓 = io.StringIO()
        with redirect_stdout(缓):
            码 = _bench.run(['--只回放', '--json'])
        self.assertEqual(码, 0)
        报 = json.loads(缓.getvalue())
        self.assertEqual(报['录像数'], len(_清单()['录像']))
        self.assertNotIn('明细', 报['回放'])
        self.assertAlmostEqual(报['回放']['判定一致率'], 1.0)
        # 15 份里 11 通过 4 拒答；通过的必须全跑通，否则「回放能跑」这句是空的
        self.assertEqual(报['回放']['通过数'], 报['回放']['通过并跑通数'])
        self.assertEqual(报['回放']['组失败数'], 0)
        self.assertGreater(报['回放']['拒答数'], 0)


if __name__ == '__main__':
    unittest.main()
