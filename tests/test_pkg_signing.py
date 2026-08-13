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
from jikuai.pkg import trust                                # noqa: E402
from jikuai.pkg import installer as I                       # noqa: E402
from jikuai.pkg.manifest import MANIFEST_NAME, load_manifest  # noqa: E402


# ---------------------------------------------------------------------------
# 固定测试环境：隔离密钥根 + 注册表根
# ---------------------------------------------------------------------------

@pytest.fixture
def 隔离环境(tmp_path, monkeypatch):
    """每个测试在独立的密钥根 + 注册表根 + 信任库中运行，返回 tmp_path。

    信任库必须隔离：TOFU pin 是**跨进程持久**的，用真实 `~/.jikuai/信任/`
    会让第一个测试 pin 的公钥污染后面所有测试（也会污染开发者本机）。
    """
    monkeypatch.setenv(keys.KEY_ROOT_ENV, str(tmp_path / '密钥'))
    monkeypatch.setenv('JIKUAI_REGISTRY', str(tmp_path / '注册表'))
    monkeypatch.setenv(trust.TRUST_ROOT_ENV, str(tmp_path / '信任'))
    monkeypatch.delenv(trust.TRUSTED_SIGNERS_ENV, raising=False)
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


def _造宿主(tmp_path, 依赖名='签名试包', 约束='*'):
    """造一个从**注册表**取依赖的宿主工程，返回其路径。

    刻意用版本约束而不是 `--路径`：只有注册表来源才会走
    `sources._fetch_registry`，也才有签名可验（路径依赖没有索引条目）。
    """
    proj = tmp_path / '宿主'
    proj.mkdir(parents=True, exist_ok=True)
    (proj / MANIFEST_NAME).write_text(json.dumps({
        '名称': '宿主', '版本': '0.1.0', '描述': 'W75 装包端验签宿主',
        '入口': 'main.jk', '依赖': {依赖名: 约束},
    }, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    (proj / 'main.jk').write_text('打印("好")\n', encoding='utf-8',
                                  newline='\n')
    return str(proj)



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


def test_注册表公钥文件是追加而非拒写(隔离环境):
    """v0.22.0 W101（ADR-36 §2.4）：注册表侧公钥文件改为「每行一把」，追加。

    v0.21.0 及之前这里是「与已有公钥不等就拒写」。改掉的理由：注册表侧的
    公钥文件是**分发渠道**（装包端 TOFU 首次拉取源），不是权威判据——
    权威判据是客户端本地 pin。限制成「一个别名只能一把钥」正是轮换会炸掉
    全部老包的根因。防「静默改身份」的职责在客户端 pin（见下面两个测试）。
    """
    keys.generate_keypair('甲')
    manifest = load_manifest(_造包(隔离环境))
    registry.publish(manifest, dry_run=False, signer='甲')

    真钥 = base64.b64encode(keys.load_public_key('甲')).decode()
    reg_pk_path = registry.registry_key_path(registry.registry_root(), '甲')

    # 手工往注册表里塞一把别的公钥（模拟管理员登记轮换后的新钥）
    假钥 = base64.b64encode(b'\x00' * 32).decode()
    with open(reg_pk_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(假钥 + '\n')

    # 再发一版：本次公钥不在列表里 → 追加，不再报错
    manifest2 = load_manifest(_造包(隔离环境, version='0.2.0'))
    registry.publish(manifest2, dry_run=False, signer='甲')

    with open(reg_pk_path, encoding='utf-8') as f:
        行 = [l.strip() for l in f if l.strip()]
    assert 行 == [假钥, 真钥]          # 先来的在前，新钥追加在后

    # 幂等：同一把钥再发一次不会重复追加
    manifest3 = load_manifest(_造包(隔离环境, version='0.3.0'))
    registry.publish(manifest3, dry_run=False, signer='甲')
    with open(reg_pk_path, encoding='utf-8') as f:
        assert len([l for l in f if l.strip()]) == 2



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


# ---------------------------------------------------------------------------
# W75 · 装包端验签（ADR-33 §2.5 / §2.7）
# ---------------------------------------------------------------------------

def _发布带签名(隔离环境, alias='甲'):
    """生成密钥 → 发布签名包，返回 PublishReport。"""
    keys.generate_keypair(alias)
    return registry.publish(load_manifest(_造包(隔离环境)),
                            dry_run=False, signer=alias)


def test_装签名包_验签通过且首次pin公钥(隔离环境):
    报告发布 = _发布带签名(隔离环境)
    proj = _造宿主(隔离环境)

    报告 = I.install(load_manifest(proj))
    assert 报告.total == 1
    assert 报告.warnings == []          # 签过名就不该有未签名告警
    assert os.path.isdir(os.path.join(proj, I.PACKAGES_DIR, '签名试包'))

    # TOFU：公钥被 pin 进信任库，且与发布方公钥一致
    pinned = os.path.join(trust.trust_root(), '甲.公钥')
    assert os.path.isfile(pinned)
    with open(pinned, encoding='utf-8') as f:
        assert base64.b64decode(f.read().strip()) == keys.load_public_key('甲')
    # 签名确实是对这个校验和签的
    assert 报告发布.checksum.startswith('sha256:')


def test_装未签名包_告警但放行(隔离环境):
    registry.publish(load_manifest(_造包(隔离环境)), dry_run=False)
    proj = _造宿主(隔离环境)

    报告 = I.install(load_manifest(proj))
    assert 报告.total == 1              # v0.20.0 过渡期：放行
    assert len(报告.warnings) == 1
    assert '未签名' in 报告.warnings[0]
    assert 'v0.21.0' in 报告.warnings[0]


def test_注册表公钥被换_pin是权威_照样装得上(隔离环境):
    """ADR-36 §2.4：pin 过之后**不再看注册表**，注册表侧被改无关紧要。

    v0.21.0 的行为是「注册表公钥 != pin 就拒装」，那是错的：只要签名本身
    还能被 pin 过的公钥验过，包就是没被动过的，注册表侧那个文件只是分发
    渠道上的一份副本，改它不该影响已建立的信任。
    """
    _发布带签名(隔离环境)
    proj = _造宿主(隔离环境)
    I.install(load_manifest(proj))       # 首次装：pin 公钥
    真公钥 = trust.pinned_keys('甲')
    assert len(真公钥) == 1

    reg_pk = registry.registry_key_path(registry.registry_root(), '甲')
    with open(reg_pk, 'w', encoding='utf-8', newline='\n') as f:
        f.write(base64.b64encode(b'\x01' * 32).decode() + '\n')

    报告 = I.install(load_manifest(proj))
    assert 报告.total == 1
    assert trust.pinned_keys('甲') == 真公钥      # 注册表那把没被并进来


def test_注册表换公钥且包重签_拒装并指向密钥信任(隔离环境):
    """真正的轮换/投毒场景：注册表有新公钥 + 包用新私钥重签，pin 还是旧钥。

    此时签名验不过任何一把受信公钥 → 必须拒，且错误信息要指向显式动作
    `jk 包 密钥 信任`（而不是含糊地说「签名失败」，那会把轮换当成攻击）。
    """
    _发布带签名(隔离环境)
    proj = _造宿主(隔离环境)
    I.install(load_manifest(proj))       # pin 旧公钥
    旧公钥 = trust.pinned_keys('甲')

    # 攻击者/轮换者：换注册表公钥 + 用新私钥对同一校验和重签
    keys.generate_keypair('乙')
    新公钥 = keys.load_public_key('乙')
    reg_pk = registry.registry_key_path(registry.registry_root(), '甲')
    with open(reg_pk, 'w', encoding='utf-8', newline='\n') as f:
        f.write(base64.b64encode(新公钥).decode() + '\n')

    base = registry.registry_root()
    shard_path = registry._category_path(base, '通用')
    with open(shard_path, encoding='utf-8') as f:
        shard = json.load(f)
    条目 = shard['签名试包']['0.1.0']
    条目['签名'] = base64.b64encode(
        ed.sign(keys.load_private_key('乙'), 条目['校验和'].encode('utf-8'))
    ).decode()
    with open(shard_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(shard, f, ensure_ascii=False)

    with pytest.raises(I.InstallError, match='密钥 信任'):
        I.install(load_manifest(proj))
    assert trust.pinned_keys('甲') == 旧公钥


def test_轮换后老包仍验签通过(隔离环境):
    """ADR-36 §3 反例 4：追加新公钥后，用旧钥签的老包不能失效。"""
    _发布带签名(隔离环境)
    proj = _造宿主(隔离环境)
    I.install(load_manifest(proj))       # pin 旧公钥

    keys.generate_keypair('乙')          # 冒充「甲轮换后的新钥」
    新公钥 = base64.b64encode(keys.load_public_key('乙')).decode()
    assert trust.trust_key('甲', 新公钥) is True
    assert trust.trust_key('甲', 新公钥) is False        # 幂等
    受信 = trust.pinned_keys('甲')
    assert len(受信) == 2
    assert base64.b64encode(受信[0]).decode() != 新公钥  # 主钥仍是第一行

    assert I.install(load_manifest(proj)).total == 1     # 老包照样装

    # 撤掉旧主钥后，老包立刻失效（撤信是真的生效，不是摆设）
    trust.untrust_key('甲', base64.b64encode(受信[0]).decode())
    assert trust.pinned_keys('甲') == [受信[1]]
    with pytest.raises(I.InstallError):
        I.install(load_manifest(proj))


def test_撤信到一把不剩_回到未建立信任(隔离环境):
    _发布带签名(隔离环境)
    proj = _造宿主(隔离环境)
    I.install(load_manifest(proj))
    (pk,) = trust.pinned_keys('甲')

    assert trust.untrust_key('甲', base64.b64encode(pk).decode()) is True
    assert trust.pinned_keys('甲') == []
    assert not os.path.exists(os.path.join(trust.trust_root(), '甲.公钥'))
    # 再撤一次是 no-op
    assert trust.untrust_key('甲', base64.b64encode(pk).decode()) is False

    # 回到 TOFU 首次使用：从注册表重新 pin
    assert I.install(load_manifest(proj)).total == 1
    assert trust.pinned_keys('甲') == [pk]


def test_pin文件多行带注释_全部受信(隔离环境):
    """手工编辑场景：空行与 # 注释不该把 pin 文件搞成非法格式。"""
    _发布带签名(隔离环境)
    proj = _造宿主(隔离环境)
    I.install(load_manifest(proj))
    (pk,) = trust.pinned_keys('甲')

    keys.generate_keypair('乙')
    另一把 = base64.b64encode(keys.load_public_key('乙')).decode()
    with open(os.path.join(trust.trust_root(), '甲.公钥'),
              'w', encoding='utf-8', newline='\n') as f:
        f.write('# 2026-08 轮换前的旧钥\n')
        f.write(base64.b64encode(pk).decode() + '\n')
        f.write('\n')
        f.write('# 新钥\n')
        f.write(另一把 + '\n')
        f.write(另一把 + '\n')          # 重复行被去重

    受信 = trust.pinned_keys('甲')
    assert len(受信) == 2 and 受信[0] == pk
    assert I.install(load_manifest(proj)).total == 1


def test_签名被篡改_拒装(隔离环境):
    _发布带签名(隔离环境)
    proj = _造宿主(隔离环境)

    # 直接改分片里的签名字段（长度合法但内容错）
    base = registry.registry_root()
    shard_path = registry._category_path(base, '通用')
    with open(shard_path, encoding='utf-8') as f:
        shard = json.load(f)
    shard['签名试包']['0.1.0']['签名'] = base64.b64encode(b'\x00' * 64).decode()
    with open(shard_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(shard, f, ensure_ascii=False)

    with pytest.raises(I.InstallError, match='签名校验失败'):
        I.install(load_manifest(proj))


def test_校验和不符_拒装(隔离环境):
    """索引里的校验和与快照实际内容不符 → 硬拒（独立于签名的完整性地板）。"""
    _发布带签名(隔离环境)
    proj = _造宿主(隔离环境)

    # 篡改注册表快照里的源码（校验和随之变化，与索引记录不再一致）
    snapshot = os.path.join(registry.registry_root(), '包', '签名试包', '0.1.0')
    with open(os.path.join(snapshot, '主.jk'), 'a', encoding='utf-8') as f:
        f.write('打印("坏")\n')

    with pytest.raises(I.InstallError, match='完整性校验失败'):
        I.install(load_manifest(proj))


def test_白名单外的签名者_拒装(隔离环境, monkeypatch):
    _发布带签名(隔离环境)
    proj = _造宿主(隔离环境)
    monkeypatch.setenv(trust.TRUSTED_SIGNERS_ENV, '乙' + os.pathsep + '丙')

    with pytest.raises(I.InstallError, match='白名单'):
        I.install(load_manifest(proj))


def test_白名单内的签名者_放行(隔离环境, monkeypatch):
    _发布带签名(隔离环境)
    proj = _造宿主(隔离环境)
    monkeypatch.setenv(trust.TRUSTED_SIGNERS_ENV, '乙' + os.pathsep + '甲')

    报告 = I.install(load_manifest(proj))
    assert 报告.total == 1


def test_白名单未设置返回None_设空则全拒(隔离环境, monkeypatch):
    monkeypatch.delenv(trust.TRUSTED_SIGNERS_ENV, raising=False)
    assert trust.trusted_signers() is None
    assert trust.is_signer_allowed('随便谁') is True

    monkeypatch.setenv(trust.TRUSTED_SIGNERS_ENV, '')
    assert trust.trusted_signers() == set()
    assert trust.is_signer_allowed('甲') is False


def test_路径依赖不发未签名告警(隔离环境):
    """路径来源没有索引条目，对它告警是噪声（会淹掉真正该看的注册表告警）。"""
    源 = _造包(隔离环境)
    proj = 隔离环境 / '宿主'
    proj.mkdir(parents=True, exist_ok=True)
    (proj / MANIFEST_NAME).write_text(json.dumps({
        '名称': '宿主', '版本': '0.1.0', '描述': 'x', '入口': 'main.jk',
        '依赖': {'签名试包': {'路径': 源}},
    }, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    (proj / 'main.jk').write_text('打印("好")\n', encoding='utf-8')

    报告 = I.install(load_manifest(str(proj)))
    assert 报告.total == 1
    assert 报告.warnings == []


def test_信任库缺公钥且注册表也没有_拒装(隔离环境):
    _发布带签名(隔离环境)
    proj = _造宿主(隔离环境)
    # 管理员误删了注册表里的公钥，信任库又还没 pin 过 → 无从建立信任
    os.remove(registry.registry_key_path(registry.registry_root(), '甲'))

    with pytest.raises(I.InstallError, match='没有公钥'):
        I.install(load_manifest(proj))

