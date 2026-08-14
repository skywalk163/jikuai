# -*- coding: utf-8 -*-
"""v0.22.0 · 真实注册表服务端 `POST /publish` 端到端（ADR-35 / ADR-36）。

已有测试的覆盖形状是「三段各测一半」：`test_registry_server_auth.py` 只测
`auth.py` 纯逻辑，`test_pkg_remote_publish.py` 用 mock 服务端测**客户端**，
`test_registry_server_deploy.py` 只测 TLS 闸门/锁文件/横幅（报文都是注定过不了
认证的假报文）。**真服务端的八步流水从没被端到端跑过一次**——本文件补的就是
这段：验签 → 校验和复核 → 解压 → 覆盖检查 → 落盘 → 签名注入 → 公钥追加 → 审计。

报文一律用客户端自己的 `registry._build_publish_payload` 造，不手搓字段：
这样测的是**真实的客户端↔服务端契约**，客户端哪天改了报文形状这里会立刻红。

拒绝路径的每条用例除了断言状态码，还统一断言错误信息里**不出现服务端文件
系统路径**（ADR-35 §「错误响应约束」：错误里带路径 = 给攻击者的地形图）。

`tools/registry-server/` 带连字符不能常规 import，按文件路径加载（同
`test_registry_server_deploy.py`）。
"""

import base64
import hashlib
import importlib.util
import io
import json
import os
import sys
import tarfile
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


S = _load('_reg_server_e2e', 'server.py')
#: **必须**复用 server.py 自己加载的那份 auth 模块，不能再 `_load` 一份。
#: `build_server` 用 `isinstance(授权, _auth_mod.AuthConfig)` 分派，两份独立
#: 加载的模块里 `AuthConfig` 是两个不同的类，isinstance 会假，配置对象就被当
#: 成文件路径丢给 `load_auth_config`，报一个莫名的 TypeError。
auth = S._auth_mod

from jikuai.pkg import _ed25519 as ed                            # noqa: E402
from jikuai.pkg import installer as I                            # noqa: E402
from jikuai.pkg import keys                                      # noqa: E402
from jikuai.pkg import registry                                  # noqa: E402
from jikuai.pkg import trust                                     # noqa: E402
from jikuai.pkg.manifest import MANIFEST_NAME, load_manifest     # noqa: E402


#: token 只能是 ASCII：它要进 `Authorization` 头，HTTP 头限 latin-1
#: （客户端 `HttpBackend` 对中文 token 有专门的提前拦截，见 W92）。
_TOKEN = 'tok-jia-e2e'


# ---------------------------------------------------------------------------
# 固定环境
# ---------------------------------------------------------------------------

@pytest.fixture
def 隔离环境(tmp_path, monkeypatch):
    """独立的密钥根 + 信任库 + 注册表定位符，并预先生成签名者「甲」。

    信任库必须隔离：TOFU pin 是**跨进程持久**的，用真实 `~/.jikuai/信任/`
    会让第一个测试 pin 的公钥污染后面所有测试，也会污染开发者本机。
    `JIKUAI_REGISTRY` 指向一个用不到的路径：服务端的注册表根是显式传参的，
    这里钉住它只为防某条链路悄悄回退到读环境变量（那会写到开发者家目录）。
    """
    monkeypatch.setenv(keys.KEY_ROOT_ENV, str(tmp_path / '密钥'))
    monkeypatch.setenv(trust.TRUST_ROOT_ENV, str(tmp_path / '信任'))
    monkeypatch.setenv('JIKUAI_REGISTRY', str(tmp_path / '客户端注册表'))
    monkeypatch.delenv(trust.TRUSTED_SIGNERS_ENV, raising=False)
    keys.generate_keypair('甲')
    return tmp_path


# ---------------------------------------------------------------------------
# 辅助：授权配置
# ---------------------------------------------------------------------------

def _token哈希(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _条目(token=_TOKEN, signer='甲', 可发布=('甲包',), 每小时次数=20,
         单包字节=1 << 20, 公钥b64=None):
    """一条授权条目。公钥默认取 `甲` 的**真**公钥——服务端验签用的就是它。"""
    return {
        _token哈希(token): {
            '签名者': signer,
            '公钥': 公钥b64 or base64.b64encode(
                keys.load_public_key(signer)).decode('ascii'),
            '可发布': list(可发布),
            '每小时次数': 每小时次数,
            '单包字节': 单包字节,
        }
    }


def _写授权(tmp_path, 令牌):
    """写 `授权.json` 并强制推进 mtime，返回路径。

    显式 `os.utime` 而不是靠文件系统自然打戳：热重载的判据是 mtime_ns+size，
    而测试里两次写相隔微秒级，某些平台/文件系统的 mtime 粒度粗到能把两次写
    看成同一时刻——那会让热重载用例变成看运气的抖动测试。照抄
    `test_registry_server_auth.py` 的 `_改写`。
    """
    path = str(tmp_path / '授权.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'协议': 1, '令牌': 令牌}, f, ensure_ascii=False, indent=2)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    return path


def _配置(tmp_path, **kw):
    """直接给 `build_server` 一个 `AuthConfig`（不需要热重载的用例走这条）。"""
    return auth.load_auth_config(_写授权(tmp_path, _条目(**kw)))


# ---------------------------------------------------------------------------
# 辅助：起服务 + 发请求
# ---------------------------------------------------------------------------

class _跑起来:
    """把 build_server 出来的实例跑在后台线程里，退出时关掉。

    刻意不起子进程：`build_server` 本身不抢 `.发布锁`（W102 的设计），同一
    进程内起多个实例互不干扰，也省掉子进程的启动等待与输出捕获。
    """

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


def _起服务(注册表根, 授权, **kw):
    return S.build_server(注册表=str(注册表根), 授权=授权,
                          host='127.0.0.1', port=0, **kw)


def _post(base, 体, token=_TOKEN, 头=None):
    """发一个 POST /publish，返回 (状态码, 响应体dict)。"""
    数据 = json.dumps(体, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(base + S.PUBLISH_PATH, data=数据,
                                 method='POST')
    req.add_header('Content-Type', 'application/json')
    if token is not None:
        req.add_header('Authorization', 'Bearer ' + token)
    for k, v in (头 or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        原文 = e.read().decode('utf-8')
        try:
            return e.code, json.loads(原文)
        except json.JSONDecodeError:
            return e.code, {'错误': 原文}


def _get(base, 相对):
    """GET 一个注册表内相对路径。请求行只能是 ASCII，中文路径先 percent-encode。"""
    url = base + '/' + urllib.parse.quote(相对)
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.status, r.read()


def _断言不泄漏路径(错误, tmp_path):
    """错误信息里绝不能出现服务端文件系统路径（ADR-35 §「错误响应约束」）。"""
    根 = str(tmp_path)
    assert 根 not in 错误
    assert 根.replace('\\', '/') not in 错误
    # 临时解压目录也在服务端磁盘上，前缀漏出去同样是地形图
    assert 'jikuai-publish-' not in 错误


# ---------------------------------------------------------------------------
# 辅助：造包 + 用客户端的函数造报文
# ---------------------------------------------------------------------------

def _造包(tmp_path, name='甲包', version='0.1.0'):
    pkg = tmp_path / ('源-' + name)
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / MANIFEST_NAME).write_text(json.dumps({
        '名称': name, '版本': version, '描述': '注册表服务端端到端测试用',
        '入口': '主.jk', '极快版本': '>=0.21.0',
    }, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    (pkg / '主.jk').write_text('定义 你好():\n  打印("好")\n',
                               encoding='utf-8', newline='\n')
    (pkg / 'README.md').write_text('# 甲包\n', encoding='utf-8', newline='\n')
    return str(pkg)


def _报文(tmp_path, name='甲包', version='0.1.0', signer='甲'):
    """用**客户端**的报文构造器产出真实报文（ADR-35 §2.2）。

    不手搓字段：手搓的报文只能证明「我以为的格式」被服务端接受了，测不到
    真实契约。这条路径同时把 `keys.load_private_key` → 签名 → tar.gz 打包
    全跑一遍，与 `jk 包 发布 --签名` 走的是同一段代码。
    """
    manifest = load_manifest(_造包(tmp_path, name, version))
    payload, _校验和, _签名, _文件数, _警告 = registry._build_publish_payload(
        manifest, '通用', signer)
    return payload


def _重签(payload, 校验和, signer='甲'):
    """把报文改签到另一个校验和上（签名对象是校验和字符串，ADR-33）。

    没有这一步就测不到「校验和复核」：验签（第 7 步）在校验和复核（第 8 步）
    之前，直接改 `校验和` 只会先倒在 403 验签失败。
    """
    payload['校验和'] = 校验和
    payload['签名'] = base64.b64encode(
        ed.sign(keys.load_private_key(signer), 校验和.encode('utf-8'))
    ).decode('ascii')
    return payload


def _造穿越归档():
    """一个成员路径带 `..` 的 tar.gz —— 解压端必须拒（ADR-34 §2.4）。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        数据 = '坏\n'.encode('utf-8')
        info = tarfile.TarInfo('../逃出去.jk')
        info.size = len(数据)
        tf.addfile(info, io.BytesIO(数据))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. 成功路径
# ---------------------------------------------------------------------------

def test_完整发布落盘四件套齐全(隔离环境, tmp_path):
    """一次真实发布之后，注册表根必须同时具备读端需要的全部四件东西。

    分开断言而不是只看 200：服务端的落盘是「先无签名 publish，再注入签名 +
    追加公钥」两步（服务端没有私钥），任一步漏了都还是 200，只有逐件检查
    才能钉住这个组合动作。
    """
    根 = tmp_path / '注册表'
    报文 = _报文(隔离环境)
    with _跑起来(_起服务(根, _配置(tmp_path))) as s:
        码, 体 = _post(s.base, 报文)
        assert 码 == 200, 体
        assert 体['结果'] == '已发布'
        assert 体['校验和'] == 报文['校验和']

        # 归档能被同一个进程当静态镜像吐出来（读端契约，W78 起逐字节一致）
        码2, 字节 = _get(s.base, '包/甲包/0.1.0.tar.gz')
        assert 码2 == 200

    # (a) 主索引有路由条目
    索引 = json.loads((根 / '索引.json').read_text(encoding='utf-8'))
    assert 索引['索引']['甲包']['版本'] == ['0.1.0']
    assert 索引['索引']['甲包']['分类'] == '通用'

    # (b) 分片里有签名者/签名——这两个字段服务端注入的，客户端报文里的
    #     `条目` 详情**不被信任**（详情由服务端从清单重算）
    分片 = json.loads((根 / '分类' / '通用.json').read_text(encoding='utf-8'))
    条目 = 分片['甲包']['0.1.0']
    assert 条目['签名者'] == '甲'
    assert 条目['签名'] == 报文['签名']
    assert 条目['校验和'] == 报文['校验和']

    # (c) 快照归档，且与 GET 拿到的逐字节一致
    归档 = 根 / '包' / '甲包' / '0.1.0.tar.gz'
    assert 归档.is_file()
    assert 归档.read_bytes() == 字节

    # (d) 公钥来自 `授权.json` 里管理员登记的那把，不是报文自带的
    公钥文件 = 根 / '密钥' / '甲.公钥'
    assert 公钥文件.read_text(encoding='utf-8').strip() == base64.b64encode(
        keys.load_public_key('甲')).decode('ascii')


def test_发布后客户端能装且验签通过(隔离环境, tmp_path, monkeypatch):
    """发布端与装包端在一个测试里接上：服务端落的盘，装包端认不认。

    这条是整条链路的收口——签名注入位置对不对、公钥文件格式对不对、校验和
    前缀带没带，任何一处与装包端的期望差一点，这里就装不上。
    """
    根 = tmp_path / '注册表'
    with _跑起来(_起服务(根, _配置(tmp_path))) as s:
        码, 体 = _post(s.base, _报文(隔离环境))
        assert 码 == 200, 体

    # 把服务端落的注册表根当客户端的注册表用
    monkeypatch.setenv('JIKUAI_REGISTRY', str(根))
    宿主 = tmp_path / '宿主'
    宿主.mkdir()
    (宿主 / MANIFEST_NAME).write_text(json.dumps({
        '名称': '宿主', '版本': '0.1.0', '描述': '装包端',
        '入口': 'main.jk', '依赖': {'甲包': '*'},
    }, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    (宿主 / 'main.jk').write_text('打印("好")\n', encoding='utf-8',
                                  newline='\n')

    报告 = I.install(load_manifest(str(宿主)))
    assert 报告.total == 1
    assert 报告.warnings == []              # 签过名就不该有未签名告警
    assert (宿主 / I.PACKAGES_DIR / '甲包' / '主.jk').is_file()
    # TOFU：公钥从注册表 `密钥/` 被 pin 进本地信任库，且就是甲的真公钥
    assert trust.pinned_keys('甲') == [keys.load_public_key('甲')]


def test_要求TLS下带头的完整发布仍是200(隔离环境, tmp_path):
    """W102 的 TLS 闸门只该挡缺头的请求，不能把正常路径一起挡掉。

    deploy 那批用的是注定过不了认证的假报文，只能证明「过了闸门」；这里用
    真报文走到底，才能证明闸门没在后面某处留下副作用。
    """
    根 = tmp_path / '注册表'
    srv = _起服务(根, _配置(tmp_path), 要求TLS=True)
    assert srv.要求TLS is True
    with _跑起来(srv) as s:
        码, 体 = _post(s.base, _报文(隔离环境),
                       头={'X-Forwarded-Proto': 'https'})
    assert 码 == 200, 体
    assert (根 / '分类' / '通用.json').is_file()


# ---------------------------------------------------------------------------
# 2. 授权热重载（ADR-36 §2.3）—— 端到端版
# ---------------------------------------------------------------------------

def test_撤销token后下一请求即401_不重启(隔离环境, tmp_path):
    """ADR-36 §3 反例 1 的端到端版：之前只有 `AuthProvider` 的单元版。

    第二次请求刻意复用同一个报文：若热重载没生效，它会走到覆盖检查拿 409，
    409 与 401 一眼可辨——这比「随便发一个必然失败的报文」更能定位问题。
    """
    根 = tmp_path / '注册表'
    路径 = _写授权(tmp_path, _条目())
    报文 = _报文(隔离环境)
    源 = auth.AuthProvider.from_path(路径)
    with _跑起来(_起服务(根, 源)) as s:
        码, 体 = _post(s.base, 报文)
        assert 码 == 200, 体

        _写授权(tmp_path, _条目(token='tok-someone-else'))   # 撤掉甲的 token
        码2, 体2 = _post(s.base, 报文)                    # 不重启服务端

    assert 码2 == 401
    assert 'token' in 体2['错误']
    assert 源.reload_count == 1
    _断言不泄漏路径(体2['错误'], tmp_path)


def test_新增token立刻可用_不重启(隔离环境, tmp_path):
    """反向：加一条 token 也该立刻生效，否则管理员发新凭证得停服。"""
    根 = tmp_path / '注册表'
    路径 = _写授权(tmp_path, _条目(token='tok-old'))
    报文 = _报文(隔离环境)
    源 = auth.AuthProvider.from_path(路径)
    with _跑起来(_起服务(根, 源)) as s:
        码, _体 = _post(s.base, 报文, token='tok-new')
        assert 码 == 401                                  # 还没登记

        令牌 = dict(_条目(token='tok-old'))
        令牌.update(_条目(token='tok-new'))
        _写授权(tmp_path, 令牌)
        码2, 体2 = _post(s.base, 报文, token='tok-new')

    assert 码2 == 200, 体2
    assert 源.reload_count == 1
    assert (根 / '索引.json').is_file()


# ---------------------------------------------------------------------------
# 3. 拒绝路径
# ---------------------------------------------------------------------------

def test_越权包名403(隔离环境, tmp_path):
    根 = tmp_path / '注册表'
    with _跑起来(_起服务(根, _配置(tmp_path, 可发布=('乙包',)))) as s:
        码, 体 = _post(s.base, _报文(隔离环境))
    assert 码 == 403
    assert '越权' in 体['错误']
    _断言不泄漏路径(体['错误'], tmp_path)
    assert not (根 / '索引.json').exists()      # 被拒就不该留下任何落盘痕迹


def test_签名者与token不一致403(隔离环境, tmp_path):
    """甲的 token 推一个署名「乙」的包必须拒——否则 A 能冒 B 的名发布。

    注意签名本身仍是甲签的、也验得过：能过验签不等于能署这个名，这两件事
    在服务端是分开判的（第 4 步授权 vs 第 7 步验签）。
    """
    根 = tmp_path / '注册表'
    报文 = _报文(隔离环境)
    报文['签名者'] = '乙'
    with _跑起来(_起服务(根, _配置(tmp_path))) as s:
        码, 体 = _post(s.base, 报文)
    assert 码 == 403
    assert '越权' in 体['错误'] and '乙' in 体['错误']
    _断言不泄漏路径(体['错误'], tmp_path)


def test_伪造签名403(隔离环境, tmp_path):
    """长度对、base64 合法、但验不过登记公钥 —— 必须倒在验签而不是形状检查。"""
    根 = tmp_path / '注册表'
    报文 = _报文(隔离环境)
    报文['签名'] = base64.b64encode(b'\x00' * ed.SIGNATURE_SIZE).decode('ascii')
    with _跑起来(_起服务(根, _配置(tmp_path))) as s:
        码, 体 = _post(s.base, 报文)
    assert 码 == 403
    assert '签名验证失败' in 体['错误']
    _断言不泄漏路径(体['错误'], tmp_path)
    assert not (根 / '包').exists()


def test_重复发布409且没有放行开关(隔离环境, tmp_path):
    """ADR-35 §2.4：远程注册表**没有**覆盖开关，重发只能提版本号。

    顺带钉住「第一次的落盘没被第二次破坏」：409 是在写锁内、publish 之前
    判的，不该出现「拒了但快照已被半覆盖」。
    """
    根 = tmp_path / '注册表'
    报文 = _报文(隔离环境)
    with _跑起来(_起服务(根, _配置(tmp_path))) as s:
        码1, 体1 = _post(s.base, 报文)
        assert 码1 == 200, 体1
        码2, 体2 = _post(s.base, 报文)
    assert 码2 == 409
    assert '已存在' in 体2['错误']
    _断言不泄漏路径(体2['错误'], tmp_path)
    分片 = json.loads((根 / '分类' / '通用.json').read_text(encoding='utf-8'))
    assert 分片['甲包']['0.1.0']['签名'] == 报文['签名']


def test_归档超单包上限413(隔离环境, tmp_path):
    """按**本 token 的** `单包字节` 判，不是全局上限。"""
    根 = tmp_path / '注册表'
    with _跑起来(_起服务(根, _配置(tmp_path, 单包字节=64))) as s:
        码, 体 = _post(s.base, _报文(隔离环境))
    assert 码 == 413
    assert '单包上限' in 体['错误'] and '64' in 体['错误']
    _断言不泄漏路径(体['错误'], tmp_path)


def test_频次超限429(隔离环境, tmp_path):
    """`每小时次数=1`：第一次 200，第二次 429（进程内滑动窗口）。

    刻意用两个不同版本而不是同一个：同版本的第二次会撞 409，就分不清是频次
    生效还是覆盖检查生效了。频次（第 5 步）在覆盖检查（第 9 步）之前，用
    新版本能确保 429 是唯一可能的拒因。
    """
    根 = tmp_path / '注册表'
    with _跑起来(_起服务(根, _配置(tmp_path, 每小时次数=1))) as s:
        码1, 体1 = _post(s.base, _报文(隔离环境, version='0.1.0'))
        assert 码1 == 200, 体1
        码2, 体2 = _post(s.base, _报文(隔离环境, version='0.2.0'))
    assert 码2 == 429
    assert '频次' in 体2['错误']
    _断言不泄漏路径(体2['错误'], tmp_path)


def test_协议版本不认400且两端版本号都报出来(隔离环境, tmp_path):
    """ADR-35 §2.7：不许只说「协议不对」——运维得知道该升哪一端。"""
    根 = tmp_path / '注册表'
    报文 = _报文(隔离环境)
    报文['协议'] = 99
    with _跑起来(_起服务(根, _配置(tmp_path))) as s:
        码, 体 = _post(s.base, 报文)
    assert 码 == 400
    assert str(S.PROTOCOL_VERSION) in 体['错误']
    assert '99' in 体['错误']
    _断言不泄漏路径(体['错误'], tmp_path)


def test_校验和与归档不一致400(隔离环境, tmp_path):
    """报文声明的校验和与归档实算不符 → 400。

    这里必须**重签**：验签在校验和复核之前，不重签就只会倒在 403，测不到
    第 8 步。重签也更贴近真实威胁模型——攻击者手里若有私钥，签一个假校验和
    是他能做的；挡住他的是服务端自己重算这一步。
    """
    根 = tmp_path / '注册表'
    报文 = _重签(_报文(隔离环境), 'sha256:' + '1' * 64)
    with _跑起来(_起服务(根, _配置(tmp_path))) as s:
        码, 体 = _post(s.base, 报文)
    assert 码 == 400
    assert '校验和不匹配' in 体['错误']
    _断言不泄漏路径(体['错误'], tmp_path)
    assert not (根 / '包').exists()


def test_归档不是合法targz400(隔离环境, tmp_path):
    """客户端塞了段垃圾字节 → 400「归档不安全」，**不是** 500。

    500 意味着「服务端自己坏了」，把客户端的畸形输入报成 500 会让运维在
    告警里追一个根本不存在的服务端故障，也会淹掉真实的 500。
    """
    根 = tmp_path / '注册表'
    报文 = _报文(隔离环境)
    报文['归档'] = base64.b64encode(
        b'\x00\x01not-a-targz-at-all' * 8).decode('ascii')
    with _跑起来(_起服务(根, _配置(tmp_path))) as s:
        码, 体 = _post(s.base, 报文)
    assert 码 == 400
    assert '归档不安全' in 体['错误']
    _断言不泄漏路径(体['错误'], tmp_path)


def test_归档含路径穿越成员400(隔离环境, tmp_path):
    """`../` 成员：解压端拒（ADR-34 §2.4），服务端转成 400 而非 500。"""
    根 = tmp_path / '注册表'
    报文 = _报文(隔离环境)
    报文['归档'] = base64.b64encode(_造穿越归档()).decode('ascii')
    with _跑起来(_起服务(根, _配置(tmp_path))) as s:
        码, 体 = _post(s.base, 报文)
    assert 码 == 400
    assert '归档不安全' in 体['错误']
    _断言不泄漏路径(体['错误'], tmp_path)
    # 穿越目标是解压临时目录的**父**目录，注册表根下不该冒出任何东西
    assert not (根 / '包').exists()


# ---------------------------------------------------------------------------
# 4. 审计（ADR-35 §2.6）
# ---------------------------------------------------------------------------

def test_审计成功与被拒各一条且不含token痕迹(隔离环境, tmp_path):
    """成功与失败都记（连续 401/403 是入侵信号，只记成功等于丢掉最有用的部分）。

    硬约束是后半段：**明文 token 与 token 的 sha256 都不许出现**。审计日志
    的读者是运维，日志本身往往权限比配置文件宽、还会被集中收集——凭证材料
    一旦进了日志，等于多了一条泄漏路径。`签名者` 已经足够定位「是谁」。
    """
    根 = tmp_path / '注册表'
    审计 = tmp_path / '审计.jsonl'
    with _跑起来(_起服务(根, _配置(tmp_path), 审计路径=str(审计))) as s:
        码1, 体1 = _post(s.base, _报文(隔离环境))
        assert 码1 == 200, 体1
        码2, _体2 = _post(s.base, _报文(隔离环境, version='0.2.0'),
                          token='tok-unknown')
        assert 码2 == 401

    原文 = 审计.read_text(encoding='utf-8')
    行 = [json.loads(l) for l in 原文.splitlines() if l.strip()]
    assert len(行) == 2
    assert 行[0]['结果'] == '已发布'
    assert 行[0]['签名者'] == '甲' and 行[0]['名称'] == '甲包'
    assert 行[0]['版本'] == '0.1.0' and 行[0]['字节'] > 0
    assert 行[1]['结果'] == '拒绝' and 行[1]['原因']

    for t in (_TOKEN, 'tok-unknown'):
        assert t not in 原文
        assert _token哈希(t) not in 原文
