# -*- coding: utf-8 -*-
"""v0.14.0 · W3-W4 · 类型对齐粘合器（`tools/ai-bridge/glue.py`）测试。

覆盖两层：

1. `type_feeds` 的类型对齐边界（8 组）——标量 / 列表协变 / 元组不喂列表 /
   元组同元数 / 任意双向 / 联合 / 字典键值 / 嵌套。
2. `TypeGraph.plan` 端到端：能链的链上、不能链的**给出可读拒绝理由**
   （ADR-26 子类型规则 + WBS「拒绝理由不静默」硬门槛）。

`tools/ai-bridge/` 不是包，按仓库既有做法用 sys.path 插入后 import。
"""

import os
import sys
import unittest

_BRIDGE = os.path.join(os.path.dirname(__file__), '..', 'tools', 'ai-bridge')
if _BRIDGE not in sys.path:
    sys.path.insert(0, os.path.abspath(_BRIDGE))

import glue  # noqa: E402

N = glue.normalize_type
喂 = glue.type_feeds


class TypeFeedsTest(unittest.TestCase):
    """8 组类型对齐边界。"""

    def test_1_标量同型可喂_异型拒(self):
        self.assertTrue(喂(N('数'), N('数')))
        self.assertFalse(喂(N('数'), N('字符串')))
        self.assertFalse(喂(N('布尔'), N('数')))

    def test_2_列表元素协变(self):
        列数 = {'类型': '列表', '元素类型': '数'}
        列串 = {'类型': '列表', '元素类型': '字符串'}
        self.assertTrue(喂(N(列数), N(列数)))
        self.assertFalse(喂(N(列数), N(列串)))

    def test_3_元组不自动喂列表(self):
        """固定形状返回值必须人工拆包——即使元素同质也不放行。"""
        元组四数 = {'类型': '元组', '元数': ['数', '数', '数', '数']}
        列数 = {'类型': '列表', '元素类型': '数'}
        self.assertFalse(喂(N(元组四数), N(列数)))

    def test_4_元组同元数才可喂(self):
        a = {'类型': '元组', '元数': ['数', '字符串']}
        b = {'类型': '元组', '元数': ['数', '字符串']}
        c = {'类型': '元组', '元数': ['数', '字符串', '布尔']}
        d = {'类型': '元组', '元数': ['字符串', '字符串']}
        self.assertTrue(喂(N(a), N(b)))
        self.assertFalse(喂(N(a), N(c)))       # 元数长度不同
        self.assertFalse(喂(N(a), N(d)))       # 逐位类型不符

    def test_5_任意双向放行(self):
        """`任意` 是顶：形参收任意放行；实参是动态值（共享常量）也放行。"""
        self.assertTrue(喂(N('数'), N('任意')))
        self.assertTrue(喂(N('任意'), N('数')))

    def test_6_联合语义(self):
        列任 = {'类型': '列表', '元素类型': '任意'}
        典任 = {'类型': '字典', '键类型': '字符串', '值类型': '任意'}
        源联合 = {'类型': '联合', '候选': [典任, 列任]}
        # 目标是联合：实参能喂任一候选即可
        self.assertTrue(喂(N(列任), N(源联合)))
        # 源是联合：每个候选都要能喂目标，否则拒
        self.assertFalse(喂(N(源联合), N(列任)))
        self.assertTrue(喂(N(源联合), N('任意')))

    def test_7_字典键值都要对齐(self):
        典串数 = {'类型': '字典', '键类型': '字符串', '值类型': '数'}
        典串串 = {'类型': '字典', '键类型': '字符串', '值类型': '字符串'}
        典数数 = {'类型': '字典', '键类型': '数', '值类型': '数'}
        self.assertTrue(喂(N(典串数), N(典串数)))
        self.assertFalse(喂(N(典串数), N(典串串)))   # 值类型不符
        self.assertFalse(喂(N(典串数), N(典数数)))   # 键类型不符

    def test_8_嵌套与跨种类(self):
        列列数 = {'类型': '列表',
                '元素类型': {'类型': '列表', '元素类型': '数'}}
        列数 = {'类型': '列表', '元素类型': '数'}
        self.assertTrue(喂(N(列列数), N(列列数)))
        self.assertFalse(喂(N(列列数), N(列数)))     # 元素层级不同
        self.assertFalse(喂(N(列数), N('数')))       # 列表不喂标量
        self.assertFalse(喂(N('数'), N(列数)))       # 标量不喂列表


class TypeGraphPlanTest(unittest.TestCase):
    """端到端：内置块库上的自动链式推断与拒绝理由。"""

    @classmethod
    def setUpClass(cls):
        cls.图 = glue.TypeGraph()

    def test_升序结果可喂求和(self):
        """升序 出 列表<数> → 求和 入 列表<数>，应自动链上 赵果1。"""
        steps = [{'块': '升序', '领域': '数据', '导出名': '顺排'},
                 {'块': '求和', '领域': '数据', '导出名': '汇总'}]
        实参, 未匹配, 拒绝 = self.图.plan(
            steps, [{'名': '赵料', '值': '列 3 1 2'}])
        self.assertEqual(实参[0], ['赵料'])
        self.assertEqual(实参[1], ['赵果1'])
        self.assertEqual(未匹配, [])
        self.assertEqual(拒绝, [])

    def test_批量统计结果不可喂升序_且理由可读(self):
        """WBS 指定的硬用例：元组四数 不该被硬塞给要 列表<数> 的升序。"""
        steps = [{'块': '批量统计', '领域': '数据', '导出名': '统览',
                  '参数': ['赵料']},
                 {'块': '升序', '领域': '数据', '导出名': '顺排'}]
        实参, 未匹配, 拒绝 = self.图.plan(steps)
        self.assertIsNone(实参[1])
        self.assertEqual(len(未匹配), 1)
        self.assertEqual(len(拒绝), 1)
        理由 = 拒绝[0]
        self.assertIn('元组[数,数,数,数]', 理由)
        self.assertIn('列表<数>', 理由)
        self.assertIn('人工拆包', 理由)

    def test_合成时把拒绝理由写进注释(self):
        # 不给 升序 任何共享常量兜底，逼它只能尝试从 批量统计 的元组产出取，
        # 从而触发「元组需人工拆包」的拒绝理由并写进注释。
        steps = [{'块': '批量统计', '领域': '数据', '导出名': '统览',
                  '参数': ['赵料']},
                 {'块': '升序', '领域': '数据', '导出名': '顺排'}]
        方案 = {'需求': '统计后排序', '步骤': steps}
        源码 = glue.synthesize(方案, 自动链式=True)
        self.assertIn('人工拆包', 源码)
        self.assertIn('从 blocks.数据.批量统计 导入 统览。', 源码)

    def test_共享常量任意可兜底填槽(self):
        """共享常量类型未知（任意），可喂任何形参——不产生拒绝。"""
        steps = [{'块': '批量统计', '领域': '数据', '导出名': '统览',
                  '参数': ['赵料']},
                 {'块': '升序', '领域': '数据', '导出名': '顺排'}]
        实参, 未匹配, 拒绝 = self.图.plan(
            steps, [{'名': '赵料', '值': '列 3 1 2'}])
        self.assertEqual(实参[1], ['赵料'])       # 任意 兜底
        self.assertEqual(拒绝, [])                # 有兜底则不报拒绝

    def test_不开自动链式时行为不变(self):
        """v0 模板合成路径必须原样保留（缺参数 → `?` 占位 + 注释）。"""
        方案 = {'步骤': [{'块': '求和', '领域': '数据', '导出名': '汇总'}]}
        源码 = glue.synthesize(方案)
        self.assertIn('需人工填参', 源码)
        self.assertIn('定义赵果1=汇总(?)。', 源码)


class BenchThresholdTest(unittest.TestCase):
    """把 `bench_glue.py` 的门槛钉进 pytest：命中率 ≥60%、语义荒谬率 =0。"""

    def test_评测集门槛(self):
        import bench_glue
        报告 = bench_glue.跑全量()
        失败 = [r for r in 报告['明细'] if not r['通过']]
        self.assertGreaterEqual(报告['用例数'], 30)
        self.assertGreaterEqual(报告['自动链式命中率'], 0.60,
                                '自动链式命中率不足；未通过：%s' % 失败)
        self.assertEqual(报告['语义荒谬率'], 0.0,
                         '出现语义荒谬（应拒被硬链）：%s' % 失败)
        self.assertEqual(报告['拒绝质量'], 1.0,
                         '有应拒用例没给出可读理由：%s' % 失败)


class 命名空间导入行Test(unittest.TestCase):
    """W69：第三方块的导入行必须带命名空间段，内置块必须一个字节都不变。

    这是 v0.19.0 三根系统闭合的最后一环——发现/执行/检索三侧都看得见第三方块了，
    但 glue 合成出来的导入路径少一段，块作者自测不出来、使用方运行时才炸。
    """

    def test_第三方块_导入行插命名空间段(self):
        步骤 = [{'块': '试倍', '领域': '数据', '导出名': '翻倍数',
                '命名空间': '钉板包'}]
        self.assertEqual(glue._导入行(步骤),
                         ['从 blocks.钉板包.数据.试倍 导入 翻倍数。'])

    def test_内置块_导入行仍是两段(self):
        步骤 = [{'块': '求和', '领域': '数据', '导出名': '求和'}]
        self.assertEqual(glue._导入行(步骤), ['从 blocks.数据.求和 导入 求和。'])
        # 显式空串与缺键同义——检索侧内置块给的就是空串
        步骤2 = [dict(步骤[0], **{'命名空间': ''})]
        self.assertEqual(glue._导入行(步骤2), glue._导入行(步骤))

    def test_跨命名空间同名块_两条导入行都留下(self):
        """`scan_blocks` 明确允许跨命名空间同名；去重键漏掉命名空间会静默吞掉一条。"""
        步骤 = [
            {'块': '求和', '领域': '数据', '导出名': '求和'},
            {'块': '求和', '领域': '数据', '导出名': '求和', '命名空间': '甲包'},
        ]
        self.assertEqual(glue._导入行(步骤), [
            '从 blocks.数据.求和 导入 求和。',
            '从 blocks.甲包.数据.求和 导入 求和。',
        ])


if __name__ == '__main__':
    unittest.main()
