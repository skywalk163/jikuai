# -*- coding: utf-8 -*-
"""v0.3.1 · D-04 / D-05 / D-08 / D-09 验收测试。

AC-37 ~ AC-48：治理项 1（D-04 + D-09，ADR-06 白名单最优先 + `自身.X` 扩容）
AC-49 ~ AC-58：治理项 2（D-08，ADR-07 BoundMethod 按元数分流）
AC-59 ~ AC-65：治理项 3（D-05，ADR-08 控制流信号顶层拦截）
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.lexer import tokenize, Lexer
from jikuai.parser import parse
from jikuai.evaluator import Evaluator, JiKuaiError, BoundMethod
from jikuai.tokens import TokenType
from jikuai.errors import ErrorCategory
from jikuai.repl_session import ReplSession


def _run(src):
    ev = Evaluator()
    return ev.eval(parse(tokenize(src)), source=src)


def _repl_lines(lines):
    """驱动一批输入喂入 REPL，返回 (session, stdout_text, stderr_text)。"""
    out, err = io.StringIO(), io.StringIO()
    s = ReplSession(out=out, err=err)
    for ln in lines:
        s.feed(ln)
    return s, out.getvalue(), err.getvalue()


# ============================================================
# 治理项 1（AC-37 ~ AC-48）· ADR-06 白名单最优先 + `自身.X` 扩容
# ============================================================

def test_ac37_method_verb_name_取值_definition_and_call():
    """AC-37: 方法名整体等于内建动词 `取值`，定义 + 调用双端通。"""
    src = ('类 王容器：\n'
           '  构造 接收 赵键：\n'
           '    自身.键=赵键。\n'
           '  。\n'
           '  方法 取值 接收 赵键：\n'
           '    返回 赵键。\n'
           '  。\n'
           '。\n'
           '定义赵c=新建王容器("k")。\n'
           '赵c.取值("hello")。')
    assert _run(src) == 'hello'


def test_ac38_embedded_verb_name_王加一_returns_correctly():
    """AC-38: 名字内嵌内建动词字 `加` 的方法 `王加一` → 5.王加一 == 6。"""
    src = ('类 王算：\n'
           '  方法 王加一 接收 赵数：\n'
           '    返回 赵数 加 1。\n'
           '  。\n'
           '。\n'
           '定义赵算=新建王算()。\n'
           '赵算.王加一(5)。')
    assert _run(src) == 6


def test_ac39_field_name_次数_kw_times_not_split():
    """AC-39: 字段 `自身.次数=0`（`次`=KW_TIMES）不被切碎。"""
    src = ('类 王计：\n'
           '  构造：\n'
           '    自身.次数=0。\n'
           '  。\n'
           '。\n'
           '定义赵对象=新建王计()。\n'
           '赵对象.次数。')
    assert _run(src) == 0


def test_ac40_field_name_当前_kw_while_not_split():
    """AC-40: 字段 `自身.当前=1`（`当`=KW_WHILE）不被切碎。"""
    src = ('类 王状：\n'
           '  构造：\n'
           '    自身.当前=1。\n'
           '  。\n'
           '。\n'
           '定义赵s=新建王状()。\n'
           '赵s.当前。')
    assert _run(src) == 1


def test_ac41_field_name_返回值_kw_return_not_split():
    """AC-41: 字段 `自身.返回值="ok"`（`返回`=KW_RETURN）不被切碎。"""
    src = ('类 王果：\n'
           '  构造：\n'
           '    自身.返回值="ok"。\n'
           '  。\n'
           '。\n'
           '定义赵r=新建王果()。\n'
           '赵r.返回值。')
    assert _run(src) == 'ok'


def test_ac42_func_name_连接_and_verb_连接_coexist():
    """AC-42: 用户函数 `连接` 与内建动词 `连接` 白名单优先命中函数。"""
    src = ('函数 连接 接收 赵甲 赵乙：\n'
           '  返回 拼接 赵甲 赵乙。\n'
           '。\n'
           '连接("你" "好")。')
    assert _run(src) == '你好'


def test_ac43_class_name_数据连接器_contains_verb_连接():
    """AC-43: 类名 `数据连接器` 内嵌动词 `连接`，`新建数据连接器()` 成功。"""
    src = ('类 数据连接器：\n'
           '  方法 王测：\n'
           '    返回 1。\n'
           '  。\n'
           '。\n'
           '定义赵c=新建数据连接器()。\n'
           '赵c.王测。')
    assert _run(src) == 1


def test_ac44_prescan_self_field_最大值():
    """AC-44: prescan 扫描 `自身.最大值=100` 并保护整体标识符。"""
    src = ('类 王边界：\n'
           '  构造：\n'
           '    自身.最大值=100。\n'
           '  。\n'
           '。\n'
           '定义赵b=新建王边界()。\n'
           '赵b.最大值。')
    assert _run(src) == 100


def test_ac45_repl_session_defs_persist_across_inputs():
    """AC-45: REPL 跨输入白名单会话级保留 —— 上次定义的方法名下次调用不再被切碎。"""
    lines = [
        '类 王容器：',
        '构造 接收 赵键：',
        '自身.键=赵键。',
        '。',
        '方法 取值 接收 赵键：',
        '返回 赵键。',
        '。',
        '。',
        '定义赵c=新建王容器("k")。',
        '赵c.取值("hello")。',
    ]
    _, out, err = _repl_lines(lines)
    assert 'hello' in out, (out, err)
    assert err == '', err


def test_ac46_no_regression_on_define_paths():
    """AC-46: 既有 `定义X`/`函数X`/`方法X`/`类X` 白名单路径零回归。"""
    # 定义 X
    assert _run('定义赵甲=100。\n赵甲。') == 100
    # 函数 X
    assert _run('函数 赵阶乘 接收 赵n：\n如果 赵n 小于等于 1 那么：\n返回 1。\n否则：\n返回 赵n 乘 赵阶乘(赵n减1)。\n。\n。\n赵阶乘(5)。') == 120
    # 方法 X
    src = ('类 王测：\n'
           '  方法 王甲：\n'
           '    返回 42。\n'
           '  。\n'
           '。\n'
           '定义赵w=新建王测()。\n'
           '赵w.王甲。')
    assert _run(src) == 42
    # 类 X
    assert _run(src) == 42


def test_ac47_single_char_verb_as_method_name_加():
    """AC-47: 单字动词作方法名 `方法 加`（定义 + 调用双端通）。

    注：方法体内不再使用 `加` 作为算术动词，避免同一次分词内 user_defs
    的名字 `加` 与内建动词 `加` 在方法体表达式里语义冲突（这是 R-A 白名单
    的固有代价：一旦把动词字整体登记为用户名，同一次分词就整体覆盖）。
    """
    src = ('类 王加器：\n'
           '  方法 加 接收 赵值：\n'
           '    返回 赵值。\n'
           '  。\n'
           '。\n'
           '定义赵a=新建王加器()。\n'
           '赵a.加(10)。')
    assert _run(src) == 10


def test_ac48_method_name_with_kw_continue_跳过():
    """AC-48: 方法名内嵌 KW_CONTINUE `跳过` —— `方法 跳过验证`。"""
    src = ('类 王校验：\n'
           '  方法 跳过验证：\n'
           '    返回 "skipped"。\n'
           '  。\n'
           '。\n'
           '定义赵v=新建王校验()。\n'
           '赵v.跳过验证。')
    assert _run(src) == 'skipped'


# ============================================================
# 治理项 2（AC-49 ~ AC-58）· ADR-07 BoundMethod 按元数分流
# ============================================================

_ANIMAL_SRC = ('类 动物：\n'
               '  构造 接收 赵名字 赵年龄：\n'
               '    自身.名字=赵名字。\n'
               '    自身.年龄=赵年龄。\n'
               '  。\n'
               '  方法 叫声：\n'
               '    返回 "..."。\n'
               '  。\n'
               '。\n'
               '类 狗 继承 动物：\n'
               '  方法 叫声：\n'
               '    返回 "汪汪！"。\n'
               '  。\n'
               '。\n')


def test_ac49_zero_arg_method_access_invokes():
    """AC-49: 0 参方法 `赵狗.叫声` 访问即调用（M-01，兼容 oop.jk）。"""
    assert _run(_ANIMAL_SRC + '定义赵狗=新建狗("旺财", 3)。\n赵狗.叫声。') == '汪汪！'


def test_ac50_one_arg_method_binds_param():
    """AC-50: 1 参方法 `赵a.存款(50)` 形参正确绑定。"""
    src = ('类 王账户：\n'
           '  构造：\n'
           '    自身.余额=0。\n'
           '  。\n'
           '  方法 存款 接收 赵额：\n'
           '    自身.余额=自身.余额加赵额。\n'
           '    返回 自身.余额。\n'
           '  。\n'
           '。\n'
           '定义赵a=新建王账户()。\n'
           '赵a.存款(50)。')
    assert _run(src) == 50


def test_ac51_multi_arg_method_binds_params():
    """AC-51: 多参方法 `赵a.转账(赵b, 50)` 形参绑定正确。"""
    src = ('类 王账户：\n'
           '  构造 接收 赵初：\n'
           '    自身.余额=赵初。\n'
           '  。\n'
           '  方法 转账 接收 赵目标 赵额：\n'
           '    自身.余额=自身.余额减赵额。\n'
           '    赵目标.余额=赵目标.余额加赵额。\n'
           '    返回 自身.余额。\n'
           '  。\n'
           '。\n'
           '定义赵a=新建王账户(100)。\n'
           '定义赵b=新建王账户(0)。\n'
           '赵a.转账(赵b 50)。\n'
           '赵b.余额。')
    assert _run(src) == 50


def test_ac52_empty_parens_equivalent_to_access():
    """AC-52: `赵狗.叫声()` 等价 `赵狗.叫声`（M-04）。"""
    assert _run(_ANIMAL_SRC + '定义赵狗=新建狗("旺财", 3)。\n赵狗.叫声()。') == '汪汪！'


def test_ac53_inherited_arg_method_binds_params():
    """AC-53: 继承链带参方法正确传参。"""
    src = ('类 基：\n'
           '  方法 王和 接收 赵甲 赵乙：\n'
           '    返回 赵甲 加 赵乙。\n'
           '  。\n'
           '。\n'
           '类 子 继承 基：\n'
           '。\n'
           '定义赵z=新建子()。\n'
           '赵z.王和(3 4)。')
    assert _run(src) == 7


def test_ac54_inherited_zero_arg_method_access_invokes():
    """AC-54: 继承链上 0 参方法保持访问即调用。"""
    src = ('类 基：\n'
           '  方法 王讯：\n'
           '    返回 "hi"。\n'
           '  。\n'
           '。\n'
           '类 子 继承 基：\n'
           '。\n'
           '定义赵z=新建子()。\n'
           '赵z.王讯。')
    assert _run(src) == 'hi'


def _bm_src():
    return ('类 王类：\n'
            '  方法 王加 接收 赵甲 赵乙：\n'
            '    返回 赵甲 加 赵乙。\n'
            '  。\n'
            '。\n'
            '定义赵o=新建王类()。\n')


def test_ac55_bound_method_cannot_be_assigned():
    """AC-55: BoundMethod 不可作为值赋值给变量。"""
    src = _bm_src() + '定义赵f=赵o.王加。'
    try:
        _run(src)
        raise AssertionError('应抛出 TypeError')
    except JiKuaiError as e:
        assert e.info is not None and e.info.category == ErrorCategory.TYPE, e.info
        assert '方法不能作为值使用' in e.info.message
        assert '王类.王加(参数)' in e.info.message


def test_ac56_bound_method_cannot_be_passed():
    """AC-56: BoundMethod 不可作为参数传递给函数/动词。"""
    src = _bm_src() + '函数 王吃 接收 赵参：\n返回 赵参。\n。\n王吃(赵o.王加)。'
    try:
        _run(src)
        raise AssertionError('应抛出 TypeError')
    except JiKuaiError as e:
        assert e.info is not None and e.info.category == ErrorCategory.TYPE
        assert '方法不能作为值使用' in e.info.message


def test_ac57_bound_method_cannot_be_returned():
    """AC-57: BoundMethod 不可作为返回值。"""
    src = _bm_src() + '函数 王取 接收 赵x：\n返回 赵x.王加。\n。\n王取(赵o)。'
    try:
        _run(src)
        raise AssertionError('应抛出 TypeError')
    except JiKuaiError as e:
        assert e.info is not None and e.info.category == ErrorCategory.TYPE
        assert '方法不能作为值使用' in e.info.message


def test_ac58_example_oop_no_regression():
    """AC-58: `examples/oop.jk` 零回归 —— 4 行预期输出全对。"""
    import contextlib
    from jikuai.main import run_source
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        with open(os.path.join(os.path.dirname(__file__), '..', 'examples', 'oop.jk'),
                  encoding='utf-8') as f:
            run_source(f.read())
    lines = [ln for ln in out.getvalue().strip().split('\n') if ln]
    assert lines == ['我是旺财，今年3岁', '我是咪咪，今年2岁', '汪汪！', '喵喵~'], lines


# ============================================================
# 治理项 3（AC-59 ~ AC-65）· ADR-08 顶层拦截三种控制流信号
# ============================================================

def _expect_syntax(src, needle):
    try:
        _run(src)
        raise AssertionError('应抛出 SYNTAX 诊断')
    except JiKuaiError as e:
        assert e.info is not None, 'JiKuaiError 缺少 info'
        assert e.info.category == ErrorCategory.SYNTAX, e.info.category
        assert needle in e.info.message, (needle, e.info.message)


def test_ac59_toplevel_return_value_gives_syntax_diag():
    """AC-59: 顶层 `返回 0。` → SYNTAX 中文诊断（固定文案）。"""
    _expect_syntax('返回 0。', '「返回」只能在函数或方法体内使用。')


def test_ac60_toplevel_return_bare_gives_syntax_diag():
    """AC-60: 顶层 `返回。` → SYNTAX 中文诊断。"""
    _expect_syntax('返回。', '「返回」只能在函数或方法体内使用。')


def test_ac61_toplevel_break_gives_syntax_diag():
    """AC-61: 顶层 `跳出。` → SYNTAX 中文诊断。"""
    _expect_syntax('跳出。', '「跳出」只能在循环体内使用。')


def test_ac62_toplevel_continue_gives_syntax_diag():
    """AC-62: 顶层 `跳过。` → SYNTAX 中文诊断。"""
    _expect_syntax('跳过。', '「跳过」只能在循环体内使用。')


def test_ac63_return_inside_loop_gives_return_diag():
    """AC-63: 循环体内 `返回` → 报"返回只能在函数或方法体内"。"""
    src = '重复 3 次：\n返回 1。\n。'
    _expect_syntax(src, '「返回」只能在函数或方法体内使用。')


def test_ac64_break_inside_function_gives_break_diag():
    """AC-64: 函数体内 `跳出` → 报"跳出只能在循环体内"。"""
    src = ('函数 王测：\n'
           '  跳出。\n'
           '。\n'
           '王测()。')
    _expect_syntax(src, '「跳出」只能在循环体内使用。')


def test_ac65_nested_function_return_still_works():
    """AC-65（R-C）：嵌套函数与闭包内合法 `返回` 不受影响。"""
    src = ('函数 外 接收 赵x：\n'
           '  函数 内 接收 赵y：\n'
           '    返回 赵y 加 1。\n'
           '  。\n'
           '  返回 内(赵x)。\n'
           '。\n'
           '外(10)。')
    assert _run(src) == 11
    # 循环 + break/continue 内部仍然正常
    assert _run('定义赵s=0。\n重复 5 次：\n定义赵s=赵s加1。\n如果 赵s 大于等于 3 那么：\n跳出。\n。\n。\n赵s。') == 3


# ============================================================
# 补充：REPL 顶层的三种控制流信号也走中文诊断（D-05 关闭确认）
# ============================================================

def test_d05_repl_toplevel_return_reports_syntax():
    _, _, err = _repl_lines(['返回 0。'])
    assert '「返回」只能在函数或方法体内使用。' in err, err


def test_d05_repl_toplevel_break_reports_syntax():
    _, _, err = _repl_lines(['跳出。'])
    assert '「跳出」只能在循环体内使用。' in err, err


def test_d05_repl_toplevel_continue_reports_syntax():
    _, _, err = _repl_lines(['跳过。'])
    assert '「跳过」只能在循环体内使用。' in err, err


# ============================================================
# 补充：lexer.get_user_defs() 契约
# ============================================================

def test_lexer_exposes_user_defs():
    """`Lexer.get_user_defs()` 应返回本次收集到的 user_defs 集合（含 `自身.X` 字段）。"""
    src = '类 王类：\n  构造：\n    自身.最大值=100。\n  。\n。'
    lx = Lexer(src)
    lx.tokenize()
    defs = lx.get_user_defs()
    assert '王类' in defs
    assert '最大值' in defs


def test_lexer_external_defs_union():
    """`external_defs` 与 prescan 结果取并集，可跨输入注入。"""
    src = '赵c.取值("x")。'
    toks = tokenize(src, external_defs={'取值', '赵c'})
    # `取值` 应被视为 IDENT 而非 VERB
    idents = [t.value for t in toks if t.type == TokenType.IDENT]
    verbs = [t.value for t in toks if t.type == TokenType.VERB]
    assert '取值' in idents, (idents, verbs)
    assert '取值' not in verbs, verbs
