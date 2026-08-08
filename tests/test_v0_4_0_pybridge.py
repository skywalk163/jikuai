# -*- coding: utf-8 -*-
"""v0.4.0 M2 · 极快 → Python 方向集成测试（≥10 项，AC-91~AC-96 + 类型映射）。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from decimal import Decimal

from jikuai.evaluator import Evaluator, JiKuaiError, RMB
from jikuai.errors import ErrorCategory
from jikuai.lexer import tokenize
from jikuai.parser import parse


def _run(src):
    """求值单段源码，返回最后一条语句的值。"""
    ev = Evaluator()
    return ev.eval(parse(tokenize(src)), source=src)


def _run_capture(src):
    """求值源码并捕获 stdout（打印结果），返回 (stdout_text, result)。"""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    ev = Evaluator()
    with redirect_stdout(buf):
        result = ev.eval(parse(tokenize(src)), source=src)
    return buf.getvalue().strip(), result


# ============================================================
# AC-91：导入 蟒:math + math.sqrt(16) → 4.0
# ============================================================

class TestPyBridgeOutbound:
    """极快 → Python 方向。"""

    def test_ac91_math_sqrt(self):
        """导入 蟒:math。打印 math.sqrt(16)。 → 4.0"""
        out, _ = _run_capture('导入 蟒:math。\n打印 math.sqrt(16)。')
        assert out == '4.0'

    def test_ac92_json_dumps(self):
        """导入 蟒:json。打印 json.dumps(列 1 2 3)。 → [1, 2, 3] JSON"""
        out, _ = _run_capture('导入 蟒:json。\n打印 json.dumps(列 1 2 3)。')
        import json
        assert json.loads(out) == [1, 2, 3]

    def test_ac93_datetime_construct(self):
        """导入 蟒:datetime + 构造对象，可打印为可读字符串。"""
        out, _ = _run_capture(
            '导入 蟒:datetime。\n定义 王时 = datetime.datetime(2026, 8, 7)。\n打印 王时。')
        assert '2026' in out and '08' in out

    def test_ac94_no_paren_syntax_error(self):
        """math.sqrt 16（无括号）→ 抛 SYNTAX 中文诊断。"""
        with pytest.raises(JiKuaiError) as exc_info:
            _run('导入 蟒:math。\nmath.sqrt 16。')
        assert exc_info.value.info.category == ErrorCategory.SYNTAX
        assert '括号' in exc_info.value.info.message

    def test_ac96_import_nonexistent_module(self):
        """导入 蟒:不存在的模块。 → 中文 ErrorInfo 诊断。"""
        with pytest.raises(JiKuaiError) as exc_info:
            _run('导入 蟒:不存在的模块。')
        assert exc_info.value.info.category == ErrorCategory.RUNTIME
        assert '找不到 Python 模块' in exc_info.value.info.message

    # ==================== 类型映射（AC-95） ====================

    def test_type_int(self):
        """极快整数 → Python int → 极快整数"""
        result = _run('导入 蟒:math。\nmath.factorial(5)。')
        assert result == 120 and isinstance(result, int)

    def test_type_float(self):
        """极快小数 → Python float → 极快小数"""
        result = _run('导入 蟒:math。\nmath.sin(0.0)。')
        assert result == 0.0 and isinstance(result, float)

    def test_type_string(self):
        """极快字符串 → Python str → 极快字符串"""
        result = _run('导入 蟒:json。\njson.dumps("你好")。')
        import json
        assert json.loads(result) == '你好'

    def test_type_bool(self):
        """极快布尔 → Python bool → 极快布尔"""
        result = _run('导入 蟒:json。\njson.loads("true")。')
        assert result is True

    def test_type_none(self):
        """极快空 → Python None → 极快空"""
        result = _run('导入 蟒:json。\njson.loads("null")。')
        assert result is None

    def test_type_list(self):
        """极快列表 → Python list（递归）→ 极快列表"""
        result = _run('导入 蟒:json。\njson.loads("[1,2,3]")。')
        assert result == [1, 2, 3]

    def test_type_dict(self):
        """极快字典 → Python dict → 极快字典"""
        result = _run('导入 蟒:json。\njson.loads(\'{"a":1}\')。')
        assert result == {'a': 1}

    def test_type_rmb_to_decimal(self):
        """RMB → Decimal（透给 Python 侧）"""
        # json.dumps 对 Decimal 会报错；用 str() 验证能过界
        out, _ = _run_capture(
            '导入 蟒:builtins。\n定义 王额 = 人民币 99.9。\n打印 builtins.str(王额)。')
        assert '99.9' in out

    # ==================== 安全约束 ====================

    def test_deny_os_system(self):
        """默认拒绝清单：os.system。"""
        with pytest.raises(JiKuaiError) as exc_info:
            _run('导入 蟒:os。\nos.system("echo hi")。')
        assert '安全限制' in exc_info.value.info.message

    def test_deny_builtins_eval(self):
        """默认拒绝清单：builtins.eval。"""
        with pytest.raises(JiKuaiError) as exc_info:
            _run('导入 蟒:builtins。\nbuiltins.eval("1+1")。')
        assert '安全限制' in exc_info.value.info.message

    # ==================== 异常翻译（AC-101） ====================

    def test_exception_translate_no_abs_path(self):
        """Python 侧异常跨界，含类名+message，不泄漏堆栈绝对路径。"""
        with pytest.raises(JiKuaiError) as exc_info:
            _run('导入 蟒:json。\njson.loads("不是json")。')
        info = exc_info.value.info
        assert info.category == ErrorCategory.RUNTIME
        assert 'Python 侧异常' in info.message
        assert 'JSONDecodeError' in info.message
        # 不应有绝对路径
        import re
        assert not re.search(r'[A-Za-z]:[\\/]', info.message)

    def test_math_operations_chain(self):
        """链式调用：math 多次使用。"""
        result = _run('导入 蟒:math。\n定义 王x = math.pow(2, 10)。\n王x。')
        assert result == 1024.0

    def test_module_non_callable_attribute(self):
        """模块的非函数属性（math.pi）按类型映射表反向编组为小数。"""
        result = _run('导入 蟒:math。\nmath.pi。')
        assert isinstance(result, float)
        assert abs(result - 3.14159265) < 1e-6

    def test_import_with_alias(self):
        """导入 蟒:math 作为 数学。 → 别名绑定生效。"""
        out, _ = _run_capture('导入 蟒:math 作为 数学。\n打印 数学.sqrt(9)。')
        assert out == '3.0'

    def test_import_dotted_module(self):
        """`蟒:os.path` 点号模块名：默认绑定顶层名 os。"""
        result = _run('导入 蟒:os.path。\nos.path.basename("a/b/c.txt")。')
        assert result == 'c.txt'

    def test_unknown_module_attribute(self):
        """访问模块不存在的属性 → 中文 RUNTIME 诊断。"""
        with pytest.raises(JiKuaiError) as exc_info:
            _run('导入 蟒:math。\nmath.根本没有这个(1)。')
        assert exc_info.value.info.category == ErrorCategory.RUNTIME
        assert '无此属性' in exc_info.value.info.message

    def test_from_python_import_rejected(self):
        """ADR-10：`从 蟒:模块 导入 名字` 显式拒绝，不静默降级。"""
        from jikuai.parser import ParseError
        with pytest.raises(ParseError) as exc_info:
            _run('从 蟒:math 导入 sqrt。')
        assert '蟒' in str(exc_info.value)


# ============================================================
# 类型映射表逐条契约（AC-95 补齐语言层难以直达的条目）
# ============================================================

class TestTypeMappingContract:
    """直接对 `jk_to_py` / `py_to_jk` 断言映射表条目。"""

    def test_rmb_to_decimal(self):
        """RMB → Decimal。"""
        from jikuai.pybridge import jk_to_py
        out = jk_to_py(RMB(12.34))
        assert isinstance(out, Decimal)
        assert out == Decimal('12.34')

    def test_decimal_back_needs_explicit_wrap(self):
        """反向：Decimal 不自动变 RMB，需极快侧 `人民币(...)` 显式包装。"""
        from jikuai.pybridge import py_to_jk
        out = py_to_jk(Decimal('5.00'))
        assert not isinstance(out, RMB)
        # 显式包装后才是人民币
        assert isinstance(RMB(out), RMB)

    def test_jikuai_instance_is_opaque(self):
        """JiKuaiInstance → opaque object（原样过界，不解构）。"""
        from jikuai.evaluator import JiKuaiClass, JiKuaiInstance
        from jikuai.pybridge import jk_to_py
        klass = JiKuaiClass('某类', None, [], [], {})
        inst = JiKuaiInstance(klass)
        assert jk_to_py(inst) is inst

    def test_jikuai_class_does_not_cross(self):
        """JiKuaiClass 不跨界 → 抛诊断。"""
        from jikuai.evaluator import JiKuaiClass
        from jikuai.pybridge import jk_to_py
        klass = JiKuaiClass('某类', None, [], [], {})
        with pytest.raises(JiKuaiError) as exc_info:
            jk_to_py(klass)
        assert '不能跨语言传递' in exc_info.value.info.message

    def test_bound_method_forbidden(self):
        """BoundMethod 禁止跨界（与 DP-3 一致）。"""
        from jikuai.ast_nodes import FuncDef
        from jikuai.evaluator import (BoundMethod, Environment, JiKuaiClass,
                                      JiKuaiInstance)
        from jikuai.pybridge import jk_to_py
        klass = JiKuaiClass('某类', None, [], [], {})
        inst = JiKuaiInstance(klass)
        bm = BoundMethod(inst, FuncDef('某方法', ['x'], []), Environment())
        with pytest.raises(JiKuaiError) as exc_info:
            jk_to_py(bm)
        assert '绑定方法不能跨语言传递' in exc_info.value.info.message

    def test_dict_key_must_be_str_or_int(self):
        """字典 key 仅允许字符串/整数。"""
        from jikuai.pybridge import jk_to_py
        assert jk_to_py({'a': 1, 2: 'b'}) == {'a': 1, 2: 'b'}
        with pytest.raises(JiKuaiError) as exc_info:
            jk_to_py({1.5: 'x'})
        assert '字典键' in exc_info.value.info.message

    def test_nested_list_recursive(self):
        """列表递归映射元素。"""
        from jikuai.pybridge import jk_to_py, py_to_jk
        assert jk_to_py([1, [2, [3]]]) == [1, [2, [3]]]
        # tuple 无对应极快类型，统一编组为列表
        assert py_to_jk((1, (2, 3))) == [1, [2, 3]]

    def test_scrub_absolute_paths(self):
        """异常文案脱敏：绝对路径被替换为占位符。"""
        from jikuai.pybridge import _scrub_paths
        assert 'C:\\Users' not in _scrub_paths(r'file C:\Users\me\a.py failed')
        assert '/home/me' not in _scrub_paths('file /home/me/a.py failed')
