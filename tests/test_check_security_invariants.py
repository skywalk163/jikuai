# -*- coding: utf-8 -*-
"""G19 门禁反例守护（v0.21.0 M22 W87）。

沿 G16/G17 反例测试的规矩：至少三类反例证明门禁真能抓漂移。
若门禁只在正例过则等于形同虚设——攻击面本来就是「删掉才不安全」的东西。

反例通过 monkeypatch 篡改**副本**（临时改被 import 的模块属性 / 用 tmp 目录
覆盖源码），不改真源文件。测完自动恢复。
"""

import os
import sys

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SCRIPTS = os.path.join(_REPO, 'scripts')
_SRC = os.path.join(_REPO, 'src')
for _p in (_SCRIPTS, _SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import check_security_invariants as G19                              # noqa: E402


def _篡改源码(monkeypatch, 目标rel, 替换fn):
    """让 G19._读源码 对指定文件返回篡改后的源码，其余文件走真源。

    比整棵 tmp 目录树省事——G19 会读好几个文件，只想改其中一个。
    """
    原始 = G19._读源码

    def 假读(rel_path):
        src = 原始(rel_path)
        if rel_path == 目标rel:
            return 替换fn(src)
        return src

    monkeypatch.setattr(G19, '_读源码', 假读)


# --- 正例：真源应通过 -----------------------------------------------------

def test_G19_正例真源全绿():
    report = G19.build_report()
    assert report['ok'], f'W86 修复未生效或被回退：{report["问题"]}'
    assert report['问题'] == []


# --- 反例：G19a · .块根.json 读侧 -----------------------------------------

def test_G19a_删掉_安全块根路径_门禁应红(monkeypatch):
    from jikuai.pkg import installer
    monkeypatch.delattr(installer, '安全块根路径', raising=True)
    report = G19.build_report()
    codes = [p['规则'] for p in report['问题']]
    assert 'G19a' in codes


def test_G19a_module_loader_少了commonpath校验_门禁应红(monkeypatch):
    """把 module_loader.py 源码里的 `commonpath` 挖掉，门禁应红。"""
    _篡改源码(monkeypatch, 'jikuai/module_loader.py',
             lambda s: s.replace('commonpath', 'joined_paths'))
    report = G19.build_report()
    codes = [p['规则'] for p in report['问题']]
    assert 'G19a' in codes


def test_G19a_read_index_不走安全块根路径_门禁应红(monkeypatch):
    """把 installer.py 源码里对 安全块根路径() 的调用挖掉。"""
    _篡改源码(monkeypatch, 'jikuai/pkg/installer.py',
             lambda s: s.replace('安全块根路径(', '不安全直拼('))
    report = G19.build_report()
    codes = [p['规则'] for p in report['问题']]
    assert 'G19a' in codes


# --- 反例：G19b · 解压上限 ------------------------------------------------

def test_G19b_删掉_MAX_MEMBERS_门禁应红(monkeypatch):
    from jikuai.pkg import sources
    monkeypatch.delattr(sources, '_MAX_MEMBERS', raising=True)
    report = G19.build_report()
    codes = [p['规则'] for p in report['问题']]
    assert 'G19b' in codes


def test_G19b_把_MAX_MEMBER_BYTES_设成非法值_门禁应红(monkeypatch):
    from jikuai.pkg import sources
    monkeypatch.setattr(sources, '_MAX_MEMBER_BYTES', 0)
    report = G19.build_report()
    codes = [p['规则'] for p in report['问题']]
    assert 'G19b' in codes


def test_G19b_extract函数不引用上限_门禁应红(monkeypatch):
    _篡改源码(monkeypatch, 'jikuai/pkg/sources.py',
             lambda s: (s.replace('_MEMBERS_ENV', '_MEMBERS_REMOVED')
                         .replace('_MEMBER_BYTES_ENV', '_MEMBER_BYTES_REMOVED')
                         .replace('_TOTAL_BYTES_ENV', '_TOTAL_BYTES_REMOVED')))
    report = G19.build_report()
    codes = [p['规则'] for p in report['问题']]
    assert 'G19b' in codes


# --- 反例：G19c · HTTP 响应体上限 -----------------------------------------

def test_G19c_删掉_MAX_RESPONSE_BYTES_门禁应红(monkeypatch):
    from jikuai.pkg import backend
    monkeypatch.delattr(backend, '_MAX_RESPONSE_BYTES', raising=True)
    report = G19.build_report()
    codes = [p['规则'] for p in report['问题']]
    assert 'G19c' in codes


def test_G19c__request改回裸_read_门禁应红(monkeypatch):
    """把分块读整段替换回裸 `resp.read()`，门禁应抓到。"""
    _篡改源码(monkeypatch, 'jikuai/pkg/backend.py',
             lambda s: s.replace(
                 'chunks = []',
                 'return resp.read()\n                _dead0 = []'))
    report = G19.build_report()
    codes = [p['规则'] for p in report['问题']]
    assert 'G19c' in codes


def test_G19c_HttpBackend_slots少_max_response_门禁应红(monkeypatch):
    _篡改源码(monkeypatch, 'jikuai/pkg/backend.py',
             lambda s: s.replace("'_max_response'",
                                 "'_max_response_removed_marker'"))
    report = G19.build_report()
    codes = [p['规则'] for p in report['问题']]
    assert 'G19c' in codes
