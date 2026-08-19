# -*- coding: utf-8 -*-
"""v0.27.0 W161：G23 规划器契约门禁（`scripts/check_planner_contract.py`）。

**这个文件的重点是反例**（执行原则 5 / v0.22.0 主教训「守卫绿≠守卫在守」）：门禁
在真仓库上退 0 什么都证明不了，得证明它**该红的时候真会红**。五类反例，两组：

录像侧（第 4 条回放，要真数据集，缺了就 skip）：

1. 篡改录像方案里的**块名** —— 幻觉块名过不了规则 2，判定从 `通过` 翻成 `拒答`
2. 删掉某步的 **`参数`** —— 规则 1 当场拒，同样与清单登记不符
3. 改清单的 **`期望判定`** —— 代码没动、登记动了，也必须红（登记是期望，不是快照）

规划器侧（第 1-3 条，纯静态 + 行为，秒级）：

4. 往 `planner.py` 里植入一个协议字段名的**裸字面量键** —— 断言 1 红
5. 摘掉 `validate_filled` 里的 `_规则3` 调用 —— 断言 2 红
6. 把 `validate_filled` 改成恒返回空列表（守卫还在、但不守了） —— 断言 3 红

第 4-6 类要**整棵桥目录的副本**：`planner.py` 会 `import glue`（同目录），只复制
单文件的话门禁会因「加载失败」而红——红得对但原因不对，那种反例证明不了断言本身。
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..'))
_AB = os.path.join(_REPO, 'tools', 'ai-bridge')
_录像目录 = os.path.join(_AB, '规划录像')
_数据集 = os.path.join(_REPO, '赛题', 'chatbi', '数据集')
_门禁路径 = os.path.join(_REPO, 'scripts', 'check_planner_contract.py')

sys.path.insert(0, os.path.join(_REPO, 'src'))


def _按路径加载(路径, 模块名):
    spec = importlib.util.spec_from_file_location(模块名, 路径)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[模块名] = mod
    spec.loader.exec_module(mod)
    return mod


_门禁 = _按路径加载(_门禁路径, '_t_check_planner_contract')


def _跑门禁(参数):
    """跑门禁，返回 (退出码, 全部输出)。它自己会动 sys.path，跑完还原。"""
    出, 错 = io.StringIO(), io.StringIO()
    原路径 = list(sys.path)
    原cwd = os.getcwd()
    try:
        with redirect_stdout(出), redirect_stderr(错):
            码 = _门禁.main(list(参数))
    finally:
        sys.path[:] = 原路径
        os.chdir(原cwd)
    return 码, 出.getvalue() + 错.getvalue()


def _抄录像(目标):
    shutil.copytree(_录像目录, 目标)
    return 目标


def _读(路径):
    with open(路径, 'r', encoding='utf-8') as f:
        return json.load(f)


def _写(路径, 数据):
    with open(路径, 'w', encoding='utf-8') as f:
        json.dump(数据, f, ensure_ascii=False, indent=2)


def _一条通过的录像(目录):
    """清单里第一条 `期望判定 == 通过` 的录像（id, 清单路径, 录像路径）。"""
    清单路径 = os.path.join(目录, '清单.json')
    清单 = _读(清单路径)
    for 条 in 清单['录像']:
        if 条['期望判定'] == '通过':
            return 条, 清单, 清单路径, os.path.join(目录, 条['文件'])
    raise AssertionError('清单里没有 期望判定=通过 的录像，反例无从造起')


class 真仓库上通过(unittest.TestCase):
    """基线。没有这条，反例红也可能只是门禁本身坏了。"""

    def test_静态与行为三条在真仓库上退0(self):
        码, 文 = _跑门禁(['--跳过回放'])
        self.assertEqual(码, 0, 文[-3000:])
        self.assertIn('跳过', 文)          # 跳过必须打出来，不许静默当通过

    @unittest.skipUnless(os.path.isdir(_数据集), '缺 赛题/chatbi/数据集，回放跳过')
    def test_四条全跑在真仓库上退0(self):
        码, 文 = _跑门禁([])
        self.assertEqual(码, 0, 文[-3000:])
        self.assertIn('录像回放全绿', 文)

    def test_缺件时显式跳过而不是假装通过(self):
        码, 文 = _跑门禁(['--录像目录', os.path.join(_REPO, '不存在的录像目录')])
        self.assertEqual(码, 0)
        self.assertIn('跳过第 4 条', 文)

    def test_规划器文件不存在退2(self):
        码, _ = _跑门禁(['--规划器', os.path.join(_REPO, '没有这个文件.py'),
                        '--跳过回放'])
        self.assertEqual(码, 2, '用法/环境问题要退 2，不能和「有违规」的 1 混')


@unittest.skipUnless(os.path.isdir(_数据集), '缺 赛题/chatbi/数据集，回放反例跳过')
class 录像反例(unittest.TestCase):
    """第 4 条：录像或清单被动过，门禁必须红。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.目录 = _抄录像(os.path.join(self._tmp.name, '规划录像'))

    def tearDown(self):
        self._tmp.cleanup()

    def _跑(self):
        return _跑门禁(['--录像目录', self.目录])

    def test_基线副本仍退0(self):
        """没动过的副本必须绿——否则后面三条红是「拷贝坏了」而不是「篡改被抓」。"""
        码, 文 = self._跑()
        self.assertEqual(码, 0, 文[-3000:])

    def test_篡改块名被抓(self):
        条, _清, _清路, 录像路径 = _一条通过的录像(self.目录)
        录像 = _读(录像路径)
        录像['方案']['步骤'][0]['块'] = '压根不存在的块'
        _写(录像路径, 录像)
        码, 文 = self._跑()
        self.assertEqual(码, 1, '幻觉块名没被规则 2 拦下')
        self.assertIn(条['id'], 文)

    def test_删掉某步参数被抓(self):
        条, _清, _清路, 录像路径 = _一条通过的录像(self.目录)
        录像 = _读(录像路径)
        for 步 in 录像['方案']['步骤']:
            步.pop('参数', None)
        _写(录像路径, 录像)
        码, 文 = self._跑()
        self.assertEqual(码, 1, '省掉实参没被规则 1 拦下——那正是 W145 静默错绑的入口')
        self.assertIn(条['id'], 文)

    def test_改清单期望判定被抓(self):
        条, 清单, 清单路径, _录 = _一条通过的录像(self.目录)
        for c in 清单['录像']:
            if c['id'] == 条['id']:
                c['期望判定'] = '拒答'
                c['期望拒因'] = ['随便一个不会出现的理由']
        _写(清单路径, 清单)
        码, 文 = self._跑()
        self.assertEqual(码, 1, '清单登记与实测不符竟然绿——那回放就只是「跑过了」')
        self.assertIn(条['id'], 文)


class 规划器反例(unittest.TestCase):
    """第 1-3 条：`planner.py` 被改坏，静态或行为断言必须红。

    每条都在 `tmp` 里复制整棵 `tools/ai-bridge`（约 1 MB / 97 文件），只跑 1-3 条。
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.桥 = os.path.join(self._tmp.name, 'ai-bridge')
        shutil.copytree(_AB, self.桥,
                        ignore=shutil.ignore_patterns('__pycache__'))
        self.规划器 = os.path.join(self.桥, 'planner.py')

    def tearDown(self):
        self._tmp.cleanup()

    def _改(self, 旧, 新):
        with open(self.规划器, 'r', encoding='utf-8') as f:
            源 = f.read()
        self.assertIn(旧, 源, '要替换的锚点不在 planner.py 里，反例已经过期')
        with open(self.规划器, 'w', encoding='utf-8') as f:
            f.write(源.replace(旧, 新, 1))

    def _跑(self):
        return _跑门禁(['--规划器', self.规划器, '--跳过回放'])

    def test_基线副本仍退0(self):
        码, 文 = self._跑()
        self.assertEqual(码, 0, 文[-3000:])

    def test_植入裸字面量键被抓(self):
        # `_R方案` → `'方案'`：一模一样的行为，但协议改名时它不会跟着改。
        self._改("方案 = 回填.get(_R方案) or {}", "方案 = 回填.get('方案') or {}")
        码, 文 = self._跑()
        self.assertEqual(码, 1)
        self.assertIn('当键用', 文)

    def test_摘掉规则3调用被抓(self):
        self._改("    理由.extend(_规则3(上下文包, 步骤表))\n", "")
        码, 文 = self._跑()
        self.assertEqual(码, 1)
        self.assertIn('_规则3', 文)

    def test_校验器恒放行被抓(self):
        """函数、调用点都在，只是先 return 了。静态断言看不见，行为断言必须看见。"""
        self._改("    if not isinstance(上下文包, dict):",
                 "    return []\n    if not isinstance(上下文包, dict):")
        码, 文 = self._跑()
        self.assertEqual(码, 1, '校验器恒返回空列表竟然绿——断言 3 白写了')
        self.assertIn('本该被拒却通过了', 文)

    def test_白名单退化成只比块名被抓(self):
        """规则 2 的三元键改成单键：块名对、领域/导出名张冠李戴就能混过去。"""
        self._改("        键 = (c.get(_F名称), c.get(_F候选领域), c.get(_F候选导出名))",
                 "        键 = (c.get(_F名称), c.get(_F名称), c.get(_F名称))")
        码, 文 = self._跑()
        self.assertEqual(码, 1)


class 串进主门禁(unittest.TestCase):

    def test_主门禁里有G23且不吞异常(self):
        路径 = os.path.join(_REPO, 'scripts', 'check_stdlib_contract.py')
        with open(路径, 'r', encoding='utf-8') as f:
            源 = f.read()
        self.assertIn('import check_planner_contract', 源)
        self.assertIn('check_planner_contract.main(["--quiet"])', 源)
        # G13+ 那套 `except → 跳过` 不许蔓延到新门禁：G23 的调用不能在 try 里
        段 = 源.split('import check_planner_contract', 1)[1][:400]
        self.assertNotIn('except', 段)


if __name__ == '__main__':
    unittest.main()
