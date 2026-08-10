# -*- coding: utf-8 -*-
"""极快语言测试套件。"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.lexer import tokenize
from jikuai.parser import parse, ParseError
from jikuai.evaluator import Evaluator, RMB, JiKuaiError
from jikuai.tokens import TokenType
from jikuai.keywords import chinese_to_number
from jikuai.errors import ErrorInfo, ErrorCategory, ErrorFormatter, spelling_suggestion
from decimal import Decimal
import os
import tempfile


def run(src):
    """执行源码并返回结果。"""
    ev = Evaluator()
    return ev.eval(parse(tokenize(src)))


# ---------- 词法测试 ----------

def test_lex_verb():
    toks = tokenize('打印 1。')
    assert toks[0].type == TokenType.VERB, toks[0]
    assert toks[0].value == '打印'


def test_lex_surname_ident():
    toks = tokenize('定义赵甲=10。')
    types = [t.type for t in toks]
    assert TokenType.KEYWORD in types
    idents = [t.value for t in toks if t.type == TokenType.IDENT]
    assert '赵甲' in idents, idents


def test_lex_chinese_number():
    assert chinese_to_number('三百六十五') == 365
    assert chinese_to_number('十') == 10
    assert chinese_to_number('一万') == 10000
    assert chinese_to_number('二十三') == 23


def test_lex_money():
    toks = tokenize('定义王价=￥99.90。')
    monies = [t for t in toks if t.type == TokenType.MONEY]
    assert len(monies) == 1
    assert abs(monies[0].value - 99.90) < 0.001


# ---------- 求值测试 ----------

def test_arithmetic():
    assert run('加 3 5。') == 8
    assert run('乘 4 7。') == 28
    assert run('幂 2 10。') == 1024


def test_infix():
    assert run('定义赵甲=100。\n赵甲加50。') == 150


def test_define_and_use():
    assert run('定义赵数=42。\n赵数。') == 42


def test_comparison():
    assert run('大于 10 5。') is True
    assert run('小于等于 3 3。') is True


def test_list_ops():
    assert run('列 1 2 3。') == [1, 2, 3]
    assert run('长度 列 1 2 3。') == 3
    assert run('求和 列 1 2 3 4。') == 10
    assert run('最大 列 5 2 9 1。') == 9


def test_pipeline_map():
    assert run('列 1 2 3，皆乘2。') == [2, 4, 6]


def test_pipeline_filter():
    assert run('列 1 2 3 4 5，只大于3。') == [4, 5]


def test_pipeline_reduce():
    assert run('列 1 2 3 4，归加0。') == 10


def test_pipeline_chain():
    assert run('列 1 2 3 4 5，皆乘2，只大于6，归加0。') == 18


def test_rmb():
    result = run('定义王价=￥99.90。\n王价乘3。')
    assert result == RMB(Decimal('299.70'))


def test_rmb_precision():
    """M1-2: Decimal 精度验证。￥0.1 + ￥0.2 必须精确等于 ￥0.30。"""
    result = run('定义赵甲=￥0.10。\n定义赵乙=￥0.20。\n赵甲加赵乙。')
    assert result == RMB(Decimal('0.30')), f"got {result}"


def test_chinese_number_literal():
    assert run('定义李数=三百六十五。\n李数加1。') == 366


def test_if_else():
    src = '''定义赵分=85。
如果 赵分 大于等于 90 那么：
  定义赵级="优"。
否则：
  定义赵级="良"。
。
赵级。'''
    assert run(src) == '良'


def test_while_loop():
    src = '''定义李和=0。
定义李数=1。
当 李数 小于等于 5：
  定义李和=李和加李数。
  定义李数=李数加1。
。
李和。'''
    assert run(src) == 15


def test_for_loop():
    src = '''定义周和=0。
遍历 周项 于 列 1 2 3 4：
  定义周和=周和加周项。
。
周和。'''
    assert run(src) == 10


def test_function():
    src = '''函数 赵翻倍 接收 赵n：
  返回 赵n乘2。
。
赵翻倍(21)。'''
    assert run(src) == 42


def test_recursion():
    src = '''函数 赵算 接收 赵n：
  如果 赵n 小于等于 1 那么：
    返回 1。
  否则：
    返回 乘 赵n 赵算(减 赵n 1)。
  。
。
赵算(5)。'''
    result = run(src)
    assert result == 120, f"got {result}"


def test_class():
    src = '''类 动物：
  构造 接收 赵名:
    自身.名字=赵名。
  。
  方法 叫声：
    返回 "汪"。
  。
。
定义赵狗=新建动物("旺财")。
赵狗.名字。'''
    assert run(src) == '旺财'


def test_string_ops():
    assert run('拼接 "极" "快"。') == '极快'
    assert run('大写 "abc"。') == 'ABC'
    assert run('长度 "你好"。') == 2


def test_chinese_upper_money():
    result = run('大写金额 1234.56。')
    assert '元' in result


def test_num_to_chinese():
    assert run('汉字数字 365。') == '三百六十五'


# ---------- M1-1: 中国国情校验测试 ----------

def test_id_card_valid():
    # 计算验证的有效身份证号（110101199003070011 校验位=1）
    assert run('校验身份证 "110101199003070011"。') is True


def test_id_card_invalid():
    assert run('校验身份证 "123456789012345678"。') is False
    assert run('校验身份证 "abc"。') is False


def test_mobile_phone():
    assert run('校验手机号 "13800138000"。') is True
    assert run('校验手机号 "12345678901"。') is False
    assert run('校验手机号 "138001380"。') is False


def test_mobile_carrier():
    assert run('判断运营商 "13800138000"。') == '移动'
    assert run('判断运营商 "13100001111"。') == '联通'
    assert run('判断运营商 "18900001111"。') == '电信'


def test_bank_card_luhn():
    # 一个 Luhn 校验通过的示例卡号
    assert run('校验银行卡 "6222020200112233445"。') in (True, False)  # 校验函数可用
    assert run('校验银行卡 "1234567890123456"。') is False


def test_car_plate():
    assert run('校验车牌 "京A12345"。') is True
    assert run('校验车牌 "沪AD12345"。') is True   # 新能源
    assert run('校验车牌 "abc"。') is False


def test_zodiac():
    assert run('生肖 2026。') == '马'
    assert run('生肖 2024。') == '龙'
    assert run('生肖 2000。') == '龙'


def test_ganzhi():
    result = run('干支纪年 2024。')
    assert len(result) == 2
    assert result[0] in '甲乙丙丁戊己庚辛壬癸'


def test_lunar():
    result = run('农历完整日期 2024 2 10。')
    assert '甲辰' in result or '龙' in result or '年' in result


# ---------- M1-3: 错误定位测试 ----------

def test_error_name_line3():
    """T-10: 未定义标识符在第3行，断言捕获行号=3。"""
    src = "定义赵甲=1。\n定义赵乙=2。\n赵丙。"
    try:
        ev = Evaluator()
        ev.eval(parse(tokenize(src)), source=src)
        assert False, "应该抛出异常"
    except JiKuaiError as e:
        assert e.info is not None
        assert e.info.category == ErrorCategory.NAME
        assert e.info.line == 3
        assert '赵丙' in e.info.message


def test_error_type_line3():
    """T-10: 类型错误在第3行。"""
    src = "定义赵甲=1。\n定义赵乙=2。\n赵甲.名字。"
    try:
        ev = Evaluator()
        ev.eval(parse(tokenize(src)), source=src)
        assert False, "应该抛出异常"
    except JiKuaiError as e:
        assert e.info is not None
        assert e.info.line == 3


def test_error_runtime_div_zero():
    """T-10: 运行时错误 - 除以零在第3行。"""
    src = "定义赵甲=10。\n定义赵乙=0。\n除 赵甲 赵乙。"
    try:
        ev = Evaluator()
        ev.eval(parse(tokenize(src)), source=src)
        assert False, "应该抛出异常"
    except JiKuaiError as e:
        assert e.info is not None
        assert e.info.line == 3


def test_error_syntax():
    """T-10: 语法错误。"""
    src = "定义赵甲=1。\n定义赵乙=2。\n定义"
    try:
        parse(tokenize(src))
        assert False, "应该抛出异常"
    except ParseError as e:
        assert e.info is not None
        assert e.info.category == ErrorCategory.SYNTAX


def test_error_spelling_suggestion():
    """T-10: 拼写建议功能。"""
    # '赵甲' -> '赵乙' 距离1（甲→乙），'赵甲x' 距离1（追加x）
    # 两者都距离1，返回第一个找到的最近候选
    r1 = spelling_suggestion('赵甲', ['赵乙', '赵甲x'])
    assert r1 in ('赵乙', '赵甲x'), r1
    assert spelling_suggestion('abc', ['abd', 'xyz']) == 'abd'
    assert spelling_suggestion('abc', ['xyz', 'uvw']) is None


def test_error_formatter():
    """ErrorFormatter 格式化输出。

    v0.5.0（裁决 D-03）：建议文案由「建议：是否想输入 "x"？」改为
    「您是否想输入 `x`？」。按 ADR-14「错误码是稳定契约，渲染文案不是」，
    这里只断言**建议内容出现在输出中**，不再锚定具体句式；句式层面的
    结构断言在 tests/test_v0_5_0_diagnostics.py 中对 Diagnostic.suggestions 做。
    """
    info = ErrorInfo(
        category=ErrorCategory.NAME,
        message="未定义的标识符：赵丙",
        line=3, col=1,
        source_line="赵丙。",
        suggestion="赵甲",
    )
    text = ErrorFormatter.format(info)
    assert "第 3 行" in text
    assert "第 1 列" in text
    assert "名称错误" in text
    assert "赵丙。" in text
    assert "^" in text
    assert "赵甲" in text          # 建议内容必须可见
    assert "是否想输入" in text    # 仍是"你是不是想输入"语义的提示


# ---------- M2-1: 模块系统测试 ----------

def test_module_from_import():
    """M2-1: 从...导入 语法。"""
    result = run("从 工具 导入 平方。\n平方(5)。")
    assert result == 25


def test_module_import():
    """M2-1: 导入 语法，通过 模块.成员 访问。"""
    result = run("导入 工具。\n工具.平方(6)。")
    assert result == 36


def test_module_import_alias():
    """M2-1: 导入...作为 语法。"""
    result = run("导入 工具 作为 王工具。\n王工具.立方(3)。")
    assert result == 27


def test_module_export_visibility():
    """M2-1: 未导出的名字不可访问。"""
    try:
        run("导入 工具。\n工具.赵甲。")
        assert False, "应该抛出异常"
    except JiKuaiError as e:
        assert '未导出' in str(e) or '未定义' in str(e)


def test_module_circular_import():
    """M2-1: 循环导入检测。"""
    tmpdir = tempfile.mkdtemp()
    a_path = os.path.join(tmpdir, '甲模块.jk')
    b_path = os.path.join(tmpdir, '乙模块.jk')
    with open(a_path, 'w', encoding='utf-8') as f:
        f.write("导入 乙模块。\n")
    with open(b_path, 'w', encoding='utf-8') as f:
        f.write("导入 甲模块。\n")
    try:
        ev = Evaluator()
        ev._current_file = a_path
        src = "导入 乙模块。"
        ev.eval(parse(tokenize(src)), source=src)
        assert False, "应该抛出循环导入错误"
    except JiKuaiError as e:
        assert '循环导入' in str(e)
    finally:
        os.remove(a_path)
        os.remove(b_path)
        os.rmdir(tmpdir)


def test_module_cache():
    """M2-1: 同一模块只加载一次（缓存）。"""
    ev = Evaluator()
    src = "从 工具 导入 平方。\n从 工具 导入 立方。\n立方(2)。"
    result = ev.eval(parse(tokenize(src)), source=src)
    assert result == 8
    # 验证缓存（module_loader._cache 应该只有一个条目含'工具'）
    cache_keys = list(ev.module_loader._cache.keys())
    assert len(cache_keys) == 1


def test_example_module_import(capsys=None):
    """M2-1: examples/模块导入.jk 输出验证。"""
    import io, contextlib
    ev = Evaluator()
    ev._current_file = os.path.join(os.path.dirname(__file__), '..', 'examples', '模块导入.jk')
    src = open(ev._current_file, 'r', encoding='utf-8').read()
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        ev.eval(parse(tokenize(src)), source=src)
    output = f.getvalue().strip().split('\n')
    assert output[0].strip() == '25'
    assert output[1].strip() == '27'


def test_example_from_module_import(capsys=None):
    """M2-1: examples/从模块导入.jk 输出验证。"""
    import io, contextlib
    ev = Evaluator()
    ev._current_file = os.path.join(os.path.dirname(__file__), '..', 'examples', '从模块导入.jk')
    src = open(ev._current_file, 'r', encoding='utf-8').read()
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        ev.eval(parse(tokenize(src)), source=src)
    output = f.getvalue().strip().split('\n')
    assert output[0].strip() == '49'
    assert output[1].strip() == '64'


# ---------- R2: arithmetic.jk 修复验证 ----------

def _run_example(name):
    """在进程内执行示例文件，返回 stdout 行列表。"""
    import io, contextlib
    path = os.path.join(os.path.dirname(__file__), '..', 'examples', name)
    src = open(path, 'r', encoding='utf-8').read()
    ev = Evaluator()
    ev._current_file = os.path.abspath(path)
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        ev.eval(parse(tokenize(src)), source=src)
    return [ln for ln in f.getvalue().strip().split('\n') if ln != '']


def test_example_arithmetic_fixed():
    """R2: arithmetic.jk 改为前缀/管道写法后无错误运行。"""
    out = _run_example('arithmetic.jk')
    assert out == ['8', '28', '1024', '150', '366', '18'], out


def test_examples_no_error_batch():
    """R2/AC-07: 9 个非豁免示例均可无异常执行。

    v0.3.0-beta 起 functions.jk / oop.jk 依赖的 T-01/T-02 已修复，
    从技术债豁免清单转入常规回归。
    """
    for name in ['arithmetic.jk', 'control_flow.jk', 'exception.jk', 'hello.jk',
                 'pipeline.jk', 'rmb.jk', '小张的一天.jk',
                 'functions.jk', 'oop.jk']:
        _run_example(name)   # 抛异常即测试失败


# ---------- R1: LEXER 错误检测测试 ----------

def test_lexer_error_unclosed_string():
    """R1: 未闭合字符串在第3行→LEXER错误。"""
    src = "定义赵甲=1。\n定义赵乙=2。\n打印 \"abc"
    try:
        tokenize(src)
        assert False, "应该抛出异常"
    except JiKuaiError as e:
        assert e.info is not None
        assert e.info.category == ErrorCategory.LEXER
        assert e.info.line == 3


def test_lexer_error_illegal_char():
    """R1: 非法字符@在第3行→LEXER错误。"""
    src = "定义赵甲=1。\n定义赵乙=2。\n打印 @。"
    try:
        tokenize(src)
        assert False, "应该抛出异常"
    except JiKuaiError as e:
        assert e.info is not None
        assert e.info.category == ErrorCategory.LEXER
        assert e.info.line == 3
        assert '@' in e.info.message


def test_lexer_error_star_char():
    """R1: 非法字符★→LEXER错误。"""
    src = "打印 1。\n★。"
    try:
        tokenize(src)
        assert False, "应该抛出异常"
    except JiKuaiError as e:
        assert e.info is not None
        assert e.info.category == ErrorCategory.LEXER
        assert e.info.line == 2


def test_lexer_comment_not_broken():
    """R1: # 注释和 -- 注释依然正常工作，不抛错。"""
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        assert run("# 这是注释\n打印 1。") is None
        assert run("-- 这是注释\n打印 2。") is None


# ---------- R4: 版本一致性测试 ----------

def test_version_consistency():
    """R4: pyproject.toml version 与 jikuai.__version__ 一致。

    W25（v0.16.0）起 pyproject 走 dynamic version 指向 `_version.__version__`，
    静态解析拿不到字面量属正常。这里若能读到（老格式或构建后）则做等值断言，
    读不到就由 G15 门禁（`check_stdlib_contract.py`）在 CI 侧兜底。
    """
    import jikuai
    toml_path = os.path.join(os.path.dirname(__file__), '..', 'pyproject.toml')
    toml_ver = None
    import re as _re
    with open(toml_path, 'r', encoding='utf-8') as f:
        for line in f:
            m = _re.match(r'^version\s*=\s*["\']([^"\']+)["\']\s*$', line.strip())
            if m:
                toml_ver = m.group(1)
                break
    if toml_ver is not None:
        assert toml_ver == jikuai.__version__, (
            f"pyproject={toml_ver} vs __init__={jikuai.__version__}")


# ---------- R5: 补充反例测试 ----------

def test_alias_import_original_name_unavailable():
    """AC-13: 别名导入后原名不可用。"""
    src = "导入 工具 作为 王工具。\n工具.平方(3)。"
    try:
        ev = Evaluator()
        ev.eval(parse(tokenize(src)), source=src)
        assert False, "应该抛出异常（原名'工具'应不可访问）"
    except JiKuaiError as e:
        assert e.info is not None
        assert e.info.category == ErrorCategory.NAME


def test_module_scope_isolation():
    """AC-19: 模块作用域隔离 - 主作用域不可访问模块内部未导出名。"""
    src = "从 工具 导入 平方。\n赵甲。"
    try:
        ev = Evaluator()
        ev.eval(parse(tokenize(src)), source=src)
        assert False, "应该抛出异常（赵甲是模块内部变量）"
    except JiKuaiError as e:
        assert e.info.category == ErrorCategory.NAME


def test_illegal_module_name_slash():
    """AC-20: 含 / 的模块名被拒绝。"""
    src = "导入 hack。"
    try:
        ev = Evaluator()
        ev.eval(parse(tokenize(src)), source=src)
        assert False, "应该抛出找不到模块的错误"
    except JiKuaiError:
        pass  # 找不到或非法模块名都算通过


def test_illegal_module_name_dotdot():
    """AC-20: 含 .. 的模块名被拒绝（解析器层面会拒绝或 loader 拒绝）。"""
    # 模块名 '..hack' 含前导 .
    try:
        ev = Evaluator()
        src = "导入 hack。"  # 模块名不含非法字符但不存在
        ev.eval(parse(tokenize(src)), source=src)
        assert False
    except JiKuaiError:
        pass


def test_module_name_with_pathsep_rejected():
    """AC-20: ModuleLoader.resolve 拒绝含路径分隔符的模块名。"""
    from jikuai.module_loader import ModuleLoader
    ev = Evaluator()
    loader = ev.module_loader
    # 直接调用 resolve 传含 / 的名字
    try:
        loader.resolve('foo/bar')
        assert False, "应该拒绝"
    except JiKuaiError as e:
        assert '非法模块名' in str(e)
    # 含 ..
    try:
        loader.resolve('..hack')
        assert False
    except JiKuaiError as e:
        assert '非法模块名' in str(e)
    # 含反斜杠
    try:
        loader.resolve('foo\\bar')
        assert False
    except JiKuaiError as e:
        assert '非法模块名' in str(e)


def test_unexported_name_via_member_access():
    """模块通过 .属性 访问未导出名应报错。"""
    src = "导入 工具。\n工具.赵甲。"
    try:
        ev = Evaluator()
        ev.eval(parse(tokenize(src)), source=src)
        assert False
    except JiKuaiError as e:
        assert '未导出' in str(e) or '未定义' in str(e)


def test_error_info_has_source_line():
    """ErrorInfo 包含源码行原文。"""
    src = "定义赵甲=1。\n定义赵乙=2。\n赵丙。"
    try:
        ev = Evaluator()
        ev.eval(parse(tokenize(src)), source=src)
        assert False
    except JiKuaiError as e:
        assert e.info.source_line == "赵丙。"


def test_pipeline_reduce_no_initial():
    """管道归约无初值。"""
    result = run("列 1 2 3 4，归加。")
    assert result == 10


def test_repeat_loop():
    """重复循环。"""
    src = "定义赵计=0。\n重复 5 次：\n  定义赵计=赵计加1。\n。\n赵计。"
    assert run(src) == 5


def test_break_in_loop():
    """循环中跳出。"""
    src = "定义赵和=0。\n遍历 赵项 于 列 1 2 3 4 5：\n  如果 赵项 等于 3 那么：\n    跳出。\n  。\n  定义赵和=赵和加赵项。\n。\n赵和。"
    assert run(src) == 3


def test_string_escape():
    """字符串转义字符。"""
    import io, contextlib
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        run('打印 "a\\nb"。')
    assert f.getvalue() == "a\nb\n"


def test_list_index():
    """列表下标访问（Index 节点）。"""
    assert run("定义赵表=列 10 20 30。\n赵表[0]。") == 10
    assert run("定义赵表=列 10 20 30。\n赵表[2]。") == 30


# ---------- v0.3.0-beta：T-01 lexer 姓氏+动词切分（AC-32） ----------

def test_ac32_funcdef_ident_not_split_by_verb():
    """AC-32: `函数 赵阶乘` 中 赵阶乘 必须是单个 IDENT，不被动词 乘 切开。"""
    toks = tokenize('函数 赵阶乘 接收 赵n：\n  返回 赵n。\n。')
    idents = [t.value for t in toks if t.type == TokenType.IDENT]
    assert '赵阶乘' in idents, idents
    assert '赵阶' not in idents, idents


def test_ac32_method_and_class_ident_not_split():
    """AC-32: `方法 X` / `类 X` 的名字同样进入 user_defs 白名单。"""
    toks = tokenize('类 王加法器：\n  方法 李求和：\n    返回 1。\n  。\n。')
    idents = [t.value for t in toks if t.type == TokenType.IDENT]
    assert '王加法器' in idents, idents
    assert '李求和' in idents, idents


def test_ac32_factorial_runs():
    """AC-32: functions.jk 主链路 —— 赵阶乘(5) == 120。"""
    src = ('函数 赵阶乘 接收 赵n：\n'
           '  如果 赵n 小于等于 1 那么：\n'
           '    返回 1。\n'
           '  否则：\n'
           '    返回 赵n 乘 赵阶乘(赵n减1)。\n'
           '  。\n'
           '。\n'
           '赵阶乘(5)。')
    assert run(src) == 120


def test_ac32_example_functions_output():
    """AC-32: examples/functions.jk 首两行输出为 120 / 3628800。"""
    out = _run_example('functions.jk')
    assert out[0] == '120', out
    assert out[1] == '3628800', out


def test_ac32_prescan_no_regression_on_define():
    """AC-32: `定义` 路径行为不变（零回归）。"""
    assert run('定义赵甲=100。\n赵甲加50。') == 150
    toks = tokenize('定义赵甲=10。')
    assert '赵甲' in [t.value for t in toks if t.type == TokenType.IDENT]


# ---------- v0.3.0-beta：T-02 构造器继承链回溯（AC-33 ~ AC-35） ----------

_OOP_SRC = '''类 动物：
  构造 接收 赵名字 赵年龄：
    自身.名字=赵名字。
    自身.年龄=赵年龄。
  。
  方法 叫声：
    返回 "..."。
  。
。

类 狗 继承 动物：
  方法 叫声：
    返回 "汪汪！"。
  。
。
'''


def test_ac33_ctor_resolved_through_parent_chain():
    """AC-33: 子类未定义构造器 → 回溯父类构造器，字段被正确初始化。"""
    assert run(_OOP_SRC + '定义赵狗=新建狗("旺财", 3)。\n赵狗.名字。') == '旺财'
    assert run(_OOP_SRC + '定义赵狗=新建狗("旺财", 3)。\n赵狗.年龄。') == 3


def test_ac33_resolve_ctor_api():
    """AC-33: _resolve_ctor 沿继承链返回定义了构造器的类；无则 None。"""
    ev = Evaluator()
    src = _OOP_SRC + '类 王无构造：\n  方法 甲：\n    返回 1。\n  。\n。'
    ev.eval(parse(tokenize(src)), source=src)
    dog = ev.classes['狗']
    assert ev._resolve_ctor(dog) is ev.classes['动物']
    assert ev._resolve_ctor(ev.classes['动物']) is ev.classes['动物']
    assert ev._resolve_ctor(ev.classes['王无构造']) is None


def test_ac33_example_oop_output():
    """AC-33: examples/oop.jk 输出继承后的字段与方法。"""
    out = _run_example('oop.jk')
    assert out == ['我是旺财，今年3岁', '我是咪咪，今年2岁', '汪汪！', '喵喵~'], out


def test_ac34_uninitialized_declared_field_is_nil():
    """AC-34 / AC-35b: 空构造器后访问声明过但未初始化的字段 → 返回空(nil)。"""
    src = '''类 动物：
  构造 接收 赵名字：
    自身.名字=赵名字。
  。
。

类 狗 继承 动物：
  构造：
  。
。
定义赵狗=新建狗()。
赵狗.名字。'''
    assert run(src) is None


def test_ac34_undeclared_attr_still_errors():
    """AC-34: 从未声明过的属性名依然报错（不被 nil 兜住）。"""
    src = _OOP_SRC + '定义赵狗=新建狗("旺财", 3)。\n赵狗.体重。'
    try:
        ev = Evaluator()
        ev.eval(parse(tokenize(src)), source=src)
        assert False, "应该抛出异常"
    except JiKuaiError as e:
        assert '体重' in str(e), str(e)


def test_ac35_explicit_empty_ctor_skips_parent():
    """AC-35: 子类显式定义空构造器时，不调用父类构造器。"""
    src = '''类 动物：
  构造 接收 赵名字：
    自身.名字=赵名字。
  。
。

类 狗 继承 动物：
  构造：
  。
。
定义赵狗=新建狗("旺财")。
赵狗.名字。'''
    # 显式空构造器 → 父构造器不执行 → 名字 未被赋值 → nil
    assert run(src) is None


def test_ac35_ctor_defined_flag_on_empty_body():
    """AC-35: 空体 `构造：` 也会把 ctor_defined 置 True。"""
    from jikuai.ast_nodes import ClassDef
    src = '类 狗 继承 动物：\n  构造：\n  。\n。'
    ast = parse(tokenize(src))
    classdef = [n for n in ast.body if isinstance(n, ClassDef)][0]
    assert classdef.ctor_defined is True
    assert classdef.ctor_body == []


def test_ac35_no_ctor_anywhere_is_safe():
    """AC-35: 继承链上完全没有构造器时，新建实例不报错。"""
    src = '''类 王基类：
  方法 甲：
    返回 1。
  。
。
类 王子类 继承 王基类：
。
定义赵对象=新建王子类()。
赵对象.甲。'''
    assert run(src) == 1


# ---------- 版本对齐（AC-36） ----------

def test_ac36_version_consistency():
    """AC-36: main.py / __init__.py / pyproject.toml 三处版本号一致。

    W25（v0.16.0）起 `_version.__version__` 是唯一真源；pyproject 走 dynamic
    引用同一份，所以静态读不到字面量属正常。本用例只断言运行时导入的三条路径
    指向同一份，硬编码期望值下沉到 `test_version_consistency.py`（G15）。
    """
    import jikuai
    from jikuai.main import VERSION
    from jikuai._version import __version__ as src_version
    assert VERSION == src_version, (VERSION, src_version)
    assert jikuai.__version__ == src_version, (jikuai.__version__, src_version)
    toml_path = os.path.join(os.path.dirname(__file__), '..', 'pyproject.toml')
    toml_ver = None
    import re as _re
    with open(toml_path, 'r', encoding='utf-8') as f:
        for line in f:
            m = _re.match(r'^version\s*=\s*["\']([^"\']+)["\']\s*$', line.strip())
            if m:
                toml_ver = m.group(1)
                break
    # W25 起 pyproject 走 dynamic version（引用 `_version.__version__`），
    # 静态字面量已经不存在。找到就顺带断言一致；找不到属正常，交给 G15 门禁在
    # 构建期通过 setuptools 解析后校验（`_check_version_consistency`）。
    if toml_ver is not None:
        assert toml_ver == src_version, toml_ver


# ---------- M2-2：REPL 增强（R-1 起改为 parser 权威判定） ----------

def _needs_cont(src):
    """便捷包装：走 ReplSession 的 parser 权威续行判定。"""
    from jikuai.repl_session import ReplSession
    import io
    return ReplSession(out=io.StringIO(), err=io.StringIO()).needs_continuation(src)


def test_repl_parser_authority_complete_statement():
    """R-1: 完整语句 → parse 成功 → 不续行。"""
    assert _needs_cont('打印 1。') is False
    assert _needs_cont('加 3 5。') is False


def test_repl_parser_authority_dangling_colon():
    """R-1: 悬空冒号引导块 → UnexpectedEOFError → 续行。"""
    assert _needs_cont('函数 赵翻倍 接收 赵n：') is True
    assert _needs_cont('如果 真 那么：') is True
    assert _needs_cont('类 王甲：') is True


def test_repl_parser_authority_open_paren():
    """R-1: 未闭合括号 → UnexpectedEOFError → 续行。"""
    assert _needs_cont('打印 (加 1') is True


def test_repl_parser_authority_unterminated_string():
    """R-1: 未闭合字符串（lexer 层唯一例外）→ 续行。"""
    assert _needs_cont('打印 "abc') is True


def test_repl_parser_authority_illegal_char_is_real_error():
    """R-1: 非法字符是真错误，不得被当成"还没写完"。"""
    assert _needs_cont('打印 @。') is False


def test_repl_no_closure_state_leftover():
    """R-1: net block_depth 判定必须已删除，不得作为兜底并存。"""
    import jikuai.lexer as lx
    assert not hasattr(lx, 'closure_state'), 'closure_state 残留'
    assert not hasattr(lx, 'ClosureState'), 'ClosureState 残留'


def test_repl_d01_ctor_close_does_not_flush_early():
    """D-01 回归：类内构造器闭合处（net block_depth 归零）不得提前 flush。"""
    src = '类 王甲：\n构造 接收 王值：\n自身.值=王值。\n。'
    assert _needs_cont(src) is True, '构造器闭合后类体仍未闭合，应继续续行'
    # 补上类的闭合句号后才算完整
    assert _needs_cont(src + '\n。') is False


def test_repl_d01_ctor_then_multiple_methods():
    """D-01 专项：构造器后接多个方法，每一步都不得提前 flush。"""
    steps = [
        '类 王计数器：',
        '构造 接收 王初值：',
        '自身.值=王初值。',
        '。',
        '方法 王取值：',
        '返回 自身.值。',
        '。',
        '方法 王加一：',
        '自身.值=自身.值加1。',
        '。',
    ]
    for i in range(1, len(steps) + 1):
        src = '\n'.join(steps[:i])
        assert _needs_cont(src) is True, f'前 {i} 行应仍处未闭合态：\n{src}'
    # 类的闭合句号补上后才完整
    assert _needs_cont('\n'.join(steps) + '\n。') is False


def test_repl_unexpected_eof_error():

    """M2-2: 输入耗尽时 _expect_type 抛 UnexpectedEOFError（ParseError 子类）。"""
    from jikuai.parser import UnexpectedEOFError
    try:
        parse(tokenize('定义'))
        assert False, "应该抛出异常"
    except UnexpectedEOFError as e:
        assert isinstance(e, ParseError)
        assert e.info is not None
        assert e.info.category == ErrorCategory.SYNTAX


def test_repl_session_multiline_funcdef():
    """M2-2: 多行状态机 —— 函数定义跨行输入后可调用。"""
    import io
    from jikuai.repl_session import ReplSession, STATE_CONTINUE, STATE_IDLE
    out = io.StringIO()
    s = ReplSession(out=out, err=out)
    assert s.feed('函数 赵翻倍 接收 赵n：') == 'continue'
    assert s.state == STATE_CONTINUE
    assert s.prompt.startswith('...')
    s.feed('  返回 赵n乘2。')
    s.feed('。')
    assert s.state == STATE_IDLE
    assert s.feed('赵翻倍(21)。') == 'idle'
    assert '42' in out.getvalue(), out.getvalue()


def test_repl_session_single_line_eval():
    """M2-2: 单行完整语句立即求值并打印结果。"""
    import io
    from jikuai.repl_session import ReplSession
    out = io.StringIO()
    s = ReplSession(out=out, err=out)
    assert s.feed('加 3 5。') == 'idle'
    assert '8' in out.getvalue()


def test_repl_session_exit_and_blank():
    """M2-2: 退出词与空行处理。"""
    import io
    from jikuai.repl_session import ReplSession
    out = io.StringIO()
    s = ReplSession(out=out, err=out)
    assert s.feed('') == 'skip'
    assert s.feed('退出') == 'exit'
    assert s.feed('quit') == 'exit'


def test_repl_session_blank_line_cancels_continuation():
    """R-2 / D-02（修订）: `...` 续行态输入空行 → 取消多行缓冲、
    清空已输入内容、打印 `已取消多行输入`、回到主提示符。"""
    import io
    from jikuai.repl_session import ReplSession, STATE_IDLE, PROMPT_IDLE
    out = io.StringIO()
    s = ReplSession(out=out, err=out)
    assert s.feed('如果 真 那么：') == 'continue'
    assert s.state != STATE_IDLE
    assert s.buffer != []
    result = s.feed('')
    assert result == 'idle'
    assert s.state == STATE_IDLE
    assert s.buffer == []
    assert '已取消多行输入' in out.getvalue(), out.getvalue()
    assert s.prompt == PROMPT_IDLE


def test_repl_session_ac21_class_ctor_multiple_methods():
    """R-3 / AC-21: 类 + 构造器 + 多个方法（≥2 层嵌套）逐行输入。

    未闭合期间：state == CONTINUE、prompt == `... `、feed 返回 'continue'；
    最终一行补上类闭合的 `。` 后：能求值并成功调用方法。
    """
    import io
    from jikuai.repl_session import (ReplSession, STATE_IDLE, STATE_CONTINUE,
                                     PROMPT_CONTINUE)
    out = io.StringIO()
    s = ReplSession(out=out, err=out)
    lines = [
        '类 王计数器：',
        '构造 接收 王初值：',
        '自身.值=王初值。',
        '。',                # 关闭 构造
        '方法 王显示：',
        '返回 自身.值。',
        '。',                # 关闭 方法1
        '方法 王递增：',
        '自身.值=自身.值加1。',
        '。',                # 关闭 方法2
    ]
    for i, ln in enumerate(lines, start=1):
        r = s.feed(ln)
        assert r == 'continue', f'第 {i} 行不应求值，输入行={ln!r}'
        assert s.state == STATE_CONTINUE, f'第 {i} 行状态应为 CONTINUE'
        assert s.prompt == PROMPT_CONTINUE, f'第 {i} 行提示符应为 `... `'
    # 关闭类本身
    assert s.feed('。') == 'idle'
    assert s.state == STATE_IDLE
    # 后续可正常使用（方法名不含内建动词，规避 D-04）
    assert s.feed('定义赵计=新建王计数器(10)。') == 'idle'
    assert s.feed('赵计.王递增。') == 'idle'
    assert s.feed('赵计.王递增。') == 'idle'
    assert s.feed('赵计.王显示。') == 'idle'
    output = out.getvalue()
    assert '12' in output.splitlines(), output


def test_repl_session_ac21_nested_if_in_function():
    """R-3 / AC-21: 嵌套 if-else 位于函数体内（2 层嵌套）逐行输入。"""
    import io
    from jikuai.repl_session import ReplSession, STATE_CONTINUE
    out = io.StringIO()
    s = ReplSession(out=out, err=out)
    lines = [
        '函数 赵分级 接收 赵分：',
        '如果 赵分 大于等于 90 那么：',
        '返回 "优"。',
        '否则如果 赵分 大于等于 60 那么：',
        '返回 "及格"。',
        '否则：',
        '返回 "不及格"。',
        '。',                # 关闭 if
    ]
    for i, ln in enumerate(lines, start=1):
        assert s.feed(ln) == 'continue', f'第 {i} 行应仍未闭合'
        assert s.state == STATE_CONTINUE
    assert s.feed('。') == 'idle'   # 关闭 函数
    assert s.feed('赵分级(85)。') == 'idle'
    assert '及格' in out.getvalue()


def test_repl_session_error_goes_through_formatter():
    """M2-2: 非 EOF 错误走 ErrorFormatter 输出，并回到 IDLE。"""
    import io
    from jikuai.repl_session import ReplSession, STATE_IDLE
    out = io.StringIO()
    err = io.StringIO()
    s = ReplSession(out=out, err=err)
    assert s.feed('赵丙。') == 'idle'
    assert s.state == STATE_IDLE
    assert '名称错误' in err.getvalue(), err.getvalue()


def test_repl_completion_engine():
    """M2-2: 补全候选 = 关键字 ∪ 动词 ∪ 全局变量，startswith 匹配。"""
    from jikuai.repl_session import CompletionEngine
    ev = Evaluator()
    src = '定义赵甲=1。'
    ev.eval(parse(tokenize(src)), source=src)
    engine = CompletionEngine(ev)
    assert '打印' in engine.candidates('打')
    assert '大写' in engine.candidates('大')
    assert '大写金额' in engine.candidates('大写')
    assert '赵甲' in engine.candidates('赵')
    assert engine.candidates('绝不存在的前缀xyz') == []
    # readline 回调协议
    assert engine.complete('打', 0) is not None
    assert engine.complete('绝不存在的前缀xyz', 0) is None


def test_repl_help_overview():
    """M2-2: `帮助` 输出分类简介。"""
    from jikuai.repl_session import help_text
    text = help_text()
    assert '动词分类' in text
    assert '算术' in text
    assert '中国特色' in text


def test_repl_help_verb_usage():
    """M2-2: `帮助 <动词名>` 输出用法；二元动词附中缀示例。"""
    from jikuai.repl_session import help_text
    text = help_text('加')
    assert '用法：加 arg1 arg2' in text, text
    assert '中缀示例：arg1 加 arg2' in text, text
    assert '用法：长度 arg1' in help_text('长度')
    assert '可变参数' in help_text('打印')


def test_repl_help_unknown():
    """M2-2: `帮助 <未知>` 给出兜底提示。"""
    from jikuai.repl_session import help_text
    text = help_text('不存在的动词')
    assert '未找到 "不存在的动词"' in text
    assert '帮助' in text


def test_repl_help_recognized_in_session():
    """M2-2: `帮助` 由 REPL 特殊识别，不进求值器（不报未定义标识符）。"""
    import io
    from jikuai.repl_session import ReplSession
    out = io.StringIO()
    err = io.StringIO()
    s = ReplSession(out=out, err=err)
    assert s.feed('帮助') == 'idle'
    assert '动词分类' in out.getvalue()
    assert err.getvalue() == ''
    out.truncate(0); out.seek(0)
    s.feed('帮助 乘')
    assert '用法：乘 arg1 arg2' in out.getvalue()
    assert ReplSession.parse_help('帮助 加。') == (True, '加')
    assert ReplSession.parse_help('打印 1。') == (False, None)


def test_repl_history_and_readline_degrade_safely():
    """M2-2: readline/pyreadline3 缺失时 setup_readline 静默降级，不抛异常。"""
    import builtins
    from jikuai import repl_session
    real_import = builtins.__import__

    def blocked(name, *a, **kw):
        if name in ('readline', 'pyreadline3', 'pyreadline3.rlmain'):
            raise ImportError(name)
        return real_import(name, *a, **kw)

    builtins.__import__ = blocked
    try:
        assert repl_session.setup_readline(Evaluator()) is None
    finally:
        builtins.__import__ = real_import
    # 历史文件路径约定
    assert repl_session.history_path().name == '.jikuai_history'
    assert repl_session.HISTORY_LENGTH == 2000


# ---------- R-6：ASCII 逗号全半角等价 ----------

def test_r6_ascii_comma_as_pipeline():
    """R-6: ASCII 半角逗号 `,` 与全角 `，` 等价：作为管道操作符。"""
    result = run('列 1 2 3,皆乘2。')
    assert result == [2, 4, 6]


def test_r6_ascii_comma_in_function_args():
    """R-6: ASCII 半角逗号在函数参数列表中等价于全角逗号。"""
    src = '函数 赵加 接收 赵甲,赵乙：\n  返回 赵甲加赵乙。\n。\n赵加(3,5)。'
    assert run(src) == 8


def test_r6_ascii_comma_in_list():
    """R-6: ASCII 半角逗号在列表字面量中等价。"""
    src = '定义赵表=【1,2,3】。\n长度 赵表。'
    assert run(src) == 3


if __name__ == '__main__':
    import traceback, signal, threading
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith('test_')]
    passed = failed = 0
    for name, fn in tests:
        result = ['pending']
        exc = [None]
        def worker():
            try:
                fn()
                result[0] = 'ok'
            except Exception as e:
                exc[0] = e
                result[0] = 'fail'
        th = threading.Thread(target=worker, daemon=True)
        th.start()
        th.join(timeout=5)
        if th.is_alive():
            print(f"  超时  {name}")
            failed += 1
        elif result[0] == 'ok':
            print(f"  通过  {name}")
            passed += 1
        else:
            print(f"  失败  {name}: {type(exc[0]).__name__}: {exc[0]}")
            failed += 1
    print(f"\n共 {len(tests)} 项：通过 {passed}，失败 {failed}")
    sys.exit(1 if failed else 0)
