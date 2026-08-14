# -*- coding: utf-8 -*-
"""v0.22.0 · W102 · 注册表服务端部署约束（ADR-36 §2.1 TLS / §2.2 单写者）。

覆盖三块，都是「防误配置」而不是功能：

  1. `--要求TLS转发`：`POST /publish` 缺 `X-Forwarded-Proto: https` 即 403，
     且这道闸门在**读 body 之前**——误配置的部署不该先把报文明文灌进来再拒。
  2. `.发布锁`：`O_CREAT|O_EXCL` 抢锁，已占用即拒绝启动；`--强制解锁` 清陈旧锁；
     正常退出删锁。**不自动判活**（pid 会复用）。
  3. 危险绑定横幅：非回环绑定且未要求 TLS 时告警，但**不拒绝启动**。

`tools/registry-server/` 带连字符不能常规 import，按文件路径加载（同
`test_registry_server_auth.py`）。server.py 自己有 `except ImportError` 分支
把 `_HERE` 挂到 sys.path 再平铺 import auth/audit，所以按路径加载是可行的。
"""

import base64
import hashlib
import importlib.util
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _load(名, 文件名):
    path = os.path.join(_REPO, 'tools', 'registry-server', 文件名)
    spec = importlib.util.spec_from_file_location(名, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _load('_reg_server_deploy', 'server.py')
#: **必须**复用 server.py 自己加载的那份 auth 模块，不能再 `_load` 一份。
#: `build_server` 用 `isinstance(授权, _auth_mod.AuthConfig)` 分派，两份独立
#: 加载的模块里 `AuthConfig` 是两个不同的类，isinstance 会假，配置对象就被当
#: 成文件路径丢给 `load_auth_config`，报一个莫名的 TypeError。
auth = S._auth_mod

from jikuai.pkg import _ed25519 as _ed                          # noqa: E402


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _授权配置(tmp_path, token='甲的token', signer='甲'):
    令牌 = {
        hashlib.sha256(token.encode('utf-8')).hexdigest(): {
            '签名者': signer,
            '公钥': base64.b64encode(
                _ed.public_key_from_seed(b'\x01' * 32)).decode('ascii'),
            '可发布': ['甲包'],
            '每小时次数': 20,
            '单包字节': 1 << 20,
        }
    }
    p = tmp_path / '授权.json'
    p.write_text(json.dumps({'协议': 1, '令牌': 令牌}, ensure_ascii=False),
                 encoding='utf-8', newline='\n')
    return auth.load_auth_config(str(p))


class _跑起来:
    """把 build_server 出来的实例跑在后台线程里，退出时关掉。"""

    def __init__(self, srv):
        self.srv = srv
        self._t = threading.Thread(target=srv.serve_forever, daemon=True)

    def __enter__(self):
        self._t.start()
        host, port = self.srv.server_address[:2]
        self.base = 'http://%s:%d' % (host, port)
        return self

    def __exit__(self, *_exc):
        self.srv.shutdown()
        self.srv.server_close()
        self._t.join(timeout=5)
        return False


def _post(base, 体, 头=None):
    """发一个 POST /publish，返回 (状态码, 响应体dict)。"""
    数据 = json.dumps(体, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(base + S.PUBLISH_PATH, data=数据,
                                 method='POST')
    req.add_header('Content-Type', 'application/json')
    for k, v in (头 or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        原文 = e.read().decode('utf-8')
        try:
            return e.code, json.loads(原文)
        except json.JSONDecodeError:
            return e.code, {'错误': 原文}


#: 一个字段齐全但注定过不了认证的报文。TLS 闸门在认证之前，所以用它就能
#: 区分「被 TLS 闸门拦下（403）」与「过了闸门、倒在后面（401）」。
_报文 = {
    '协议': 1, '名称': '甲包', '版本': '0.1.0',
    '校验和': 'sha256:' + '0' * 64, '签名者': '甲',
    '签名': base64.b64encode(b'\x00' * 64).decode('ascii'),
    '归档': base64.b64encode(b'not-a-real-targz').decode('ascii'),
}


# ---------------------------------------------------------------------------
# 1. TLS 转发闸门（ADR-36 §2.1）
# ---------------------------------------------------------------------------

def test_要求TLS_缺头即403(tmp_path):
    srv = S.build_server(注册表=str(tmp_path / '注册表'),
                         授权=_授权配置(tmp_path),
                         host='127.0.0.1', port=0, 要求TLS=True)
    with _跑起来(srv) as s:
        码, 体 = _post(s.base, _报文)
    assert 码 == 403
    assert 'X-Forwarded-Proto' in 体['错误']
    assert '要求TLS转发' in 体['错误']


def test_要求TLS_带头则放过闸门(tmp_path):
    """带对了头就该继续走流水线——这里必然倒在认证（401），不是 403。"""
    srv = S.build_server(注册表=str(tmp_path / '注册表'),
                         授权=_授权配置(tmp_path),
                         host='127.0.0.1', port=0, 要求TLS=True)
    with _跑起来(srv) as s:
        码, _体 = _post(s.base, _报文,
                        {'X-Forwarded-Proto': 'https'})
    assert 码 == 401


@pytest.mark.parametrize('头值', ['HTTPS', ' https ', 'Https'])
def test_要求TLS_头值大小写与空白不敏感(tmp_path, 头值):
    srv = S.build_server(注册表=str(tmp_path / '注册表'),
                         授权=_授权配置(tmp_path),
                         host='127.0.0.1', port=0, 要求TLS=True)
    with _跑起来(srv) as s:
        码, _体 = _post(s.base, _报文, {'X-Forwarded-Proto': 头值})
    assert 码 == 401                      # 过了 TLS 闸门


@pytest.mark.parametrize('头值', ['http', 'ws', ''])
def test_要求TLS_非https一律403(tmp_path, 头值):
    srv = S.build_server(注册表=str(tmp_path / '注册表'),
                         授权=_授权配置(tmp_path),
                         host='127.0.0.1', port=0, 要求TLS=True)
    with _跑起来(srv) as s:
        码, _体 = _post(s.base, _报文, {'X-Forwarded-Proto': 头值})
    assert 码 == 403


def test_默认不要求TLS(tmp_path):
    """默认关：不带头也照样进流水线（倒在认证）。"""
    srv = S.build_server(注册表=str(tmp_path / '注册表'),
                         授权=_授权配置(tmp_path),
                         host='127.0.0.1', port=0)
    assert srv.要求TLS is False
    with _跑起来(srv) as s:
        码, _体 = _post(s.base, _报文)
    assert 码 == 401


def test_要求TLS_闸门在读body之前(tmp_path):
    """缺 Content-Length 也该先撞 403 而不是 411——顺序反了就说明闸门放晚了。

    urllib 一定会带 Content-Length，所以这里直接对 handler 的行为取巧：
    发一个超过上限的体量。若闸门在体量检查之前，得到 403；反之 413。
    """
    srv = S.build_server(注册表=str(tmp_path / '注册表'),
                         授权=_授权配置(tmp_path),
                         host='127.0.0.1', port=0, 要求TLS=True,
                         max_body=16)
    with _跑起来(srv) as s:
        码, _体 = _post(s.base, _报文)
    assert 码 == 403                      # 不是 413


def test_GET不受TLS闸门影响(tmp_path):
    """读端是静态镜像，本该由反代/CDN 直接吐；闸门只管写端点。"""
    根 = tmp_path / '注册表'
    根.mkdir()
    (根 / '索引.json').write_text('{"索引": {}}', encoding='utf-8')
    srv = S.build_server(注册表=str(根), 授权=_授权配置(tmp_path),
                         host='127.0.0.1', port=0, 要求TLS=True)
    with _跑起来(srv) as s:
        # 请求行必须是 ASCII，中文路径得先 percent-encode（服务端会 unquote）
        url = s.base + '/' + urllib.parse.quote('索引.json')
        with urllib.request.urlopen(url, timeout=10) as r:
            assert r.status == 200
            assert json.loads(r.read().decode('utf-8')) == {'索引': {}}


# ---------------------------------------------------------------------------
# 2. 单写者锁文件（ADR-36 §2.2）
# ---------------------------------------------------------------------------

def test_抢锁成功_写入pid(tmp_path):
    根 = str(tmp_path / '注册表')
    path = S.抢发布锁(根)
    assert path == S.锁路径(根)
    assert os.path.isfile(path)
    with open(path, encoding='utf-8') as f:
        记 = json.load(f)
    assert 记['pid'] == os.getpid()
    assert 记['注册表根'] == os.path.abspath(根)
    assert 记['启动时刻'].endswith('Z')
    assert S.释放发布锁(path) is True


def test_第二次抢锁被拒且说明可操作(tmp_path):
    根 = str(tmp_path / '注册表')
    S.抢发布锁(根)
    with pytest.raises(S.锁冲突) as ei:
        S.抢发布锁(根)
    说明 = str(ei.value)
    assert '单写者' in 说明
    assert str(os.getpid()) in 说明           # 告诉运维是谁持着
    assert '--强制解锁' in 说明               # 告诉运维怎么办
    S.释放发布锁(S.锁路径(根))


def test_强制解锁能抢到(tmp_path):
    根 = str(tmp_path / '注册表')
    S.抢发布锁(根)
    path = S.抢发布锁(根, 强制解锁=True)      # 不抛
    with open(path, encoding='utf-8') as f:
        assert json.load(f)['pid'] == os.getpid()
    S.释放发布锁(path)


def test_强制解锁_没有锁也不报错(tmp_path):
    根 = str(tmp_path / '注册表')
    path = S.抢发布锁(根, 强制解锁=True)
    assert os.path.isfile(path)
    S.释放发布锁(path)


def test_释放锁幂等(tmp_path):
    根 = str(tmp_path / '注册表')
    path = S.抢发布锁(根)
    assert S.释放发布锁(path) is True
    assert S.释放发布锁(path) is False        # 已经没了，不抛


def test_锁文件内容坏也能给出说明(tmp_path):
    """崩在写锁半路留下的半截文件：不能因为读不出 pid 就哑掉。"""
    根 = str(tmp_path / '注册表')
    os.makedirs(根, exist_ok=True)
    with open(S.锁路径(根), 'w', encoding='utf-8') as f:
        f.write('{ 半截')
    with pytest.raises(S.锁冲突) as ei:
        S.抢发布锁(根)
    assert '读不出' in str(ei.value)
    assert '--强制解锁' in str(ei.value)


def test_不同注册表根互不影响(tmp_path):
    甲 = str(tmp_path / '甲注册表')
    乙 = str(tmp_path / '乙注册表')
    p甲 = S.抢发布锁(甲)
    p乙 = S.抢发布锁(乙)                      # 不抛：锁的粒度是注册表根
    assert p甲 != p乙
    S.释放发布锁(p甲)
    S.释放发布锁(p乙)


def test_build_server不抢锁(tmp_path):
    """锁属于进程生命周期，由 main 管；构造函数不该把部署约束带进来。"""
    根 = tmp_path / '注册表'
    srv = S.build_server(注册表=str(根), 授权=_授权配置(tmp_path),
                         host='127.0.0.1', port=0)
    try:
        assert not os.path.exists(S.锁路径(str(根)))
    finally:
        srv.server_close()


def test_main遇锁冲突退3(tmp_path, capsys):
    根 = str(tmp_path / '注册表')
    授权路径 = str(tmp_path / '授权.json')
    _授权配置(tmp_path)                        # 顺带把 授权.json 写出来
    S.抢发布锁(根)
    try:
        码 = S.main(['--注册表', 根, '--授权', 授权路径,
                     '--监听', '127.0.0.1', '--端口', '0'])
        assert 码 == 3
        err = capsys.readouterr().err
        assert '无法启动' in err
        assert '--强制解锁' in err
    finally:
        S.释放发布锁(S.锁路径(根))


def test_main授权配置坏退2且不留锁(tmp_path, capsys):
    """退出码归位之外还有一条：失败路径也必须把锁删掉，否则下次得手工解锁。"""
    根 = str(tmp_path / '注册表')
    坏授权 = tmp_path / '坏授权.json'
    坏授权.write_text(json.dumps({'协议': 99, '令牌': {}}, ensure_ascii=False),
                      encoding='utf-8', newline='\n')
    码 = S.main(['--注册表', 根, '--授权', str(坏授权),
                 '--监听', '127.0.0.1', '--端口', '0'])
    assert 码 == 2
    assert '授权配置错误' in capsys.readouterr().err
    assert not os.path.exists(S.锁路径(根))


# ---------------------------------------------------------------------------
# 3. 危险绑定横幅（ADR-36 §2.1(b)）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('host', ['127.0.0.1', '::1', 'localhost',
                                  '127.0.0.53', 'LOCALHOST', '[::1]'])
def test_回环绑定不告警(host):
    assert S.危险绑定横幅(host, False) == ''


@pytest.mark.parametrize('host', ['0.0.0.0', '::', '10.0.0.5',
                                  '192.168.1.5', ''])
def test_非回环且未要求TLS_告警(host):
    横幅 = S.危险绑定横幅(host, False)
    assert 横幅
    assert '警告' in 横幅
    assert 'X-Forwarded-Proto' in 横幅
    assert '远程注册表部署.md' in 横幅
    assert '不拒绝启动' in 横幅            # 语义必须写在横幅里


@pytest.mark.parametrize('host', ['0.0.0.0', '10.0.0.5'])
def test_非回环但要求TLS_不告警(host):
    assert S.危险绑定横幅(host, True) == ''
