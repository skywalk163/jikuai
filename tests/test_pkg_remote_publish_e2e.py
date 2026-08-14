# -*- coding: utf-8 -*-
"""v0.21.0 · W93 · 远程发布端到端跨进程回归（ADR-35）。

**为什么必须跨进程**：W92 的单元测试用线程内 mock 服务端，测的是客户端的
请求构造与错误映射。但远程发布的真实风险在**两端的口径是否一致** ——
客户端算的校验和、服务端复核出的校验和、装包端重算的校验和必须是同一个数；
签名的字节序、tar.gz 的成员根、base64 的 padding，任一处两端理解不同，
线程内 mock 都测不出来（同一进程共享同一份代码常量）。所以这里真起
`tools/registry-server/server.py` 子进程。

覆盖路线图 §三 M23 的四条验收：

  1. **正常闭环**：客户端签名推包 → 服务端落盘 → **另一个项目目录**从远端
     装回 → 验签通过 + TOFU pin 公钥
  2. **越权推包被拒**：token 白名单是 `[甲包, 甲-*]`，推 `乙包` → 403 + 中文原因
  3. **覆盖已发布被拒**：同名同版本再推 → 409（远程没有覆盖开关，§2.4）
  4. **未签名被拒**：客户端侧直接拦（不发请求），服务端侧也拒（双端）

外加两条服务端不变量：审计日志成功/失败都记且不含 token；GET 静态托管与
POST 写端点同一进程共存。
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg import backend as B                              # noqa: E402
from jikuai.pkg import installer as I                            # noqa: E402
from jikuai.pkg import keys                                       # noqa: E402
from jikuai.pkg import registry                                   # noqa: E402
from jikuai.pkg import trust                                      # noqa: E402
from jikuai.pkg.manifest import MANIFEST_NAME, load_manifest      # noqa: E402

_SERVER = os.path.join(_REPO, 'tools', 'registry-server', 'server.py')

#: 端到端 token。测试用固定串——它进不了任何产物，只在 tmp_path 的授权配置里。
_TOKEN = 'e2e-token-w93'


# ---------------------------------------------------------------------------
# 子进程服务端
# ---------------------------------------------------------------------------

class _服务端进程:
    """起 `server.py` 为子进程，等端口可达后交给测试用。"""

    def __init__(self, 注册表根, 授权路径, 审计路径=None):
        self.注册表根 = 注册表根
        self.授权路径 = 授权路径
        self.审计路径 = 审计路径
        self.端口 = _空闲端口()
        self.proc = None

    @property
    def base(self):
        return 'http://127.0.0.1:%d' % self.端口

    def __enter__(self):
        参数 = [sys.executable, _SERVER,
                '--注册表', self.注册表根,
                '--授权', self.授权路径,
                '--监听', '127.0.0.1',
                '--端口', str(self.端口)]
        if self.审计路径:
            参数 += ['--审计', self.审计路径]
        # stderr 收进管道：服务端启动横幅与 logging 不该污染 pytest 输出。
        # 环境显式带上 PYTHONIOENCODING：子进程在 Windows 默认 GBK 控制台下
        # 打中文横幅会炸 UnicodeEncodeError，那会让服务端起不来而非测试失败。
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        env.pop('JIKUAI_REGISTRY', None)     # 服务端只认 --注册表
        self.proc = subprocess.Popen(
            参数, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            env=env, cwd=_REPO)
        if not self._等就绪():
            残余 = b''
            if self.proc.poll() is not None:
                残余 = self.proc.stderr.read() or b''
            self.__exit__()
            raise RuntimeError(
                '服务端未在预期时间内就绪：%s'
                % 残余.decode('utf-8', 'replace')[-2000:])
        return self

    def _等就绪(self, 超时=20.0):
        """轮询任一端点，能连上（含 404）即视为就绪。"""
        截止 = time.monotonic() + 超时
        探测 = self.base + '/' + urllib.parse.quote(registry.INDEX_NAME)
        while time.monotonic() < 截止:
            if self.proc.poll() is not None:
                return False        # 进程已退出，别白等
            try:
                urllib.request.urlopen(探测, timeout=1.0).read()
                return True
            except urllib.error.HTTPError:
                # 404 = 索引还没建，但**端口已在服务** —— 正是就绪信号
                return True
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        return False

    def __exit__(self, *_exc):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if self.proc is not None and self.proc.stderr is not None:
            self.proc.stderr.close()
        return False


import urllib.parse                                              # noqa: E402


def _空闲端口():
    import socket
    s = socket.socket()
    try:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 固定环境
# ---------------------------------------------------------------------------

@pytest.fixture
def 隔离环境(tmp_path, monkeypatch):
    monkeypatch.setenv(keys.KEY_ROOT_ENV, str(tmp_path / '密钥'))
    monkeypatch.setenv(trust.TRUST_ROOT_ENV, str(tmp_path / '信任'))
    monkeypatch.setenv(B.INSECURE_ENV, '1')       # 子进程服务端是明文 http
    monkeypatch.setenv(B.TOKEN_ENV, _TOKEN)
    monkeypatch.delenv(trust.TRUSTED_SIGNERS_ENV, raising=False)
    monkeypatch.delenv(B.TIMEOUT_ENV, raising=False)
    return tmp_path


def _造包(tmp_path, name='甲包', version='1.0.0', 目录名=None):
    pkg = tmp_path / (目录名 or name)
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / MANIFEST_NAME).write_text(json.dumps({
        '名称': name, '版本': version, '描述': 'W93 端到端远程发布',
        '入口': '主.jk', '极快版本': '>=0.21.0',
    }, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    (pkg / '主.jk').write_text('定义 你好():\n  打印("好")\n',
                               encoding='utf-8', newline='\n')
    (pkg / 'README.md').write_text('# %s\n' % name, encoding='utf-8')
    return str(pkg)


def _造宿主(tmp_path, 依赖名='甲包', 规格='*', 名='宿主'):
    proj = tmp_path / 名
    proj.mkdir(parents=True, exist_ok=True)
    (proj / MANIFEST_NAME).write_text(json.dumps({
        '名称': 名, '版本': '0.1.0', '描述': 'W93 远程装包宿主',
        '入口': 'main.jk', '依赖': {依赖名: 规格},
    }, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    (proj / 'main.jk').write_text('打印("好")\n', encoding='utf-8',
                                  newline='\n')
    return str(proj)


def _写授权(tmp_path, 别名='甲', 可发布=('甲包', '甲-*'), **额外):
    """生成密钥对并写服务端 `授权.json`（token 只存 sha256）。"""
    keys.generate_keypair(别名)
    公钥 = base64.b64encode(keys.load_public_key(别名)).decode('ascii')
    条目 = {'签名者': 别名, '公钥': 公钥, '可发布': list(可发布)}
    条目.update(额外)
    配置 = {
        '协议': 1,
        '令牌': {
            hashlib.sha256(_TOKEN.encode('utf-8')).hexdigest(): 条目,
        },
    }
    路径 = tmp_path / '授权.json'
    路径.write_text(json.dumps(配置, ensure_ascii=False, indent=2),
                    encoding='utf-8', newline='\n')
    return str(路径)


@pytest.fixture
def 远程注册表(隔离环境):
    """一个跑着的服务端 + 已登记的 `甲` 签名身份 + 审计日志。"""
    注册表根 = str(隔离环境 / '服务端注册表')
    os.makedirs(注册表根, exist_ok=True)
    授权 = _写授权(隔离环境)
    审计 = str(隔离环境 / '审计.jsonl')
    with _服务端进程(注册表根, 授权, 审计) as 服务:
        yield 服务, 注册表根, 审计


def _读审计(路径):
    if not os.path.isfile(路径):
        return []
    with open(路径, 'r', encoding='utf-8') as f:
        return [json.loads(行) for 行 in f if 行.strip()]


# ---------------------------------------------------------------------------
# 验收 1：发布 → 装 → 验签
# ---------------------------------------------------------------------------

def test_端到端_远程发布后另一目录装回并验签通过(远程注册表, 隔离环境,
                                                monkeypatch):
    服务, 注册表根, 审计 = 远程注册表
    monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)

    # --- 推 ---
    报告 = registry.publish(load_manifest(_造包(隔离环境)),
                            dry_run=False, signer='甲')
    assert 报告.dry_run is False
    assert 报告.name == '甲包' and 报告.version == '1.0.0'

    # --- 服务端确实落盘了（跨进程可见） ---
    assert os.path.isfile(os.path.join(注册表根, registry.INDEX_NAME))
    assert os.path.isfile(os.path.join(
        注册表根, *registry.archive_rel('甲包', '1.0.0').split('/')))
    assert os.path.isfile(os.path.join(
        注册表根, *registry.key_rel('甲').split('/')))

    # --- 装：另一个项目目录，只认远端 URL ---
    proj = _造宿主(隔离环境)
    装报告 = I.install(load_manifest(proj))
    assert 装报告.total == 1
    assert 装报告.warnings == []          # 签过名不该有未签名告警
    assert os.path.isdir(os.path.join(proj, I.PACKAGES_DIR, '甲包'))
    # 公钥经 HTTP 拉到并 pin 进本地信任库（TOFU）
    assert os.path.isfile(os.path.join(trust.trust_root(), '甲.公钥'))

    # --- 校验和三端一致：客户端算的 = 索引里记的 ---
    版本, 条目 = registry.lookup_entry('甲包', '1.0.0')
    assert 版本 == '1.0.0'
    assert 条目['校验和'] == 报告.checksum
    assert 条目['签名者'] == '甲'
    assert 条目['签名'] == 报告.signature

    # --- 审计记了成功且不含 token ---
    记录 = _读审计(审计)
    assert any(r.get('结果') == '已发布' and r.get('名称') == '甲包'
               for r in 记录)
    原文 = json.dumps(记录, ensure_ascii=False)
    assert _TOKEN not in 原文
    assert hashlib.sha256(_TOKEN.encode('utf-8')).hexdigest() not in 原文


def test_端到端_读写同一进程共存(远程注册表, 隔离环境, monkeypatch):
    """POST 写完之后，同一个进程的 GET 立刻能读到——运维只跑一个东西。"""
    服务, _根, _审计 = 远程注册表
    monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
    registry.publish(load_manifest(_造包(隔离环境)), dry_run=False, signer='甲')

    后端 = B.get_backend(服务.base)
    索引 = 后端.read_json(registry.INDEX_NAME)
    assert '甲包' in (索引.get('索引') or {})
    assert 后端.read_bytes(registry.archive_rel('甲包', '1.0.0'))
    assert 后端.read_text(registry.key_rel('甲')).strip()


# ---------------------------------------------------------------------------
# 验收 2：越权推包被拒
# ---------------------------------------------------------------------------

def test_端到端_越权推包被拒且给中文理由(远程注册表, 隔离环境, monkeypatch):
    """token 白名单是 `[甲包, 甲-*]`；`乙包` 不在其中 → 403。"""
    服务, 注册表根, 审计 = 远程注册表
    monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)

    with pytest.raises(registry.RegistryError) as 抓:
        registry.publish(load_manifest(_造包(隔离环境, name='乙包')),
                         dry_run=False, signer='甲')
    消息 = str(抓.value)
    assert '越权' in 消息
    assert '乙包' in 消息

    # 没落盘：索引里不该出现 乙包
    索引路径 = os.path.join(注册表根, registry.INDEX_NAME)
    if os.path.isfile(索引路径):
        with open(索引路径, 'r', encoding='utf-8') as f:
            assert '乙包' not in (json.load(f).get('索引') or {})

    # 审计记了拒绝 —— 失败侧才是审计的价值所在
    记录 = _读审计(审计)
    assert any(r.get('结果') == '拒绝' and '越权' in (r.get('原因') or '')
               for r in 记录)


def test_端到端_通配前缀内的包名放行(远程注册表, 隔离环境, monkeypatch):
    """`甲-*` 通配让同一作者的系列包不必逐个登记（ADR-35 §2.3 规则 2）。"""
    服务, _根, _审计 = 远程注册表
    monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
    报告 = registry.publish(
        load_manifest(_造包(隔离环境, name='甲-工具')), dry_run=False, signer='甲')
    assert 报告.name == '甲-工具'


def test_端到端_签名者与token绑定不符被拒(隔离环境, monkeypatch):
    """A 的 token 推一个署名 B 的包 → 403。防冒名。"""
    注册表根 = str(隔离环境 / '服务端注册表')
    os.makedirs(注册表根, exist_ok=True)
    授权 = _写授权(隔离环境, 别名='甲', 可发布=('甲包', '乙包'))
    keys.generate_keypair('乙')          # 本机有乙的私钥，但服务端没登记乙
    with _服务端进程(注册表根, 授权) as 服务:
        monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
        with pytest.raises(registry.RegistryError, match='越权'):
            registry.publish(load_manifest(_造包(隔离环境, name='乙包')),
                             dry_run=False, signer='乙')


def test_端到端_token不认识被拒(远程注册表, 隔离环境, monkeypatch):
    服务, _根, _审计 = 远程注册表
    monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
    monkeypatch.setenv(B.TOKEN_ENV, 'wrong-token-xyz')
    with pytest.raises(registry.RegistryError, match='鉴权失败'):
        registry.publish(load_manifest(_造包(隔离环境)),
                         dry_run=False, signer='甲')


# ---------------------------------------------------------------------------
# 验收 3：覆盖已发布版本被拒
# ---------------------------------------------------------------------------

def test_端到端_覆盖已发布版本被拒(远程注册表, 隔离环境, monkeypatch):
    """远程没有覆盖开关（§2.4）：同名同版本再推一定 409。"""
    服务, _根, 审计 = 远程注册表
    monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
    包 = _造包(隔离环境)
    registry.publish(load_manifest(包), dry_run=False, signer='甲')

    with pytest.raises(registry.RegistryError) as 抓:
        registry.publish(load_manifest(包), dry_run=False, signer='甲')
    消息 = str(抓.value)
    assert '已存在' in 消息
    assert '提升版本号' in 消息

    记录 = _读审计(审计)
    assert any(r.get('结果') == '拒绝' and '已存在' in (r.get('原因') or '')
               for r in 记录)


def test_端到端_提升版本号可继续发布(远程注册表, 隔离环境, monkeypatch):
    """拒覆盖的代价必须是「换版本号就行」，不能把作者堵死。"""
    服务, _根, _审计 = 远程注册表
    monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
    registry.publish(load_manifest(_造包(隔离环境, version='1.0.0')),
                     dry_run=False, signer='甲')
    registry.publish(
        load_manifest(_造包(隔离环境, version='1.0.1', 目录名='甲包-新')),
        dry_run=False, signer='甲')
    版本, _条目 = registry.lookup_entry('甲包')
    assert 版本 == '1.0.1'


def test_端到端_客户端允许覆盖对远程无效(远程注册表, 隔离环境, monkeypatch):
    """`--允许覆盖` 只对本地有效；对远程必须在客户端就报错，别发请求试。"""
    服务, _根, _审计 = 远程注册表
    monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
    with pytest.raises(registry.RegistryError, match='不支持覆盖'):
        registry.publish(load_manifest(_造包(隔离环境)), dry_run=False,
                         signer='甲', allow_overwrite=True)


# ---------------------------------------------------------------------------
# 验收 4：未签名被拒（双端）
# ---------------------------------------------------------------------------

def test_端到端_未签名包客户端直接拒(远程注册表, 隔离环境, monkeypatch):
    服务, 注册表根, _审计 = 远程注册表
    monkeypatch.setenv('JIKUAI_REGISTRY', 服务.base)
    with pytest.raises(registry.RegistryError, match='必须签名'):
        registry.publish(load_manifest(_造包(隔离环境)),
                         dry_run=False, signer=None)
    # 客户端拦住 → 服务端注册表根应该还是空的
    assert not os.path.isfile(os.path.join(注册表根, registry.INDEX_NAME))


def test_端到端_未签名报文服务端也拒(远程注册表, 隔离环境):
    """绕过客户端直接 POST 一个空签名报文 → 403。

    客户端拒是体验，服务端拒才是安全 —— 攻击者不会用我们的客户端。
    """
    服务, _根, _审计 = 远程注册表
    报文 = {
        '协议': B.PUBLISH_PROTOCOL,
        '名称': '甲包', '版本': '1.0.0', '分类': '通用',
        '校验和': 'sha256:' + '0' * 64,
        '签名者': '甲', '签名': '',           # ← 空签名
        '归档': base64.b64encode(b'x').decode('ascii'),
    }
    状态, 体 = _裸POST(服务.base, 报文, _TOKEN)
    assert 状态 == 403
    assert '签名' in 体.get('错误', '')


def test_端到端_伪造签名被服务端拒(远程注册表, 隔离环境):
    """签名格式合法但验不过 → 403。服务端用**登记的公钥**验，不信报文。"""
    服务, _根, _审计 = 远程注册表
    报文 = {
        '协议': B.PUBLISH_PROTOCOL,
        '名称': '甲包', '版本': '1.0.0', '分类': '通用',
        '校验和': 'sha256:' + '0' * 64,
        '签名者': '甲',
        '签名': base64.b64encode(b'\x00' * 64).decode('ascii'),
        '归档': base64.b64encode(b'x').decode('ascii'),
    }
    状态, 体 = _裸POST(服务.base, 报文, _TOKEN)
    assert 状态 == 403
    assert '签名验证失败' in 体.get('错误', '')


def test_端到端_协议版本不认给明确提示(远程注册表):
    服务, _根, _审计 = 远程注册表
    报文 = {
        '协议': 999,
        '名称': '甲包', '版本': '1.0.0', '校验和': 'sha256:' + '0' * 64,
        '签名者': '甲', '签名': 'AA==', '归档': 'AA==',
    }
    状态, 体 = _裸POST(服务.base, 报文, _TOKEN)
    assert 状态 == 400
    assert '协议' in 体.get('错误', '')
    assert '999' in 体.get('错误', '')


def test_端到端_错误响应不泄露服务端路径(远程注册表, 隔离环境):
    """跨网错误里带绝对路径 = 给攻击者的地形图（ADR-35 §2.7）。"""
    服务, 注册表根, _审计 = 远程注册表
    报文 = {
        '协议': B.PUBLISH_PROTOCOL,
        '名称': '甲包', '版本': '1.0.0', '校验和': 'sha256:' + '0' * 64,
        '签名者': '甲',
        '签名': base64.b64encode(b'\x00' * 64).decode('ascii'),
        '归档': base64.b64encode(b'not-a-targz').decode('ascii'),
    }
    _状态, 体 = _裸POST(服务.base, 报文, _TOKEN)
    错误 = 体.get('错误', '')
    assert 注册表根 not in 错误
    assert str(隔离环境) not in 错误


def _裸POST(base, 报文, token):
    """不走 `HttpBackend`，直接 POST —— 测服务端自己的判定。"""
    data = json.dumps(报文, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(base + B.PUBLISH_PATH, data=data,
                                 method='POST')
    req.add_header('Content-Type', 'application/json; charset=utf-8')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {'错误': raw}
