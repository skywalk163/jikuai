# -*- coding: utf-8 -*-
"""v0.26.0 W143 · chatbi 评测集分档的结构与真实性断言。

不评检索质量（那是 bench_chatbi.py 的事），只把「四份评测集建对了没有」钉成
可回归的门禁：schema 字段齐、条数达标、正例引用的块名在 索引.json 里**真实存在**、
负例声明的缺口能力**确实不存在**、留出集纪律文字在位。

为什么要反向断言缺口不存在（近边缘档最要紧的一条）：近边缘负例集会随块库演进而
失效——哪天真补了「交付率」块，这条就不再是负例。把「缺口块名不在索引里」写成
断言，等于给负例集上锁：补了块测试当场红，提醒把该条从负例集挪走，而不是让它
悄悄变成一条错标签继续误导拒答评测。

零第三方依赖，纯标准库 + pytest。
"""

import json
import os

import pytest

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..'))
_BRIDGE = os.path.join(_REPO, 'tools', 'ai-bridge')
_INDEX = os.path.join(_REPO, 'src', 'jikuai', 'stdlib', 'blocks', '索引.json')

_POS_TUNE = os.path.join(_BRIDGE, '评测集-chatbi.json')
_POS_HOLD = os.path.join(_BRIDGE, '评测集-chatbi-留出.json')
_NEG_FAR = os.path.join(_BRIDGE, '评测集-chatbi-无覆盖.json')
_NEG_EDGE = os.path.join(_BRIDGE, '评测集-chatbi-近边缘.json')


def _load(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture(scope='module')
def 块名集():
    """索引.json 里所有块的目录名集合（含全部域，不止制造）。"""
    idx = _load(_INDEX)
    return {b['名称'] for b in idx['块']}


@pytest.fixture(scope='module')
def 制造块名集():
    idx = _load(_INDEX)
    return {b['名称'] for b in idx['块'] if '制造' in (b.get('领域') or [])}


# ---------------------------------------------------------------------------
# 四份都能解析、都有 用例
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('path', [_POS_TUNE, _POS_HOLD, _NEG_FAR, _NEG_EDGE])
def test_可解析且有用例(path):
    d = _load(path)
    assert isinstance(d, dict), '%s 顶层不是对象' % path
    assert '说明' in d, '%s 缺 说明 字段' % path
    assert isinstance(d.get('用例'), list) and d['用例'], '%s 的 用例 应为非空列表' % path


# ---------------------------------------------------------------------------
# 条数达标（WBS/任务约定）
# ---------------------------------------------------------------------------

def test_条数达标():
    assert len(_load(_POS_TUNE)['用例']) == 15, '调优集应为 10 题 + 5 预置异常 = 15 条'
    assert len(_load(_POS_HOLD)['用例']) == 5, '留出集应为 question_hidden.csv 的 5 题'
    assert len(_load(_NEG_FAR)['用例']) >= 8, '远离档至少 8 条'
    assert len(_load(_NEG_EDGE)['用例']) >= 6, '近边缘档至少 6 条'


# ---------------------------------------------------------------------------
# 正例 schema：需求 / 期望 / 期望结果 齐；预置异常 5 条在位
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('path', [_POS_TUNE, _POS_HOLD])
def test_正例schema字段齐(path):
    for c in _load(path)['用例']:
        assert set(c) >= {'id', '需求', '期望', '期望结果'}, \
            '正例用例缺字段：%s' % c.get('需求')
        assert isinstance(c['期望'], list) and c['期望'], \
            '期望 应为非空块名列表：%s' % c['需求']
        assert isinstance(c['需求'], str) and c['需求']


def test_调优集含5个预置异常():
    ids = {c['id'] for c in _load(_POS_TUNE)['用例']}
    假设异常 = {'A_01', 'A_02', 'A_03', 'A_04', 'A_05'}
    assert 假设异常 <= ids, '缺预置异常：%s' % (假设异常 - ids)
    公开题 = {'Q_PUB_%03d' % i for i in range(1, 11)}
    assert 公开题 <= ids, '缺 question_public 题：%s' % (公开题 - ids)


def test_留出集是hidden五题():
    ids = {c['id'] for c in _load(_POS_HOLD)['用例']}
    assert ids == {'Q_HID_%03d' % i for i in range(1, 6)}, \
        '留出集 id 应恰为 Q_HID_001..005，实为 %s' % sorted(ids)


# ---------------------------------------------------------------------------
# 正例引用的块名必须在 索引.json 里真实存在
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('path', [_POS_TUNE, _POS_HOLD])
def test_正例期望块真实存在(path, 块名集):
    for c in _load(path)['用例']:
        缺 = [n for n in c['期望'] if n not in 块名集]
        assert not 缺, '%s 引用了索引里不存在的块名 %s（期望填的是目录名不是导出名）' \
            % (c['需求'], 缺)


# ---------------------------------------------------------------------------
# 负例 schema + 反向断言：缺口能力确实不在库里、兄弟块确实在库里
# ---------------------------------------------------------------------------

def test_远离档schema字段齐():
    for c in _load(_NEG_FAR)['用例']:
        assert set(c) >= {'需求', '类别'}, '远离档用例缺字段：%s' % c
        assert c['需求'] and c['类别']


def test_近边缘档schema字段齐():
    for c in _load(_NEG_EDGE)['用例']:
        assert set(c) >= {'需求', '类别', '兄弟块', '缺口块名'}, \
            '近边缘档用例缺字段：%s' % c
        assert isinstance(c['兄弟块'], list) and c['兄弟块']
        assert isinstance(c['缺口块名'], list) and c['缺口块名']


def test_近边缘_兄弟块真实存在(制造块名集):
    """兄弟块是负例判定的对照物，必须真在制造域里——否则这条就不是『近边缘』了。"""
    for c in _load(_NEG_EDGE)['用例']:
        缺 = [n for n in c['兄弟块'] if n not in 制造块名集]
        assert not 缺, '%s 的兄弟块 %s 不在制造域块清单里' % (c['需求'], 缺)


def test_近边缘_缺口能力确实不存在(块名集):
    """核心断言：缺口块名不得出现在索引里。

    补了对应块时本测试当场红，提醒把该条从负例集挪走——负例会随块库演进失效，
    这条断言就是防它悄悄变错的锁。
    """
    for c in _load(_NEG_EDGE)['用例']:
        撞 = [n for n in c['缺口块名'] if n in 块名集]
        assert not 撞, ('%s 声称缺口 %s，但索引里已存在同名块——该条已不再是负例，'
                       '请从近边缘档移除') % (c['需求'], 撞)


# ---------------------------------------------------------------------------
# 留出集纪律文字在位（文件头 + bench docstring）
# ---------------------------------------------------------------------------

def test_留出集纪律文字在位():
    d = _load(_POS_HOLD)
    纪律 = d.get('纪律', '') + d.get('说明', '')
    assert '只当裁判' in 纪律, '留出集必须写明「只当裁判」纪律'
    assert '调参' in 纪律 and 'miss' in 纪律, '留出集纪律要写明「绝不看 miss 明细调参」'


def test_bench脚本docstring写死留出纪律():
    with open(os.path.join(_BRIDGE, 'bench_chatbi.py'), 'r', encoding='utf-8') as f:
        head = f.read(4000)
    assert '只当裁判' in head, 'bench_chatbi.py docstring 缺留出集纪律'
    assert '绝不看' in head and 'miss' in head, \
        'bench_chatbi.py docstring 要写明「绝不看 miss 明细调参」'


# ---------------------------------------------------------------------------
# bench 能一次跑出四档且四档都在
# ---------------------------------------------------------------------------

def test_bench一次跑出四档():
    """跑真实脚本进程要 --json 拿结构化四档数字。

    走子进程而非 in-process import：bench_chatbi 顶部的 _reconfigure_utf8() 会把
    sys.stdout 换成裹 buffer 的 TextIOWrapper，in-process 会砸掉 pytest 的输出捕获。
    """
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, os.path.join(_BRIDGE, 'bench_chatbi.py'), '--json'],
        capture_output=True, cwd=_REPO)
    assert proc.returncode == 0, proc.stderr.decode('utf-8', 'replace')
    报告 = json.loads(proc.stdout.decode('utf-8'))
    # 25 → 27：v0.27.0 W154-W155 加了 `窗间对比` / `基线偏离`（ADR-40 §5.4 双块）。
    # 用等值而非下界，理由同 `块背衬PY数`：块数变了就该有人来改这个数并解释一句。
    assert 报告['制造域块数'] == 27, '制造域应为 27 块，实为 %s' % 报告['制造域块数']

    assert set(报告['正例档']) == {'调优', '留出'}
    assert set(报告['负例档']) == {'远离', '近边缘'}
    # 两档负例拒答率如实为 0（检索层无阈值）
    for 名, r in 报告['负例档'].items():
        assert r['拒答率'] == 0.0, '%s 档拒答率应如实为 0' % 名
