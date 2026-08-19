# -*- coding: utf-8 -*-
"""v0.27.0 W158-W159 · `jk 块 规划` / `jk 块 问` 与分级报告测试（ADR-41 §2）。

被测：`jikuai.pkg.blocks_cli` 的 `_cmd_plan` / `_cmd_ask` / `_分级报告` /
`_请求回填` / `_planner`。

W158 的 DoD 是「两条命令的正常路径 + 未知选项 + 缺 `--模型` 各有断言」，加上
「端点返回垃圾 JSON 时退 1 并给可读理由，不抛裸异常」。W159 的拒答与分歧告警在
上下文包里（`planner` 侧已有 W156/W157 的用例），这里只测**CLI 这一层**有没有把
它们摆到该摆的位置：拒答要在调端点**之前**就停，分歧告警要打出来。

`问` 的端点用一个**真的**本机 HTTP server（`127.0.0.1:0` 随机端口）而不是替掉
`_请求回填`：那个函数本身就是被测对象之一（协议白名单、鉴权头、垃圾响应处理都在
它里面），mock 掉它等于把要测的东西测没了。
"""

import io
import json
import os
import threading
import unittest
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from jikuai.pkg import blocks_cli
from jikuai.service import schema

#: 一条**制造域**问句：语义层能命中业务词，所以 `拒答建议.覆盖` 为真，
#: `问` 会一路走到端点。同时它命中 ADR-40 §5.1 的口径分歧点，顺带覆盖分歧告警。
覆盖问句 = '2026年6月各产线达成率是多少'

#: 一条**库外**问句：语义层只登记制造域业务词，所以这条判未覆盖。
库外问句 = '把这段话转成繁体再算个SHA256'

#: 一条**覆盖且无口径分歧**的问句：端到端用例用它。达成率那条覆盖但命中分歧点，
#: 规则 3 会因「命中口径却两侧都没选」拒掉任何不含分歧块的回填——那对测拒答对，
#: 但没法用来测「一路跑通」。这条历法向问句覆盖、零分歧，才能走完 组 → 跑。
端到端问句 = '6月按班次看有多少天'


def _跑(*argv):
    """跑一条 `jk 块 <...>`，返回 `(返回码, stdout, stderr)`。"""
    出, 错 = io.StringIO(), io.StringIO()
    with redirect_stdout(出), redirect_stderr(错):
        码 = blocks_cli.run(list(argv))
    return 码, 出.getvalue(), 错.getvalue()


class _假端点(BaseHTTPRequestHandler):
    """一个只认 POST 的假模型端点。`回应` 由每条用例换。

    `回应(包, 请求头) -> (HTTP 状态码, 响应体文本)`。收到的上下文包与请求头都存进
    `收件箱`，供用例断言「端点到底被不被调用」「Authorization 头有没有发出去」。
    """

    回应 = None
    收件箱 = None

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        包 = json.loads(self.rfile.read(n).decode('utf-8'))
        type(self).收件箱.append((包, dict(self.headers)))
        码, 体 = type(self).回应(包, self.headers)
        raw = 体.encode('utf-8')
        self.send_response(码)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        """吞掉 BaseHTTPRequestHandler 默认往 stderr 打的访问日志。"""


class _静默服务(ThreadingMixIn, HTTPServer):
    """并发 + 不往 stderr 吐连接异常栈的 HTTPServer。

    单线程 `HTTPServer` 在 Windows 上关闭时会因客户端先断开而由 `socketserver`
    的默认 `handle_error` 打一整段 `ConnectionAbortedError` 栈到 stderr——那既不是
    被测代码的问题，也会污染整轮回归的输出。这里一并吞掉。
    """

    daemon_threads = True

    def handle_error(self, request, client_address):
        """连接层异常不打栈：假端点的连接生死不是被测对象。"""


@contextmanager
def _端点(回应):
    """起一个后台 HTTP server，`yield (url, 收件箱)`，退出时关掉。"""
    收件箱 = []
    类 = type('_临时端点', (_假端点,), {'回应': staticmethod(回应),
                                       '收件箱': 收件箱})
    srv = _静默服务(('127.0.0.1', 0), 类)
    线 = threading.Thread(target=srv.serve_forever, daemon=True)
    线.start()
    try:
        yield 'http://127.0.0.1:%d/fill' % srv.server_address[1], 收件箱
    finally:
        srv.shutdown()
        srv.server_close()
        线.join(timeout=5)


def _造回填(包):
    """从上下文包里挑一个**全标量入参**的候选，造一份能过五条硬规则的回填。

    刻意从包里动态挑而不是写死块名：检索排序会随块库变，写死等于给自己埋一颗
    定时炸弹。挑不到就返回 None，由用例 `skipTest`。
    """
    for c in (包.get('候选') or []):
        槽 = c.get('输入槽') or []
        if 槽 and all(s.get('类型') == '数' for s in 槽):
            共享 = [{'名': '赵参%d' % (i + 1), '值': '1', '类型': '数'}
                    for i in range(len(槽))]
            方案 = {
                '需求': 包.get('需求'),
                '共享': 共享,
                '步骤': [{'块': c.get('名称'), '领域': c.get('领域'),
                          '导出名': c.get('导出名'),
                          '参数': [x['名'] for x in 共享]}],
            }
            return schema.make_filled_envelope(包.get('需求'), 方案, '假端点')
    return None


class 规划命令(unittest.TestCase):
    """`jk 块 规划`：纯离线出上下文包 / 分级报告。"""

    def test_人读模式出四段分级报告(self):
        """默认输出是 W159 的四段分级报告，按「该先看什么」排序。"""
        码, 出, _ = _跑('规划', 覆盖问句)
        self.assertEqual(码, 0)
        for 段 in ('【1/4 拒答判定】', '【2/4 口径分歧】',
                   '【3/4 语义命中】', '【4/4 候选】'):
            self.assertIn(段, 出)
        # 分级 = 有序：拒答判定必须排在候选前面，不然「该停手」这条会被候选冲掉。
        self.assertLess(出.index('【1/4'), 出.index('【4/4'))

    def test_json模式出合法上下文包(self):
        """`--json` 出的必须是过 `validate_context_envelope` 的协议原文。"""
        码, 出, _ = _跑('规划', 覆盖问句, '--json')
        self.assertEqual(码, 0)
        包 = json.loads(出)
        self.assertEqual(schema.validate_context_envelope(包), [])

    def test_覆盖问句命中口径分歧点(self):
        """制造域达成率问句要带出 ADR-40 §5.1 的分歧告警（W159）。"""
        _, 出, _ = _跑('规划', 覆盖问句, '--json')
        告警表 = json.loads(出).get('分歧告警') or []
        self.assertTrue(告警表)
        self.assertTrue(all(w['须显式选一条'] for w in 告警表))

    def test_库外问句判未覆盖但退出码仍是0(self):
        """拒答判定是**包里的一个字段**，不是命令失败——退出码必须还是 0。

        这条是刻意的口径：`规划` 的产出就是那份包，判定给调用方读；把它变成非零
        退出码会让 CI 与管道把一次正常的「劝你别用」当成工具坏了。
        """
        码, 出, _ = _跑('规划', 库外问句)
        self.assertEqual(码, 0)
        self.assertIn('判为库外能力', 出)

    def test_缺问句退1(self):
        码, _, 错 = _跑('规划')
        self.assertEqual(码, 1)
        self.assertIn('缺少问句', 错)

    def test_未知选项退1(self):
        码, _, 错 = _跑('规划', 覆盖问句, '--瞎给的')
        self.assertEqual(码, 1)
        self.assertIn('未知选项', 错)

    def test_top非正整数退1(self):
        for 值 in ('零', '0', '-3'):
            码, _, _ = _跑('规划', 覆盖问句, '--top', 值)
            self.assertEqual(码, 1, 值)


class 问命令(unittest.TestCase):
    """`jk 块 问`：端到端 + 各条拒绝路径。"""

    def test_缺模型退1并指向规划(self):
        """不给 `--模型` 不许静默降级到某个默认端点（ADR-41 §2）。"""
        码, _, 错 = _跑('问', 覆盖问句)
        self.assertEqual(码, 1)
        self.assertIn('规划', 错)
        self.assertIn(blocks_cli._ENV问密钥, 错)

    def test_缺问句退1(self):
        码, _, 错 = _跑('问', '--模型', 'http://127.0.0.1:1/x')
        self.assertEqual(码, 1)
        self.assertIn('缺少问句', 错)

    def test_未知选项退1(self):
        码, _, 错 = _跑('问', 覆盖问句, '--模型', 'http://127.0.0.1:1/x', '--瞎给的')
        self.assertEqual(码, 1)
        self.assertIn('未知选项', 错)

    def test_非http端点退1(self):
        """`file:`/`data:` 交给 urlopen 会去读本机文件，白名单外一律拒。"""
        for 端点 in ('file:///etc/passwd', 'data:,x', '不是URL'):
            码, _, 错 = _跑('问', 覆盖问句, '--模型', 端点)
            self.assertEqual(码, 1, 端点)
            self.assertIn('白名单', 错)

    def test_端点URL带中文路径退1(self):
        """请求行必须是 ASCII（CVE-2019-9740 的防线），中文路径不许冒裸 traceback。

        这条是写本文件时真踩出来的：`http.client` 在 `_encode_request` 抛
        `UnicodeEncodeError`，它不是 `OSError`，只捕 `OSError` 会漏。
        """
        码, _, 错 = _跑('问', 覆盖问句, '--模型', 'http://127.0.0.1:1/回填')
        self.assertEqual(码, 1)
        self.assertIn('请求端点失败', 错)

    def test_库外问句在调端点之前就停(self):
        """拒答要省掉那次推理，也不给模型一个注定要编的上下文。"""
        with _端点(lambda 包, 头: (200, '{}')) as (url, 收件箱):
            码, _, 错 = _跑('问', 库外问句, '--模型', url)
        self.assertEqual(码, 1)
        self.assertIn('拒答', 错)
        self.assertEqual(收件箱, [])

    def test_端点返回垃圾JSON退1且理由可读(self):
        """W158 DoD：不抛裸异常，理由里要带响应开头便于排障。"""
        with _端点(lambda 包, 头: (200, '<html>502 Bad Gateway</html>')) as (url, _):
            码, _, 错 = _跑('问', 覆盖问句, '--模型', url)
        self.assertEqual(码, 1)
        self.assertIn('不是合法 JSON', 错)
        self.assertIn('502 Bad Gateway', 错)

    def test_端点返回JSON数组退1(self):
        """合法 JSON 但不是对象，同样不是回填响应。"""
        with _端点(lambda 包, 头: (200, '[1, 2, 3]')) as (url, _):
            码, _, 错 = _跑('问', 覆盖问句, '--模型', url)
        self.assertEqual(码, 1)
        self.assertIn('不是回填响应对象', 错)

    def test_端点返回HTTP错误退1(self):
        with _端点(lambda 包, 头: (500, '{}')) as (url, _):
            码, _, 错 = _跑('问', 覆盖问句, '--模型', url)
        self.assertEqual(码, 1)
        self.assertIn('HTTP 500', 错)

    def test_端点幻觉块名被校验器拦下(self):
        """端点响应一律当不可信输入：幻觉块名走的是同一个 `validate_filled`。"""
        def 回应(包, 头):
            方案 = {'步骤': [{'块': '并不存在的块', '领域': '制造',
                              '导出名': '瞎编', '参数': []}]}
            return 200, json.dumps(
                schema.make_filled_envelope(包['需求'], 方案, '假端点'),
                ensure_ascii=False)
        with _端点(回应) as (url, 收件箱):
            码, _, 错 = _跑('问', 覆盖问句, '--模型', url)
        self.assertEqual(len(收件箱), 1)          # 端点确实被调了
        self.assertEqual(码, 1)
        self.assertIn('校验拒绝', 错)
        self.assertIn('白名单', 错)

    def test_密钥只从环境变量取(self):
        """命令行不收 key；环境变量里有就发 Bearer，没有就一个鉴权头都不发。

        token 用纯 ASCII：HTTP 头值走 latin-1 编码，中文 token 会在
        `http.client.putheader` 就炸——那条路径由 `test_中文密钥不抛裸异常` 单独覆盖。
        """
        原值 = os.environ.pop(blocks_cli._ENV问密钥, None)
        try:
            with _端点(lambda 包, 头: (200, '{}')) as (url, 收件箱):
                _跑('问', 覆盖问句, '--模型', url)
                self.assertNotIn('Authorization', 收件箱[0][1])
                os.environ[blocks_cli._ENV问密钥] = 'test-token-123'
                _跑('问', 覆盖问句, '--模型', url)
                self.assertEqual(收件箱[1][1].get('Authorization'),
                                 'Bearer test-token-123')
        finally:
            os.environ.pop(blocks_cli._ENV问密钥, None)
            if 原值 is not None:
                os.environ[blocks_cli._ENV问密钥] = 原值

    def test_中文密钥不抛裸异常(self):
        """环境变量里塞了中文 token：报可读理由退 1，不冒 UnicodeEncodeError。"""
        原值 = os.environ.get(blocks_cli._ENV问密钥)
        os.environ[blocks_cli._ENV问密钥] = '中文token'
        try:
            with _端点(lambda 包, 头: (200, '{}')) as (url, 收件箱):
                码, _, 错 = _跑('问', 覆盖问句, '--模型', url)
            self.assertEqual(码, 1)
            self.assertIn('请求端点失败', 错)
            self.assertEqual(收件箱, [])
        finally:
            os.environ.pop(blocks_cli._ENV问密钥, None)
            if 原值 is not None:
                os.environ[blocks_cli._ENV问密钥] = 原值

    def test_端到端正常路径(self):
        """包 → 端点 → 校验 → 组 → 跑，退出码 0，stdout 有结果。"""
        造好的 = {}

        def 回应(包, 头):
            回填 = _造回填(包)
            造好的['回填'] = 回填
            if 回填 is None:
                return 200, '{}'
            return 200, json.dumps(回填, ensure_ascii=False)

        with _端点(回应) as (url, _):
            码, 出, 错 = _跑('问', 端到端问句, '--模型', url)
        if 造好的.get('回填') is None:
            self.skipTest('本轮候选里没有全标量入参的块，端到端用例无从构造')
        self.assertEqual(码, 0, 错)
        self.assertTrue(出.strip(), '正常路径该有 stdout')
        self.assertIn('回填来源：假端点', 错)

    def test_端到端json模式出跑响应(self):
        """`--json` 出的是既有的「跑响应」信封，形状与 `jk 块 跑 --json` 同构。"""
        造好的 = {}

        def 回应(包, 头):
            回填 = _造回填(包)
            造好的['回填'] = 回填
            if 回填 is None:
                return 200, '{}'
            return 200, json.dumps(回填, ensure_ascii=False)

        with _端点(回应) as (url, _):
            码, 出, 错 = _跑('问', 端到端问句, '--模型', url, '--json')
        if 造好的.get('回填') is None:
            self.skipTest('本轮候选里没有全标量入参的块，端到端用例无从构造')
        self.assertEqual(码, 0, 错)
        信封 = json.loads(出)
        self.assertEqual(schema.validate_run_envelope(信封), [])

    def test_分歧告警打到stderr(self):
        """覆盖问句命中分歧点时，`问` 要把告警摆到人眼前（W159）。"""
        with _端点(lambda 包, 头: (200, '{}')) as (url, _):
            _, _, 错 = _跑('问', 覆盖问句, '--模型', url)
        self.assertIn('口径分歧告警', 错)


class 接线(unittest.TestCase):
    """别名表 / 分发表 / 用法文本三处不许漏（新增子命令有 5 个改点）。"""

    def test_别名与分发表齐(self):
        for 别名, 规范 in (('规划', 'plan'), ('plan', 'plan'),
                          ('问', 'ask'), ('问答', 'ask'), ('ask', 'ask')):
            self.assertEqual(blocks_cli._ALIASES.get(别名), 规范, 别名)
        self.assertIn('plan', blocks_cli._DISPATCH)
        self.assertIn('ask', blocks_cli._DISPATCH)

    def test_用法文本提到两条新命令(self):
        for 片段 in ('jk 块 规划', 'jk 块 问', blocks_cli._ENV问密钥):
            self.assertIn(片段, blocks_cli._USAGE)

    def test_规划器按路径加载得到(self):
        """`_planner()` 与 `_glue()` 同一套按路径加载，且缓存同一个对象。"""
        p = blocks_cli._planner()
        self.assertTrue(hasattr(p, 'build_context'))
        self.assertTrue(hasattr(p, 'validate_filled'))
        self.assertIs(p, blocks_cli._planner())


if __name__ == '__main__':
    unittest.main()
