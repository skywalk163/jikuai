# -*- coding: utf-8 -*-
"""极快 Web UI 通道测试（v0.15.0 W17/W18，W20 协议收敛）。

覆盖面：

- 五个端点各至少一条正例（`GET /api/blocks|/api/能力`、`POST /api/选|组|跑`）
- 静态单页可取（`GET /`）+ 目录穿越被拒
- 坏 JSON / 缺 Content-Length / 超大 body / 未知端点 的错误分层
- `降级说明` 的降级链路（mock 掉 sidecar，**绝不真跑模型**）
- W20：`/api/跑` 的 `跑响应` 信封与 CLI `跑 --json` 逐字同构

工程约束：

* **不用 requests**（W17 DoD：零新增 pip 依赖），只用标准库 `http.client`。
* 服务跑在后台线程里，端口用 `0` 让内核分配 —— 写死 5000 会因为端口被占
  让 CI 随机红。测完 `shutdown()` + `server_close()`。
* `tools/web` 不是包，按文件路径 `importlib` 加载 `server.py`，与
  `blocks_cli._glue()` 加载 `glue.py` 同一套做法。
"""

import http.client
import importlib.util
import json
import os
import socket
import sys
import threading
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SERVER_PY = os.path.join(_REPO, 'tools', 'web', 'server.py')


def _load_server():
    """按文件路径加载 `tools/web/server.py`（该目录不是包，刻意的）。"""
    spec = importlib.util.spec_from_file_location('_jikuai_web_server', _SERVER_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


server = _load_server()


# ---- 夹具 -------------------------------------------------------------

@pytest.fixture(scope='module')
def 服务():
    """后台线程里跑一个真服务，端口交给内核分配。返回 (host, port)。"""
    srv = server.build_server('127.0.0.1', 0)
    t = threading.Thread(target=srv.serve_forever, name='jk-web-test', daemon=True)
    t.start()
    try:
        yield srv.server_address[0], srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def _请求(服务, 方法, 路径, body=None, 原始body=None, 头=None):
    """发一次 HTTP 请求，返回 `(状态码, 响应体bytes, 头对象)`。"""
    host, port = 服务
    conn = http.client.HTTPConnection(host, port, timeout=30)
    try:
        数据 = None
        头 = dict(头 or {})
        if 原始body is not None:
            数据 = 原始body
        elif body is not None:
            数据 = json.dumps(body, ensure_ascii=False).encode('utf-8')
        if 数据 is not None:
            头.setdefault('Content-Type', 'application/json; charset=utf-8')
        # 请求行必须是 ASCII（http.client 的硬约束，也防 CVE-2019-9740）。
        # 端点名带中文（`/api/选`），发之前 percent-encode。
        路径ascii = urllib.parse.quote(路径, safe="/?&=%:")
        conn.request(方法, 路径ascii, body=数据, headers=头)
        resp = conn.getresponse()
        return resp.status, resp.read(), resp
    finally:
        conn.close()


def _JSON(服务, 方法, 路径, body=None, **kw):
    状态, 原文, resp = _请求(服务, 方法, 路径, body=body, **kw)
    assert 'application/json' in resp.getheader('Content-Type', ''), \
        '所有 API 响应都该是 JSON，实际 %r' % resp.getheader('Content-Type')
    return 状态, json.loads(原文.decode('utf-8'))


def _裸请求(服务, 报文: bytes) -> bytes:
    """裸 socket 发一段原始 HTTP 报文，收全部响应字节。

    专门用于构造 `http.client` 不肯发的畸形请求（比如缺 Content-Length）。
    """
    host, port = 服务
    s = socket.create_connection((host, port), timeout=30)
    try:
        s.sendall(报文)
        块 = []
        while True:
            b = s.recv(4096)
            if not b:
                break
            块.append(b)
        return b''.join(块)
    finally:
        s.close()


#: 一份能真跑出结果的最小方案：把 `列 10 20 30` 求和 → 60。
_方案求和 = {
    '需求': '把一批数求和',
    '共享': [{'名': '赵料', '值': '列 10 20 30'}],
    '步骤': [{'块': '求和', '领域': '数据', '导出名': '汇总', '参数': ['赵料']}],
    '打印': ['赵果1'],
}


# ---- GET /api/blocks --------------------------------------------------

def test_blocks_返回索引原文(服务):
    """`GET /api/blocks` 吐 `索引.json` 原文：版本 + 生成时间 + 块数组。"""
    状态, data = _JSON(服务, 'GET', '/api/blocks')
    assert 状态 == 200
    assert set(data) >= {'版本', '生成时间', '块'}
    assert isinstance(data['块'], list) and data['块']
    名字 = {b['名称'] for b in data['块']}
    assert '求和' in 名字
    # 索引里带 `导出`，前端就是靠它把候选补成方案里的 `导出名`（见 README）
    求和 = next(b for b in data['块'] if b['名称'] == '求和')
    assert 求和['导出'] and isinstance(求和['领域'], list)


# ---- POST /api/选 -----------------------------------------------------

def test_选_返回协议候选(服务):
    """`POST /api/选` 的候选逐字段符合 `schema` 协议（含 `层级`）。"""
    from jikuai.service import schema
    状态, data = _JSON(服务, 'POST', '/api/选', {'需求': '求和', 'top': 3})
    assert 状态 == 200
    assert data['需求'] == '求和'
    assert 1 <= len(data['候选']) <= 3
    for c in data['候选']:
        assert schema.validate_candidate(c) == [], c
    assert '降级说明' not in data, '没要神经就不该出现降级说明'
    assert {c['名称'] for c in data['候选']} & {'求和'}


def test_选_候选带导出名且与索引一致(服务):
    """W37：Web 通道的候选必须带 `导出名`，且值来自 `索引.json` 的 `导出`。

    专挑目录名≠导出名的真实块（`个税` 导出 `缴税`）来验——目录名=导出名的块
    即使兜底成 `名称` 也看不出错，只有这类块能暴露 v0.16.0 的缺陷。
    """
    from jikuai.service import schema
    表 = schema.export_table()
    状态, data = _JSON(服务, 'POST', '/api/选', {'需求': '个人所得税', 'top': 8})
    assert 状态 == 200
    for c in data['候选']:
        assert c['导出名'], c
        assert c['导出名'] == 表.get(c['名称'], c['名称']), c
    命中 = {c['名称']: c['导出名'] for c in data['候选']}
    assert 命中.get('个税') == '缴税', '目录名≠导出名的块必须回真实导出名：%r' % (命中,)



def test_选_需求为空被拒(服务):
    状态, data = _JSON(服务, 'POST', '/api/选', {'需求': '   '})
    assert 状态 == 400
    assert '需求' in data['错误']


def test_选_top越界被拒(服务):
    状态, data = _JSON(服务, 'POST', '/api/选', {'需求': '求和', 'top': 0})
    assert 状态 == 400
    assert 'top' in data['错误']
    状态, data = _JSON(服务, 'POST', '/api/选',
                     {'需求': '求和', 'top': 10 ** 6})
    assert 状态 == 400


def test_选_神经不可用时降级并带说明(服务, monkeypatch):
    """`神经: true` 但 sidecar 拿不到向量 → 200 + 启发式候选 + `降级说明`。

    这里 mock 的是 `embed_client.fetch_query_vector`，不起子进程、不碰 torch。
    """
    from jikuai.ai import embed_client
    monkeypatch.setattr(embed_client, 'fetch_query_vector',
                        lambda *a, **k: (None, '测试注入：sidecar 不存在'))
    状态, data = _JSON(服务, 'POST', '/api/选',
                     {'需求': '求和', 'top': 2, '神经': True})
    assert 状态 == 200
    assert data['候选']
    assert '降级说明' in data
    assert '测试注入' in data['降级说明']
    assert '启发式' in data['降级说明']


def test_选_神经字段类型错被拒(服务):
    状态, data = _JSON(服务, 'POST', '/api/选', {'需求': '求和', '神经': '是'})
    assert 状态 == 400
    assert '神经' in data['错误']


# ---- POST /api/组 -----------------------------------------------------

def test_组_信封式方案出源码(服务):
    """`{"方案": {...}}` 信封写法 → `{源码}`，导入行与调用行都对。"""
    状态, data = _JSON(服务, 'POST', '/api/组', {'方案': _方案求和})
    assert 状态 == 200
    assert '从 blocks.数据.求和 导入 汇总' in data['源码']
    assert '汇总(赵料)' in data['源码']
    assert data['源码'].endswith('\n')


def test_组_裸方案也收(服务):
    """直接把方案本体当 body（`jk 块 组 -` 的 JSON 原样贴）也要能组。"""
    状态, data = _JSON(服务, 'POST', '/api/组', _方案求和)
    assert 状态 == 200
    assert '汇总(赵料)' in data['源码']


def test_组_缺步骤被schema拒(服务):
    状态, data = _JSON(服务, 'POST', '/api/组', {'方案': {'需求': '空'}})
    assert 状态 == 400
    assert '步骤' in data['错误']


def test_组_未知字段被schema拒(服务):
    """协议不允许通道私自加字段——多一个键就该 400，这是 W20 硬门槛的下沉。"""
    坏 = dict(_方案求和, 乱入=1)
    状态, data = _JSON(服务, 'POST', '/api/组', {'方案': 坏})
    assert 状态 == 400
    assert '未知字段' in data['错误']


def test_组_块不存在被拒(服务):
    坏 = {'步骤': [{'块': '不存在的块XYZ', '领域': '数据', '导出名': 'x'}]}
    状态, data = _JSON(服务, 'POST', '/api/组', {'方案': 坏})
    assert 状态 == 400
    assert '不存在' in data['错误']


def test_组_领域不在白名单被拒(服务):
    坏 = {'步骤': [{'块': '求和', '领域': '玄学', '导出名': '汇总'}]}
    状态, data = _JSON(服务, 'POST', '/api/组', {'方案': 坏})
    assert 状态 == 400
    assert '白名单' in data['错误']


# ---- POST /api/跑 -----------------------------------------------------
# W20 起 `/api/跑` 回的是 `跑响应` 信封 `{源码, 执行结果[, 需求]}`，与
# `jk 块 跑 --json` 完全一致；不再是裸 `执行结果`。这是有意的契约变更
# （见 docs/协议-三通道.md §契约变更史），旧断言随之上移一层。

def test_跑_端到端出结果(服务):
    """`POST /api/跑` 返回 `跑响应` 信封：`执行结果.stdout` 里有 60，不带 `错误`。"""
    from jikuai.service import schema
    状态, data = _JSON(服务, 'POST', '/api/跑', {'方案': _方案求和})
    assert 状态 == 200
    assert schema.validate_run_envelope(data) == [], data
    assert '从 blocks.数据.求和 导入 汇总' in data['源码']
    结果 = data['执行结果']
    assert '60' in 结果['stdout']
    assert '错误' not in 结果
    assert 结果['耗时毫秒'] >= 0


def test_跑_信封与CLI逐字同构(服务, tmp_path):
    """同一份方案，Web `/api/跑` 与 CLI `跑 --json` 的键必须逐字相同。

    W20 硬门槛的核心断言：三通道同构不是「差不多」。`耗时毫秒` 值必然不同，
    所以比键集合 + 比 `源码`/`stdout` 这两个应当完全相同的值。
    """
    import io
    from contextlib import redirect_stdout
    from jikuai.pkg import blocks_cli

    状态, web = _JSON(服务, 'POST', '/api/跑', {'方案': _方案求和})
    assert 状态 == 200

    p = tmp_path / '方案.json'
    p.write_text(json.dumps(_方案求和, ensure_ascii=False), encoding='utf-8')
    缓 = io.StringIO()
    with redirect_stdout(缓):
        rc = blocks_cli.run(['跑', str(p), '--json'])
    assert rc == 0, 缓.getvalue()
    cli = json.loads(缓.getvalue())

    assert set(web) == set(cli), (sorted(web), sorted(cli))
    assert set(web['执行结果']) == set(cli['执行结果'])
    assert web['源码'] == cli['源码']
    assert web['执行结果']['stdout'] == cli['执行结果']['stdout']
    assert web['需求'] == cli['需求']


def test_跑_执行失败是200带错误字段(服务):
    """业务失败不是传输失败：参数填不上 → 200 + `执行结果.错误`，不是 5xx。"""
    from jikuai.service import schema
    无参方案 = {'步骤': [{'块': '求和', '领域': '数据', '导出名': '汇总'}]}
    状态, data = _JSON(服务, 'POST', '/api/跑', {'方案': 无参方案})
    assert 状态 == 200
    assert schema.validate_run_envelope(data) == [], data
    assert '需人工填参' in data['执行结果']['错误']


def test_跑_解释器报错收敛成错误字段(服务):
    """参数写成不存在的变量 → 解释器抛错 → 200 + `执行结果.错误`，不含 traceback。"""
    from jikuai.service import schema
    方案 = {'步骤': [{'块': '求和', '领域': '数据', '导出名': '汇总',
                  '参数': ['赵没定义过的东西']}]}
    状态, data = _JSON(服务, 'POST', '/api/跑', {'方案': 方案})
    assert 状态 == 200
    assert schema.validate_run_envelope(data) == [], data
    assert data['执行结果']['错误']
    assert 'Traceback' not in data['执行结果']['错误']
    assert _REPO not in data['执行结果']['错误'], '错误消息不该泄露服务端绝对路径'


# ---- GET /api/能力 ----------------------------------------------------

def test_能力_契约字段齐全(服务):
    """`GET /api/能力` 的键集合就是 W19 前端约定的三个，一个不多一个不少。"""
    状态, data = _JSON(服务, 'GET', '/api/能力')
    assert 状态 == 200
    assert set(data) == {'神经可用', '索引版本', '块数'}
    assert isinstance(data['神经可用'], bool)
    assert isinstance(data['索引版本'], str)
    assert isinstance(data['块数'], int)
    # 仓库里有真索引，块数必然为正
    assert data['块数'] > 0
    assert data['索引版本']


def test_能力_神经可用为真当sidecar与索引都在(服务, monkeypatch):
    """sidecar 命令可解析 + 向量索引能加载 → `神经可用: true`。

    两个判据都 mock 掉运行时的现成函数（而不是伪造文件），断言 `/api/能力`
    真的复用了它们、没有自己 `os.path.isfile` 另搞一套。
    """
    from jikuai.ai import embed_client, retrieval
    monkeypatch.setattr(embed_client, 'resolve_command', lambda: ['python', 'x.py'])
    monkeypatch.setattr(retrieval, 'load_vector_index', lambda *a, **k: object())
    状态, data = _JSON(服务, 'GET', '/api/能力')
    assert 状态 == 200
    assert data['神经可用'] is True


def test_能力_神经不可用当sidecar缺失(服务, monkeypatch):
    """sidecar 解析不出命令（pip 安装场景 `tools/` 不随包发布）→ `神经可用: false`。"""
    from jikuai.ai import embed_client
    monkeypatch.setattr(embed_client, 'resolve_command', lambda: None)
    状态, data = _JSON(服务, 'GET', '/api/能力')
    assert 状态 == 200
    assert data['神经可用'] is False
    # 能力探测不该因此报错：索引信息照给
    assert data['块数'] > 0


def test_能力_神经不可用当索引加载不了(服务, monkeypatch):
    """向量索引读不到 / 魔数不对 → `神经可用: false`（判据走 load_vector_index）。"""
    from jikuai.ai import embed_client, retrieval
    monkeypatch.setattr(embed_client, 'resolve_command', lambda: ['python', 'x.py'])
    monkeypatch.setattr(retrieval, 'load_vector_index', lambda *a, **k: None)
    状态, data = _JSON(服务, 'GET', '/api/能力')
    assert 状态 == 200
    assert data['神经可用'] is False


# ---- 静态资源与目录穿越 -----------------------------------------------

def test_根路径给单页(服务):
    """`GET /` → `static/index.html`，Content-Type 是 HTML。"""
    状态, 原文, resp = _请求(服务, 'GET', '/')
    assert 状态 == 200
    assert 'text/html' in resp.getheader('Content-Type', '')
    正文 = 原文.decode('utf-8')
    assert '<html' in 正文.lower()
    assert 'app.js' in 正文


def test_静态js可取(服务):
    状态, 原文, resp = _请求(服务, 'GET', '/app.js')
    assert 状态 == 200
    assert 'javascript' in resp.getheader('Content-Type', '')
    assert b'fetch' in 原文


@pytest.mark.parametrize('路径', [
    '/../server.py',
    '/../../pyproject.toml',
    '/%2e%2e/server.py',
    '/..%2f..%2fpyproject.toml',
    '/....//server.py',
    '/subdir/../../server.py',
])
def test_目录穿越被拒(服务, 路径):
    """`..` 各种编码变体都不能读到 `static/` 之外的文件。

    判据不是「返回码是几」而是「拿不到内容」：403（越界）与 404（归一化后
    确实不存在）都算拒绝，泄露文件内容才算失守。
    """
    状态, 原文, _ = _请求(服务, 'GET', 路径)
    assert 状态 in (403, 404), '路径 %s 竟然返回了 %d' % (路径, 状态)
    assert b'JiKuaiHandler' not in 原文
    assert b'[tool.pytest' not in 原文


def test_绝对路径被拒(服务):
    """`GET //etc/passwd` 这类绝对路径要在 join 之前就拦掉。"""
    状态, _原文, _ = _请求(服务, 'GET', '//etc/passwd')
    assert 状态 in (403, 404)


# ---- 传输层错误分层 ---------------------------------------------------

def test_坏JSON_400(服务):
    状态, data = _JSON(服务, 'POST', '/api/选',
                     原始body='{这不是合法JSON'.encode('utf-8'))
    assert 状态 == 400
    assert '合法 JSON' in data['错误']


def test_body非对象_400(服务):
    状态, data = _JSON(服务, 'POST', '/api/选', 原始body=b'[1,2,3]')
    assert 状态 == 400
    assert '对象' in data['错误']


def test_空body_400(服务):
    """`http.client` 不带 body 时会自动补 `Content-Length: 0` —— 落到「空 body」这条。"""
    状态, data = _JSON(服务, 'POST', '/api/选')
    assert 状态 == 400
    assert '对象' in data['错误']


def test_缺ContentLength_400(服务):
    """真正**不带** Content-Length 头 → 400。

    这条必须裸 socket 发：`http.client` 会替你补上 `Content-Length: 0`，
    用它压根构造不出这个请求。
    """
    报文 = ('POST /api/%E9%80%89 HTTP/1.1\r\n'
          'Host: 127.0.0.1\r\n'
          'Content-Type: application/json\r\n'
          'Connection: close\r\n\r\n').encode('ascii')
    原文 = _裸请求(服务, 报文)
    头, _, body = 原文.partition(b'\r\n\r\n')
    assert b'400' in 头.split(b'\r\n')[0]
    assert 'Content-Length' in json.loads(body.decode('utf-8'))['错误']


def test_body超限_413(服务):
    """超过 `MAX_BODY` 直接 413，不把 body 读进内存。"""
    大 = ('{"需求":"' + 'x' * (server.MAX_BODY + 64) + '"}').encode('utf-8')
    状态, data = _JSON(服务, 'POST', '/api/选', 原始body=大)
    assert 状态 == 413
    assert '上限' in data['错误']


def test_未知端点_404(服务):
    状态, data = _JSON(服务, 'POST', '/api/不存在', {'x': 1})
    assert 状态 == 404
    assert '未知端点' in data['错误']
    状态, data = _JSON(服务, 'GET', '/api/不存在')
    assert 状态 == 404


def test_响应头有防护(服务):
    _状态, _原文, resp = _请求(服务, 'GET', '/api/blocks')
    assert resp.getheader('X-Content-Type-Options') == 'nosniff'
    assert resp.getheader('X-Frame-Options') == 'DENY'


# ---- 静态目录解析的单元级校验 -----------------------------------------

def test_静态根在tools_web_static下():
    根 = server.static_root()
    assert 根.endswith(os.path.join('tools', 'web', 'static'))
    assert os.path.isdir(根)


def test_安全提示文案含关键风险():
    """启动横幅必须点明「无鉴权」「执行任意代码」「别绑 0.0.0.0」三件事。"""
    for 关键 in ('无鉴权', '执行', '0.0.0.0', '本地'):
        assert 关键 in server.SAFETY_NOTICE


# ---- 方案存档端点（W31）-------------------------------------------------

@pytest.fixture()
def 存档目录(tmp_path, monkeypatch):
    """把存档目录指向 tmp_path 子目录，不污染真实 ~/.jikuai。"""
    根 = str(tmp_path / 'web-方案')
    monkeypatch.setenv(server.PLANS_DIR_ENV, 根)
    return 根


def _存方案(服务, 方案=None, 标题=None):
    """辅助：存一份方案，返回 (状态, data)。"""
    body = {'方案': 方案 or _方案求和}
    if 标题:
        body['标题'] = 标题
    return _JSON(服务, 'POST', '/api/方案/存', body)


def test_方案_存取列删_端到端(服务, 存档目录):
    """主链路：存 → 列 → 取 → 删 → 列空。"""
    状态, 存 = _存方案(服务, 标题='测试方案一')
    assert 状态 == 200, 存
    assert server.ID_PATTERN.match(存['id'])
    assert 存['标题'] == '测试方案一'
    assert 存['时间戳']

    # 列
    状态, 列 = _JSON(服务, 'GET', '/api/方案/列')
    assert 状态 == 200
    assert len(列['方案列表']) == 1
    assert 列['方案列表'][0]['id'] == 存['id']

    # 取
    状态, 取 = _JSON(服务, 'GET', '/api/方案/' + 存['id'])
    assert 状态 == 200
    assert '方案' in 取
    assert 取['方案']['步骤']

    # 删
    状态, 删 = _JSON(服务, 'DELETE', '/api/方案/' + 存['id'])
    assert 状态 == 200
    assert 删['id'] == 存['id']

    # 列空
    状态, 列 = _JSON(服务, 'GET', '/api/方案/列')
    assert 状态 == 200
    assert 列['方案列表'] == []


def test_方案_存_无标题则取需求(服务, 存档目录):
    """不传 `标题` 时，自动从方案 `需求` 字段提取。"""
    状态, 存 = _存方案(服务)
    assert 状态 == 200
    assert 存['标题'] == '把一批数求和'


def test_方案_取_不存在404(服务, 存档目录):
    状态, data = _JSON(服务, 'GET', '/api/方案/aabbccdd12345678')
    assert 状态 == 404
    assert '不存在' in data['错误']


def test_方案_删_不存在404(服务, 存档目录):
    状态, data = _JSON(服务, 'DELETE', '/api/方案/aabbccdd12345678')
    assert 状态 == 404
    assert '不存在' in data['错误']


@pytest.mark.parametrize('坏id', [
    '../../etc/passwd',
    '..%2f..%2fetc%2fpasswd',
    '/etc/passwd',
    'C:\\Windows\\win.ini',
    'CON',
    'aabb..ccdd',
    'AABBCCDD12345678',   # 大写 hex 不在白名单
    'short',              # 太短 (<8)
    'x' * 100,           # 太长 (>64)
    '12345678/../../etc',
    '123456789' + '\x00' + 'abc',
])
def test_方案_穿越攻击被拒(服务, 存档目录, 坏id):
    """路径穿越 / 非法 id 必须被白名单正则拦住，返回 400/404，不落盘。"""
    # GET
    状态, _, _ = _请求(服务, 'GET', '/api/方案/' + 坏id)
    assert 状态 in (400, 404), '坏 id %r 竟然返回 %d（GET）' % (坏id, 状态)
    # DELETE
    状态, _, _ = _请求(服务, 'DELETE', '/api/方案/' + 坏id)
    assert 状态 in (400, 404), '坏 id %r 竟然返回 %d（DELETE）' % (坏id, 状态)
    # 确认没有什么被写入存档目录
    根 = 存档目录
    if os.path.isdir(根):
        assert all(
            server.ID_PATTERN.match(n[:-5]) if n.endswith('.json') else True
            for n in os.listdir(根)
        ), '穿越攻击后存档目录出现了非白名单文件'


def test_方案_存_坏方案被拒(服务, 存档目录):
    """存的方案必须过 schema.ensure_plan，坏方案不落盘。"""
    状态, data = _JSON(服务, 'POST', '/api/方案/存', {'方案': {'乱来': True}})
    assert 状态 == 400
    assert '步骤' in data['错误'] or '未知字段' in data['错误']


def test_单页含历史侧栏与保存按钮(服务):
    """W31 单页交互的静态自证：保存按钮、历史容器、无障碍 label 都在。

    没有 headless 浏览器（会引入 pip 依赖），所以只做 DOM 契约的静态断言：
    `app.js` 拿这几个 id 做 `$()`，id 掉了整页 JS 会在启动时炸。
    """
    _状态, 页, _ = _请求(服务, 'GET', '/')
    正文 = 页.decode('utf-8')
    for id in ('btn-save', 'hist', 'hist-list', 'hist-hint'):
        assert 'id="%s"' % id in 正文, '单页缺少 #%s' % id
    # 无障碍：侧栏有可读 label，列表有 role
    assert 'aria-label="已保存方案列表"' in 正文
    assert 'role="list"' in 正文

    _状态, 脚本, _ = _请求(服务, 'GET', '/app.js')
    js = 脚本.decode('utf-8')
    for 端点 in ('/api/方案/存', '/api/方案/列', '/api/方案/'):
        assert 端点 in js, 'app.js 没接 %s' % 端点
    # 删除走 DELETE 而不是 POST
    assert "method: 'DELETE'" in js


def test_方案_体积上限断言():
    """常量在模块上——单条 64 KB，总量 4 MB，条数 200。"""
    assert server.MAX_PLAN_BYTES == 64 * 1024
    assert server.MAX_STORE_BYTES == 4 * 1024 * 1024
    assert server.MAX_PLAN_COUNT == 200


# ---- 单页 gzip 体积上限 -------------------------------------------------

def test_单页gzip体积不超18KB():
    """单页（`index.html` + `app.js`）gzip 总体积 ≤18 KB。

    这条自动化断言保障「零框架、无 CDN」的品牌主张：一旦有人偷偷引了 React
    或 Tailwind，gzip 立刻超限。上限从 v0.15.0 的 15 KB 提高到 18 KB，
    为 W31 历史侧栏 + 保存按钮留了交互额度。
    """
    import gzip
    根 = server.static_root()
    总 = 0
    for 名 in ('index.html', 'app.js'):
        路径 = os.path.join(根, 名)
        if not os.path.isfile(路径):
            pytest.skip('%s 不存在' % 名)
        with open(路径, 'rb') as f:
            原 = f.read()
        压 = gzip.compress(原, compresslevel=9)
        总 += len(压)
    上限 = 18 * 1024
    assert 总 <= 上限, '单页 gzip 总体积 %d 字节，超过上限 %d 字节' % (总, 上限)


# ---- 方案原地更新（W46）--------------------------------------------------

def test_方案_取_带版本标记(服务, 存档目录):
    """W46：GET /api/方案/<id> 响应必须多一个「版本」派生字段，供后续 PUT 用。"""
    状态, 存 = _存方案(服务, 标题='版本测试')
    assert 状态 == 200
    状态, 取 = _JSON(服务, 'GET', '/api/方案/' + 存['id'])
    assert 状态 == 200
    assert '版本' in 取 and isinstance(取['版本'], str) and len(取['版本']) == 16


def test_方案_更新_成功且版本推进(服务, 存档目录):
    """PUT 正例：带正确期望版本，更新成功，回新版本 + 新时间戳。"""
    状态, 存 = _存方案(服务, 标题='初版')
    _状态, 取 = _JSON(服务, 'GET', '/api/方案/' + 存['id'])
    旧版本 = 取['版本']
    新方案 = dict(_方案求和)
    新方案 = json.loads(json.dumps(新方案))
    新方案['需求'] = '把一批数求和并加倍'
    状态, 更 = _JSON(服务, 'PUT', '/api/方案/' + 存['id'],
                    {'方案': 新方案, '期望版本': 旧版本, '标题': '改版'})
    assert 状态 == 200, 更
    assert 更['id'] == 存['id']
    assert 更['标题'] == '改版'
    assert 更['版本'] != 旧版本
    # 二次 GET 拿到的方案确实是新内容
    _状态, 取2 = _JSON(服务, 'GET', '/api/方案/' + 存['id'])
    assert 取2['方案']['需求'] == '把一批数求和并加倍'
    assert 取2['版本'] == 更['版本']


def test_方案_更新_版本冲突_409(服务, 存档目录):
    """乐观锁：过期的期望版本必须回 409 且不动存档。"""
    状态, 存 = _存方案(服务, 标题='冲突测试')
    _状态, 取1 = _JSON(服务, 'GET', '/api/方案/' + 存['id'])
    # 别处先改一次（第一个 PUT 会成功）
    状态, _更1 = _JSON(服务, 'PUT', '/api/方案/' + 存['id'],
                     {'方案': _方案求和, '期望版本': 取1['版本'], '标题': '别人改的'})
    assert 状态 == 200
    # 我们拿着旧版本再改 → 409
    状态, 冲 = _JSON(服务, 'PUT', '/api/方案/' + 存['id'],
                    {'方案': _方案求和, '期望版本': 取1['版本'], '标题': '我的改动'})
    assert 状态 == 409
    assert '已被别处修改' in 冲['错误']
    # 存档保持「别人改的」而不是「我的改动」
    _状态, 取2 = _JSON(服务, 'GET', '/api/方案/' + 存['id'])
    assert 取2['标题'] == '别人改的'


def test_方案_更新_缺期望版本_400(服务, 存档目录):
    """静默覆盖是这个端点唯一不能犯的错——缺期望版本直接拒。"""
    状态, 存 = _存方案(服务)
    状态, 错 = _JSON(服务, 'PUT', '/api/方案/' + 存['id'], {'方案': _方案求和})
    assert 状态 == 400
    assert '期望版本' in 错['错误']


def test_方案_更新_不存在的id_404(服务, 存档目录):
    """PUT 不创建新存档——id 不存在回 404，避免调用方自造 id 塞文件。"""
    状态, 错 = _JSON(服务, 'PUT', '/api/方案/aabbccdd12345678',
                   {'方案': _方案求和, '期望版本': 'x' * 16})
    assert 状态 == 404


@pytest.mark.parametrize('坏id', [
    '../../etc/passwd',
    '..%2fetc%2fpasswd',
    '/etc/passwd',
    'ABCDEF12',           # 大写不合法
    'ab',                 # 太短
    'AB' * 40,            # 太长
])
def test_方案_更新_穿越攻击被拒(服务, 存档目录, 坏id):
    """W31 安全基线在 PUT 上一字不放松：白名单外的 id 无论正文如何都拒。"""
    状态, 错 = _JSON(服务, 'PUT', '/api/方案/' + 坏id,
                   {'方案': _方案求和, '期望版本': 'x' * 16})
    assert 状态 in (400, 404)


def test_方案_更新_方案坏schema_400(服务, 存档目录):
    """PUT 走同一份 schema.ensure_plan，坏方案不会靠 PUT 蒙混过关。"""
    状态, 存 = _存方案(服务)
    _状态, 取 = _JSON(服务, 'GET', '/api/方案/' + 存['id'])
    状态, 错 = _JSON(服务, 'PUT', '/api/方案/' + 存['id'],
                   {'方案': {'不合法': True}, '期望版本': 取['版本']})
    assert 状态 == 400

