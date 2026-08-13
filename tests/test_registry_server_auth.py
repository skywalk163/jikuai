# -*- coding: utf-8 -*-
"""v0.21.0 · M23 · W90-W91 · 远程注册表服务端授权单元测试（ADR-35 §2.3/§2.5）。

只测 `tools/registry-server/auth.py`——认证、授权、频次这三件纯逻辑，
不起 HTTP 服务（端到端在 `test_registry_server_publish.py` 覆盖）。

覆盖点：
  1. token 认证成功 / 失败
  2. 包名授权精确匹配
  3. 包名授权通配 `甲-*`
  4. 签名者不匹配拒绝
  5. 频次限额边界（第 N+1 次被拒）

风格对齐 `test_pkg_http_registry.py`：模块级函数 + 手动挂 `sys.path`。
"""

import base64
import hashlib
import importlib.util
import json
import os
import sys

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _load_auth_module():
    """`tools/registry-server/` 带连字符，不能常规 import；按文件路径加载。"""
    path = os.path.join(_REPO, 'tools', 'registry-server', 'auth.py')
    spec = importlib.util.spec_from_file_location('_reg_server_auth', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


auth = _load_auth_module()

from jikuai.pkg import _ed25519 as _ed                          # noqa: E402


# ---------------------------------------------------------------------------
# 辅助：造一份 授权.json 并加载
# ---------------------------------------------------------------------------

def _公钥b64(seed=b'\x01' * 32):
    return base64.b64encode(_ed.public_key_from_seed(seed)).decode('ascii')


def _token哈希(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _写配置(tmp_path, 令牌):
    data = {'协议': 1, '令牌': 令牌}
    p = tmp_path / '授权.json'
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding='utf-8', newline='\n')
    return str(p)


def _标准配置(tmp_path, token='甲的token', signer='甲',
             可发布=('甲包', '甲-*'), 每小时次数=20, 单包字节=16777216):
    令牌 = {
        _token哈希(token): {
            '签名者': signer,
            '公钥': _公钥b64(),
            '可发布': list(可发布),
            '每小时次数': 每小时次数,
            '单包字节': 单包字节,
        }
    }
    return auth.load_auth_config(_写配置(tmp_path, 令牌))


# ---------------------------------------------------------------------------
# 1. token 认证
# ---------------------------------------------------------------------------

def test_认证成功返回条目(tmp_path):
    配置 = _标准配置(tmp_path, token='甲的token')
    条目 = 配置.authenticate('甲的token')
    assert 条目 is not None
    assert 条目.signer == '甲'
    assert len(条目.public_key_bytes) == 32


def test_认证失败返回None(tmp_path):
    配置 = _标准配置(tmp_path, token='甲的token')
    assert 配置.authenticate('错的token') is None
    assert 配置.authenticate('') is None
    assert 配置.authenticate(None) is None


# ---------------------------------------------------------------------------
# 2 & 3. 包名授权：精确 + 通配
# ---------------------------------------------------------------------------

def test_包名精确匹配放行(tmp_path):
    配置 = _标准配置(tmp_path, 可发布=('甲包',))
    条目 = 配置.authenticate('甲的token')
    assert 配置.authorize_publish(条目, '甲包', '甲') is None


def test_包名不在白名单被拒(tmp_path):
    配置 = _标准配置(tmp_path, 可发布=('甲包',))
    条目 = 配置.authenticate('甲的token')
    理由 = 配置.authorize_publish(条目, '乙包', '甲')
    assert 理由 is not None
    assert '越权' in 理由


def test_包名通配前缀放行(tmp_path):
    配置 = _标准配置(tmp_path, 可发布=('甲-*',))
    条目 = 配置.authenticate('甲的token')
    assert 配置.authorize_publish(条目, '甲-工具', '甲') is None
    assert 配置.authorize_publish(条目, '甲-网络库', '甲') is None
    # 前缀不同不该命中通配
    assert 配置.authorize_publish(条目, '乙-工具', '甲') is not None


def test_纯星号通配在配置层被拒(tmp_path):
    """ADR-35 §2.3：没有「全部」通配，纯 `*` 应在加载配置时就报错。"""
    令牌 = {
        _token哈希('t'): {
            '签名者': '甲', '公钥': _公钥b64(),
            '可发布': ['*'], '每小时次数': 5, '单包字节': 1024,
        }
    }
    with pytest.raises(auth.AuthError, match=r'\*'):
        auth.load_auth_config(_写配置(tmp_path, 令牌))


# ---------------------------------------------------------------------------
# 4. 签名者不匹配
# ---------------------------------------------------------------------------

def test_签名者不匹配被拒(tmp_path):
    配置 = _标准配置(tmp_path, signer='甲', 可发布=('甲包',))
    条目 = 配置.authenticate('甲的token')
    理由 = 配置.authorize_publish(条目, '甲包', '乙')
    assert 理由 is not None
    assert '签名者' in 理由 or '越权' in 理由


# ---------------------------------------------------------------------------
# 5. 频次限额边界
# ---------------------------------------------------------------------------

def test_频次限额边界(tmp_path):
    配置 = _标准配置(tmp_path, 每小时次数=3)
    条目 = 配置.authenticate('甲的token')
    # 前 3 次放行
    assert 配置.check_rate(条目) is None
    assert 配置.check_rate(条目) is None
    assert 配置.check_rate(条目) is None
    # 第 4 次超限
    理由 = 配置.check_rate(条目)
    assert 理由 is not None
    assert '频次' in 理由


def test_频次窗口滑动放行(tmp_path):
    """窗口外的时间戳应被淘汰，让新请求重新放行。"""
    配置 = _标准配置(tmp_path, 每小时次数=2)
    条目 = 配置.authenticate('甲的token')
    基准 = 1_000_000.0
    assert 配置.check_rate(条目, now=基准) is None
    assert 配置.check_rate(条目, now=基准 + 1) is None
    # 立刻再来一次 → 超限
    assert 配置.check_rate(条目, now=基准 + 2) is not None
    # 窗口整体右移超过 RATE_WINDOW_SEC → 旧戳淘汰，重新放行
    远 = 基准 + auth.RATE_WINDOW_SEC + 10
    assert 配置.check_rate(条目, now=远) is None


# ---------------------------------------------------------------------------
# 配置加载校验
# ---------------------------------------------------------------------------

def test_协议版本不认被拒(tmp_path):
    p = tmp_path / '授权.json'
    p.write_text(json.dumps({'协议': 99, '令牌': {}}, ensure_ascii=False),
                 encoding='utf-8', newline='\n')
    with pytest.raises(auth.AuthError, match='协议'):
        auth.load_auth_config(str(p))


def test_公钥长度非32被拒(tmp_path):
    令牌 = {
        _token哈希('t'): {
            '签名者': '甲',
            '公钥': base64.b64encode(b'\x00' * 16).decode('ascii'),
            '可发布': ['甲包'], '每小时次数': 5, '单包字节': 1024,
        }
    }
    with pytest.raises(auth.AuthError, match='32 字节'):
        auth.load_auth_config(_写配置(tmp_path, 令牌))


def test_token键非64位hex被拒(tmp_path):
    令牌 = {
        '短哈希': {
            '签名者': '甲', '公钥': _公钥b64(),
            '可发布': ['甲包'], '每小时次数': 5, '单包字节': 1024,
        }
    }
    with pytest.raises(auth.AuthError, match='hex'):
        auth.load_auth_config(_写配置(tmp_path, 令牌))


def test_令牌为空被拒(tmp_path):
    p = tmp_path / '授权.json'
    p.write_text(json.dumps({'协议': 1, '令牌': {}}, ensure_ascii=False),
                 encoding='utf-8', newline='\n')
    with pytest.raises(auth.AuthError, match='令牌'):
        auth.load_auth_config(str(p))


# ---------------------------------------------------------------------------
# v0.22.0 · W101 · 授权热重载（ADR-36 §2.3）的反例测试
# ---------------------------------------------------------------------------

def _条目(token, signer='甲', 可发布=('甲包',), 每小时次数=20,
         单包字节=16777216):
    return {
        _token哈希(token): {
            '签名者': signer,
            '公钥': _公钥b64(),
            '可发布': list(可发布),
            '每小时次数': 每小时次数,
            '单包字节': 单包字节,
        }
    }


def _改写(path, 令牌):
    """原地改写 授权.json，并强制推进 mtime。

    显式 `os.utime` 而不是靠文件系统自然打戳：测试里两次写相隔微秒级，
    某些平台/文件系统的 mtime 粒度粗到能把两次写看成同一时刻，那会让这批
    测试变成看运气的抖动测试。热重载的判据本身（mtime_ns + size）是对的，
    这里只是把「时钟确实走了」这个前提在测试里钉死。
    """
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'协议': 1, '令牌': 令牌}, f, ensure_ascii=False, indent=2)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_撤销token后下一请求即失效(tmp_path):
    """ADR-36 §3 反例 1：改完 授权.json 不重启，下一个请求就认不出旧 token。"""
    path = _写配置(tmp_path, _条目('甲的token'))
    源 = auth.AuthProvider.from_path(path)
    assert 源.current().authenticate('甲的token') is not None

    _改写(path, _条目('新的token'))                # 撤旧发新
    assert 源.current().authenticate('甲的token') is None
    assert 源.current().authenticate('新的token') is not None
    assert 源.reload_count == 1                    # 只重载了一次，不是每次都读


def test_重载不重置频次配额(tmp_path):
    """ADR-36 §3 反例 2：改配置不能成为清空配额的后门。"""
    path = _写配置(tmp_path, _条目('甲的token', 每小时次数=2))
    源 = auth.AuthProvider.from_path(path)
    条目 = 源.current().authenticate('甲的token')
    assert 源.current().check_rate(条目) is None
    assert 源.current().check_rate(条目) is None
    assert 源.current().check_rate(条目) is not None    # 用尽

    # 改一个与频次无关的字段触发重载
    _改写(path, _条目('甲的token', 每小时次数=2, 单包字节=1024))
    新条目 = 源.current().authenticate('甲的token')
    assert 源.reload_count == 1
    assert 新条目.max_package_bytes == 1024            # 新配置确实生效了
    assert 源.current().check_rate(新条目) is not None  # 但配额没被清零


def test_删掉的token连配额一起丢(tmp_path):
    """被删除**并被观察到**再加回的 token 拿到干净窗口——它中间确实不存在过。

    注意中间那次 `current()` 不能省：热重载是「请求驱动」的，删了又立刻加回、
    期间没有任何请求进来，provider 根本没见过那个中间态，配额自然还留着。
    那也是对的（更保守），但不是本测试要钉的那条语义。
    """
    path = _写配置(tmp_path, _条目('甲的token', 每小时次数=1))
    源 = auth.AuthProvider.from_path(path)
    条目 = 源.current().authenticate('甲的token')
    assert 源.current().check_rate(条目) is None
    assert 源.current().check_rate(条目) is not None

    _改写(path, _条目('别的token'))                    # 甲的 token 消失
    assert 源.current().authenticate('甲的token') is None   # 被观察到了
    _改写(path, _条目('甲的token', 每小时次数=1))       # 又加回来
    新条目 = 源.current().authenticate('甲的token')
    assert 源.reload_count == 2
    assert 源.current().check_rate(新条目) is None      # 干净窗口


def test_坏JSON不致瘫_保留旧配置且持续重试(tmp_path):
    """ADR-36 §3 反例 3：管理员手抖写坏 JSON，服务继续用旧配置顶着。"""
    path = _写配置(tmp_path, _条目('甲的token'))
    源 = auth.AuthProvider.from_path(path)
    assert 源.current().authenticate('甲的token') is not None

    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('{ 这不是 json')
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    assert 源.current().authenticate('甲的token') is not None   # 旧配置还在
    assert 源.reload_count == 0
    # 刻意不更新 stamp → 每次请求都会再试一遍，改对了立刻生效
    _改写(path, _条目('修好的token'))
    assert 源.current().authenticate('修好的token') is not None
    assert 源.reload_count == 1


def test_协议版本写错也算坏配置_保留旧的(tmp_path):
    """坏配置不只是语法坏：语义校验失败（AuthError）也走保留旧配置这条路。"""
    path = _写配置(tmp_path, _条目('甲的token'))
    源 = auth.AuthProvider.from_path(path)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'协议': 99, '令牌': _条目('甲的token')}, f,
                  ensure_ascii=False)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    assert 源.current().authenticate('甲的token') is not None
    assert 源.reload_count == 0


def test_文件被移走时保留旧配置(tmp_path):
    """原子替换（写 tmp → rename）中间有个文件不存在的空窗，不能因此全拒。"""
    path = _写配置(tmp_path, _条目('甲的token'))
    源 = auth.AuthProvider.from_path(path)
    os.remove(path)
    assert 源.current().authenticate('甲的token') is not None
    assert 源.reload_count == 0


def test_未改动则不重载(tmp_path):
    path = _写配置(tmp_path, _条目('甲的token'))
    源 = auth.AuthProvider.from_path(path)
    第一份 = 源.current()
    assert 源.current() is 第一份           # 同一对象，没白读文件
    assert 源.reload_count == 0


def test_from_config不监视文件(tmp_path):
    """直接塞配置构造的 provider 不该去 stat 任何东西。"""
    配置 = _标准配置(tmp_path, token='甲的token')
    源 = auth.AuthProvider.from_config(配置)
    assert 源.current() is 配置
    _改写(str(tmp_path / '授权.json'), _条目('新的token'))
    assert 源.current() is 配置
    assert 源.reload_count == 0


def test_启动期配置坏直接抛(tmp_path):
    """热重载的容忍只针对**运行期**：启动时读不出配置就该起不来。"""
    p = tmp_path / '授权.json'
    p.write_text('{ 坏的', encoding='utf-8', newline='\n')
    with pytest.raises((auth.AuthError, ValueError)):
        auth.AuthProvider.from_path(str(p))

