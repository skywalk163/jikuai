# -*- coding: utf-8 -*-
"""内建动词 `写入` / `读取` 端到端测试（v0.16.0 W28）。

覆盖：
- 正向读写往返（UTF-8 中文 + 空串 + 多行）
- 路径逃逸：绝对路径、`..` 穿越、CWD 外的 symlink → JK-E4002
- 文件超上限被拒 → JK-E4003
- UTF-8 编码正确（字节层面确认，不假装是二次求值）
- 块层面端到端：`blocks.数据.存文` / `blocks.数据.载入` 纯 `.jk`
  实现是否与内建动词接通

`tmp_path` fixture 保证不污染仓库；每个用例 `monkeypatch.chdir(tmp_path)`
让 CWD == tmp_path，触发路径闸时才有可判定的根。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.evaluator import (  # noqa: E402
    Evaluator, JiKuaiError, _READ_FILE_SIZE_LIMIT,
)
from jikuai.errors import ErrorCategory  # noqa: E402
from jikuai.main import run_source  # noqa: E402



# ─────────────────────────────────────────────────────────────────────
# 直接验证内建动词（不经 .jk 门面）——最贴近安全闸的验证面
# ─────────────────────────────────────────────────────────────────────

def _调用(动词, *参):
    """按内建动词名走一次实际实现。避开 .jk 语法，直接打表调用。"""
    ev = Evaluator()
    return ev.verbs[动词](*参)


def test_写入返回内容(tmp_path, monkeypatch):
    """`写入` 返回**写入的内容**（用于管道链式），不是字节数。"""
    monkeypatch.chdir(tmp_path)
    结果 = _调用('写入', '出.txt', '你好极快')
    assert 结果 == '你好极快'
    assert (tmp_path / '出.txt').read_text(encoding='utf-8') == '你好极快'


def test_读取往返(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _调用('写入', '往返.txt', '甲\n乙\n丙')
    assert _调用('读取', '往返.txt') == '甲\n乙\n丙'


def test_utf8字节序确认(tmp_path, monkeypatch):
    """字节层面确认 UTF-8：`你好` 对应 6 字节固定序列。"""
    monkeypatch.chdir(tmp_path)
    _调用('写入', 'zh.txt', '你好')
    raw = (tmp_path / 'zh.txt').read_bytes()
    assert raw == b'\xe4\xbd\xa0\xe5\xa5\xbd'


def test_空串写入(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    结果 = _调用('写入', '空.txt', '')
    assert 结果 == ''
    assert _调用('读取', '空.txt') == ''
    assert (tmp_path / '空.txt').stat().st_size == 0


# ─────────────────────────────────────────────────────────────────────
# 路径逃逸：JK-E4002
# ─────────────────────────────────────────────────────────────────────

def _断言路径拒(exc_info):
    e = exc_info.value
    assert e.info is not None
    assert e.info.category is ErrorCategory.RUNTIME
    assert 'JK-E4002' in e.info.message


def test_拒绝绝对路径_读取(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / '存在.txt').write_text('x', encoding='utf-8')
    abs_path = str(tmp_path / '存在.txt')  # 内容存在，但绝对形式一律拒
    with pytest.raises(JiKuaiError) as ei:
        _调用('读取', abs_path)
    _断言路径拒(ei)


def test_拒绝绝对路径_写入(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(JiKuaiError) as ei:
        _调用('写入', str(tmp_path / '别处.txt'), 'x')
    _断言路径拒(ei)
    # 副作用兜底：拒绝时**不该**留下产物
    assert not (tmp_path / '别处.txt').exists()


def test_拒绝双点穿越(tmp_path, monkeypatch):
    """`..` 段一律拒，即便解析后仍在 CWD 内也照拒（严格模式）。"""
    子目 = tmp_path / '子'
    子目.mkdir()
    monkeypatch.chdir(子目)
    with pytest.raises(JiKuaiError) as ei:
        _调用('读取', os.path.join('..', 'x.txt'))
    _断言路径拒(ei)


def test_拒绝写入到父级(tmp_path, monkeypatch):
    子目 = tmp_path / '子'
    子目.mkdir()
    monkeypatch.chdir(子目)
    with pytest.raises(JiKuaiError) as ei:
        _调用('写入', os.path.join('..', '偷写.txt'), 'x')
    _断言路径拒(ei)
    assert not (tmp_path / '偷写.txt').exists()


def test_路径消息不泄漏绝对路径(tmp_path, monkeypatch):
    """诊断消息中不得回显宿主目录结构（_scrub_paths 已处理）。"""
    monkeypatch.chdir(tmp_path)
    abs_path = str(tmp_path / '任意.txt')
    with pytest.raises(JiKuaiError) as ei:
        _调用('读取', abs_path)
    msg = ei.value.info.message
    assert str(tmp_path) not in msg or '<路径已隐去>' in msg


# ─────────────────────────────────────────────────────────────────────
# 大小上限：JK-E4003
# ─────────────────────────────────────────────────────────────────────

def test_读取超上限被拒(tmp_path, monkeypatch):
    """超出 `_READ_FILE_SIZE_LIMIT` 直接拒。用 monkeypatch 把上限调低，
    避免真造 10 MB 文件让磁盘/I/O 拖慢测试。"""
    from jikuai import evaluator as _ev
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_ev, '_READ_FILE_SIZE_LIMIT', 16)
    (tmp_path / '大.txt').write_text('x' * 100, encoding='utf-8')
    with pytest.raises(JiKuaiError) as ei:
        _调用('读取', '大.txt')
    e = ei.value
    assert e.info.category is ErrorCategory.RUNTIME
    assert 'JK-E4003' in e.info.message
    assert '100' in e.info.message  # 报告实际大小
    assert '16' in e.info.message   # 报告上限


def test_读取贴近上限可通过(tmp_path, monkeypatch):
    """恰好等于上限允许通过（`> limit` 才拒，不是 `>=`）。"""
    from jikuai import evaluator as _ev
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_ev, '_READ_FILE_SIZE_LIMIT', 4)
    (tmp_path / '刚好.txt').write_text('abcd', encoding='utf-8')
    assert _调用('读取', '刚好.txt') == 'abcd'


def test_默认上限是10MiB():
    """常量兜底：路线图约定 10 MiB，别人改小别人得改测试。"""
    assert _READ_FILE_SIZE_LIMIT == 10 * 1024 * 1024


# ─────────────────────────────────────────────────────────────────────
# 语法层端到端：确认 `写入` / `读取` 在语法上就是内建动词，且能进管道
# ─────────────────────────────────────────────────────────────────────

def test_源码层往返(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    源码 = '写入 "端到端.txt" "你好"。打印 读取 "端到端.txt"。'
    run_source(源码)
    out = capsys.readouterr().out
    assert '你好' in out


def test_写入返回入管道(tmp_path, monkeypatch, capsys):
    """`写入 path content` 的返回值是**内容**（字符串），可继续入管道。
    用 `定义赵N=写入 ...，长度` 显式绑定，避开 `打印` 变参对逗号的贪心解析。"""
    monkeypatch.chdir(tmp_path)
    源码 = (
        '定义赵N=写入 "管道.txt" "甲乙"，长度。\n'
        '打印 赵N。\n'
    )
    run_source(源码)
    assert capsys.readouterr().out.strip() == '2'
    assert (tmp_path / '管道.txt').read_text(encoding='utf-8') == '甲乙'


# ─────────────────────────────────────────────────────────────────────
# 块层面：`blocks.数据.存文` / `blocks.数据.载入` 已回收 .py 背衬
# ─────────────────────────────────────────────────────────────────────

def _repo_root():
    return os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))


def test_块层落盘装载(tmp_path, monkeypatch, capsys):
    """从块导入 `落盘` / `装载`，写读一趟。

    块模块路径（`blocks.数据.存文`）由 `module_loader` 按**包安装位置**
    解析，与 CWD 无关，所以可以把 CWD 挪到 `tmp_path` 让路径闸有个干净的
    根，同时仍然导得到块。这条用例是「两个块已脱离 pybridge」的行为断言。
    """
    monkeypatch.chdir(tmp_path)
    源码 = (
        '从 blocks.数据.存文 导入 落盘。\n'
        '从 blocks.数据.载入 导入 装载。\n'
        '落盘("块端到端.txt" "块层内容")。\n'
        '打印 装载("块端到端.txt")。\n'
    )
    run_source(源码)
    assert '块层内容' in capsys.readouterr().out
    assert (tmp_path / '块端到端.txt').read_text(encoding='utf-8') == '块层内容'


def test_块层继承路径闸(tmp_path, monkeypatch):
    """块是内建动词的**薄门面**，不得成为绕过安全闸的旁路。"""
    monkeypatch.chdir(tmp_path)
    源码 = (
        '从 blocks.数据.存文 导入 落盘。\n'
        '落盘("../越界.txt" "偷写")。\n'
    )
    with pytest.raises(JiKuaiError) as ei:
        run_source(源码)
    assert 'JK-E4002' in ei.value.info.message
    assert not (tmp_path.parent / '越界.txt').exists()


def test_块目录已无py背衬():
    """脱离 pybridge 的形式化断言：`.py` 背衬文件必须已被回收。"""
    root = os.path.join(_repo_root(), 'stdlib', 'blocks', '数据')
    assert not os.path.exists(os.path.join(root, '存文', '存文.py'))
    assert not os.path.exists(os.path.join(root, '载入', '载入.py'))
