# -*- coding: utf-8 -*-
"""v0.29.0 W181 · 参考回填端点（`tools/ai-bridge/参考回填端点.py`）测试。

它是 `jk 块 问 --模型` 的**对拍端点**：独立进程收上下文包、回裸回填 JSON。
v0.28.0 之前这条链路只在**同进程** fixture 上验过（W158），本文件补的是
「模块逻辑 + 真 socket 往返 + 真 Bearer 头」这一层。端到端（真起子进程 + 真跑
`jk 块 问`）在 W182 的 `真跑_问.py` 里，那部分不进 CI（ADR-41 §8：真调只在本机）。

`tools/ai-bridge/` 不是包，按仓库既有约定用 importlib 按绝对路径加载。
"""

import importlib.util
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager

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


端点模块 = _载入('参考回填端点_test', '参考回填端点.py')
planner = _载入('planner_for_w181', 'planner.py')

from jikuai.service import schema  # noqa: E402
from jikuai.service.schema import CONTEXT_ENVELOPE_REQUIRED  # noqa: E402

#: 上下文包的字段名从 schema 常量取，测试里也不写裸字面量。
_C需求, _C语义命中, _C候选, _C回填契约, _C拒答建议 = CONTEXT_ENVELOPE_REQUIRED

#: 一条历法向问句：`标量` 模式能命中一个全标量入参的候选（口径同 W158 端到端问句）。
标量问句 = '6月按班次看有多少天'
#: 一条制造域问句：录像库里有逐字相同的 Q_PUB_001，且其块在 top=8 能承载。
录像问句 = '2026年6月各车型的总产量是多少？按产量从高到低排序。'


@contextmanager
def _起(模式='标量', 令牌=''):
    srv, url = 端点模块.起服务(0, 模式, 令牌)
    线 = threading.Thread(target=srv.serve_forever, daemon=True)
    线.start()
    try:
        yield url
    finally:
        srv.shutdown()
        srv.server_close()
        线.join(timeout=5)


def _post(url, 对象, 头=None):
    体 = json.dumps(对象, ensure_ascii=False).encode('utf-8')
    h = {'Content-Type': 'application/json; charset=utf-8'}
    if 头:
        h.update(头)
    req = urllib.request.Request(url, data=体, headers=h, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))


class 造回填逻辑(unittest.TestCase):
    """三种模式的机械造填逻辑，纯离线、不起 socket。"""

    def test_标量模式造出能过校验器的一步方案(self):
        包 = planner.build_context(标量问句, top=8)
        回填, 理由 = 端点模块.造标量回填(包)
        self.assertIsNone(理由, 理由)
        self.assertEqual(schema.validate_filled_envelope(回填), [])
        self.assertEqual(planner.validate_filled(回填, 包), [])

    def test_标量模式模型标识必带参考端点前缀(self):
        """回填来源不许伪装成模型名——它是对拍端点，不是 LLM。"""
        包 = planner.build_context(标量问句, top=8)
        回填, _ = 端点模块.造标量回填(包)
        self.assertTrue(回填['模型'].startswith('参考端点·'), 回填['模型'])

    def test_录像模式逐字命中并转投(self):
        包 = planner.build_context(录像问句, top=8)
        回填, 理由 = 端点模块.造录像回填(包)
        self.assertIsNone(理由, 理由)
        self.assertEqual(planner.validate_filled(回填, 包), [])
        # 转投保留原始来源，不把人工产物洗成端点产物。
        self.assertIn('录像转投', 回填['模型'])
        self.assertIn('人工', 回填['模型'])

    def test_录像模式差一个字就不投(self):
        """刻意不做模糊匹配：投一份邻近问句的方案，跑出来的数会像对的。"""
        包 = planner.build_context(录像问句 + '吗', top=8)
        回填, 理由 = 端点模块.造录像回填(包)
        self.assertIsNone(回填)
        self.assertIn('逐字', 理由)

    def test_录像模式承载不下时回无能力而不硬凑(self):
        """白名单装不下录像用到的块 → 不投 + 列缺块，不是造个假方案顶上。

        构造法是**把包的候选清空**，而不是把 `top` 调小——见下一条：调小 `top`
        拦不住语义旁路，那条路不构成「装不下」的场景。
        """
        包 = planner.build_context(录像问句, top=8)
        包[_C候选] = []
        回填, 理由 = 端点模块.造录像回填(包)
        self.assertIsNone(回填)
        self.assertIn('候选里', 理由)
        # 缺的块名要点出来，否则「装不下」这句话没法照着修。
        self.assertIn('表载入', 理由)

    def test_调小top拦不住语义旁路所以承载不随top缩水(self):
        """W181 实测：`top=1` 下 Q_PUB_001 的三个块**照样全在候选里**。

        原因是 v0.28.0 W174 的语义层直取旁路挂在候选表尾部、**不受 `top` 约束**
        （ADR-41 §9），骨架块（`表载入`/`窗口`）本来就是从那条路进来的。
        所以「把 top 调小来模拟装不下」是个错的构造法，本条把这个事实钉住——
        哪天旁路改成受 K 约束，这里会红，逼人回来重看上一条测试的构造法。
        """
        包 = planner.build_context(录像问句, top=1)
        候选名 = {c['名称'] for c in 包[_C候选]}
        for 块 in ('表载入', '窗口', '产量汇总'):
            self.assertIn(块, 候选名, '旁路应当把 %s 塞进来' % 块)

    def test_幻觉模式回不存在的块被校验器拦(self):
        包 = planner.build_context(录像问句, top=8)
        回填, _ = 端点模块.造幻觉回填(包)
        # 形状合法（能过信封校验），但规则 2 拦得住。
        self.assertEqual(schema.validate_filled_envelope(回填), [])
        拒 = planner.validate_filled(回填, 包)
        self.assertTrue(拒)
        self.assertTrue(any('白名单' in r for r in 拒), 拒)

    def test_无能力时给422而不是200空对象(self):
        """`构建回填` 造不出时的状态码：不给 200，否则会被误读成端点填错。"""
        包 = planner.build_context(录像问句, top=8)
        包[_C候选] = []
        码, 体 = 端点模块.构建回填(包, '录像')
        self.assertEqual(码, 端点模块.状态_无能力)
        self.assertIn('理由', json.loads(体))


class 取令牌(unittest.TestCase):
    def test_空令牌放行(self):
        self.assertEqual(端点模块.取令牌({}), '')

    def test_ascii令牌通过(self):
        self.assertEqual(端点模块.取令牌({端点模块.ENV令牌: 'abc-123'}), 'abc-123')

    def test_非ascii令牌启动即拒(self):
        """中文 Token 会让客户端 putheader 抛 UnicodeEncodeError，病根离报错很远。"""
        with self.assertRaises(ValueError) as cm:
            端点模块.取令牌({端点模块.ENV令牌: '中文令牌'})
        self.assertIn('ASCII', str(cm.exception))


class 起服务(unittest.TestCase):
    def test_主机硬编码为本地回环(self):
        srv, url = 端点模块.起服务(0, '标量', '')
        try:
            self.assertEqual(srv.server_address[0], '127.0.0.1')
            self.assertIn('127.0.0.1', url)
        finally:
            srv.server_close()

    def test_未知模式拒绝(self):
        with self.assertRaises(ValueError):
            端点模块.起服务(0, '瞎给的', '')


class 真socket往返(unittest.TestCase):
    """起真服务、走真 HTTP：证明 socket + 编码 + 鉴权头在跨进程边界上的行为。"""

    def test_标量模式真往返出合法回填(self):
        包 = planner.build_context(标量问句, top=8)
        with _起('标量') as url:
            码, 体 = _post(url, 包)
        self.assertEqual(码, 200)
        self.assertEqual(schema.validate_filled_envelope(体), [])

    def test_get被拒(self):
        with _起('标量') as url:
            req = urllib.request.Request(url, method='GET')
            try:
                urllib.request.urlopen(req, timeout=10)
                码 = 200
            except urllib.error.HTTPError as e:
                码 = e.code
        self.assertEqual(码, 405)

    def test_请求体非JSON回400(self):
        with _起('标量') as url:
            体 = b'<html>not json</html>'
            req = urllib.request.Request(
                url, data=体, headers={'Content-Type': 'application/json'},
                method='POST')
            try:
                urllib.request.urlopen(req, timeout=10)
                码 = 200
            except urllib.error.HTTPError as e:
                码 = e.code
        self.assertEqual(码, 400)

    def test_设令牌后无Bearer回401(self):
        包 = planner.build_context(标量问句, top=8)
        with _起('标量', 令牌='secret-abc') as url:
            码, _ = _post(url, 包)
        self.assertEqual(码, 401)

    def test_设令牌后带对的Bearer放行(self):
        包 = planner.build_context(标量问句, top=8)
        with _起('标量', 令牌='secret-abc') as url:
            码, 体 = _post(url, 包, 头={'Authorization': 'Bearer secret-abc'})
        self.assertEqual(码, 200)
        self.assertEqual(schema.validate_filled_envelope(体), [])

    def test_设令牌后带错的Bearer回401(self):
        包 = planner.build_context(标量问句, top=8)
        with _起('标量', 令牌='secret-abc') as url:
            码, _ = _post(url, 包, 头={'Authorization': 'Bearer wrong'})
        self.assertEqual(码, 401)


if __name__ == '__main__':
    unittest.main()
