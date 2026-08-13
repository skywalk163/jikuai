# -*- coding: utf-8 -*-
"""v0.22.0 · W99 · 来源抓取 `pkg/sources.py` 的错误分支与安全上限。

之前 sources.py 只被 installer 间接跑过正路径（70.1%），三种来源的错误
分支、解压炸弹上限、路径穿越校验基本没测。这个文件补：

  1. `_ensure_within`：越出基准目录 / 跨盘符
  2. `_fetch_path`：目录不存在、缺清单、包名不匹配
  3. `_fetch_git`：git 不在 PATH、缺「仓库」字段、clone 失败（子进程报错 /
     OSError）、clone 后缺清单、包名不匹配、成功路径
  4. `_safe_extract_targz`：链接成员、设备节点、绝对路径成员、`..` 穿越、
     成员数上限、单成员上限、合计上限
  5. `_limit`：环境变量覆盖生效 / 非数字与非正数回落默认
  6. `_fetch_registry`：本地快照缺清单、包名不匹配、索引读不到签名时不拦、
     远程定位符拿不到后端
  7. `resolve_source`：未知种类
  8. `compute_checksum` / `_iter_source_files`：只挑源码扩展、跳过隐藏目录
     与 `极快_包`、同内容不同路径哈希不同、确定性

git 相关分支刻意**不依赖本机装了 git**：`subprocess.run` 用 monkeypatch
替掉，既能覆盖失败分支也能覆盖成功分支，CI 上不会因环境差异飘。
"""

import io
import json
import os
import subprocess
import sys
import tarfile

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg import sources as S                              # noqa: E402
from jikuai.pkg.manifest import MANIFEST_NAME, Dependency        # noqa: E402


# ---------------------------------------------------------------------------
# 造数据
# ---------------------------------------------------------------------------

def _造包目录(目录, 名称='甲', 版本='0.1.0'):
    os.makedirs(目录, exist_ok=True)
    with open(os.path.join(目录, MANIFEST_NAME), 'w', encoding='utf-8',
              newline='\n') as f:
        json.dump({'名称': 名称, '版本': 版本, '描述': '', '入口': 'main.jk',
                   '依赖': {}}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(目录, 'main.jk'), 'w', encoding='utf-8',
              newline='\n') as f:
        f.write('打印("好")\n')
    return 目录


def _打tar(成员, 类型=None):
    """把 `[(名, 内容)]` 打成 tar.gz 字节流。`类型` 非空时造特殊成员。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for 名, 内容 in 成员:
            info = tarfile.TarInfo(名)
            if 类型 is not None:
                info.type = 类型
                if 类型 in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    info.linkname = 'main.jk'
                tf.addfile(info)
                continue
            data = 内容.encode('utf-8')
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _ensure_within
# ---------------------------------------------------------------------------

def test_路径校验_树内放行(tmp_path):
    里 = str(tmp_path / '子' / '孙.jk')
    assert S._ensure_within(str(tmp_path), 里) == os.path.abspath(里)


def test_路径校验_越出基准目录被拒(tmp_path):
    with pytest.raises(S.SourceError) as e:
        S._ensure_within(str(tmp_path / '基准'), str(tmp_path / '外面.jk'))
    assert '越出基准目录' in str(e.value)


@pytest.mark.skipif(sys.platform != 'win32', reason='跨盘符只在 Windows 成立')
def test_路径校验_跨盘符被拒():
    with pytest.raises(S.SourceError) as e:
        S._ensure_within('C:\\基准', 'D:\\外面')
    assert '不在同一根下' in str(e.value)


# ---------------------------------------------------------------------------
# _fetch_path
# ---------------------------------------------------------------------------

def test_路径来源_成功不复制目录(tmp_path):
    src = _造包目录(str(tmp_path / '甲'))
    got = S.resolve_source(Dependency('甲', path='甲'), str(tmp_path))
    assert got.kind == '路径'
    assert got.ephemeral is False          # 用户已有目录，绝不能删
    assert got.root == os.path.abspath(src)
    assert got.signer == '' and got.expected_checksum == ''


def test_路径来源_绝对路径也认(tmp_path):
    src = _造包目录(str(tmp_path / '甲'))
    got = S.resolve_source(Dependency('甲', path=src), str(tmp_path / '无关'))
    assert got.root == os.path.abspath(src)


def test_路径来源_目录不存在(tmp_path):
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(Dependency('甲', path='没有的'), str(tmp_path))
    assert '指向的目录不存在' in str(e.value)


def test_路径来源_缺清单(tmp_path):
    os.makedirs(tmp_path / '甲')
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(Dependency('甲', path='甲'), str(tmp_path))
    assert '缺少可读的 包.json' in str(e.value)


def test_路径来源_包名不匹配(tmp_path):
    _造包目录(str(tmp_path / '甲'), 名称='其实叫乙')
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(Dependency('甲', path='甲'), str(tmp_path))
    assert '路径依赖名不匹配' in str(e.value)


# ---------------------------------------------------------------------------
# _fetch_git
# ---------------------------------------------------------------------------

def test_仓库来源_git不在PATH(monkeypatch, tmp_path):
    monkeypatch.setattr(S, '_git_available', lambda: False)
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(Dependency('甲', repo='https://x.git'), str(tmp_path))
    assert '未找到 `git`' in str(e.value)


def test_仓库来源_缺仓库字段(monkeypatch, tmp_path):
    monkeypatch.setattr(S, '_git_available', lambda: True)
    with pytest.raises(S.SourceError) as e:
        S._fetch_git(Dependency('甲'), str(tmp_path))     # repo=None
    assert '缺少「仓库」字段' in str(e.value)


def _假clone(monkeypatch, 行为):
    """替掉 sources 里的 subprocess.run，`行为(argv)` 决定 clone 结果。"""
    monkeypatch.setattr(S, '_git_available', lambda: True)

    def 跑(argv, **_kw):
        行为(argv)
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(S.subprocess, 'run', 跑)


def test_仓库来源_clone失败带上git的stderr(monkeypatch, tmp_path):
    def 炸(argv):
        raise subprocess.CalledProcessError(
            128, argv, output='', stderr='fatal: repository not found')
    _假clone(monkeypatch, 炸)
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(Dependency('甲', repo='https://x.git'), str(tmp_path))
    assert '抓取失败' in str(e.value)
    assert 'repository not found' in str(e.value)


def test_仓库来源_clone起不来是OSError(monkeypatch, tmp_path):
    def 炸(argv):
        raise OSError('exec 不了')
    _假clone(monkeypatch, 炸)
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(Dependency('甲', repo='https://x.git'), str(tmp_path))
    assert '抓取失败' in str(e.value)


def test_仓库来源_clone出来缺清单(monkeypatch, tmp_path):
    _假clone(monkeypatch, lambda argv: os.makedirs(argv[-1], exist_ok=True))
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(Dependency('甲', repo='https://x.git'), str(tmp_path))
    assert '缺少可读的 包.json' in str(e.value)


def test_仓库来源_包名不匹配(monkeypatch, tmp_path):
    _假clone(monkeypatch,
             lambda argv: _造包目录(argv[-1], 名称='其实叫乙'))
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(Dependency('甲', repo='https://x.git'), str(tmp_path))
    assert '仓库依赖名不匹配' in str(e.value)


def test_仓库来源_成功且标签进argv(monkeypatch, tmp_path):
    见到 = {}

    def 造(argv):
        见到['argv'] = list(argv)
        _造包目录(argv[-1], 名称='甲')
    _假clone(monkeypatch, 造)
    got = S.resolve_source(
        Dependency('甲', repo='https://x.git', tag='v1.2.0'), str(tmp_path))
    assert got.kind == '仓库'
    assert got.ephemeral is True           # 临时目录，装完要清
    assert got.origin == 'https://x.git'
    assert '--depth' in 见到['argv']
    assert 见到['argv'][见到['argv'].index('--branch') + 1] == 'v1.2.0'
    assert '--' in 见到['argv']             # 防 URL 被当选项


# ---------------------------------------------------------------------------
# _safe_extract_targz：路径安全
# ---------------------------------------------------------------------------

def test_解压_正常归档(tmp_path):
    data = _打tar([('甲/main.jk', '打印("好")\n')])
    S._safe_extract_targz(data, str(tmp_path))
    assert (tmp_path / '甲' / 'main.jk').is_file()


@pytest.mark.parametrize('类型,片段', [
    (tarfile.SYMTYPE, '含链接成员'),
    (tarfile.LNKTYPE, '含链接成员'),
    (tarfile.CHRTYPE, '含设备节点'),
    (tarfile.BLKTYPE, '含设备节点'),
])
def test_解压_拒绝链接与设备节点(tmp_path, 类型, 片段):
    data = _打tar([('坏东西', '')], 类型=类型)
    with pytest.raises(S.SourceError) as e:
        S._safe_extract_targz(data, str(tmp_path))
    assert 片段 in str(e.value)


def test_解压_拒绝绝对路径成员(tmp_path):
    data = _打tar([('/etc/passwd', 'x')])
    with pytest.raises(S.SourceError) as e:
        S._safe_extract_targz(data, str(tmp_path))
    assert '含绝对路径成员' in str(e.value)


def test_解压_拒绝上跳穿越(tmp_path):
    data = _打tar([('../外面.jk', 'x')])
    with pytest.raises(S.SourceError) as e:
        S._safe_extract_targz(data, str(tmp_path))
    assert '越出解压目录' in str(e.value)


def test_解压_反斜杠也按分隔符看(tmp_path):
    """Windows 风格分隔符不能绕过穿越校验。"""
    data = _打tar([('..\\外面.jk', 'x')])
    with pytest.raises(S.SourceError) as e:
        S._safe_extract_targz(data, str(tmp_path))
    assert '越出解压目录' in str(e.value)


# ---------------------------------------------------------------------------
# _safe_extract_targz：体量上限（W86）
# ---------------------------------------------------------------------------

def test_解压_成员数超上限(tmp_path, monkeypatch):
    monkeypatch.setenv(S._MEMBERS_ENV, '2')
    data = _打tar([(f'f{i}.jk', 'x') for i in range(3)])
    with pytest.raises(S.SourceError) as e:
        S._safe_extract_targz(data, str(tmp_path))
    assert '超过上限 2' in str(e.value)
    assert S._MEMBERS_ENV in str(e.value)      # 给出逃生门


def test_解压_单成员超上限(tmp_path, monkeypatch):
    monkeypatch.setenv(S._MEMBER_BYTES_ENV, '4')
    data = _打tar([('大.jk', 'x' * 100)])
    with pytest.raises(S.SourceError) as e:
        S._safe_extract_targz(data, str(tmp_path))
    assert '超过单成员上限 4' in str(e.value)


def test_解压_合计超上限(tmp_path, monkeypatch):
    monkeypatch.setenv(S._MEMBER_BYTES_ENV, '100')
    monkeypatch.setenv(S._TOTAL_BYTES_ENV, '120')
    data = _打tar([('a.jk', 'x' * 80), ('b.jk', 'y' * 80)])
    with pytest.raises(S.SourceError) as e:
        S._safe_extract_targz(data, str(tmp_path))
    assert '合计超过上限 120' in str(e.value)


@pytest.mark.parametrize('原始值', ['不是数字', '0', '-5', ''])
def test_上限_配错回落默认值(monkeypatch, 原始值):
    """上限是安全网，配错要退回更安全的默认值而不是让导入挂掉。"""
    monkeypatch.setenv(S._MEMBERS_ENV, 原始值)
    assert S._limit(S._MEMBERS_ENV, S._MAX_MEMBERS) == S._MAX_MEMBERS


def test_上限_正整数生效(monkeypatch):
    monkeypatch.setenv(S._MEMBERS_ENV, '7')
    assert S._limit(S._MEMBERS_ENV, S._MAX_MEMBERS) == 7


def test_上限_未设时用默认(monkeypatch):
    monkeypatch.delenv(S._MEMBERS_ENV, raising=False)
    assert S._limit(S._MEMBERS_ENV, 123) == 123


# ---------------------------------------------------------------------------
# _fetch_registry
# ---------------------------------------------------------------------------

def test_注册表来源_快照缺清单(monkeypatch, tmp_path):
    from jikuai.pkg import registry
    空 = tmp_path / '快照'
    空.mkdir()
    monkeypatch.setattr(registry, 'lookup',
                        lambda *a, **k: ('1.0.0', str(空)))
    monkeypatch.setenv('JIKUAI_REGISTRY', str(tmp_path / '注册表'))
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(Dependency('甲', constraint='*'), str(tmp_path))
    assert '缺少可读的 包.json' in str(e.value)


def test_注册表来源_包名不匹配(monkeypatch, tmp_path):
    from jikuai.pkg import registry
    快照 = _造包目录(str(tmp_path / '快照'), 名称='其实叫乙')
    monkeypatch.setattr(registry, 'lookup', lambda *a, **k: ('1.0.0', 快照))
    monkeypatch.setenv('JIKUAI_REGISTRY', str(tmp_path / '注册表'))
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(Dependency('甲', constraint='*'), str(tmp_path))
    assert '注册表包名不匹配' in str(e.value)


def test_注册表来源_索引读不到签名时不拦(monkeypatch, tmp_path):
    """签名读不到应当交给装包端按「无签名」处理，抓取阶段抛错会误报包不存在。"""
    from jikuai.pkg import registry
    快照 = _造包目录(str(tmp_path / '快照'), 名称='甲')

    def 炸(*a, **k):
        raise registry.RegistryError('索引坏了')
    monkeypatch.setattr(registry, 'lookup', lambda *a, **k: ('1.0.0', 快照))
    monkeypatch.setattr(registry, 'lookup_signature', 炸)
    monkeypatch.setenv('JIKUAI_REGISTRY', str(tmp_path / '注册表'))
    got = S.resolve_source(Dependency('甲', constraint='*'), str(tmp_path))
    assert got.kind == '注册表'
    assert got.ephemeral is False          # 只读快照，不能删
    assert (got.signer, got.signature, got.expected_checksum) == ('', '', '')


def test_注册表来源_lookup报错转成SourceError(monkeypatch, tmp_path):
    from jikuai.pkg import registry

    def 炸(*a, **k):
        raise registry.RegistryError('注册表里没有包 甲')
    monkeypatch.setattr(registry, 'lookup', 炸)
    monkeypatch.setenv('JIKUAI_REGISTRY', str(tmp_path / '注册表'))
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(Dependency('甲', constraint='*'), str(tmp_path))
    assert '没有包 甲' in str(e.value)


def test_注册表来源_远程后端打不开(monkeypatch, tmp_path):
    from jikuai.pkg import backend as B

    def 炸(*a, **k):
        raise B.BackendError('明文 http 被拒')
    monkeypatch.setattr(B, 'get_backend', 炸)
    dep = Dependency('甲', constraint='*',
                     registry_url='https://reg.example.com')
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(dep, str(tmp_path))
    assert '明文 http 被拒' in str(e.value)


def test_注册表来源_per_dependency定位符优先(monkeypatch, tmp_path):
    """依赖自带 registry_url 时不能拿全局 JIKUAI_REGISTRY 去查。"""
    from jikuai.pkg import registry
    快照 = _造包目录(str(tmp_path / '快照'), 名称='甲')
    见到 = {}

    def 假lookup(name, constraint, root=None):
        见到['root'] = root
        return '1.0.0', 快照
    monkeypatch.setattr(registry, 'lookup', 假lookup)
    monkeypatch.setattr(registry, 'lookup_signature',
                        lambda *a, **k: ('甲签', 'sig', 'sha256:abc'))
    monkeypatch.setenv('JIKUAI_REGISTRY', str(tmp_path / '全局注册表'))
    dep = Dependency('甲', constraint='*',
                     registry_url=str(tmp_path / '专用注册表'))
    got = S.resolve_source(dep, str(tmp_path))
    assert 见到['root'] == str(tmp_path / '专用注册表')
    assert got.registry_locator == str(tmp_path / '专用注册表')
    assert got.signer == '甲签'


# ---------------------------------------------------------------------------
# resolve_source 调度
# ---------------------------------------------------------------------------

def test_调度_未知种类(tmp_path):
    class 假依赖:
        kind = '天外飞仙'
        name = '甲'
    with pytest.raises(S.SourceError) as e:
        S.resolve_source(假依赖(), str(tmp_path))
    assert '未知的依赖种类' in str(e.value)


# ---------------------------------------------------------------------------
# compute_checksum / _iter_source_files
# ---------------------------------------------------------------------------

def _写(路径, 内容=''):
    os.makedirs(os.path.dirname(路径), exist_ok=True)
    with open(路径, 'w', encoding='utf-8', newline='\n') as f:
        f.write(内容)


def test_校验和_只挑源码扩展并跳过噪声目录(tmp_path):
    根 = str(tmp_path / '包')
    _写(os.path.join(根, 'main.jk'), 'a')
    _写(os.path.join(根, '工具.py'), 'b')
    _写(os.path.join(根, MANIFEST_NAME), '{}')
    _写(os.path.join(根, '说明.md'), '不该算')          # 扩展不在白名单
    _写(os.path.join(根, '.git', 'config.json'), '不该算')
    _写(os.path.join(根, '__pycache__', 'x.py'), '不该算')
    _写(os.path.join(根, '极快_包', '乙', 'main.jk'), '不该算')
    _写(os.path.join(根, 'node_modules', 'x.json'), '不该算')

    参与 = [rel for rel, _ in S._iter_source_files(根)]
    assert 参与 == ['main.jk', MANIFEST_NAME, '工具.py']   # 按相对路径码位升序

    h, total = S.compute_checksum(根)
    assert len(h) == 64
    assert total == len('a') + len('b') + len('{}')


def test_校验和_确定性且对路径敏感(tmp_path):
    甲 = str(tmp_path / '甲')
    乙 = str(tmp_path / '乙')
    _写(os.path.join(甲, '子', 'main.jk'), '同样的内容')
    _写(os.path.join(乙, '另', 'main.jk'), '同样的内容')
    assert S.compute_checksum(甲)[0] == S.compute_checksum(甲)[0]
    # 文件名进哈希，所以相对路径不同则校验和不同（防换目录冒充同一份源码）
    assert S.compute_checksum(甲)[0] != S.compute_checksum(乙)[0]


def test_校验和_空目录也有稳定值(tmp_path):
    空 = str(tmp_path / '空')
    os.makedirs(空)
    h, total = S.compute_checksum(空)
    assert total == 0
    assert h == S.compute_checksum(空)[0]
