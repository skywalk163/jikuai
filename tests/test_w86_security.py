# -*- coding: utf-8 -*-
"""v0.21.0 · W86 · 安全审计修复的反例守护测试。

覆盖三处 defense-in-depth 加固：

  1. `.块根.json` 读侧的路径归属校验（installer + module_loader 两侧）
     —— 写侧一直有 `_验证块根` 校验，但读侧此前完全信任索引 `路径` 字段
     的内容，`..` 和绝对路径能穿越出 `极快_包/` 目录。这在 v0.21.0 M23
     开写端后放大成 RCE 重定向原语（命中的目录会进 `module_loader` 搜索
     路径，`.jk` 命中后同目录同名 `.py` 会被 `exec_module` 执行）。

  2. `_safe_extract_targz` 的解压炸弹上限（成员数 / 单成员 / 合计大小）
     —— 路径安全挡的是「写到哪」，体量上限挡的是「写多少」，二者互补。

  3. `HttpBackend._request` 的响应体大小上限 —— 恶意注册表可用超大响应
     让客户端 OOM，与解压炸弹是同族问题的下载侧。

三处都设有环境变量放宽门，供确有大资源包的合理场景逃生；反例测试同时
验证「默认拒 + env 覆盖后放行」两条路径。
"""

import io
import json
import os
import sys
import tarfile

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg import installer as I                              # noqa: E402
from jikuai.pkg import sources                                     # noqa: E402
from jikuai.pkg import backend as B                                # noqa: E402
from jikuai.module_loader import ModuleLoader                      # noqa: E402


# --- Fix 1：.块根.json 读侧路径归属 -------------------------------------

def _写索引(pkg_dir, 路径列表):
    path = os.path.join(pkg_dir, I.BLOCK_ROOTS_INDEX)
    data = {'索引版本': I.BLOCK_ROOTS_INDEX_VERSION,
            '块根': [{'包': 'x', '路径': p} for p in 路径列表]}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def test_安全块根路径_拒绝相对逃逸(tmp_path):
    base = str(tmp_path)
    assert I.安全块根路径(base, '../外面') is None
    assert I.安全块根路径(base, 'a/../../外') is None
    assert I.安全块根路径(base, '..') is None


def test_安全块根路径_拒绝绝对路径(tmp_path):
    base = str(tmp_path)
    assert I.安全块根路径(base, os.path.abspath(str(tmp_path))) is None
    # POSIX 风格绝对
    assert I.安全块根路径(base, '/etc/passwd') is None
    # 反斜杠归一后的绝对
    if os.name == 'nt':
        assert I.安全块根路径(base, 'C:\\Windows') is None


def test_安全块根路径_接受合法相对(tmp_path):
    base = str(tmp_path)
    (tmp_path / '甲' / 'blocks').mkdir(parents=True)
    got = I.安全块根路径(base, '甲/blocks')
    assert got is not None
    assert os.path.samefile(got, str(tmp_path / '甲' / 'blocks'))


def test_安全块根路径_拒绝等于_base(tmp_path):
    # 块根若解析成 base 自身（如空段 '.' 拼），语义等于「整个 极快_包/」
    # 也是不合理的，拒。
    base = str(tmp_path)
    assert I.安全块根路径(base, '.') is None


def test_read_block_roots_index_跳过越界条目(tmp_path):
    base = str(tmp_path)
    good = tmp_path / '甲' / 'blocks'
    good.mkdir(parents=True)
    _写索引(base, ['../外面', '甲/blocks', '/etc'])
    got = I.read_block_roots_index(base)
    assert len(got) == 1
    assert os.path.samefile(got[0], str(good))


def test_module_loader_块根父目录_同规则跳过越界(tmp_path):
    """module_loader._block_root_parents 与 installer 同规则（不 import
    pkg，保持核心加载路径独立），需独立守护。"""
    base = str(tmp_path)
    good = tmp_path / '甲' / 'blocks'
    good.mkdir(parents=True)
    _写索引(base, ['../外面/blocks', '甲/blocks', '/opt/blocks'])
    # `_block_root_parents` 不碰 evaluator，传 None 足够（不引 Evaluator 依赖）
    loader = ModuleLoader(None)
    parents = loader._block_root_parents(base)
    # good.parent 应在；越界的两条应被过滤
    assert len(parents) == 1
    assert os.path.samefile(parents[0], str(good.parent))


# --- Fix 2：_safe_extract_targz 解压炸弹上限 -----------------------------

def _造归档(members):
    """members: [(name, size)]，size 只写头部，body 用 0 填充到该长度。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for name, size in members:
            info = tarfile.TarInfo(name=name)
            info.size = size
            tf.addfile(info, io.BytesIO(b'\x00' * size))
    return buf.getvalue()


def test_解压_成员数超上限_拒绝(tmp_path, monkeypatch):
    monkeypatch.setenv('JIKUAI_PKG_MAX_MEMBERS', '2')
    data = _造归档([('a.txt', 1), ('b.txt', 1), ('c.txt', 1)])
    with pytest.raises(sources.SourceError, match='成员数.*超过上限'):
        sources._safe_extract_targz(data, str(tmp_path))


def test_解压_单成员超上限_拒绝(tmp_path, monkeypatch):
    monkeypatch.setenv('JIKUAI_PKG_MAX_MEMBER_BYTES', '10')
    data = _造归档([('big.bin', 100)])
    with pytest.raises(sources.SourceError, match='超过单成员上限'):
        sources._safe_extract_targz(data, str(tmp_path))


def test_解压_合计超上限_拒绝(tmp_path, monkeypatch):
    monkeypatch.setenv('JIKUAI_PKG_MAX_MEMBER_BYTES', '100')
    monkeypatch.setenv('JIKUAI_PKG_MAX_TOTAL_BYTES', '150')
    data = _造归档([('a.bin', 80), ('b.bin', 80)])
    with pytest.raises(sources.SourceError, match='解压后合计超过上限'):
        sources._safe_extract_targz(data, str(tmp_path))


def test_解压_默认上限内的小归档正常放行(tmp_path):
    """确认加固不误伤正常包：几个小文件的归档必须能装。"""
    data = _造归档([('包.json', 40), ('main.jk', 20)])
    sources._safe_extract_targz(data, str(tmp_path))
    assert (tmp_path / '包.json').is_file()
    assert (tmp_path / 'main.jk').is_file()


def test_解压_环境变量非法值回落默认(tmp_path, monkeypatch):
    """安全网配错应退回**更安全**的默认，而非崩溃或放行任意大。"""
    monkeypatch.setenv('JIKUAI_PKG_MAX_MEMBERS', 'not-a-number')
    monkeypatch.setenv('JIKUAI_PKG_MAX_MEMBER_BYTES', '-1')
    # 小归档在默认 4096 成员 / 64 MiB 单成员限内，应正常
    data = _造归档([('a.jk', 10)])
    sources._safe_extract_targz(data, str(tmp_path))
    assert (tmp_path / 'a.jk').is_file()


# --- Fix 3：HttpBackend 响应体上限 --------------------------------------

def test_http_响应体超上限_中断下载(monkeypatch, tmp_path):
    """通过 mock urlopen 让响应体超上限，_request 应抛 BackendError。"""
    import http.server
    import threading

    # 造一个大响应，服务端不停发字节
    big = b'x' * (2 * 1024 * 1024)

    class 处理器(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(big)))
            self.end_headers()
            self.wfile.write(big)

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), 处理器)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        monkeypatch.setenv('JIKUAI_REGISTRY_INSECURE', '1')
        monkeypatch.setenv('JIKUAI_REGISTRY_MAX_RESPONSE', '1024')  # 1 KB 上限
        port = server.server_address[1]
        be = B.HttpBackend(f'http://127.0.0.1:{port}')
        with pytest.raises(B.BackendError, match='超过上限'):
            be.read_bytes('任意.json')
    finally:
        server.shutdown()
        server.server_close()


def test_http_响应体在上限内_正常返回(monkeypatch):
    """加固不误伤正常小响应：索引/公钥/分类 JSON 都必须能读。"""
    import http.server
    import threading

    payload = '{"版本": "0.1.0"}'.encode('utf-8')

    class 处理器(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), 处理器)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        monkeypatch.setenv('JIKUAI_REGISTRY_INSECURE', '1')
        # 不设 MAX_RESPONSE，走默认 512 MiB
        port = server.server_address[1]
        be = B.HttpBackend(f'http://127.0.0.1:{port}')
        got = be.read_bytes('索引.json')
        assert got == payload
    finally:
        server.shutdown()
        server.server_close()
