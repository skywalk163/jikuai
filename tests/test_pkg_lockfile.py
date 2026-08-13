# -*- coding: utf-8 -*-
"""v0.22.0 · W99 · 锁文件 `包.锁` 的读写与校验分支。

`pkg/lockfile.py` 之前只被 installer 间接跑过正路径（68.4%），所有校验
分支和大半容器接口没有测。这个文件按「确定性 + 拒读损坏文件」两条契约补：

  1. 序列化：可选字段空则不写键、条目按名排序、`依赖` 内部也排序
  2. 反序列化：非对象条目 / 缺必填字段 / `依赖` 非数组一律抛 LockError
  3. 容器接口：len / in / iter / get / put / remove / names
  4. `load_lockfile`：不存在→空锁、传目录→自动拼文件名、非法 JSON、
     顶层非对象、锁版本不兼容、`包` 非数组
  5. `save_lockfile`：原子写、传目录时自动拼文件名、回填 `lock.path`
"""

import json
import os
import sys

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg.lockfile import (                                # noqa: E402
    LOCKFILE_NAME, LOCK_VERSION, LockError,
    LockedPackage, Lockfile, load_lockfile, save_lockfile,
)


# ---------------------------------------------------------------------------
# LockedPackage 序列化
# ---------------------------------------------------------------------------

def test_条目_空可选字段不写键():
    d = LockedPackage('甲', '1.0.0').to_dict()
    assert d == {'名称': '甲', '版本': '1.0.0', '来源': '注册表'}


def test_条目_全字段都写出():
    p = LockedPackage('甲', '1.2.0', source='仓库', constraint='^1.0.0',
                      path='../甲', repo='https://x.git', tag='v1.2.0',
                      checksum='sha256:abc', deps=['丙', '乙'])
    d = p.to_dict()
    assert d['来源'] == '仓库'
    assert d['约束'] == '^1.0.0'
    assert d['路径'] == '../甲'
    assert d['仓库'] == 'https://x.git'
    assert d['标签'] == 'v1.2.0'
    assert d['校验和'] == 'sha256:abc'
    assert d['依赖'] == ['丙', '乙']          # 构造时就排好序（按码位）


def test_条目_repr带名称版本来源():
    assert repr(LockedPackage('甲', '1.0.0')) == '<锁定 甲@1.0.0 来源=注册表>'


def test_条目_往返一致():
    原 = LockedPackage('甲', '1.2.0', source='路径', path='../甲',
                       checksum='sha256:abc', deps=['乙'])
    回 = LockedPackage.from_dict(原.to_dict())
    assert 回.to_dict() == 原.to_dict()


@pytest.mark.parametrize('坏条目,片段', [
    ('不是对象', '必须是对象'),
    (['也不是'], '必须是对象'),
    ({'版本': '1.0.0'}, '缺少「名称」'),
    ({'名称': '甲'}, '缺少「版本」'),
    ({'名称': '甲', '版本': '1.0.0', '依赖': '不是数组'}, '「依赖」必须是数组'),
])
def test_条目_反序列化拒绝坏数据(坏条目, 片段):
    with pytest.raises(LockError) as e:
        LockedPackage.from_dict(坏条目)
    assert 片段 in str(e.value)


def test_条目_依赖为null当空处理():
    p = LockedPackage.from_dict({'名称': '甲', '版本': '1.0.0', '依赖': None})
    assert p.deps == []


# ---------------------------------------------------------------------------
# Lockfile 容器接口
# ---------------------------------------------------------------------------

def test_锁_容器接口():
    lock = Lockfile([LockedPackage('丙', '1.0.0'), LockedPackage('甲', '2.0.0')])
    assert len(lock) == 2
    assert '甲' in lock and '乙' not in lock
    assert lock.names() == ['丙', '甲']            # 按码位排序，确定
    assert [p.name for p in lock] == ['丙', '甲']
    assert lock.get('甲').version == '2.0.0'
    assert lock.get('乙') is None

    lock.put(LockedPackage('乙', '0.1.0'))
    assert lock.names() == ['丙', '乙', '甲']
    assert lock.remove('乙') is True
    assert lock.remove('乙') is False            # 第二次没得删
    assert '乙' not in lock


def test_锁_to_dict带版本且按名排序():
    lock = Lockfile([LockedPackage('丙', '1.0.0'), LockedPackage('甲', '2.0.0')])
    d = lock.to_dict()
    assert d['锁版本'] == LOCK_VERSION
    assert [e['名称'] for e in d['包']] == ['丙', '甲']


# ---------------------------------------------------------------------------
# load_lockfile
# ---------------------------------------------------------------------------

def test_读_文件不存在返回空锁(tmp_path):
    lock = load_lockfile(str(tmp_path / 'nope.锁'))
    assert len(lock) == 0
    assert os.path.isabs(lock.path)


def test_读_传目录时自动拼文件名(tmp_path):
    (tmp_path / LOCKFILE_NAME).write_text(json.dumps({
        '锁版本': LOCK_VERSION,
        '包': [{'名称': '甲', '版本': '1.0.0'}],
    }, ensure_ascii=False), encoding='utf-8', newline='\n')
    lock = load_lockfile(str(tmp_path))
    assert lock.names() == ['甲']
    assert lock.path.endswith(LOCKFILE_NAME)


def test_读_非法json给行号与自救提示(tmp_path):
    p = tmp_path / LOCKFILE_NAME
    p.write_text('{不是 json', encoding='utf-8', newline='\n')
    with pytest.raises(LockError) as e:
        load_lockfile(str(p))
    assert '不是合法 JSON' in str(e.value)
    assert 'jk 包 装' in str(e.value)


@pytest.mark.parametrize('内容,片段', [
    ('[]', '顶层必须是对象'),
    ('{"锁版本": 99, "包": []}', '不兼容'),
    ('{"包": []}', '不兼容'),                     # 缺锁版本 = None，一样拒读
    ('{"锁版本": 1, "包": "不是数组"}', '「包」必须是数组'),
])
def test_读_拒绝损坏或不兼容(tmp_path, 内容, 片段):
    p = tmp_path / LOCKFILE_NAME
    p.write_text(内容, encoding='utf-8', newline='\n')
    with pytest.raises(LockError) as e:
        load_lockfile(str(p))
    assert 片段 in str(e.value)


def test_读_包字段为null当空数组(tmp_path):
    p = tmp_path / LOCKFILE_NAME
    p.write_text('{"锁版本": 1, "包": null}', encoding='utf-8', newline='\n')
    assert len(load_lockfile(str(p))) == 0


# ---------------------------------------------------------------------------
# save_lockfile
# ---------------------------------------------------------------------------

def test_写_读回一致且不留临时文件(tmp_path):
    lock = Lockfile([LockedPackage('甲', '1.0.0', deps=['乙']),
                     LockedPackage('乙', '0.1.0', source='路径',
                                   path='../乙')])
    目标 = save_lockfile(lock, str(tmp_path / LOCKFILE_NAME))
    assert os.path.isfile(目标)
    assert not os.path.exists(目标 + '.tmp')
    assert lock.path == 目标                      # 回填
    回 = load_lockfile(目标)
    assert 回.to_dict() == lock.to_dict()


def test_写_传目录时自动拼文件名(tmp_path):
    目标 = save_lockfile(Lockfile([LockedPackage('甲', '1.0.0')]),
                         str(tmp_path))
    assert 目标 == str(tmp_path / LOCKFILE_NAME)


def test_写_用lock自带path(tmp_path):
    lock = Lockfile([], path=str(tmp_path / '自带.锁'))
    assert save_lockfile(lock) == str(tmp_path / '自带.锁')


def test_写_输出确定性(tmp_path):
    """同样的内容、不同的插入顺序，写出的字节必须完全一样。"""
    甲先 = Lockfile([LockedPackage('甲', '1.0.0'), LockedPackage('乙', '2.0.0')])
    乙先 = Lockfile([LockedPackage('乙', '2.0.0'), LockedPackage('甲', '1.0.0')])
    a = save_lockfile(甲先, str(tmp_path / 'a.锁'))
    b = save_lockfile(乙先, str(tmp_path / 'b.锁'))
    assert open(a, 'rb').read() == open(b, 'rb').read()
