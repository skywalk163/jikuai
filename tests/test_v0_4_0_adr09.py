# -*- coding: utf-8 -*-
"""AC-66 ~ AC-70 · ADR-09 X1 类作用域限定白名单回归。

覆盖点：
  - AC-66 类内 `方法 长度`，类外顶层 `打印 长度 列 1 2 3` 走内建动词 → 3
  - AC-67 类实例 `.长度` 走类内方法（成员访问点松弛路径）
  - AC-68 另一个类的方法体内 `长度 自身.吴项` 走内建（跨类不透传）
  - AC-69 REPL 跨输入白名单：`方法 长度` + 下一行 `实例.长度` 调通
  - AC-70 字段名同规则：类内 `自身.求和=0`，类外 `求和 列 1 2 3` 走内建

设计约束：所有断言不越 lexer 输出层——AC-66/AC-68/AC-70 直接检查 token 类型，
AC-67/AC-69 通过 evaluator 执行验证。这样即便 evaluator 层还没接入 X1，token
契约的正确性也能独立守护。
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from jikuai.lexer import Lexer, tokenize, DefEntry, ScopeMap
from jikuai.parser import parse
from jikuai.evaluator import Evaluator, JiKuaiError
from jikuai.tokens import TokenType
from jikuai.errors import ErrorCategory
from jikuai.repl_session import ReplSession


def _run(src):
    ev = Evaluator()
    return ev.eval(parse(tokenize(src)), source=src)


def _tokens_of(src, external_defs=None):
    lx = Lexer(src, external_defs=external_defs)
    return lx, lx.tokenize()


def _repl_lines(lines):
    out, err = io.StringIO(), io.StringIO()
    s = ReplSession(out=out, err=err)
    for ln in lines:
        s.feed(ln)
    return s, out.getvalue(), err.getvalue()


# ============================================================
# AC-66 · 类内定义 `方法 长度`，类外顶层走内建 → 3
# ============================================================

def test_ac66_class_scoped_method_does_not_shadow_builtin_outside():
    """AC-66: 类内 `方法 长度` 只在该类区间内是 IDENT；类外 `打印 长度 列 1 2 3` 走内建。"""
    src = (
        '类 王测：\n'
        '  方法 长度：\n'
        '    返回 999。\n'
        '  。\n'
        '。\n'
        '打印 长度 列 1 2 3。'
    )
    lx, toks = _tokens_of(src)
    # 类外顶层 `打印 长度 列 1 2 3` 中的 `长度` 必须是 VERB
    printed_area = toks[toks.index(next(t for t in toks if t.type == TokenType.VERB and t.value == '打印')):]
    verbs_after_print = [t.value for t in printed_area if t.type == TokenType.VERB]
    assert '长度' in verbs_after_print, [t for t in printed_area if t.type in (TokenType.VERB, TokenType.IDENT)]
    # 但 ScopeMap 里确实登记了 `长度`（class-scoped）
    all_names = lx.scope_map.all_names()
    assert '长度' in all_names, all_names
    # 完整求值：`打印 长度 列 1 2 3` 输出 3（副作用），最后表达式返回 None
    import sys
    from io import StringIO
    saved = sys.stdout
    try:
        sys.stdout = StringIO()
        _run(src)
        printed = sys.stdout.getvalue().strip()
    finally:
        sys.stdout = saved
    # 打印结果包含 `3`
    assert '3' in printed.splitlines(), printed


# ============================================================
# AC-67 · 类实例 `.长度` 仍走类内方法（成员访问松弛路径）
# ============================================================

def test_ac67_member_access_recovers_class_method_by_dot_relaxation():
    """AC-67: 类实例 `实例.长度` 中的 `长度` 是 IDENT（成员访问松弛），走方法路径。"""
    src = (
        '类 王测：\n'
        '  方法 长度：\n'
        '    返回 999。\n'
        '  。\n'
        '。\n'
        '定义赵a=新建王测()。\n'
        '赵a.长度。'
    )
    lx, toks = _tokens_of(src)
    # `赵a.长度` 中 DOT 后的 `长度` 必须是 IDENT，不是 VERB
    dot_positions = [i for i, t in enumerate(toks) if t.type == TokenType.DOT]
    assert dot_positions, toks
    last_dot = dot_positions[-1]
    nxt = toks[last_dot + 1]
    assert nxt.type == TokenType.IDENT and nxt.value == '长度', (nxt, toks[last_dot:last_dot + 3])
    # evaluator 端：方法调用（0 参访问即调用）返回 999
    assert _run(src) == 999


# ============================================================
# AC-68 · 另一个类的方法体 `长度 自身.吴项` 走内建（跨类作用域隔离）
# ============================================================

def test_ac68_other_class_body_uses_builtin_verb():
    """AC-68: A 类定义 `方法 长度`；B 类方法体内 `长度 自身.吴项` 中的 `长度` 走内建。

    B 类字符区间不覆盖 A 类的 `长度` 登记，`visible_at` 在 B 类内不包含 `长度`，
    从而 `_try_user_def_strict` 匹配失败，fallback 到 `_try_longest_keyword` 认作
    VERB。
    """
    src = (
        '类 甲类：\n'
        '  方法 长度：\n'
        '    返回 42。\n'
        '  。\n'
        '。\n'
        '类 乙类：\n'
        '  构造：\n'
        '    自身.吴项=列 10 20 30。\n'
        '  。\n'
        '  方法 王读：\n'
        '    返回 长度 自身.吴项。\n'
        '  。\n'
        '。\n'
    )
    lx, toks = _tokens_of(src)
    # 精确检查：`长度 自身.吴项` 段的 `长度` 是 VERB（在乙类方法体内）
    # 找到 `王读` 之后第一个 `长度` 的 token
    seen_wangdu = False
    verb_after_wangdu = None
    for t in toks:
        if t.type == TokenType.IDENT and t.value == '王读':
            seen_wangdu = True
            continue
        if seen_wangdu and t.value == '长度':
            verb_after_wangdu = t
            break
    assert verb_after_wangdu is not None, toks
    assert verb_after_wangdu.type == TokenType.VERB, (verb_after_wangdu, toks)
    # evaluator 端：新建乙类实例后调用 王读 得到 3（长度 列 10 20 30）
    exec_src = src + '定义 赵b = 新建 乙类()。\n赵b.王读。'
    assert _run(exec_src) == 3


# ============================================================
# AC-69 · REPL 跨输入白名单：类内方法名跨行调用仍生效
# ============================================================

def test_ac69_repl_cross_input_method_call_survives():
    """AC-69: REPL 会话内先定义 `方法 长度`，下一次输入 `实例.长度` 仍能命中方法。

    机制：第一次分词后 `_session_defs` 累积 `长度`；第二次分词时它作为
    `external_defs` 注入并登记为**全域可见**，跨行仍能整体识别为 IDENT。
    """
    lines = [
        '类 王容器：',
        '构造：',
        '自身.键="k"。',
        '。',
        '方法 长度：',
        '返回 777。',
        '。',
        '。',
        '定义赵c=新建王容器()。',
        '赵c.长度。',
    ]
    _, out, err = _repl_lines(lines)
    assert '777' in out, (out, err)
    assert err == '', err


def test_ac69_repl_toplevel_builtin_verb_after_class_definition(capsys):
    """AC-69（PRD 原文 · DEF-02 回归）：REPL 中定义含 `方法 长度` 的类后，
    **独立的下一行**顶层 `打印 长度 列 1 2 3。` 应输出 `3`。

    DEF-02 根因：`external_defs` 曾以 `add_global` 全域注入，把类作用域名字
    提升为会话全域，导致 REPL 路径下 ADR-06 副作用未根治（报「未定义的标识符：
    长度」）。修复后 `_session_defs` 携带 `(name, kind, owner_class)`，类内
    method/field 在下次注入时限于同名类区间；本次输入无 `王测` 类块 →
    `长度` 登记为空区间 → 顶层不可见 → 走内建动词。

    注：`打印` 内建走 Python `print()` 到 `sys.stdout`，不经 `session.out`，
    因此用 pytest `capsys` 捕获。
    """
    out, err = io.StringIO(), io.StringIO()
    s = ReplSession(out=out, err=err)
    # 第一批：定义含 `方法 长度` 的类（多行续行）
    for ln in ['类 王测：', '  方法 长度：', '    返回 999。', '  。', '。']:
        s.feed(ln)
    assert err.getvalue() == '', err.getvalue()
    assert ('长度', 'method', '王测') in s._session_defs, sorted(s._session_defs)
    capsys.readouterr()   # 丢弃前置输出
    # 第二批：独立的下一行 —— 顶层必须走内建动词 `长度`
    s.feed('打印 长度 列 1 2 3。')
    printed = capsys.readouterr().out
    assert err.getvalue() == '', err.getvalue()
    assert '未定义的标识符' not in err.getvalue()
    assert '3' in printed.splitlines(), printed


def test_def02_session_defs_carry_kind_and_owner():
    """DEF-02 契约：`_session_defs` 元素为 `(name, kind, owner_class)` 三元组，
    类内成员携带 owner_class，顶层定义 owner_class 为 None。"""
    lines = [
        '类 王测：',
        '  构造：',
        '    自身.求和=0。',
        '  。',
        '  方法 长度：',
        '    返回 999。',
        '  。',
        '。',
    ]
    session, _, err = _repl_lines(lines)
    assert err == '', err
    sigs = session._session_defs
    assert ('长度', 'method', '王测') in sigs, sorted(sigs)
    assert ('求和', 'field', '王测') in sigs, sorted(sigs)
    assert ('王测', 'class', None) in sigs, sorted(sigs)


def test_def02_member_only_injection_not_visible_at_toplevel():
    """DEF-02 单元级：类内成员以三元组注入且本次源码无该类 → 顶层不可见，
    但 `.成员` 松弛路径仍可命中。"""
    src = '打印 长度 列 1 2 3。\n赵a.长度。'
    lx = Lexer(src, external_defs={('长度', 'method', '王测'), ('赵a', 'define', None)})
    toks = lx.tokenize()
    # 名字仍在全量集合里（供 DOT 松弛）
    assert '长度' in lx.scope_map.all_names()
    # 但任何位置都不可见（空区间）
    assert '长度' not in lx.scope_map.visible_at(0)
    assert '长度' not in lx.scope_map.visible_at(len(src) - 1)
    # 顶层 `打印 长度 ...` 的 长度 是 VERB；DOT 后的 长度 是 IDENT
    kinds = [(t.value, t.type) for t in toks if t.value == '长度']
    assert (('长度', TokenType.VERB) in kinds), kinds
    assert (('长度', TokenType.IDENT) in kinds), kinds


def test_def02_member_only_injection_scoped_when_class_present():
    """DEF-02：注入的类内成员，若本次源码存在同名类块 → 在该类区间内可见。"""
    src = (
        '类 王测：\n'
        '  方法 王读：\n'
        '    返回 1。\n'
        '  。\n'
        '。\n'
        '打印 长度 列 1 2 3。'
    )
    lx = Lexer(src, external_defs={('长度', 'method', '王测')})
    lx.tokenize()
    regions = lx._class_regions_by_name()
    start, end = regions['王测'][0]
    assert '长度' in lx.scope_map.visible_at(start + 1)
    assert '长度' not in lx.scope_map.visible_at(end + 1)


# ============================================================
# AC-68b · 同类内内建动词名被本类方法/字段遮蔽时，诊断文案必须可操作
# ============================================================
#
# 交付总监裁决：AC-68 口径收敛为「跨作用域」（已 PASS）。同类内的遮蔽属既定
# 行为，作用域模型不动；本组只守护 **evaluator 诊断文案** 的可操作性——
# 说明是「遮蔽」而非「未定义」，并给出至少一条规避方式。


def _name_error_info(src):
    """执行 src，断言抛出携带 ErrorInfo 的 JiKuaiError，返回其 info。"""
    with pytest.raises(JiKuaiError) as ei:
        _run(src)
    info = ei.value.info
    assert info is not None, ei.value
    return info


def test_ac68b_method_name_shadowing_gives_actionable_diagnostic():
    """AC-68b（方法名遮蔽）：类内 `方法 长度` 使同类另一方法体的 `长度` 成为 IDENT，
    运行时诊断必须指出「遮蔽」，而不是误导性的「未定义的标识符」。"""
    src = (
        '类 王容器：\n'
        '  构造：\n'
        '    自身.吴项=列 10 20 30。\n'
        '  。\n'
        '  方法 长度：\n'
        '    返回 42。\n'
        '  。\n'
        '  方法 王读：\n'
        '    返回 长度 自身.吴项。\n'
        '  。\n'
        '。\n'
        '定义 赵a = 新建 王容器()。\n'
        '赵a.王读。'
    )
    # 前置事实：该 `长度` 确实被降级为 IDENT（遮蔽的直接证据，作用域模型未改）
    lx, toks = _tokens_of(src)
    shadowed = [t for t in toks if t.value == '长度']
    assert shadowed and all(t.type == TokenType.IDENT for t in shadowed), shadowed

    info = _name_error_info(src)
    assert info.category is ErrorCategory.NAME, info
    msg = info.message
    # ① 说明是遮蔽而非未定义
    assert '遮蔽' in msg, msg
    assert '未定义的标识符' not in msg, msg
    # 涉事名字出现在文案里
    assert '长度' in msg, msg
    # ② 至少一条可操作规避方式
    assert ('改用其他名字' in msg) or ('拆分到不同文件' in msg), msg
    # ③ 不含 Python 实现细节
    for leak in ('Traceback', 'lambda', 'TypeError', 'KeyError', 'NameError',
                 'Environment', 'env.get', 'None', 'self.'):
        assert leak not in msg, (leak, msg)
    # 定位信息可用（指向使用处所在行，而非类定义行）
    assert info.line == 9, info
    assert '长度' in info.source_line, info


def test_ac68b_field_name_shadowing_gives_actionable_diagnostic():
    """AC-68b（字段名遮蔽）：构造器里 `自身.求和=0` 使同类方法体的 `求和` 成为 IDENT，
    诊断文案与方法名遮蔽同口径。"""
    src = (
        '类 王统计：\n'
        '  构造：\n'
        '    自身.求和=0。\n'
        '    自身.吴项=列 1 2 3。\n'
        '  。\n'
        '  方法 王算：\n'
        '    返回 求和 自身.吴项。\n'
        '  。\n'
        '。\n'
        '定义 赵b = 新建 王统计()。\n'
        '赵b.王算。'
    )
    lx, toks = _tokens_of(src)
    # 方法体内的 `求和`（非 `自身.求和` 的 DOT 后属性名）被降级为 IDENT
    body_qiuhe = [t for t in toks if t.value == '求和' and t.line == 7]
    assert body_qiuhe, [t for t in toks if t.value == '求和']
    assert all(t.type == TokenType.IDENT for t in body_qiuhe), body_qiuhe

    info = _name_error_info(src)
    assert info.category is ErrorCategory.NAME, info
    msg = info.message
    assert '遮蔽' in msg, msg
    assert '未定义的标识符' not in msg, msg
    assert '求和' in msg, msg
    assert ('改用其他名字' in msg) or ('拆分到不同文件' in msg), msg
    assert info.line == 7, info


def test_ac68b_non_verb_undefined_name_keeps_original_diagnostic():
    """AC-68b 边界：普通（非内建动词名）的未定义标识符仍走原「未定义的标识符」
    分支并保留拼写建议，遮蔽文案不得泛化污染。"""
    info = _name_error_info('定义 赵甲 = 1。\n打印 赵乙。')
    assert info.category is ErrorCategory.NAME, info
    assert '未定义的标识符：赵乙' == info.message, info
    assert '遮蔽' not in info.message, info
    # 编辑距离 1 的候选 `赵甲` 应被建议
    assert info.suggestion == '赵甲', info


# ============================================================
# AC-70 · 字段名同规则：类内声明只在类区间内是 IDENT
# ============================================================


def test_ac70_field_name_same_scope_rule():
    """AC-70: 类内 `自身.求和=0` 使 `求和` 类内是 IDENT，类外顶层是 VERB。"""
    src = (
        '类 王统计：\n'
        '  构造：\n'
        '    自身.求和=0。\n'
        '  。\n'
        '。\n'
        '打印 求和 列 1 2 3 4。'
    )
    lx, toks = _tokens_of(src)
    # 类外顶层 `求和` 必须是 VERB
    top_level_qiuhe = [t for t in toks if t.value == '求和' and t.line >= 6]
    assert top_level_qiuhe, toks
    assert all(t.type == TokenType.VERB for t in top_level_qiuhe), top_level_qiuhe
    # `求和` 登记进 ScopeMap（字段名）
    assert '求和' in lx.scope_map.all_names()


# ============================================================
# 补充契约：ScopeMap / DefEntry 数据模型
# ============================================================

def test_scope_map_data_model_contract():
    """DefEntry / ScopeMap 数据模型契约（架构师 T-01 定义）。"""
    m = ScopeMap()
    m.add(DefEntry(name='阿', kind='class', scope_start=0, scope_end=-1))
    m.add(DefEntry(name='内', kind='method', scope_start=5, scope_end=20,
                   owner_class='阿'))
    assert '阿' in m.visible_at(0)
    assert '阿' in m.visible_at(100)
    assert '内' in m.visible_at(10)
    assert '内' not in m.visible_at(3)
    assert '内' not in m.visible_at(25)
    assert m.all_names() == frozenset({'阿', '内'})


def test_scope_map_legacy_mode_returns_all_names():
    """`JIKUAI_LEGACY_ADR06=1` 时 `visible_at` 退化返回全部名字（回退开关 T-06）。"""
    m = ScopeMap(legacy=True)
    m.add(DefEntry(name='甲', kind='method', scope_start=100, scope_end=200))
    # 即便 offset 在区间外也应返回该名字（旧的平坦集合语义）
    assert '甲' in m.visible_at(0)
    assert '甲' in m.visible_at(500)


def test_legacy_env_switch_smoke(monkeypatch):
    """T-06 端到端：设置 `JIKUAI_LEGACY_ADR06=1` 后 Lexer 走旧行为，类外也能命中类内名。"""
    monkeypatch.setenv('JIKUAI_LEGACY_ADR06', '1')
    src = (
        '类 王测：\n'
        '  方法 长度：\n'
        '    返回 999。\n'
        '  。\n'
        '。\n'
        '打印 长度 列 1 2 3。'
    )
    lx = Lexer(src)
    toks = lx.tokenize()
    # legacy 模式：类外 `长度` 被降级为 IDENT（旧 ADR-06 副作用）
    idents_after_print = False
    for t in toks:
        if t.type == TokenType.VERB and t.value == '打印':
            idents_after_print = True
            continue
        if idents_after_print and t.value == '长度':
            assert t.type == TokenType.IDENT, t
            return
    pytest.fail('未在 print 后找到 长度 token')
