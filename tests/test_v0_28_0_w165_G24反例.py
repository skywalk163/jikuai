# -*- coding: utf-8 -*-
"""G24 演示端点安全契约门禁的**反例**测试（v0.28.0 W165）。

沿用 v0.18.0 W55 / v0.19.0 W62 / v0.26.0 W144 的规矩：**新门禁必造反例**，
逐一证明它抓得到。只测正例等于没有门禁。

六类反例（静态 4 + 行为 2 各覆盖）：

1. 删掉鉴权判断        → 行为 B1 该红（无 Token 不再 401）
2. 白名单清空          → 静态 S2 该红
3. 白名单过滤被短路    → 行为 B3 该红
4. 换回进程内执行      → 静态 S1 该红
5. 路径闸被短路        → 行为 B4 该红
6. `subprocess.run` 去掉 `timeout=` → 静态 S4 该红

**反例副本必须放在 `tools/web/` 下**（不能放 `tmp_path`）：`demo_server.py` 靠
`__file__` 往上两级算 `_REPO` 再挂 `src/`，挪到别处就 import 不到 `jikuai`，
那样红的是「加载失败」而不是「门禁抓到了」——证不出任何东西。副本用
`_g24反例_` 前缀命名，`finally` 里删掉。
"""

import importlib.util
import os
import sys
import uuid

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_WEB = os.path.join(_REPO, 'tools', 'web')
_DEMO_PY = os.path.join(_WEB, 'demo_server.py')
_GATE_PY = os.path.join(_REPO, 'scripts', 'check_demo_endpoint_contract.py')


def _加载门禁():
    spec = importlib.util.spec_from_file_location('_g24_gate', _GATE_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


门禁 = _加载门禁()


def _原文():
    with open(_DEMO_PY, 'r', encoding='utf-8') as f:
        return f.read()


class _反例副本:
    """把改坏的源码写进 `tools/web/` 下的临时文件，用完删掉。"""

    def __init__(self, 文本):
        self.文本 = 文本
        self.路径 = os.path.join(_WEB, '_g24反例_%s.py' % uuid.uuid4().hex[:8])

    def __enter__(self):
        with open(self.路径, 'w', encoding='utf-8', newline='\n') as f:
            f.write(self.文本)
        return self.路径

    def __exit__(self, *_):
        try:
            os.unlink(self.路径)
        except OSError:
            pass
        return False


def _改(旧, 新, *, 次数=None):
    """在原文里做一处替换，替换不到就当场失败（防「反例其实没改坏」）。"""
    文本 = _原文()
    assert 旧 in 文本, '反例锚点已漂：找不到 %r' % 旧
    实际 = 文本.count(旧)
    if 次数 is not None:
        assert 实际 == 次数, '反例锚点出现 %d 次，预期 %d 次' % (实际, 次数)
    return 文本.replace(旧, 新)


# ---- 先证正例是绿的（否则下面的红说明不了问题）--------------------------

def test_未改动的演示端点门禁全绿():
    assert 门禁.静态检查(_DEMO_PY) == []
    assert 门禁.行为检查(_DEMO_PY) == []


# ---- 反例 1：删掉鉴权判断 → 行为 B1 该红 -------------------------------

def test_反例_删掉鉴权判断被抓():
    文本 = _改('        _核验令牌(_提取Bearer(self.headers))\n', '',
             次数=2)
    with _反例副本(文本) as 路径:
        问题 = 门禁.行为检查(路径)
    assert any('B1' in 条 for 条 in 问题), 问题


# ---- 反例 2 / 3：白名单 -----------------------------------------------

def test_反例_白名单清空被抓():
    文本 = _原文()
    起 = 文本.index('允许块 = frozenset({')
    止 = 文本.index('})', 起) + 2
    文本 = 文本[:起] + '允许块 = frozenset({})' + 文本[止:]
    with _反例副本(文本) as 路径:
        问题 = 门禁.静态检查(路径)
    assert any('S2' in 条 for 条 in 问题), 问题


def test_反例_白名单过滤被短路被抓():
    文本 = _改('def _校验白名单(方案):\n',
             'def _校验白名单(方案):\n    return 方案  # 反例：短路\n')
    with _反例副本(文本) as 路径:
        问题 = 门禁.行为检查(路径)
    assert any('B3' in 条 for 条 in 问题), 问题


# ---- 反例 4：换回进程内执行 → 静态 S1 该红 -----------------------------

def test_反例_换回进程内执行被抓():
    文本 = _改('def _子进程跑(源码):\n',
             'def _子进程跑(源码):\n'
             '    from jikuai.main import run_source\n'
             '    run_source(源码)  # 反例：进程内执行\n')
    with _反例副本(文本) as 路径:
        问题 = 门禁.静态检查(路径)
    assert any('S1' in 条 for 条 in 问题), 问题


# ---- 反例 5：路径闸被短路 → 行为 B4 该红 -------------------------------

def test_反例_路径闸被短路被抓():
    文本 = _改('def _校验数据集路径(方案):\n',
             'def _校验数据集路径(方案):\n    return 方案  # 反例：短路\n')
    with _反例副本(文本) as 路径:
        问题 = 门禁.行为检查(路径)
    assert any('B4' in 条 for 条 in 问题), 问题


# ---- 反例 6：subprocess.run 去掉 timeout → 静态 S4 该红 ----------------

def test_反例_去掉执行超时被抓():
    文本 = _改('                errors=\'replace\', timeout=EXEC_TIMEOUT, '
             'cwd=_REPO, env=环境,\n',
             '                errors=\'replace\', cwd=_REPO, env=环境,\n')
    with _反例副本(文本) as 路径:
        问题 = 门禁.静态检查(路径)
    assert any('S4' in 条 for 条 in 问题), 问题


# ---- 反例 7：命令行收 Token → 静态 S3 该红 -----------------------------

def test_反例_命令行收令牌被抓():
    文本 = _改("    args = p.parse_args(argv)\n",
             "    p.add_argument('--令牌', dest='token')  # 反例：命令行收 key\n"
             "    args = p.parse_args(argv)\n")
    with _反例副本(文本) as 路径:
        问题 = 门禁.静态检查(路径)
    assert any('S3' in 条 for 条 in 问题), 问题


# ---- 收尾：反例副本没有残留 -------------------------------------------

def test_反例副本已清干净():
    残留 = [名 for 名 in os.listdir(_WEB) if 名.startswith('_g24反例_')]
    assert not 残留, '反例副本残留在 tools/web/：%s' % 残留
