# -*- coding: utf-8 -*-
"""v0.20.0 · W74 · 包签名端到端测试（ADR-33）。

覆盖：
  1. 生成密钥对 → API 可用；重复别名被拒
  2. 带签名发布 → 索引条目含 签名者/签名 字段，且签名可由公钥验过
  3. 不带签名发布 → 索引条目无签名字段（老条目兼容路径）
  4. 公钥写入注册表 `密钥/` 目录，同别名换身份被拒
  5. CLI `密钥 生成/列表/导出` 端到端
  6. 签名者别名的路径安全校验

风格对齐 `test_pkg_block_e2e.py`：模块级函数 + pytest fixture，不用 TestCase。
"""

import base64
import json
import os
import sys

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg import _ed25519 as ed                      # noqa: E402
from jikuai.pkg import keys                                 # noqa: E402
from jikuai.pkg import registry                             # noqa: E402
from jikuai.pkg.manifest import MANIFEST_NAME, load_manifest  # noqa: E402


# ---------------------------------------------------------------------------
# 固定测试环境：隔离密钥根 + 注册表根
# ---------------------------------------------------------------------------

@pytest.fixture
def 隔离环境(tmp_path, monkeypatch):
    """每个测试在独立的密钥根 + 注册表根中运行，返回 tmp_path。"""
    monkeypatch.setenv(keys.KEY_ROOT_ENV, str(tmp_path / '密钥'))
    monkeypatch.setenv('JIKUAI_REGISTRY', str(tmp_path / '注册表'))
    return tmp_path


def _造包(tmp_path, name='签名试包', version='0.1.0'):
    """造一个最小可发布的包目录，返回其路径。"""
    pkg = tmp_path / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / MANIFEST_NAME).write_text(json.dumps({
        '名称': name,
        '版本': version,
        '描述': 'W74 签名测试用',
        '入口': '主.jk',
        '极快版本': '>=0.19.0',
    }, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    (pkg / '主.jk').write_text('定义 你好():\n  打印("好")\n',
                               encoding='utf-8', newline='\n')
    (pkg / 'README.md').write_text('# 签名试包\n', encoding='utf-8')
    return str(pkg)


# ---------------------------------------------------------------------------
# 密钥管理
# ---------------------------------------------------------------------------

def test_生成密钥对_列表可见(隔离环境):
    pub_b64 = keys.generate_keypair('甲')
    assert len(base64.b64decode(pub_b64)) == ed.PUBLIC_KEY_SIZE
    rows = keys.list_keys()
    assert rows == [('甲', True, True)]


def test_导出公钥为44字符base64(隔离环境):
    keys.generate_keypair('乙')
    b64 = keys.export_public_key_b64('乙')
    assert len(b64) == 44
    assert len(base64.b64decode(b64)) == ed.PUBLIC_KEY_SIZE


def test_重复别名被拒(隔离环境):
    keys.generate_keypair('甲')
    with pytest.raises(FileExistsError):
        keys.generate_keypair('甲')


# ---------------------------------------------------------------------------
# 带签名发布
# ---------------------------------------------------------------------------

def test_带签名发布_索引有字段且签名可验(隔离环境):
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    report = registry.publish(manifest, dry_run=False, signer='甲')

    assert report.signer == '甲'
    sig_bytes = base64.b64decode(report.signature)
    assert len(sig_bytes) == ed.SIGNATURE_SIZE

    cat = registry._load_category(registry.registry_root(), report.category)
    entry = cat['签名试包']['0.1.0']
    assert entry['签名者'] == '甲'
    assert entry['签名'] == report.signature

    pk = keys.load_public_key('甲')
    assert ed.verify(pk, report.checksum.encode('utf-8'), sig_bytes) is True


def test_签名对象是校验和字符串(隔离环境):
    """篡改校验和字符串后，同一签名验不过——确认签名对象即校验和。"""
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    report = registry.publish(manifest, dry_run=False, signer='甲')
    pk = keys.load_public_key('甲')
    sig = base64.b64decode(report.signature)
    坏校验和 = report.checksum.replace('sha256:', 'sha256:0')
    assert ed.verify(pk, 坏校验和.encode('utf-8'), sig) is False


def test_公钥落入注册表密钥目录(隔离环境):
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    registry.publish(manifest, dry_run=False, signer='甲')

    reg_pk_path = registry.registry_key_path(registry.registry_root(), '甲')
    assert os.path.isfile(reg_pk_path)
    with open(reg_pk_path, 'r', encoding='utf-8') as f:
        reg_b64 = f.read().strip()
    assert base64.b64decode(reg_b64) == keys.load_public_key('甲')


def test_同别名换身份被拒(隔离环境):
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    registry.publish(manifest, dry_run=False, signer='甲')

    # 手工把注册表里的公钥换成假的，模拟身份替换
    reg_pk_path = registry.registry_key_path(registry.registry_root(), '甲')
    with open(reg_pk_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(base64.b64encode(b'\x00' * 32).decode() + '\n')

    manifest2 = load_manifest(_造包(隔离环境, version='0.2.0'))
    with pytest.raises(registry.RegistryError, match='不允许静默替换'):
        registry.publish(manifest2, dry_run=False, signer='甲')


def test_演练带签名不落盘但报告有签名(隔离环境):
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    report = registry.publish(manifest, dry_run=True, signer='甲')
    assert report.dry_run is True
    assert report.signature  # 演练也算签名，方便预检
    # 演练不写注册表：密钥目录不该冒出来
    reg_pk_path = registry.registry_key_path(registry.registry_root(), '甲')
    assert not os.path.isfile(reg_pk_path)


# ---------------------------------------------------------------------------
# 无签名发布（老条目兼容）
# ---------------------------------------------------------------------------

def test_无签名发布无签名字段(隔离环境):
    manifest = load_manifest(_造包(隔离环境))
    report = registry.publish(manifest, dry_run=False)
    assert report.signer == ''
    assert report.signature == ''
    cat = registry._load_category(registry.registry_root(), report.category)
    entry = cat['签名试包']['0.1.0']
    assert '签名者' not in entry
    assert '签名' not in entry


# ---------------------------------------------------------------------------
# CLI 密钥子命令
# ---------------------------------------------------------------------------

def test_cli_密钥生成列表导出(隔离环境, capsys):
    from jikuai.pkg.cli import run
    assert run(['密钥', '生成', '丙']) == 0
    assert '已生成密钥对' in capsys.readouterr().out

    assert run(['密钥', '列表']) == 0
    out = capsys.readouterr().out
    assert '丙' in out and '可签名' in out

    assert run(['密钥', '导出', '丙']) == 0
    assert len(capsys.readouterr().out.strip()) == 44


def test_cli_密钥缺子命令报错(隔离环境, capsys):
    from jikuai.pkg.cli import run
    assert run(['密钥']) == 1
    assert '子命令' in capsys.readouterr().err


def test_cli_密钥生成无别名报错(隔离环境, capsys):
    from jikuai.pkg.cli import run
    assert run(['密钥', '生成']) == 1
    assert '别名' in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 别名路径安全
# ---------------------------------------------------------------------------

def test_合法别名通过():
    assert keys.validate_alias('甲') == '甲'
    assert keys.validate_alias('my_key') == 'my_key'
    assert keys.validate_alias('密钥-1号') == '密钥-1号'


@pytest.mark.parametrize('bad', [
    '../etc/passwd',   # 目录穿越
    'a/b',            # 路径分隔符
    'a.b',            # 点
    '',               # 空
    'x' * 65,         # 超长
])
def test_非法别名被拒(bad):
    with pytest.raises(ValueError):
        keys.validate_alias(bad)
