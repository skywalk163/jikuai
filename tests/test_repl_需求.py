# -*- coding: utf-8 -*-
"""W10 · REPL `需求` 元命令测试。

四条 DoD：
  1. 候选展示（`需求 求个平均` 列出块候选并进入选块态）
  2. 选中追加到 buffer（输入编号 → 导入行 + 调用行进 buffer）
  3. 无匹配时的提示
  4. 非法数字输入（选块态下 非数字 / 越界 都不崩、不污染 buffer）

REPL 的判定逻辑都是不依赖 stdin 的纯方法，直接 feed() 驱动即可，不需要
fake stdin——沿用 tests/test_jikuai.py 里既有 REPL 用例的风格。
物理隔离：本文件只 import jikuai.repl_session / jikuai.ai.retrieval，
不碰 lsp 桩包，也不改 sys.modules，保证 test_v0_5_0_lsp_stub.py 不受影响。
"""

import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.repl_session import (  # noqa: E402
    CompletionEngine, ReplSession, REQUIREMENT_WORD, PROMPT_SELECT,
    STATE_CONTINUE, STATE_IDLE, STATE_SELECTING, help_text, requirement_usage,
)


def _mk():
    """建一个 out/err 都走内存缓冲的会话。"""
    out, err = io.StringIO(), io.StringIO()
    return ReplSession(out=out, err=err), out, err


# ---------------------------------------------------------------------------
# parse_requirement：纯解析（骨架照 parse_help）
# ---------------------------------------------------------------------------

def test_parse_requirement_recognizes_command():
    assert ReplSession.parse_requirement('需求 求个平均') == (True, '求个平均')
    assert ReplSession.parse_requirement('需求 求个平均。') == (True, '求个平均')
    assert ReplSession.parse_requirement('需求') == (True, None)
    assert ReplSession.parse_requirement('打印 1。') == (False, None)
    assert ReplSession.parse_requirement('加 3 5') == (False, None)


# ---------------------------------------------------------------------------
# DoD-1：候选展示
# ---------------------------------------------------------------------------

def test_requirement_shows_candidates():
    """`需求 求个平均` 列出候选清单并进入选块态。"""
    s, out, err = _mk()
    assert s.feed('需求 求个平均') == 'select'
    assert s.state == STATE_SELECTING
    assert s.prompt == PROMPT_SELECT
    text = out.getvalue()
    assert '需求：求个平均' in text
    assert '1. ' in text
    assert '均值' in text                 # 「求个平均」top1 命中「均值」块
    assert '取消' in text
    assert s.buffer == []                 # 展示阶段不污染编辑缓冲
    assert err.getvalue() == ''
    assert 1 <= len(s.pending_hits) <= 5   # 候选已暂存且不超过 top-K


# ---------------------------------------------------------------------------
# DoD-2：选中 → 追加到 buffer
# ---------------------------------------------------------------------------

def test_requirement_selection_appends_to_buffer():
    """选中编号后，导入行与调用行按协议格式追加到 buffer。"""
    s, out, err = _mk()
    s.feed('需求 求个平均')
    out.truncate(0)
    out.seek(0)
    assert s.feed('1') == 'continue'      # 转续行态，等用户接着编辑
    assert s.state == STATE_CONTINUE
    assert len(s.buffer) == 2
    imp, call = s.buffer
    # 导入用目录名（ADR-15 §3.7）：从 blocks.<领域>.<块名> 导入 <导出名>。
    assert imp.startswith('从 blocks.数据.均值 导入 ')
    assert imp.endswith('。')
    # 调用用导出名 + 占位实参，结果变量沿用桥接的 赵果1
    assert call.startswith('定义赵果1=')
    assert '(?)' in call
    assert call.endswith('。')
    # 均值块导出名是「中位」，两行引用一致
    assert '中位' in imp
    assert '中位(' in call
    printed = out.getvalue()
    assert imp in printed
    assert call in printed


def test_requirement_selection_with_inline_args():
    """编号后带实参时，占位符被替换成用户给的实参。"""
    s, out, err = _mk()
    s.feed('需求 求个平均')
    s.feed('1 列 1 2 3')
    imp, call = s.buffer
    assert call == '定义赵果1=中位(列 1 2 3)。'
    assert '?' not in call


# ---------------------------------------------------------------------------
# DoD-3：无匹配时的提示
# ---------------------------------------------------------------------------

def test_requirement_no_match(monkeypatch):
    """检索为空时给提示，不进选块态、不污染 buffer。"""
    from jikuai.ai import retrieval
    monkeypatch.setattr(retrieval, 'retrieve', lambda *a, **k: [])
    s, out, err = _mk()
    assert s.feed('需求 完全匹配不到任何块的怪需求xyz') == 'idle'
    assert s.state == STATE_IDLE
    assert s.buffer == []
    assert s.pending_hits == []
    assert '没有匹配' in out.getvalue()


# ---------------------------------------------------------------------------
# DoD-4：非法数字输入
# ---------------------------------------------------------------------------

def test_requirement_illegal_selection():
    """选块态下非数字 / 越界都提示并停留在选块态，不污染 buffer。"""
    s, out, err = _mk()
    s.feed('需求 求个平均')
    total = len(s.pending_hits)

    assert s.feed('abc') == 'select'          # 非数字
    assert s.state == STATE_SELECTING
    assert s.buffer == []
    assert '编号' in err.getvalue()

    err.truncate(0)
    err.seek(0)
    assert s.feed(str(total + 99)) == 'select'  # 越界
    assert s.state == STATE_SELECTING
    assert s.buffer == []
    assert '超出范围' in err.getvalue()

    assert s.feed('0') == 'idle'               # 0 取消
    assert s.state == STATE_IDLE
    assert s.buffer == []
    assert '取消' in out.getvalue()


def test_requirement_cancel_with_blank_line():
    """选块态下空行等价取消。"""
    s, out, err = _mk()
    s.feed('需求 求个平均')
    assert s.feed('') == 'idle'
    assert s.state == STATE_IDLE
    assert s.buffer == []
    assert '取消' in out.getvalue()


# ---------------------------------------------------------------------------
# 补全 & 帮助
# ---------------------------------------------------------------------------

def test_requirement_tab_completion():
    """tab 补全 `需` 能出 `需求`。"""
    engine = CompletionEngine()
    assert '需求' in engine.candidates('需')
    assert '需求' in engine._static


def test_help_requirement_usage():
    """`帮助 需求` 出用法说明，且走 out 不走 err。"""
    text = help_text(REQUIREMENT_WORD)
    assert text == requirement_usage()
    assert REQUIREMENT_WORD in text
    assert '从 blocks.' in text
    s, out, err = _mk()
    assert s.feed('帮助 需求') == 'idle'
    assert '从 blocks.' in out.getvalue()
    assert err.getvalue() == ''


def test_requirement_bare_word_prints_usage():
    """只写 `需求` 时打用法，不进选块态。"""
    s, out, err = _mk()
    assert s.feed('需求') == 'idle'
    assert s.state == STATE_IDLE
    assert '用法：需求' in out.getvalue()