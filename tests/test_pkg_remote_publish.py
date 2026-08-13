# -*- coding: utf-8 -*-
"""v0.21.0 · W92 · 客户端远程发布（ADR-35 §2.8）。

覆盖：
  1. `_build_publish_payload` 产出的报文字段齐全、归档可解压、签名可验
  2. `HttpBackend.publish_package` 对 200 / 401 / 403 / 409 / 413 / 429 的映射
  3. `publish()` 远程分支：未签名拒发（不发请求）、`--允许覆盖` 拒收
  4. `--演练` 对远程只做本地体检，不发请求
  5. `write_text` / `write_bytes` / `remove` 仍抛 `UnsupportedOperation`
     （ADR-35 §2.8：远程写的粒度是「发布一个包」而非「写一个文件」）

mock 服务端只实现 `POST /publish`，按预设脚本回状态码 —— 这一层测的是
**客户端**行为，真服务端逻辑由 `test_registry_server_auth.py` 与端到端测试覆盖。
"""

import base64
import io
import json
import os
import sys
import tarfile
import threading
import http.server

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg import backend as B                              # noqa: E402
from jikuai.pkg import keys                                       # noqa: E402
from jikuai.pkg import registry                                   # noqa: E402
from jikuai.pkg import trust                                      # noqa: E402
from jikuai.pkg import _ed25519 as ed                             # noqa: E402
from jikuai.pkg.manifest import MANIFEST_NAME, load_manifest      # noqa: E402


# ---------------------------------------------------------------------------
# mock 发布服务端（只管 POST /publish，按脚本回码）
# ---------------------------------------------------------------------------

class _发布服务:
    """按预设脚本回码的最小发布端点。`记录` 里存收到的报文，供断言。"""

    def __init__(self, 状态码=200, 错误='', 响应=None, token=None):
        self.状态码 = 状态码
        self.错误 = 错误
        self.响应 = 响应
        self.token = token
        self.记录 = []
        持有 = self

        class 处理器(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                if self.path != B.PUBLISH_PATH:
                    self.send_error(404, 'not found')
                    return
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length)
                try:
                    持有.记录.append(json.loads(raw.decode('utf-8')))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    持有.记录.append(None)
                持有.记录头 = dict(self.headers)
                if 持有.状态码 == 200:
                    body = json.dumps(
                        持有.响应 or {'结果': '已发布'},
                        ensure_ascii=False).encode('utf-8')
                    self.send_response(200)
                else:
                    body = json.dumps(
                        {'错误': 持有.错误}, ensure_ascii=False).encode('utf-8')
                    self.send_response(持有.状态码)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), 处理器)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)

    @property
    def base(self):
        host, port = self._server.server_address[:2]
        return 'http://%s:%d' % (host, port)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False


# ---------------------------------------------------------------------------
# 固定环境
# ---------------------------------------------------------------------------

@pytest.fixture
def 隔离环境(tmp_path, monkeypatch):
    monkeypatch.setenv(keys.KEY_ROOT_ENV, str(tmp_path / '密钥'))
    monkeypatch.setenv(trust.TRUST_ROOT_ENV, str(tmp_path / '信任'))
    monkeypatch.setenv(B.INSECURE_ENV, '1')
    monkeypatch.delenv(trust.TRUSTED_SIGNERS_ENV, raising=False)
    monkeypatch.delenv(B.TOKEN_ENV, raising=False)
    monkeypatch.delenv(B.TIMEOUT_ENV, raising=False)
    return tmp_path


def _造包(tmp_path, name='推包试用', version='1.0.0'):
    pkg = tmp_path / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / MANIFEST_NAME).write_text(json.dumps({
        '名称': name, '版本': version, '描述': 'W92 远程发布测试用',
        '入口': '主.jk', '极快版本': '>=0.21.0',
    }, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    (pkg / '主.jk').write_text('定义 你好():\n  打印("好")\n',
                               encoding='utf-8', newline='\n')
    (pkg / 'README.md').write_text('# 推包试用\n', encoding='utf-8')
    return str(pkg)


# ---------------------------------------------------------------------------
# 报文构造
# ---------------------------------------------------------------------------

def test_报文字段齐全且签名可验(隔离环境):
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    报文, 校验和, 签名, 文件数, _警告 = registry._build_publish_payload(
        manifest, '通用', '甲')

    assert 报文['协议'] == B.PUBLISH_PROTOCOL
    assert 报文['名称'] == '推包试用'
    assert 报文['版本'] == '1.0.0'
    assert 报文['分类'] == '通用'
    assert 报文['校验和'] == 校验和 and 校验和.startswith('sha256:')
    assert 报文['签名者'] == '甲'
    assert 报文['签名'] == 签名
    assert 文件数 >= 2

    # 签名对象是**校验和字符串**（ADR-33），用登记公钥验得过
    公钥 = keys.load_public_key('甲')
    assert ed.verify(公钥, 校验和.encode('utf-8'),
                     base64.b64decode(签名)) is True

    # 条目详情与索引分片同 schema
    条目 = 报文['条目']
    for 键 in ('名称', '版本', '描述', '入口', '分类', '校验和', '文件数',
               '依赖', '极快版本', '快照'):
        assert 键 in 条目, 键


def test_报文归档可解压且成员以版本号为根(隔离环境):
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    报文, _c, _s, _n, _w = registry._build_publish_payload(
        manifest, '通用', '甲')
    原始 = base64.b64decode(报文['归档'])
    with tarfile.open(fileobj=io.BytesIO(原始), mode='r:gz') as tf:
        名单 = [m.name.replace('\\', '/') for m in tf.getmembers()]
    assert '1.0.0' in 名单
    assert any(n.endswith('1.0.0/主.jk') for n in 名单)


# ---------------------------------------------------------------------------
# publish_package 的状态码映射
# ---------------------------------------------------------------------------

def test_推包成功回报文(隔离环境):
    with _发布服务(响应={'结果': '已发布', '名称': '甲包',
                        '版本': '1.0.0'}) as 服务:
        后端 = B.get_backend(服务.base)
        回应 = 后端.publish_package({'协议': 1, '名称': '甲包'})
    assert 回应['结果'] == '已发布'
    assert 服务.记录[0]['名称'] == '甲包'


def test_推包带token走Authorization头(隔离环境, monkeypatch):
    # token 必须是 latin-1 可编码的（HTTP 头限制），读端 W79 已有同款校验
    monkeypatch.setenv(B.TOKEN_ENV, 'tok-abc-123')
    with _发布服务() as 服务:
        B.get_backend(服务.base).publish_package({'协议': 1})
    assert 服务.记录头.get('Authorization') == 'Bearer tok-abc-123'


def test_推包token含非latin1字符提前拦住(隔离环境, monkeypatch):
    """中文 token 会在 urllib 内部炸 UnicodeEncodeError，提前转成可读中文错误。"""
    monkeypatch.setenv(B.TOKEN_ENV, '秘钥串中文')
    with _发布服务() as 服务:
        后端 = B.get_backend(服务.base)
        with pytest.raises(B.BackendError, match='latin-1'):
            后端.publish_package({'协议': 1})
        assert 服务.记录 == []


@pytest.mark.parametrize('码,片段', [
    (401, '鉴权失败'),
    (403, '被拒'),
    (409, '版本已存在'),
    (413, '包体过大'),
    (429, '过于频繁'),
    (500, '远程发布失败'),
])
def test_推包错误码映射为中文原因(隔离环境, 码, 片段):
    with _发布服务(状态码=码, 错误='服务端给的中文原因') as 服务:
        后端 = B.get_backend(服务.base)
        with pytest.raises(B.BackendError) as 抓:
            后端.publish_package({'协议': 1})
    消息 = str(抓.value)
    assert 片段 in 消息
    assert '服务端给的中文原因' in 消息


def test_远程后端逐文件写仍被拒(隔离环境):
    """ADR-35 §2.8：远程写的粒度是「发布一个包」，不是「写一个文件」。

    逐文件可写会让多客户端并发发布撕裂索引（读-改-写竞态），而客户端之间
    没有协调手段。这条断言防止后来者「顺手」把它实现了。
    """
    with _发布服务() as 服务:
        后端 = B.get_backend(服务.base)
        with pytest.raises(B.UnsupportedOperation):
            后端.write_text('索引.json', '{}')
        with pytest.raises(B.UnsupportedOperation):
            后端.write_bytes('包/甲/1.0.0.tar.gz', b'x')
        with pytest.raises(B.UnsupportedOperation):
            后端.remove('索引.json')


def test_本地后端不支持远程发布(tmp_path):
    后端 = B.get_backend(str(tmp_path))
    with pytest.raises(B.UnsupportedOperation):
        后端.publish_package({'协议': 1})


# ---------------------------------------------------------------------------
# publish() 的远程分支
# ---------------------------------------------------------------------------

def test_远程发布未签名直接拒且不发请求(隔离环境, monkeypatch):
    manifest = load_manifest(_造包(隔离环境))
    with _发布服务() as 服务:
        monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
        with pytest.raises(registry.RegistryError, match='必须签名'):
            registry.publish(manifest, dry_run=False, signer=None)
        assert 服务.记录 == []          # 客户端就拦住了，没白跑网络


def test_远程发布不接受允许覆盖(隔离环境, monkeypatch):
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    with _发布服务() as 服务:
        monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
        with pytest.raises(registry.RegistryError, match='不支持覆盖'):
            registry.publish(manifest, dry_run=False, signer='甲',
                             allow_overwrite=True)
        assert 服务.记录 == []


def test_远程演练不发请求(隔离环境, monkeypatch):
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    with _发布服务() as 服务:
        monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
        报告 = registry.publish(manifest, dry_run=True, signer='甲')
        assert 服务.记录 == []
    assert 报告.dry_run is True
    assert 报告.signer == '甲'
    assert 报告.signature
    assert 报告.target == 服务.base


def test_远程发布成功回报告(隔离环境, monkeypatch):
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    with _发布服务() as 服务:
        monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
        报告 = registry.publish(manifest, dry_run=False, signer='甲')
        推送的 = 服务.记录[0]
    assert 报告.dry_run is False
    assert 报告.name == '推包试用' and 报告.version == '1.0.0'
    assert 推送的['校验和'] == 报告.checksum
    assert 推送的['签名'] == 报告.signature
    assert 推送的['归档']            # 报文自带归档，服务端不用回头拉


def test_服务端回报校验和不一致则报错(隔离环境, monkeypatch):
    """服务端说它落的是另一个校验和 → 发布结果存疑，必须报出来而非静默成功。"""
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    with _发布服务(响应={'结果': '已发布', '校验和': 'sha256:' + '0' * 64}) as 服务:
        monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
        with pytest.raises(registry.RegistryError, match='不一致'):
            registry.publish(manifest, dry_run=False, signer='甲')


def test_远程发布错误转为RegistryError(隔离环境, monkeypatch):
    """`BackendError` 不该泄漏到 CLI —— CLI 只 catch `RegistryError`。"""
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    with _发布服务(状态码=403, 错误='越权：token 无权发布 推包试用') as 服务:
        monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
        with pytest.raises(registry.RegistryError, match='越权'):
            registry.publish(manifest, dry_run=False, signer='甲')


def test_本地发布不受影响(隔离环境, monkeypatch):
    """远程分支加进来后，本地发布行为必须逐字节不变（回归底线）。"""
    monkeypatch.setenv('JIKUAI_REGISTRY', str(隔离环境 / '注册表'))
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    报告 = registry.publish(manifest, dry_run=False, signer='甲')
    assert 报告.dry_run is False
    assert os.path.isdir(报告.target)
    版本, 条目 = registry.lookup_entry('推包试用', '1.0.0')
    assert 版本 == '1.0.0'
    assert 条目['校验和'] == 报告.checksum
    assert 条目['签名者'] == '甲'
