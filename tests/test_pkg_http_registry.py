# -*- coding: utf-8 -*-
"""v0.20.0 · W80 · 远程 HTTP 注册表端到端测试（ADR-34）。

覆盖：
  1. `HttpBackend` 基本读：索引/分片可读，缺失文件返回 None
  2. 明文 http:// 默认被拒，`JIKUAI_REGISTRY_INSECURE=1` 才放行
  3. 端到端：本地签名发布 → 静态托管注册表根 → `JIKUAI_REGISTRY=http://...`
     装包 → 验签通过 + TOFU pin 公钥（公钥也走 HTTP 拉）
  4. token 鉴权：服务端要 token，不带 → 鉴权失败；带对了 → 装成
  5. per-dependency override：`{"注册表": url}` 走指定注册表而非全局
  6. tar.gz 安全解压：含 `..` / 绝对路径 / 链接成员的归档被拒
  7. 远端归档被篡改 → 完整性校验失败（M19 的地板检查对 HTTP 同样生效）
  8. `Dependency` 的 registry_url round-trip 无损

mock 服务端用标准库 `http.server`，只做静态文件托管 + 可选 Bearer 校验——
这正是 ADR-34 §2.1 所说的「远程注册表就是本地目录的静态镜像」。

风格对齐 `test_pkg_signing.py`：模块级函数 + pytest fixture。
"""

import http.server
import io
import json
import os
import sys
import tarfile
import threading
import urllib.parse

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg import backend as B                            # noqa: E402
from jikuai.pkg import installer as I                          # noqa: E402
from jikuai.pkg import keys                                     # noqa: E402
from jikuai.pkg import registry                                 # noqa: E402
from jikuai.pkg import sources                                  # noqa: E402
from jikuai.pkg import trust                                    # noqa: E402
from jikuai.pkg.manifest import (                               # noqa: E402
    MANIFEST_NAME, Dependency, load_manifest,
)


# ---------------------------------------------------------------------------
# mock 静态注册表服务端
# ---------------------------------------------------------------------------

class _静态注册表服务:
    """把一个本地目录用 HTTP 暴露出来；`token` 非空时校验 Bearer 头。"""

    def __init__(self, root, token=None):
        self.root = os.path.abspath(root)
        self.token = token
        持有 = self

        class 处理器(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):        # 静音：不污染 pytest 输出
                pass

            def do_GET(self):
                if 持有.token:
                    auth = self.headers.get('Authorization') or ''
                    if auth != 'Bearer ' + 持有.token:
                        self.send_error(401, 'unauthorized')
                        return
                rel = urllib.parse.unquote(self.path.lstrip('/'))
                if '..' in rel.split('/'):
                    self.send_error(400, 'bad path')
                    return
                full = os.path.join(持有.root, *rel.split('/'))
                if not os.path.isfile(full):
                    self.send_error(404, 'not found')
                    return
                with open(full, 'rb') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

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
    """隔离密钥根 / 注册表根 / 信任库，并放行明文 http（mock 服务端是 http）。"""
    monkeypatch.setenv(keys.KEY_ROOT_ENV, str(tmp_path / '密钥'))
    monkeypatch.setenv('JIKUAI_REGISTRY', str(tmp_path / '注册表'))
    monkeypatch.setenv(trust.TRUST_ROOT_ENV, str(tmp_path / '信任'))
    monkeypatch.setenv(B.INSECURE_ENV, '1')
    monkeypatch.delenv(trust.TRUSTED_SIGNERS_ENV, raising=False)
    monkeypatch.delenv(B.TOKEN_ENV, raising=False)
    monkeypatch.delenv(B.TIMEOUT_ENV, raising=False)
    return tmp_path


def _造包(tmp_path, name='远端试包', version='0.2.0'):
    pkg = tmp_path / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / MANIFEST_NAME).write_text(json.dumps({
        '名称': name, '版本': version, '描述': 'W80 远程注册表测试用',
        '入口': '主.jk', '极快版本': '>=0.19.0',
    }, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    (pkg / '主.jk').write_text('定义 你好():\n  打印("好")\n',
                               encoding='utf-8', newline='\n')
    (pkg / 'README.md').write_text('# 远端试包\n', encoding='utf-8')
    return str(pkg)


def _造宿主(tmp_path, 依赖名='远端试包', 规格='*', 名='宿主'):
    proj = tmp_path / 名
    proj.mkdir(parents=True, exist_ok=True)
    (proj / MANIFEST_NAME).write_text(json.dumps({
        '名称': 名, '版本': '0.1.0', '描述': 'W80 远程装包宿主',
        '入口': 'main.jk', '依赖': {依赖名: 规格},
    }, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    (proj / 'main.jk').write_text('打印("好")\n', encoding='utf-8',
                                  newline='\n')
    return str(proj)


def _发布带签名(tmp_path, 别名='甲'):
    keys.generate_keypair(别名)
    return registry.publish(load_manifest(_造包(tmp_path)),
                            dry_run=False, signer=别名)


# ---------------------------------------------------------------------------
# HttpBackend 基本读
# ---------------------------------------------------------------------------

def test_远程后端读索引与分片(隔离环境):
    _发布带签名(隔离环境)
    根 = registry.registry_root()
    with _静态注册表服务(根) as 服务:
        后端 = B.get_backend(服务.base)
        assert 后端.remote is True
        索引 = 后端.read_json(registry.INDEX_NAME)
        assert '远端试包' in (索引.get('索引') or {})
        分片 = 后端.read_json('分类/通用.json')
        assert '0.2.0' in 分片['远端试包']
        # 缺失文件与本地 `os.path.isfile` 为假同义：返回 None 而非抛
        assert 后端.read_text('分类/不存在.json') is None
        assert 后端.exists(registry.INDEX_NAME) is True


def test_远程后端拒绝写(隔离环境):
    with _静态注册表服务(str(隔离环境)) as 服务:
        后端 = B.get_backend(服务.base)
        with pytest.raises(B.UnsupportedOperation, match='M21'):
            后端.write_text('索引.json', '{}')


def test_明文http默认被拒(隔离环境, monkeypatch):
    monkeypatch.delenv(B.INSECURE_ENV, raising=False)
    with pytest.raises(B.BackendError, match=B.INSECURE_ENV):
        B.get_backend('http://127.0.0.1:1/')
    # https 不受这条限制（不发请求，只构造）
    assert B.get_backend('https://例子.com/注册表').remote is True


def test_相对路径逃逸被拒(隔离环境):
    with _静态注册表服务(str(隔离环境)) as 服务:
        后端 = B.get_backend(服务.base)
        with pytest.raises(B.BackendError, match=r'\.\.'):
            后端.read_text('分类/../../机密')
    本地 = B.get_backend(str(隔离环境))
    with pytest.raises(B.BackendError, match=r'\.\.'):
        本地.read_text('包/../../机密')


# ---------------------------------------------------------------------------
# 端到端：远程装签名包
# ---------------------------------------------------------------------------

def test_远程装签名包_验签通过并pin公钥(隔离环境, monkeypatch):
    _发布带签名(隔离环境)
    本地根 = registry.registry_root()
    proj = _造宿主(隔离环境)

    with _静态注册表服务(本地根) as 服务:
        monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
        报告 = I.install(load_manifest(proj))

    assert 报告.total == 1
    assert 报告.warnings == []          # 签过名不该有未签名告警
    assert os.path.isdir(os.path.join(proj, I.PACKAGES_DIR, '远端试包'))
    # 公钥经 HTTP 拉到并 pin 进信任库
    assert os.path.isfile(os.path.join(trust.trust_root(), '甲.公钥'))


def test_远程装未签名包_告警但放行(隔离环境, monkeypatch):
    registry.publish(load_manifest(_造包(隔离环境)), dry_run=False)
    本地根 = registry.registry_root()
    proj = _造宿主(隔离环境)

    with _静态注册表服务(本地根) as 服务:
        monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
        报告 = I.install(load_manifest(proj))

    assert 报告.total == 1
    assert len(报告.warnings) == 1
    assert '未签名' in 报告.warnings[0]


def test_远端归档被篡改_完整性校验失败(隔离环境, monkeypatch):
    _发布带签名(隔离环境)
    本地根 = registry.registry_root()
    proj = _造宿主(隔离环境)

    # 重打一个内容不同的归档冒充原件：索引里的校验和不变 → 装包端必须拒
    归档 = os.path.join(本地根, *registry.archive_rel('远端试包', '0.2.0').split('/'))
    篡改源 = 隔离环境 / '篡改'
    (篡改源 / '0.2.0').mkdir(parents=True)
    (篡改源 / '0.2.0' / MANIFEST_NAME).write_text(json.dumps({
        '名称': '远端试包', '版本': '0.2.0', '描述': '被改过',
        '入口': '主.jk',
    }, ensure_ascii=False), encoding='utf-8', newline='\n')
    (篡改源 / '0.2.0' / '主.jk').write_text('打印("后门")\n',
                                            encoding='utf-8', newline='\n')
    with tarfile.open(归档, 'w:gz') as tf:
        tf.add(str(篡改源 / '0.2.0'), arcname='0.2.0')

    with _静态注册表服务(本地根) as 服务:
        monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
        with pytest.raises(I.InstallError, match='完整性校验失败'):
            I.install(load_manifest(proj))


def test_远端缺归档_报明确错误(隔离环境, monkeypatch):
    _发布带签名(隔离环境)
    本地根 = registry.registry_root()
    proj = _造宿主(隔离环境)
    os.remove(os.path.join(本地根,
                           *registry.archive_rel('远端试包', '0.2.0').split('/')))

    with _静态注册表服务(本地根) as 服务:
        monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
        with pytest.raises(Exception, match='快照归档'):
            I.install(load_manifest(proj))


# ---------------------------------------------------------------------------
# token 鉴权
# ---------------------------------------------------------------------------

def test_私有注册表_无token被拒(隔离环境):
    _发布带签名(隔离环境)
    with _静态注册表服务(registry.registry_root(), token='secret123') as 服务:
        后端 = B.get_backend(服务.base)
        with pytest.raises(B.BackendError, match='鉴权失败'):
            后端.read_json(registry.INDEX_NAME)


def test_非latin1的token被提前拦下(隔离环境, monkeypatch):
    """HTTP 头只能是 latin-1；中文 token 要报可读错误而不是漏 urllib 异常。"""
    with _静态注册表服务(str(隔离环境)) as 服务:
        monkeypatch.setenv(B.TOKEN_ENV, '秘钥123')
        后端 = B.get_backend(服务.base)
        with pytest.raises(B.BackendError, match='latin-1'):
            后端.read_text('索引.json')


def test_私有注册表_环境变量token放行(隔离环境, monkeypatch):
    _发布带签名(隔离环境)
    本地根 = registry.registry_root()
    proj = _造宿主(隔离环境)

    with _静态注册表服务(本地根, token='secret123') as 服务:
        monkeypatch.setenv(B.TOKEN_ENV, 'secret123')
        monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
        报告 = I.install(load_manifest(proj))

    assert 报告.total == 1


def test_凭证文件按最长前缀匹配(隔离环境, monkeypatch):
    """`~/.jikuai/凭证.json` 里更具体的 URL 前缀应胜出。"""
    家 = 隔离环境 / '家'
    (家 / '.jikuai').mkdir(parents=True)
    (家 / '.jikuai' / B.CREDENTIALS_NAME).write_text(json.dumps({
        'https://reg.example.com': '泛用',
        'https://reg.example.com/内部': '专用',
    }, ensure_ascii=False), encoding='utf-8', newline='\n')
    monkeypatch.setenv('USERPROFILE', str(家))
    monkeypatch.setenv('HOME', str(家))

    assert B.resolve_token('https://reg.example.com/内部') == '专用'
    assert B.resolve_token('https://reg.example.com/公共') == '泛用'
    assert B.resolve_token('https://别处.com') == ''
    # 环境变量优先于凭证文件
    monkeypatch.setenv(B.TOKEN_ENV, '来自环境')
    assert B.resolve_token('https://reg.example.com/内部') == '来自环境'


# ---------------------------------------------------------------------------
# per-dependency 注册表覆盖
# ---------------------------------------------------------------------------

def test_依赖级注册表覆盖_走指定源而非全局(隔离环境, monkeypatch):
    _发布带签名(隔离环境)
    本地根 = registry.registry_root()

    with _静态注册表服务(本地根) as 服务:
        proj = _造宿主(隔离环境, 规格={'注册表': 服务.base, '版本': '^0.2.0'})
        # 全局指向一个空注册表：只有 override 生效才装得上
        monkeypatch.setenv('JIKUAI_REGISTRY', str(隔离环境 / '空注册表'))
        报告 = I.install(load_manifest(proj))

    assert 报告.total == 1
    assert os.path.isdir(os.path.join(proj, I.PACKAGES_DIR, '远端试包'))


def test_依赖规格round_trip无损():
    # 显式 override → dict 形态
    d = Dependency.from_spec('丙', {'注册表': 'https://例子.com', '版本': '^1.2.0'})
    assert d.kind == '注册表'
    assert d.registry_url == 'https://例子.com'
    assert d.to_spec() == {'注册表': 'https://例子.com', '版本': '^1.2.0'}
    # 纯字符串依赖不能被改写成 dict（否则既有 包.json 全是 diff 噪声）
    assert Dependency.from_spec('丁', '^1.0.0').to_spec() == '^1.0.0'
    # 路径 / 仓库形态不受影响
    assert Dependency.from_spec('戊', {'路径': '../戊'}).to_spec() == {'路径': '../戊'}


# ---------------------------------------------------------------------------
# tar.gz 安全解压
# ---------------------------------------------------------------------------

def _造归档(成员名, 内容=b'x', 类型=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        info = tarfile.TarInfo(成员名)
        if 类型 is not None:
            info.type = 类型
            info.linkname = '目标'
            tf.addfile(info)
        else:
            info.size = len(内容)
            tf.addfile(info, io.BytesIO(内容))
    return buf.getvalue()


def test_解压拒绝父目录逃逸(tmp_path):
    with pytest.raises(sources.SourceError, match='越出解压目录'):
        sources._safe_extract_targz(_造归档('../逃逸.jk'), str(tmp_path))


def test_解压拒绝绝对路径(tmp_path):
    with pytest.raises(sources.SourceError, match='绝对路径'):
        sources._safe_extract_targz(_造归档('/etc/坏文件'), str(tmp_path))


def test_解压拒绝链接成员(tmp_path):
    with pytest.raises(sources.SourceError, match='链接成员'):
        sources._safe_extract_targz(
            _造归档('软链', 类型=tarfile.SYMTYPE), str(tmp_path))


def test_解压正常归档(tmp_path):
    内容 = '打印("好")\n'.encode('utf-8')
    sources._safe_extract_targz(_造归档('包/主.jk', 内容), str(tmp_path))
    assert (tmp_path / '包' / '主.jk').read_bytes() == 内容


# ---------------------------------------------------------------------------
# 本地行为不回退
# ---------------------------------------------------------------------------

def test_本地注册表仍按目录返回_不打包解压(隔离环境):
    """W78 引入后端抽象后，本地路径必须与 v0.19.0 逐字节同行为。"""
    _发布带签名(隔离环境)
    版本, 快照 = registry.lookup('远端试包', '^0.2.0')
    assert 版本 == '0.2.0'
    assert os.path.isdir(快照)
    assert os.path.isfile(os.path.join(快照, MANIFEST_NAME))
    # 归档与目录并存：目录给本地读，归档给远程分发
    assert os.path.isfile(快照 + registry.ARCHIVE_SUFFIX)


def test_远程根不允许发布(隔离环境):
    with pytest.raises(registry.RegistryError, match='远程注册表'):
        registry.publish(load_manifest(_造包(隔离环境)),
                         root='https://例子.com/注册表', dry_run=False)
