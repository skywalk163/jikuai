# -*- coding: utf-8 -*-
"""演示端点测试（v0.28.0 W164，ADR-42）。

覆盖面 —— **反例为主**，这是个安全模块，只测正例等于没测：

正例 3 条：`GET /演示/白名单`、`POST /演示/问`、`POST /演示/跑`。

反例 10 条（四道闸各自被绕的路子）：

* 闸 1 鉴权：无 Token / 错 Token / `Bearer` 前缀缺失 → 401
* 闸 2 白名单：块不在白名单 / 领域不是制造 → 400
* 闸 4 路径：`../` 逃逸 / 绝对路径 → 400
* 「不收源码」：请求体带 `源码` 键 / 方案里带 `源码` 键 → 400
* 启动前置：未设 `JIKUAI_DEMO_TOKEN` 时 `build_server` 抛、`main` 退 1

工程约束同 `test_web_server.py`：**不用 requests**（零新增 pip 依赖），
只用标准库 `http.client`；服务跑后台线程、端口交内核；`tools/web` 不是包，
按文件路径 `importlib` 加载。
"""

import http.client
import importlib.util
import json
import os
import sys
import threading
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_DEMO_PY = os.path.join(_REPO, 'tools', 'web', 'demo_server.py')
_数据集 = os.path.join(_REPO, '赛题', 'chatbi', '数据集')

#: Token 必须是纯 ASCII —— HTTP 头值只收 latin-1（`http.client` 硬约束，也是
#: CVE-2019-9740 的防线）。这不是测试的迁就，是 `build_server` 明确拒绝非 ASCII
#: Token 的那条前置（见 test_非ASCII令牌拒绝构造）。
_令牌 = 'w164-test-token-0123456789abcdef'


def _load_demo():
    spec = importlib.util.spec_from_file_location('_jikuai_demo_server', _DEMO_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


demo = _load_demo()


# ---- 夹具 -------------------------------------------------------------

@pytest.fixture(scope='module')
def 服务():
    """后台线程里跑一个真演示服务。Token 通过环境变量注入（唯一来源）。"""
    旧 = os.environ.get(demo.TOKEN_ENV)
    os.environ[demo.TOKEN_ENV] = _令牌
    srv = demo.build_server('127.0.0.1', 0)
    t = threading.Thread(target=srv.serve_forever, name='jk-demo-test', daemon=True)
    t.start()
    try:
        yield srv.server_address[0], srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)
        if 旧 is None:
            os.environ.pop(demo.TOKEN_ENV, None)
        else:
            os.environ[demo.TOKEN_ENV] = 旧


def _请求(服务, 方法, 路径, body=None, 令牌=_令牌, 原始body=None, 头=None):
    host, port = 服务
    conn = http.client.HTTPConnection(host, port, timeout=60)
    try:
        头 = dict(头 or {})
        数据 = None
        if 原始body is not None:
            数据 = 原始body
        elif body is not None:
            数据 = json.dumps(body, ensure_ascii=False).encode('utf-8')
        if 数据 is not None:
            头.setdefault('Content-Type', 'application/json; charset=utf-8')
        if 令牌 is not None:
            头.setdefault('Authorization', 'Bearer ' + 令牌)
        # 路径含中文，必须先百分号编码——`http.client` 的请求行只收 ASCII
        # （也是 CVE-2019-9740 的防线）。服务端 `_GET`/`_POST` 会 unquote 回来。
        conn.request(方法, urllib.parse.quote(路径), body=数据, headers=头)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _JSON(原始):
    return json.loads(原始.decode('utf-8'))


def _能耗方案():
    """一份真能跑的制造域方案（照 Q_PUB_009 口径：六月各车间总电耗）。"""
    return {
        '需求': '各车间2026年6月总电耗是多少？',
        '共享': [
            {'名': '赵能耗路', '值': '“赛题/chatbi/数据集/fact_energy_usage.csv”'},
            {'名': '赵耗日列', '值': '“usage_date”'},
            {'名': '赵六月始', '值': '“2026-06-01”'},
            {'名': '赵六月末', '值': '“2026-06-30”'},
            {'名': '赵车间维', '值': '【“workshop”】'},
        ],
        '步骤': [
            {'块': '表载入', '领域': '制造', '导出名': '读表',
             '参数': ['赵能耗路']},
            {'块': '窗口', '领域': '制造', '导出名': '截期',
             '参数': ['赵果1', '赵耗日列', '赵六月始', '赵六月末']},
            {'块': '能耗汇总', '领域': '制造', '导出名': '计耗',
             '参数': ['赵果2', '赵车间维']},
        ],
        '打印': ['赵果3'],
    }


# ---- 正例 -------------------------------------------------------------

def test_白名单端点回非空清单与数据集根(服务):
    状态, 原始 = _请求(服务, 'GET', '/演示/白名单')
    assert 状态 == 200
    包 = _JSON(原始)
    assert 包['允许块'], '白名单不能是空的——空白名单等于这道闸没在守'
    assert '表载入' in 包['允许块'] and '能耗汇总' in 包['允许块']
    # 白名单外的东西不许出现（举证：财务域的块名）
    assert '个税' not in 包['允许块']
    assert 包['数据集根'].replace('\\', '/').endswith('赛题/chatbi/数据集')


def test_问端点出规划上下文包且不碰模型(服务):
    状态, 原始 = _请求(服务, 'POST', '/演示/问',
                    body={'需求': '2026年6月各车型总产量'})
    assert 状态 == 200
    包 = _JSON(原始)
    from jikuai.service import schema
    for 键 in schema.CONTEXT_ENVELOPE_REQUIRED:
        assert 键 in 包, '上下文包缺字段 %s' % 键
    # 上下文包是离线产物，不该带任何模型标识。
    assert '模型' not in 包


@pytest.mark.skipif(not os.path.isdir(_数据集), reason='赛题数据集不在场')
def test_跑端点组出源码并在子进程里执行(服务):
    状态, 原始 = _请求(服务, 'POST', '/演示/跑', body={'方案': _能耗方案()})
    assert 状态 == 200, 原始
    包 = _JSON(原始)
    from jikuai.service import schema
    for 键 in schema.RUN_ENVELOPE_REQUIRED:
        assert 键 in 包
    assert '从 blocks.制造.表载入 导入' in 包['源码']
    结果 = 包['执行结果']
    assert 结果.get('错误') is None, 结果
    assert 结果['stdout'].strip(), '应有打印输出'


# ---- 反例：闸 1 鉴权 ---------------------------------------------------

def test_无令牌一律401(服务):
    for 方法, 路径, body in (('GET', '/演示/白名单', None),
                            ('POST', '/演示/问', {'需求': 'x'}),
                            ('POST', '/演示/跑', {'方案': _能耗方案()})):
        状态, 原始 = _请求(服务, 方法, 路径, body=body, 令牌=None)
        assert 状态 == 401, (方法, 路径, 状态)
        assert '未授权' in _JSON(原始)['错误']


def test_错令牌401(服务):
    状态, 原始 = _请求(服务, 'GET', '/演示/白名单', 令牌='wrong-token')
    assert 状态 == 401
    assert '未授权' in _JSON(原始)['错误']


def test_缺Bearer前缀也401(服务):
    状态, _ = _请求(服务, 'GET', '/演示/白名单', 令牌=None,
                  头={'Authorization': _令牌})
    assert 状态 == 401


# ---- 反例：不收源码 ---------------------------------------------------

def test_请求体带源码键被拒(服务):
    状态, 原始 = _请求(服务, 'POST', '/演示/跑',
                    body={'方案': _能耗方案(), '源码': '打印 1。'})
    assert 状态 == 400
    assert '不接受' in _JSON(原始)['错误']


def test_方案里带源码键被拒(服务):
    方案 = _能耗方案()
    方案['源码'] = '打印 1。'
    状态, 原始 = _请求(服务, 'POST', '/演示/跑', body={'方案': 方案})
    assert 状态 == 400
    assert '源码' in _JSON(原始)['错误']


# ---- 反例：闸 2 白名单 -------------------------------------------------

def test_白名单外的块被拒(服务):
    方案 = _能耗方案()
    方案['步骤'][0] = {'块': '不存在的块', '领域': '制造', '导出名': 'x',
                     '参数': ['赵能耗路']}
    状态, 原始 = _请求(服务, 'POST', '/演示/跑', body={'方案': 方案})
    assert 状态 == 400
    assert '白名单' in _JSON(原始)['错误']


def test_非制造域被拒(服务):
    方案 = _能耗方案()
    方案['步骤'][0] = {'块': '个税', '领域': '财务', '导出名': '缴税',
                     '参数': ['赵能耗路']}
    状态, 原始 = _请求(服务, 'POST', '/演示/跑', body={'方案': 方案})
    assert 状态 == 400
    理由 = _JSON(原始)['错误']
    assert '白名单' in 理由 and '制造' in 理由


# ---- 反例：闸 4 数据集只读 ---------------------------------------------

def test_路径逃逸被拒(服务):
    方案 = _能耗方案()
    方案['共享'][0]['值'] = '“赛题/chatbi/数据集/../../../pyproject.toml”'
    状态, 原始 = _请求(服务, 'POST', '/演示/跑', body={'方案': 方案})
    assert 状态 == 400
    assert '越界' in _JSON(原始)['错误']


def test_绝对路径被拒(服务):
    方案 = _能耗方案()
    方案['共享'][0]['值'] = '“%s”' % os.path.join(_REPO, 'pyproject.toml')
    状态, 原始 = _请求(服务, 'POST', '/演示/跑', body={'方案': 方案})
    assert 状态 == 400
    理由 = _JSON(原始)['错误']
    assert '绝对路径' in 理由 or '越界' in 理由


# ---- 反例：启动前置 ---------------------------------------------------

def test_未设令牌时拒绝构造服务(monkeypatch):
    monkeypatch.delenv(demo.TOKEN_ENV, raising=False)
    with pytest.raises(RuntimeError) as e:
        demo.build_server('127.0.0.1', 0)
    assert demo.TOKEN_ENV in str(e.value)


def test_未设令牌时main退1(monkeypatch, capsys):
    monkeypatch.delenv(demo.TOKEN_ENV, raising=False)
    assert demo.main(['--端口', '0']) == 1
    assert demo.TOKEN_ENV in capsys.readouterr().err


def test_非ASCII令牌拒绝构造(monkeypatch):
    """带中文的 Token 会让客户端发请求时就抛 UnicodeEncodeError，启动这一刻就该拒。"""
    monkeypatch.setenv(demo.TOKEN_ENV, '中文令牌')
    with pytest.raises(RuntimeError) as e:
        demo.build_server('127.0.0.1', 0)
    assert 'ASCII' in str(e.value)


# ---- 静态不变量（与 G24 同一批断言，pytest 侧也兜一遍）-----------------

def test_源文件里没有进程内执行的调用():
    """演示端点绝不能在服务进程内 `run_source`/`exec`/`eval`。

    用 AST 而不是按行 grep：`run_source` 这个词必然出现在文档串与
    `_子进程引导` 那段**要交给 subprocess 去跑的字符串**里，按文本扫必然假红。
    要断言的是「没有以它为被调者的 Call 节点」。
    """
    import ast
    with open(_DEMO_PY, 'r', encoding='utf-8') as f:
        树 = ast.parse(f.read(), filename=_DEMO_PY)
    # 刻意**不**把 `compile` 列进来：`re.compile` 是正常用法，按被调者名字扫
    # 会假红（第一版就栽在这里）。要禁的是「在本进程里跑别人给的代码」这三个。
    禁 = {'run_source', 'exec', 'eval'}
    命中 = []
    for 节点 in ast.walk(树):
        if not isinstance(节点, ast.Call):
            continue
        名 = None
        if isinstance(节点.func, ast.Name):
            名 = 节点.func.id
        elif isinstance(节点.func, ast.Attribute):
            名 = 节点.func.attr
        if 名 in 禁:
            命中.append('%s 行 %d' % (名, 节点.lineno))
    assert not 命中, '演示端点里出现进程内执行调用：%s' % 命中


def test_令牌只从环境变量取():
    """Token 的唯一来源是 `os.environ[TOKEN_ENV]`，命令行不许收 key。"""
    with open(_DEMO_PY, 'r', encoding='utf-8') as f:
        文本 = f.read()
    assert 'add_argument' in 文本
    for 行 in 文本.splitlines():
        if 'add_argument' in 行:
            assert 'token' not in 行.lower() and '令牌' not in 行, \
                '命令行不许收 Token：%s' % 行.strip()
