# -*- coding: utf-8 -*-
"""v0.29.0 W182/W183 · `jk 块 问` 真端点真跑台架与归档件测试。

台架本体（`tools/ai-bridge/真跑_问.py`）**刻意不在这里真跑一遍**：它要起两个子进程、
占端口、跑真数据集，放进回归就是把「端口/时序抖动」混进回归信号（v0.28.0 那条
`WinError 10053` 抖动是先例）。所以这里测的是：

1. **台架的判据没被悄悄放宽**（用例表形状、期望判定、正反例都在）；
2. **归档件真的是绿的、且逐条对得上**（`真跑记录/清单.json` + 每份记录）；
3. **诚实性守卫**——归档件与台架文档必须仍然写着「这不证明真实模型能回填对」，
   且这批记录**没混进 `规划录像/`**（混进去会换掉 `白名单可承载率` 的分母）。

第 3 条是本文件最要紧的部分：真跑绿了以后，最容易发生的事就是有人把
`docs/BACKLOG.md` §12.4 那条挂账整条划掉，当成「模型端点已验证」。
"""

import importlib.util
import json
import os
import sys
import unittest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_桥 = os.path.join(_REPO, 'tools', 'ai-bridge')
_SRC = os.path.join(_REPO, 'src')
for _p in (_SRC, _桥):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _载入(名, 相对):
    路径 = os.path.join(_桥, 相对)
    spec = importlib.util.spec_from_file_location(名, 路径)
    模块 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(模块)
    return 模块


台架 = _载入('真跑_问_test', '真跑_问.py')
端点模块 = _载入('参考回填端点_for_w182', '参考回填端点.py')

记录目录 = os.path.join(_桥, '真跑记录')
录像目录 = os.path.join(_桥, '规划录像')


def _清单():
    with open(os.path.join(记录目录, '清单.json'), encoding='utf-8') as f:
        return json.load(f)


class 台架用例表(unittest.TestCase):
    """判据形状：正反例都得有，`期望判定` 一条不许缺。"""

    def test_每条用例都有期望判定(self):
        for 用例 in 台架.用例表:
            self.assertIn(用例.get('期望判定'), ('通过', '拒'), 用例.get('名'))

    def test_正反例都在(self):
        判定集 = [u['期望判定'] for u in 台架.用例表]
        self.assertGreaterEqual(判定集.count('通过'), 2, '至少两条正例')
        self.assertGreaterEqual(判定集.count('拒'), 3, '至少三条反例')

    def test_三条关键拒绝路径都被覆盖(self):
        """幻觉块名 / 鉴权不匹配 / 库外拒答——少一条这台架就白跑。"""
        名集 = {u['名'] for u in 台架.用例表}
        for 名 in ('幻觉反例', '鉴权不匹配反例', '库外问句反例'):
            self.assertIn(名, 名集)

    def test_端点侧令牌必须纯ascii(self):
        """首跑就栽在这：用例里写了中文令牌，端点按自己的闸拒绝启动。

        报出来的是「端点进程启动就退了」，看着像台架坏了。用例表里的令牌一律 ASCII。
        """
        for 用例 in 台架.用例表:
            令牌 = 用例.get('令牌') or ''
            self.assertTrue(令牌.isascii(), 用例['名'])

    def test_客户端引导不含执行入口(self):
        """台架起的客户端只调 `blocks_cli.run`，不自己 `run_source` 执行极快代码。"""
        self.assertIn('blocks_cli.run', 台架._客户端引导)
        self.assertNotIn('run_source', 台架._客户端引导)

    def test_子进程环境显式给两个UTF8变量(self):
        """Windows 子进程默认按 GBK 写 stdout，父进程按 UTF-8 解就成片炸。"""
        环境 = 台架._子进程环境()
        self.assertEqual(环境.get('PYTHONUTF8'), '1')
        self.assertEqual(环境.get('PYTHONIOENCODING'), 'utf-8')
        self.assertTrue(环境.get('PYTHONPATH', '').startswith(_SRC))

    def test_子进程环境默认不带令牌(self):
        """不显式给令牌时要把它从环境里摘掉，否则「未启用鉴权」那档测不准。"""
        原值 = os.environ.get(台架.ENV令牌)
        os.environ[台架.ENV令牌] = 'leaked-token'
        try:
            self.assertNotIn(台架.ENV令牌, 台架._子进程环境())
            self.assertEqual(台架._子进程环境('给了')[台架.ENV令牌], '给了')
        finally:
            os.environ.pop(台架.ENV令牌, None)
            if 原值 is not None:
                os.environ[台架.ENV令牌] = 原值


class 归档件(unittest.TestCase):
    """W183：归档件进版本库、可复核。"""

    def test_清单与用例表逐条对齐(self):
        清单 = _清单()
        self.assertEqual(清单['总数'], len(台架.用例表))
        登记 = {条['用例'] for 条 in 清单['记录']}
        self.assertEqual(登记, {u['名'] for u in 台架.用例表})

    def test_归档件全绿(self):
        清单 = _清单()
        self.assertEqual(清单['一致数'], 清单['总数'],
                         '归档件里有不一致的用例，别当它绿了')

    def test_每份记录文件都在且判定与清单一致(self):
        for 条 in _清单()['记录']:
            路径 = os.path.join(记录目录, 条['文件'])
            self.assertTrue(os.path.isfile(路径), 路径)
            with open(路径, encoding='utf-8') as f:
                记录 = json.load(f)
            self.assertEqual(记录['判定'], 条['判定'], 条['用例'])
            self.assertEqual(记录['期望判定'], 条['期望判定'], 条['用例'])
            self.assertTrue(记录['一致'], 条['用例'])

    def test_录像正例真跑到了真数据(self):
        """不是「链路通」而已：这条要能在真 CSV 上出真数字，否则等于只测了管道。"""
        with open(os.path.join(记录目录, '录像正例.json'), encoding='utf-8') as f:
            记录 = json.load(f)
        self.assertEqual(记录['退出码'], 0)
        self.assertIn('M003', 记录['执行stdout'])
        self.assertIn('产量', 记录['执行stdout'])
        self.assertEqual(记录['信封问题'], '（合法跑响应）')

    def test_幻觉反例真被校验器拦下(self):
        """跨进程下规则 2 仍然拦得住——此前只在同进程 mock 上证过。"""
        with open(os.path.join(记录目录, '幻觉反例.json'), encoding='utf-8') as f:
            记录 = json.load(f)
        self.assertEqual(记录['退出码'], 1)
        self.assertIn('校验拒绝', 记录['stderr'])
        self.assertIn('白名单', 记录['stderr'])

    def test_库外问句反例根本没到端点(self):
        with open(os.path.join(记录目录, '库外问句反例.json'), encoding='utf-8') as f:
            记录 = json.load(f)
        self.assertIn('拒答', 记录['stderr'])


class 诚实性守卫(unittest.TestCase):
    """真跑绿了之后最容易发生的事，是把「契约端点」读成「模型已验证」。"""

    def test_清单说明写明不证明真实模型(self):
        说明 = _清单()['说明']
        self.assertIn('不证明', 说明)
        self.assertIn('规划录像', 说明)

    def test_台架文档写明不证明真实模型且仍挂账(self):
        with open(os.path.join(_桥, '真跑_问.py'), encoding='utf-8') as f:
            正文 = f.read()
        for 片段 in ('不证明', '挂账', 'BACKLOG'):
            self.assertIn(片段, 正文)

    def test_回填来源标识永远带参考端点前缀(self):
        """端点不许把自己的产出标成某个模型名——溯源一旦被洗掉就查不回来了。"""
        for 模式 in 端点模块.模式表:
            self.assertTrue(端点模块._模型标识(模式).startswith('参考端点·'))
        self.assertIn('录像转投', 端点模块._模型标识('录像', '人工·某版'))
        self.assertIn('人工·某版', 端点模块._模型标识('录像', '人工·某版'))

    def test_真跑记录没混进规划录像(self):
        """混进去会换掉 `白名单可承载率` 的分母，让跨轮次数字没法比（W175 的教训）。"""
        with open(os.path.join(录像目录, '清单.json'), encoding='utf-8') as f:
            录像清单 = json.load(f)
        self.assertEqual(len(录像清单['录像']), 18, '录像份数变了就得重看承载率分母')
        录像文件 = {条['文件'] for 条 in 录像清单['录像']}
        for 条 in _清单()['记录']:
            self.assertNotIn(条['文件'], 录像文件)

    def test_台架不进CI(self):
        """记档的是「真跑只在本机」（ADR-41 §8）。这条守它没被悄悄串进门禁/CI。"""
        待查 = [os.path.join(_REPO, 'scripts', 'check_stdlib_contract.py'),
                os.path.join(_REPO, '.gitea', 'workflows', 'ci.yml'),
                os.path.join(_REPO, '.gitea', 'workflows', 'release.yml')]
        for 路径 in 待查:
            if not os.path.isfile(路径):
                continue
            with open(路径, encoding='utf-8') as f:
                self.assertNotIn('真跑_问', f.read(), 路径)


if __name__ == '__main__':
    unittest.main()
