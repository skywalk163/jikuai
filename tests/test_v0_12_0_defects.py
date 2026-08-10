# -*- coding: utf-8 -*-
"""v0.12.0 · 六项缺陷修复回归测试。

覆盖：
- D-1：`读取`/`写入` 内建动词实现
- D-2：lexer 负号字面量 `-5`
- D-3：opaque Python 对象成员访问
- D-4：字典 `值集`/`对集`/`合并` 内建动词
- D-5：免括号嵌套动词结合方向（有意行为固化）
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from jikuai.lexer import tokenize
from jikuai.parser import parse
from jikuai.evaluator import Evaluator, JiKuaiError
from jikuai.tokens import TokenType
from jikuai.ast_nodes import Call, Ident, NumberLit


def run(src):
    """求值一段极快源码，返回最后一条语句的值。"""
    return Evaluator().eval(parse(tokenize(src)), source=src)


def _numbers(src):
    """取一段源码里的所有 NUMBER token 值。"""
    return [t.value for t in tokenize(src) if t.type == TokenType.NUMBER]


def _sexp(node):
    """把 Call/Ident/NumberLit AST 序列化成 S 表达式，用于断言解析结构。"""
    if isinstance(node, Call):
        return '{}({})'.format(node.verb, ', '.join(_sexp(a) for a in node.args))
    if isinstance(node, Ident):
        return node.name
    if isinstance(node, NumberLit):
        return repr(node.value)
    return type(node).__name__


# ===========================================================================
# D-1：`读取` / `写入`
# ===========================================================================

class TestReadWriteVerbs:
    """v0.16.0 W28 更新：两个动词加了路径闸（限 CWD 内，绝对路径一律拒）。
    原用例用 `tmp_path` 的**绝对路径**，现在会被 JK-E4002 挡下——这是有意的
    行为变更，不是回归。改为 chdir 到 tmp_path + 相对路径，保持原断言不变。
    安全闸自身的用例见 `tests/test_builtin_io.py`。
    """

    def test_read_existing_file(self, tmp_path, monkeypatch):
        (tmp_path / "hello.txt").write_text("你好，极快", encoding='utf-8')
        monkeypatch.chdir(tmp_path)
        assert run('读取 "hello.txt"。') == "你好，极快"

    def test_write_then_read_back(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # 写入返回内容（便于管道传递），并能被读回
        assert run('写入 "out.txt" "内容甲"。') == "内容甲"
        assert run('读取 "out.txt"。') == "内容甲"


# ===========================================================================
# D-2：负号字面量
# ===========================================================================

class TestNegativeNumberLiteral:
    def test_neg_int_literal(self):
        assert _numbers('-5。') == [-5]
        assert run('-5。') == -5

    def test_neg_float_literal(self):
        assert _numbers('-3.14。') == [-3.14]
        assert run('-3.14。') == -3.14

    def test_neg_in_define(self):
        assert run('定义赵x=-5。\n赵x。') == -5

    def test_neg_as_verb_argument(self):
        # `加 3 -5`：`-5` 作为负数参数 → 加(3, -5) = -2
        assert _numbers('加 3 -5。') == [3, -5]
        assert run('加 3 -5。') == -2

    def test_minus_verb_still_works(self):
        # `3 减 5`：`减` 仍被识别为 VERB，负号规则不吞它
        toks = [(t.type, t.value) for t in tokenize('3 减 5。')]
        assert (TokenType.VERB, '减') in toks
        assert run('3 减 5。') == -2

    def test_unary_negate_verb_unaffected(self):
        # 一元动词 `负` 不受负号字面量规则影响
        assert run('列 负 5。') == [-5]


# ===========================================================================
# D-3：opaque Python 对象成员访问
# ===========================================================================

class TestOpaqueMemberAccess:
    def test_hashlib_sha256_hexdigest(self):
        import hashlib
        expected = hashlib.sha256("abc".encode()).hexdigest()
        result = run(
            '导入 蟒:hashlib。\n'
            '定义赵b="abc"。\n'
            '定义赵h=hashlib.sha256(赵b.encode())。\n'
            '赵h.hexdigest()。')
        assert isinstance(result, str)
        assert result == expected

    def test_python_list_append_method(self):
        # Python 列表对象的 `.append()` 可用（取到 bound method 后括号调用）
        assert run('定义赵l=列 1 2 3。\n赵l.append(4)。\n赵l。') == [1, 2, 3, 4]

    def test_missing_attribute_raises(self):
        with pytest.raises(JiKuaiError) as exc_info:
            run('导入 蟒:hashlib。\n'
                '定义赵b="x"。\n'
                '定义赵h=hashlib.sha256(赵b.encode())。\n'
                '赵h.查无此属。')
        assert '查无此属' in str(exc_info.value)


# ===========================================================================
# D-4：字典 `值集` / `对集` / `合并`
# ===========================================================================

class TestDictVerbs:
    def test_values_normal(self):
        assert run('值集 {"a": 1, "b": 2}。') == [1, 2]

    def test_values_empty(self):
        assert run('值集 {}。') == []

    def test_pairs_normal(self):
        assert run('对集 {"a": 1, "b": 2}。') == [['a', 1], ['b', 2]]

    def test_pairs_empty(self):
        assert run('对集 {}。') == []

    def test_merge_normal(self):
        # 浅合并，dict2 覆盖 dict1
        assert run('合并 {"a": 1, "b": 2} {"b": 20, "c": 3}。') == {
            'a': 1, 'b': 20, 'c': 3}

    def test_merge_empty(self):
        assert run('合并 {} {}。') == {}


# ===========================================================================
# D-5：免括号嵌套动词结合方向（有意行为，固化 assert）
# ===========================================================================

class TestNestedVerbAssociativity:
    def test_bare_nested_infix_merge(self):
        """`乘 100 幂 2 赵步` 因中缀合并解析为 `乘(幂(100,2), 赵步)`。

        与"逐层前缀嵌套"的直觉 `乘(100, 幂(2, 赵步))` 不同：`乘` 取第一个
        primary `100` 后，右侧紧跟二元动词 `幂`，触发中缀合并成 `幂(100,2)`
        作为第一实参；第二实参才是 `赵步`。
        """
        prog = parse(tokenize('定义赵步=3。\n乘 100 幂 2 赵步。'))
        assert _sexp(prog.body[-1]) == '乘(幂(100, 2), 赵步)'
        assert run('定义赵步=3。\n乘 100 幂 2 赵步。') == 30000

    def test_parenthesized_gives_intuitive_nesting(self):
        """正确写法：用括号显式表达 `乘(100, 幂(2, 赵步))`。"""
        prog = parse(tokenize('定义赵步=3。\n乘 100 (幂 2 赵步)。'))
        assert _sexp(prog.body[-1]) == '乘(100, 幂(2, 赵步))'
        assert run('定义赵步=3。\n乘 100 (幂 2 赵步)。') == 800
