# -*- coding: utf-8 -*-
"""极快语言 - 求值器（Evaluator）+ 内建函数。

包含中国特色内建：人民币运算、大写金额、中文数字转换等。
"""

import math
import operator
import os
import dataclasses
from decimal import Decimal, ROUND_HALF_UP
from .ast_nodes import *
from .errors import ErrorInfo, ErrorCategory, ErrorFormatter, spelling_suggestion


def _scan_self_fields(node, out):
    """递归扫描 AST，收集所有 `自身.X = ...` 里的 X，作为"声明字段"。

    ADR-02 的简化实现：语言目前没有字段声明语法，因此把构造器与方法体中
    对 `自身.X` 的赋值视为字段声明。这样"声明过但未初始化"可以返回 nil，
    而彻底没出现过的属性名仍然报错。
    """
    if isinstance(node, (list, tuple)):
        for item in node:
            _scan_self_fields(item, out)
        return
    if isinstance(node, dict):
        for item in node.values():
            _scan_self_fields(item, out)
        return
    if not isinstance(node, Node):
        return
    if isinstance(node, Assign) and isinstance(node.target, MemberAccess):
        target = node.target
        if isinstance(target.obj, Ident) and target.obj.name == '自身':
            out.add(target.attr)
    for f in dataclasses.fields(node):
        _scan_self_fields(getattr(node, f.name, None), out)


class JiKuaiError(Exception):
    """极快运行时错误。可选携带结构化 ErrorInfo。

    同时传入 msg 与 info 时，`str(e)` 用 msg（含类别前缀的完整文案），
    `e.info` 供 ErrorFormatter 渲染（避免类别前缀重复）。
    """
    def __init__(self, msg=None, info=None):
        if info is not None:
            self.info = info
            super().__init__(msg if msg is not None else info.message)
        else:
            self.info = None
            super().__init__(msg)


class ReturnSignal(Exception):
    def __init__(self, value=None):
        self.value = value


class BreakSignal(Exception):
    pass


class ContinueSignal(Exception):
    pass


class BoundMethod:
    """ADR-07：绑定方法对象。仅承载 (实例, 方法定义, 闭包环境)，
    按元数分流产生（≥1 参方法访问返回它），只能被 `_eval_FuncCall` 立即调用。

    DP-3：不可赋值、传参、返回（由 `_reject_bound_method` 守护）。

    M10-1：新增 `defining_class` —— 该方法**定义所在的类**（不是实例的类）。
    它是 `父类` 能正确工作的前提：`父类` 必须相对于「当前执行的方法定义在哪个类」
    取父，而不是相对于 `实例.klass`，否则三层继承里孙类调用父类方法、父类方法内
    再写 `父类.X` 会又解析回自己，造成无限递归。语义与 Python 的 `__class__` 一致。
    """
    __slots__ = ('instance', 'method_def', 'closure_env', 'defining_class')

    def __init__(self, instance, method_def, closure_env, defining_class=None):
        self.instance = instance
        self.method_def = method_def
        self.closure_env = closure_env
        self.defining_class = defining_class

    @property
    def arity(self):
        return len(self.method_def.params)

    def __repr__(self):
        return f"<绑定方法:{self.instance.klass.name}.{self.method_def.name}>"


#: M10-1：方法调用环境里记录「当前方法定义所在类」的内部键。
#: 用双下划线包裹的中文名，用户永远写不出来——极快标识符必须以百家姓开头，
#: 所以这个键不可能与任何用户变量冲突，不需要额外的命名空间隔离机制。
_DEFINING_CLASS_KEY = '__定义类__'


class SuperProxy:
    """M10-1：`父类` 的运行时代理。

    只承载 (实例, 查找起点类)。`查找起点类` 是「当前方法定义所在类」的父类，
    方法查找从它开始沿继承链向上，因此 `父类.方法名` 永远跳过当前这一层。

    与 BoundMethod 同样受 DP-3 约束：不可赋值、传参、返回。
    """
    __slots__ = ('instance', 'start_class')

    def __init__(self, instance, start_class):
        self.instance = instance
        self.start_class = start_class

    def __repr__(self):
        name = self.start_class.name if self.start_class else '无'
        return f"<父类:{name}>"


class Environment:
    """作用域链。"""

    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def get(self, name):
        if name in self.vars:
            return self.vars[name]
        if self.parent:
            return self.parent.get(name)
        raise JiKuaiError(f"未定义的标识符：{name}")

    def set(self, name, value):
        self.vars[name] = value

    def update(self, name, value):
        if name in self.vars:
            self.vars[name] = value
            return
        if self.parent:
            self.parent.update(name, value)
            return
        raise JiKuaiError(f"未定义的标识符，无法赋值：{name}")


class JiKuaiClass:
    """运行时类对象。"""
    def __init__(self, name, parent, ctor_params, ctor_body, methods,
                 ctor_defined=False, declared_fields=None, def_env=None):
        self.name = name
        self.parent = parent
        self.ctor_params = ctor_params
        self.ctor_body = ctor_body
        self.methods = methods
        # ADR-02：是否显式写了 构造（即使空体）。用于继承链回溯的终止判定。
        self.ctor_defined = ctor_defined
        # ADR-02：静态扫描出的"声明字段"集合（含父类链合并）。
        # 现有 AST 无字段声明语法，故以"曾被 自身.X = ... 赋值过"近似。
        self.declared_fields = set(declared_fields or ())
        # ADR-22：类定义处的环境（词法作用域）。构造器与方法体以它为父环境求值，
        # 使方法能看到**定义它的模块**里 导入/定义 的名字，而不是调用者的作用域。
        self.def_env = def_env


class JiKuaiInstance:
    """运行时实例对象。"""
    def __init__(self, klass):
        self.klass = klass
        self.attrs = {}

    def get_attr(self, name):
        if name in self.attrs:
            return self.attrs[name]
        # 查方法
        method = self._find_method(name, self.klass)
        if method:
            return method
        # ADR-02：声明过但尚未初始化的字段 → 返回空(nil)；
        # 完全未声明的属性 → 继续报错。
        if self.is_declared_field(name):
            return None
        raise JiKuaiError(f"对象 {self.klass.name} 无属性/方法：{name}")

    def is_declared_field(self, name):
        """沿继承链判断 name 是否为声明字段。"""
        klass = self.klass
        while klass is not None:
            if name in getattr(klass, 'declared_fields', ()):
                return True
            klass = klass.parent
        return False

    def _find_method(self, name, klass):
        if klass is None:
            return None
        if name in klass.methods:
            return klass.methods[name]
        return self._find_method(name, klass.parent)

    def find_method_with_owner(self, name, klass):
        """M10-1：沿继承链找方法，同时返回**定义它的类**。

        返回 (方法定义, 定义所在类)，找不到时 (None, None)。
        `父类` 的正确性完全依赖这个 owner —— 见 BoundMethod.defining_class 注释。
        """
        while klass is not None:
            if name in klass.methods:
                return klass.methods[name], klass
            klass = klass.parent
        return None, None


    def __repr__(self):
        return f"<{self.klass.name} 实例>"


class RMB:
    """人民币类型。内部使用 Decimal 保证金融精度，量化到分。

    设计决策（M1-2）：
      - 内部存储 decimal.Decimal，避免 float 二进制误差
      - 所有运算结果量化到 0.01（分），舍入策略为 ROUND_HALF_UP（四舍五入）
      - 选 ROUND_HALF_UP 而非银行家舍入，因为中国会计惯例与用户直觉都是四舍五入
    """

    __slots__ = ('amount',)
    _CENT = Decimal('0.01')

    def __init__(self, amount):
        self.amount = self._quantize(amount)

    @classmethod
    def _quantize(cls, value):
        if isinstance(value, RMB):
            value = value.amount
        elif isinstance(value, float):
            # 经 str 转换避免 float 的二进制表示误差进入 Decimal
            value = Decimal(str(value))
        elif not isinstance(value, Decimal):
            value = Decimal(str(value))
        return value.quantize(cls._CENT, rounding=ROUND_HALF_UP)

    @staticmethod
    def _to_decimal(other):
        if isinstance(other, RMB):
            return other.amount
        if isinstance(other, float):
            return Decimal(str(other))
        return Decimal(str(other))

    def __repr__(self):
        return f"￥{self.amount}"

    def __eq__(self, other):
        if isinstance(other, RMB):
            return self.amount == other.amount
        try:
            return self.amount == self._to_decimal(other)
        except Exception:
            return NotImplemented

    def __hash__(self):
        return hash(self.amount)

    def __lt__(self, other):
        return self.amount < self._to_decimal(other)

    def __le__(self, other):
        return self.amount <= self._to_decimal(other)

    def __gt__(self, other):
        return self.amount > self._to_decimal(other)

    def __ge__(self, other):
        return self.amount >= self._to_decimal(other)

    def __add__(self, other):
        return RMB(self.amount + self._to_decimal(other))

    __radd__ = __add__

    def __sub__(self, other):
        return RMB(self.amount - self._to_decimal(other))

    def __rsub__(self, other):
        return RMB(self._to_decimal(other) - self.amount)

    def __mul__(self, other):
        return RMB(self.amount * self._to_decimal(other))

    __rmul__ = __mul__

    def __truediv__(self, other):
        divisor = self._to_decimal(other)
        if divisor == 0:
            raise JiKuaiError("金额不能除以零")
        return RMB(self.amount / divisor)

    def __neg__(self):
        return RMB(-self.amount)

    def __float__(self):
        return float(self.amount)


def _rmb_to_chinese_upper(amount):
    """数字金额转中文大写金额（壹贰叁肆伍陆柒捌玖拾佰仟万亿元角分整）。"""
    digits = '零壹贰叁肆伍陆柒捌玖'
    units_int = ['', '拾', '佰', '仟']
    units_big = ['', '万', '亿']

    amount = round(abs(float(amount)), 2)
    int_part = int(amount)
    dec_part = round((amount - int_part) * 100)
    jiao = dec_part // 10
    fen = dec_part % 10

    # 整数部分
    if int_part == 0:
        result = '零元'
    else:
        result = ''
        str_int = str(int_part)
        n = len(str_int)
        for i, ch in enumerate(str_int):
            d = int(ch)
            pos = n - 1 - i
            section = pos // 4
            unit_pos = pos % 4
            if d != 0:
                result += digits[d] + units_int[unit_pos]
            else:
                if result and not result.endswith('零'):
                    result += '零'
            if unit_pos == 0 and section > 0:
                result = result.rstrip('零') + units_big[section]
        result = result.rstrip('零') + '元'

    # 小数部分
    if jiao == 0 and fen == 0:
        result += '整'
    else:
        if jiao > 0:
            result += digits[jiao] + '角'
        if fen > 0:
            result += digits[fen] + '分'

    return result


def _num_to_chinese(n):
    """整数转中文小写数字（一二三...）。"""
    digits = '零一二三四五六七八九'
    if n < 0:
        return '负' + _num_to_chinese(-n)
    if n < 10:
        return digits[n]
    if n < 100:
        s = ''
        if n >= 20:
            s = digits[n // 10] + '十'
        else:
            s = '十'
        if n % 10 != 0:
            s += digits[n % 10]
        return s
    # 简单处理到万
    result = ''
    units = [(10000, '万'), (1000, '千'), (100, '百'), (10, '十')]
    for unit_val, unit_name in units:
        if n >= unit_val:
            result += digits[n // unit_val] + unit_name
            n %= unit_val
        elif result:
            result += '零'
    if n > 0:
        result += digits[n]
    return result.rstrip('零') or '零'


class ExecHook:
    """执行钩子（M6-P3 · T-M6-D01）。默认实现全是 no-op。

    唯一侵入点：`Evaluator._eval_body` 在每条语句执行前回调 `before_stmt`。
    `hook is None` 时求值器只多做一次局部变量判空，不产生函数调用开销。

    调试层（`jikuai_dap`）继承本类实现断点判定与暂停。
    反向依赖禁止：本模块不得 import 任何 DAP 相关模块。
    """

    def before_stmt(self, node, env) -> None:
        """每条语句执行前调用。node 为 AST 语句节点（含 .line/.col），env 为当前环境。

        语义约定：本方法抛出的异常不被求值器捕获或吞掉，会沿调用栈原样上抛
        （调试层用它实现「终止调试」）。
        """
        pass

    def on_break(self, node, env) -> None:
        """命中断点/单步暂停点时调用。由 `before_stmt` 的实现自行判定后回调。"""
        pass


class Evaluator:
    """极快语言树遍历求值器。"""

    def __init__(self, hook=None):
        # T-M6-D01：可选执行钩子（ExecHook 或 None）。None 时零额外开销。
        self._hook = hook
        self.global_env = Environment()
        self._setup_builtins()
        self.classes = {}
        # M2-1: 模块系统上下文
        from .module_loader import ModuleLoader
        self.module_loader = ModuleLoader(self)
        self._current_module_env = None       # 当前正在执行的模块环境
        self._current_exports = None          # 当前模块的导出名集合
        self._current_file = None             # 当前源文件绝对路径
        self._current_source = None           # 当前源码文本

    def _setup_builtins(self):
        """注册内建动词实现。"""
        self.verbs = {
            # 算术
            '加': lambda a, b: a + b,
            '减': lambda a, b: a - b,
            '乘': lambda a, b: a * b,
            '除': lambda a, b: a / b if b != 0 else _err("除数不能为零"),
            '取余': lambda a, b: a % b,
            '幂': lambda a, b: a ** b,
            '整除': lambda a, b: a // b,
            '加上': lambda a, b: a + b,
            '减去': lambda a, b: a - b,
            '乘以': lambda a, b: a * b,
            '除以': lambda a, b: a / b if b != 0 else _err("除数不能为零"),
            '负': lambda a: -a,
            '绝对值': lambda a: abs(a),
            # 比较
            '等于': lambda a, b: a == b,
            '不等于': lambda a, b: a != b,
            '大于': lambda a, b: a > b,
            '小于': lambda a, b: a < b,
            '大于等于': lambda a, b: a >= b,
            '小于等于': lambda a, b: a <= b,
            # 逻辑
            '且': lambda a, b: a and b,
            '或': lambda a, b: a or b,
            '非': lambda a: not a,
            # 列表
            '列': lambda *args: list(args),
            '长度': lambda a: len(a),
            '首个': lambda a: a[0] if a else None,
            '其余': lambda a: a[1:] if a else [],
            '末个': lambda a: a[-1] if a else None,
            '追加': lambda a, b: a + [b] if isinstance(a, list) else [a, b],
            '连接': lambda a, b: a + b,
            '包含': lambda a, b: b in a,
            '反转': lambda a: list(reversed(a)) if isinstance(a, list) else a[::-1],
            '排序': lambda a: sorted(a),
            '去重': lambda a: list(dict.fromkeys(a)),
            '取值': lambda a, b: a[int(b)] if isinstance(a, list) else a.get(b),
            '范围': lambda *args: list(range(*[int(x) for x in args])),
            # 聚合
            '求和': lambda a: sum(a),
            '最大': lambda a: max(a),
            '最小': lambda a: min(a),
            '平均': lambda a: sum(a) / len(a) if a else 0,
            # 字符串
            '拼接': lambda *args: ''.join(str(x) for x in args),
            '分割': lambda a, b: a.split(b),
            '替换': lambda a, b, c: a.replace(b, c),
            '子串': lambda a, b, c: a[int(b):int(c)],
            '大写': lambda a: a.upper(),
            '小写': lambda a: a.lower(),
            '转字符串': lambda a: str(a),
            '转整数': lambda a: int(a),
            '转小数': lambda a: float(a),
            '去空白': lambda a: str(a).strip(),
            # I/O
            '打印': self._builtin_print,
            '输入': self._builtin_input,
            # 中国特色
            '人民币': lambda a: RMB(a),
            '大写金额': lambda a: _rmb_to_chinese_upper(a.amount if isinstance(a, RMB) else a),
            '汉字数字': lambda a: _num_to_chinese(int(a)),
            # 中国国情校验（M1-1）
            '校验身份证': _stdlib_call('校验', '校验身份证'),
            '提取身份证信息': _stdlib_call('校验', '提取身份证信息'),
            '校验手机号': _stdlib_call('校验', '校验手机号'),
            '判断运营商': _stdlib_call('校验', '判断运营商'),
            '校验银行卡': _stdlib_call('校验', '校验银行卡'),
            '校验车牌': _stdlib_call('校验', '校验车牌'),
            '校验社会信用代码': _stdlib_call('校验', '校验社会信用代码'),
            # 中国历法（M1-1）
            '公历转农历': _stdlib_call('历法', '公历转农历'),
            '干支纪年': _stdlib_call('历法', '干支纪年'),
            '生肖': _stdlib_call('历法', '生肖'),
            '农历完整日期': _stdlib_call('历法', '农历完整日期'),
            # 面向对象反射（M9-4）
            '是否是': self._builtin_is_instance_of,
            '类名': self._builtin_class_name,
        }

    def _builtin_is_instance_of(self, obj, class_name):
        """`是否是 实例 "类名"` —— 沿继承链判定实例是否属于某个类。

        多态代码常需要「按具体类型分流」，此前极快只能靠给每个类加一个
        返回类名的方法来变通。这里直接读继承链，子类实例对父类名也返回真
        （与 Python `isinstance` 的语义一致）。

        非实例对象一律返回假而不报错：`是否是` 的用途就是**判定**，
        对任意值提问都应该有答案。
        """
        if not isinstance(obj, JiKuaiInstance):
            return False
        target = str(class_name)
        k = obj.klass
        while k is not None:
            if k.name == target:
                return True
            k = k.parent
        return False

    def _builtin_class_name(self, obj):
        """`类名 实例` —— 返回实例所属类的名字（字符串）。

        非实例对象抛类型错误：拿不到类名时返回空字符串会让调用方
        误以为「有个叫空串的类」，报错更诚实。
        """
        if not isinstance(obj, JiKuaiInstance):
            raise JiKuaiError(f"「类名」需要一个对象实例，收到：{type(obj).__name__}")
        return obj.klass.name


    def _builtin_print(self, *args):
        print(*[self._format_value(a) for a in args])
        return None

    def _builtin_input(self, *args):
        prompt = args[0] if args else ''
        return input(str(prompt))

    def _format_value(self, v):
        if v is None:
            return '空'
        if isinstance(v, bool):
            return '真' if v else '假'
        if isinstance(v, RMB):
            return str(v)
        return str(v)

    # ======================== 求值入口 ========================

    def eval(self, program, source=None):
        """求值 Program AST。source 为源码文本（可选，用于错误报告行原文）。

        ADR-08：唯一的顶层拦截点。三种控制流信号逃逸到这里说明它们出现在
        函数/方法体或循环体之外，转为携带 ErrorInfo 的 SYNTAX 诊断。
        R-C：`_eval_FuncCall` / `_invoke_method` / 循环内部的捕获保持不变，
        嵌套函数与闭包内的合法 `返回` 不受影响。
        """
        if source is not None:
            self._current_source = source
        try:
            return self._eval_body(program.body, self.global_env)
        except ReturnSignal:
            raise JiKuaiError(info=ErrorInfo(
                category=ErrorCategory.SYNTAX,
                message="「返回」只能在函数或方法体内使用。", line=0, col=0)) from None
        except BreakSignal:
            raise JiKuaiError(info=ErrorInfo(
                category=ErrorCategory.SYNTAX,
                message="「跳出」只能在循环体内使用。", line=0, col=0)) from None
        except ContinueSignal:
            raise JiKuaiError(info=ErrorInfo(
                category=ErrorCategory.SYNTAX,
                message="「跳过」只能在循环体内使用。", line=0, col=0)) from None

    def _eval_body(self, stmts, env):
        result = None
        hook = self._hook          # T-M6-D01：提到循环外，无 hook 时每条语句仅一次判空
        for stmt in stmts:
            if hook is not None:
                hook.before_stmt(stmt, env)
            result = self._eval_node(stmt, env)
        return result

    def _eval_node(self, node, env):
        method_name = f'_eval_{type(node).__name__}'
        method = getattr(self, method_name, None)
        if not method:
            raise JiKuaiError(f"未支持的节点类型：{type(node).__name__}")
        try:
            return method(node, env)
        except JiKuaiError as e:
            # 附加位置信息（若尚未携带 info）
            if getattr(e, 'info', None) is None:
                msg = str(e)
                if '未定义' in msg or '未知动词' in msg:
                    cat = ErrorCategory.NAME
                elif ('无法访问' in msg or '无属性' in msg or '需要作用于' in msg
                      or '不可调用' in msg or '不支持' in msg):
                    cat = ErrorCategory.TYPE
                else:
                    cat = ErrorCategory.RUNTIME
                e.info = ErrorInfo(
                    category=cat, message=msg,
                    line=getattr(node, 'line', 0),
                    col=getattr(node, 'col', 0),
                    source_line=self._source_line(getattr(node, 'line', 0)),
                )
            raise
        except (ZeroDivisionError, TypeError, ValueError, IndexError,
                KeyError, AttributeError) as e:
            # 将 Python 原生异常包装为携带 info 的 JiKuaiError
            info = ErrorInfo(
                category=ErrorCategory.TYPE if isinstance(e, (TypeError, AttributeError))
                else ErrorCategory.RUNTIME,
                message=str(e) or type(e).__name__,
                line=getattr(node, 'line', 0),
                col=getattr(node, 'col', 0),
                source_line=self._source_line(getattr(node, 'line', 0)),
            )
            raise JiKuaiError(info=info) from e

    def _source_line(self, line):
        """从当前源码文本取指定行（1-based）原文。"""
        if not self._current_source or line <= 0:
            return ""
        lines = self._current_source.split('\n')
        if 1 <= line <= len(lines):
            return lines[line - 1]
        return ""

    def _collect_names(self, env):
        """收集当前作用域可见的所有名字（变量名 + 动词名）用于拼写建议。"""
        names = set(self.verbs.keys())
        e = env
        while e is not None:
            names.update(e.vars.keys())
            e = e.parent
        return names

    # ======================== 语句求值 ========================

    def _eval_Define(self, node, env):
        value = self._eval_node(node.value, env)
        self._reject_bound_method(value, node)
        # 如果变量已存在于任何外层作用域，则更新它（赋值语义）
        try:
            env.update(node.name, value)
        except JiKuaiError:
            # 变量不存在，新建
            env.set(node.name, value)
        return value

    def _eval_Assign(self, node, env):
        value = self._eval_node(node.value, env)
        self._reject_bound_method(value, node)
        if isinstance(node.target, Ident):
            env.update(node.target.name, value)
        elif isinstance(node.target, MemberAccess):
            obj = self._eval_node(node.target.obj, env)
            if isinstance(obj, JiKuaiInstance):
                obj.attrs[node.target.attr] = value
        elif isinstance(node.target, Index):
            obj = self._eval_node(node.target.obj, env)
            idx = self._eval_node(node.target.index, env)
            obj[int(idx)] = value
        return value

    def _eval_If(self, node, env):
        cond = self._eval_node(node.cond, env)
        if cond:
            return self._eval_body(node.then_branch, env)
        for elif_cond, elif_body in node.elif_branches:
            if self._eval_node(elif_cond, env):
                return self._eval_body(elif_body, env)
        if node.else_branch:
            return self._eval_body(node.else_branch, env)
        return None

    def _eval_While(self, node, env):
        result = None
        while self._eval_node(node.cond, env):
            try:
                result = self._eval_body(node.body, env)
            except BreakSignal:
                break
            except ContinueSignal:
                continue
        return result

    def _eval_For(self, node, env):
        iterable = self._eval_node(node.iterable, env)
        result = None
        for item in iterable:
            loop_env = Environment(env)
            loop_env.set(node.var, item)
            try:
                result = self._eval_body(node.body, loop_env)
            except BreakSignal:
                break
            except ContinueSignal:
                continue
        return result

    def _eval_Repeat(self, node, env):
        count = int(self._eval_node(node.count, env))
        result = None
        for _ in range(count):
            try:
                result = self._eval_body(node.body, Environment(env))
            except BreakSignal:
                break
            except ContinueSignal:
                continue
        return result

    def _eval_Break(self, node, env):
        raise BreakSignal()

    def _eval_Continue(self, node, env):
        raise ContinueSignal()

    def _eval_Return(self, node, env):
        value = self._eval_node(node.value, env) if node.value else None
        self._reject_bound_method(value, node)
        raise ReturnSignal(value)

    def _eval_FuncDef(self, node, env):
        env.set(node.name, ('func', node, env))
        return None

    def _eval_ClassDef(self, node, env):
        parent_class = None
        if node.parent:
            parent_class = self.classes.get(node.parent)
        # ADR-02：静态扫描本类构造器与方法体中的 自身.X 赋值，得到声明字段，
        # 并沿父类链合并（父类字段对子类实例可见）。
        declared = set()
        _scan_self_fields(node.ctor_body, declared)
        _scan_self_fields(node.methods, declared)
        if parent_class is not None:
            declared |= getattr(parent_class, 'declared_fields', set())
        klass = JiKuaiClass(
            name=node.name, parent=parent_class,
            ctor_params=node.ctor_params, ctor_body=node.ctor_body,
            methods=node.methods,
            ctor_defined=getattr(node, 'ctor_defined', False),
            declared_fields=declared,
            def_env=env,          # ADR-22：捕获定义处环境，供方法/构造器求值
        )
        self.classes[node.name] = klass
        env.set(node.name, klass)
        return None

    def _eval_Try(self, node, env):
        try:
            return self._eval_body(node.body, Environment(env))
        except (ReturnSignal, BreakSignal, ContinueSignal):
            # D-13 / ADR-08：三种控制流信号不是错误，尝试/捕获/最终 不得吞掉。
            # 必须原样透传给外层函数体/循环去处理；此 raise 早于下方
            # `except JiKuaiError` 与 `except Exception` 兜底，避免被 捕获 分支误接。
            # finally 分支（若存在）仍会在 raise 穿透前执行，见 Python 语义保证。
            raise
        except JiKuaiError as e:
            if node.catch_body:
                catch_env = Environment(env)
                if node.catch_var:
                    catch_env.set(node.catch_var, str(e))
                return self._eval_body(node.catch_body, catch_env)
        except Exception as e:
            if node.catch_body:
                catch_env = Environment(env)
                if node.catch_var:
                    catch_env.set(node.catch_var, str(e))
                return self._eval_body(node.catch_body, catch_env)
        finally:
            if node.finally_body:
                self._eval_body(node.finally_body, Environment(env))

    def _eval_Throw(self, node, env):
        value = self._eval_node(node.value, env)
        raise JiKuaiError(str(value))

    def _eval_Import(self, node, env):
        """M2-1: 加载 .jk 模块；ADR-10: `蟒:` 前缀路由到 Python 桥。"""
        if getattr(node, 'kind', 'jk') == 'python':
            from .pybridge import py_import
            # 语义（对齐 Python `import x.y`）：
            #   `导入 蟒:os.path。`            → env['os']  = <os>（顶层）
            #   `导入 蟒:os.path 作为 王p。`   → env['王p'] = <os.path>
            top_name = node.module.split('.')[0]
            bind_to_top = (node.alias is None
                           or node.alias == top_name and '.' in node.module)
            module = py_import(node.module, top_level=bind_to_top,
                               current_file=self._current_file)
            env.set(node.alias or top_name, module)
            return None
        module = self.module_loader.load(node.module, self._current_file)
        if node.names:
            # 从 模块 导入 名字1 名字2
            for name in node.names:
                value = module.get(name)
                if value is not None:
                    env.set(name, value)
            return None
        # 导入 模块 [作为 别名]；别名导入后原名不可用
        env.set(node.alias or node.module, module)
        return None

    def _eval_Export(self, node, env):
        """M2-1: 记录当前模块的导出名。"""
        if self._current_exports is None:
            # 顶层脚本的 导出 语句无实际作用，但不报错
            return None
        for name in node.names:
            self._current_exports.add(name)
        return None

    # ======================== 表达式求值 ========================

    def _eval_NumberLit(self, node, env):
        return node.value

    def _eval_StringLit(self, node, env):
        return node.value

    def _eval_MoneyLit(self, node, env):
        return RMB(node.value)

    def _eval_BoolLit(self, node, env):
        return node.value

    def _eval_NilLit(self, node, env):
        return None

    def _eval_Ident(self, node, env):
        try:
            return env.get(node.name)
        except JiKuaiError:
            # AC-68b：若未定义标识符恰好是内建动词名，则本次求值走到这里，
            # 一定是「lexer 已在本作用域将该名字降级为 IDENT」的结果——即
            # 本作用域内用户定义（方法/字段）遮蔽了同名内建动词。此时保留
            # 作用域模型不动（裁决边界），仅把诊断文案改为可操作提示，
            # 避免「未定义的标识符」这种误导性表述。
            from .keywords import VERB_ARITY
            if node.name in VERB_ARITY:
                msg = (
                    f"「{node.name}」已被本作用域内的用户定义"
                    f"（方法/字段）遮蔽，内建动词语义在此不可用。"
                    f"请改用其他名字（如「王{node.name}」），"
                    f"或将类定义与顶层脚本拆分到不同文件。"
                )
                info = ErrorInfo(
                    category=ErrorCategory.NAME,
                    message=msg,
                    line=node.line, col=node.col,
                    source_line=self._source_line(node.line),
                )
                raise JiKuaiError(info=info) from None
            # 未定义标识符：附带拼写建议（候选=当前作用域变量名+动词名）
            candidates = list(self._collect_names(env))
            sugg = spelling_suggestion(node.name, candidates)
            info = ErrorInfo(
                category=ErrorCategory.NAME,
                message=f"未定义的标识符：{node.name}",
                line=node.line, col=node.col,
                source_line=self._source_line(node.line),
                suggestion=sugg,
            )
            raise JiKuaiError(info=info) from None

    def _eval_Call(self, node, env):
        # DP-3：动词参数求值处拒绝 BoundMethod
        args = self._eval_args(node.args, env)
        verb = node.verb
        if verb in self.verbs:
            fn = self.verbs[verb]
            # D-10（v0.3.2）：元数守卫 —— 内建动词实参不足/过多时抛结构化 SYNTAX 中文
            # 诊断，避免 Python TypeError/lambda 消息泄漏。变参（arity=-1）跳过校验。
            self._check_verb_arity(verb, args, node)
            return fn(*args)
        # 也可能是用户定义的函数名
        try:
            func = env.get(verb)
            return self._call_function(func, args, env)
        except JiKuaiError:
            raise JiKuaiError(f"未知动词：{verb}")

    def _check_verb_arity(self, verb, args, node):
        """内建动词实参数量与声明元数的守卫（D-10）。

        - 声明元数来自 `keywords.VERB_ARITY`；正数为固定元数、-1 为变参（跳过）。
        - 不匹配抛携带 `ErrorInfo`（category=SYNTAX）的 `JiKuaiError`；错误消息
          仅使用中文动词名与数字，不出现 `lambda` / `_setup_builtins` / Python 类型名。
        """
        from .keywords import VERB_ARITY
        expected = VERB_ARITY.get(verb)
        # 未声明元数（安全侧倾）或变参：不校验
        if expected is None or expected == -1 or expected == -2:
            return
        if len(args) == expected:
            return
        msg = f"动词「{verb}」需要 {expected} 个参数，实际收到 {len(args)} 个"
        info = ErrorInfo(
            category=ErrorCategory.SYNTAX,
            message=msg,
            line=getattr(node, 'line', 0),
            col=getattr(node, 'col', 0),
            source_line=self._source_line(getattr(node, 'line', 0)),
        )
        raise JiKuaiError(f"语法错误：{msg}", info=info)

    def _eval_FuncCall(self, node, env):
        # M-04：`obj.成员(...)` 走 auto_invoke=False，避免 0 参方法「访问即调用」
        # 之后再对返回值二次调用。
        if isinstance(node.func, MemberAccess):
            obj = self._eval_node(node.func.obj, env)
            target = self._member_lookup(obj, node.func, env, auto_invoke=False)
            args = self._eval_args(node.args, env)
            if isinstance(target, BoundMethod):
                return self._invoke_method(target.instance, target.method_def,
                                           args, target.closure_env,
                                           defining_class=target.defining_class)
            # ADR-10/11: PyCallable 直接调用（括号已在 AST 层确认）
            from .pybridge import PyCallable as _PyCallable
            if isinstance(target, _PyCallable):
                return target(*args)
            return self._call_function(target, args, env)
        func = self._eval_node(node.func, env)
        args = self._eval_args(node.args, env)
        if isinstance(func, BoundMethod):
            return self._invoke_method(func.instance, func.method_def,
                                       args, func.closure_env,
                                       defining_class=func.defining_class)
        # ADR-10/11: PyCallable 通过标识符直接调用（理论上不常见，以防万一）
        from .pybridge import PyCallable as _PyCallable
        if isinstance(func, _PyCallable):
            return func(*args)
        return self._call_function(func, args, env)

    def _eval_Pipeline(self, node, env):
        """管道求值：每一步的结果作为下一步动词调用的第一参数。"""
        result = self._eval_node(node.stages[0], env)
        for stage in node.stages[1:]:
            if isinstance(stage, Call):
                args = [result] + [self._eval_node(a, env) for a in stage.args]
                verb = stage.verb
                if verb in self.verbs:
                    result = self.verbs[verb](*args)
                else:
                    func = env.get(verb)
                    result = self._call_function(func, args, env)
            elif isinstance(stage, AdverbCall):
                result = self._apply_adverb(stage, result, env)
            else:
                # 一般表达式（不太常见）
                result = self._eval_node(stage, env)
        return result

    def _eval_AdverbCall(self, node, env):
        return self._apply_adverb(node, None, env)

    def _apply_adverb(self, node, collection, env):
        """应用副词（皆/只/归）到集合上。"""
        adverb = node.adverb
        inner = node.inner

        if collection is None:
            # 可能 inner 的第一参数就是集合
            if isinstance(inner, Call) and inner.args:
                collection = self._eval_node(inner.args[0], env)
                inner = Call(verb=inner.verb, args=inner.args[1:])

        if not isinstance(collection, list):
            raise JiKuaiError(f"副词 {adverb} 需要作用于列表")

        if adverb == '皆':   # map
            return [self._apply_verb_to_item(inner, item, env) for item in collection]
        elif adverb == '只':  # filter
            return [item for item in collection if self._apply_verb_to_item(inner, item, env)]
        elif adverb == '归':  # reduce
            if isinstance(inner, Call) and inner.args:
                acc = self._eval_node(inner.args[-1], env)
                verb = inner.verb
                for item in collection:
                    acc = self.verbs[verb](acc, item)
                return acc
            else:
                # 默认 reduce 无初值
                verb = inner.verb if isinstance(inner, Call) else str(inner)
                acc = collection[0]
                for item in collection[1:]:
                    acc = self.verbs[verb](acc, item)
                return acc
        return collection

    def _apply_verb_to_item(self, inner, item, env):
        """将 verb call 应用到单个元素上。"""
        if isinstance(inner, Call):
            # 副词语义：item 作为第一参数，inner.args 作为其余参数
            remaining_args = [self._eval_node(a, env) for a in inner.args]
            all_args = [item] + remaining_args
            if inner.verb in self.verbs:
                return self.verbs[inner.verb](*all_args)
        return item

    def _eval_MemberAccess(self, node, env):
        obj = self._eval_node(node.obj, env)
        return self._member_lookup(obj, node, env, auto_invoke=True)

    def _member_lookup(self, obj, node, env, auto_invoke):
        """成员求值（字段优先，其次方法）。ADR-07 按元数分流。

        auto_invoke=True（裸 `obj.成员`）：0 参方法「访问即调用」（M-01，兼容 oop.jk）。
        auto_invoke=False（`obj.成员(...)` 调用点）：0 参方法也返回 BoundMethod，
        由 `_eval_FuncCall` 统一带参调用，从而让 `赵狗.叫声()` 等价 `赵狗.叫声`（M-04）。
        """
        # M2-1: 模块成员访问（只暴露导出名）
        from .module_loader import ModuleValue
        if isinstance(obj, ModuleValue):
            return obj.get(node.attr)
        # M2-2 · ADR-10/11: Python 桥模块成员访问
        from .pybridge import PyCallable, PyModule
        if isinstance(obj, PyModule):
            value = obj.member(node.attr)
            if auto_invoke and isinstance(value, PyCallable):
                # ADR-11：Python 函数必须括号调用。裸 `math.sqrt` 不进免括号
                # 元数路径、也不能作为值流转，这里直接抛 SYNTAX 中文诊断，
                # 避免 `math.sqrt 16` 静默 fallthrough 成两条语句（AC-94）。
                raise self._py_paren_required_error(obj, node)
            return value
        if isinstance(obj, SuperProxy):
            # M10-1：`父类.方法名`。从 start_class（当前方法定义类的父类）起查方法，
            # 绑定回同一个实例，但把 defining_class 设成命中类，以支持多层 super 链。
            attr = node.attr
            if obj.start_class is None:
                raise JiKuaiError(
                    "`父类` 无可用父类：当前类没有继承任何父类，"
                    "或方法定义类已到继承链顶端")
            method, owner = obj.instance.find_method_with_owner(attr, obj.start_class)
            if method is None:
                raise JiKuaiError(
                    f"父类中无方法：{obj.start_class.name} 及其祖先均未定义 `{attr}`")
            # 与实例分支不同：这里**不做** auto_invoke。`父类.方法名` 必须写括号，
            # 否则 0 参方法在「取值」和「调用」两种意图之间无法区分。
            return BoundMethod(obj.instance, method, env, defining_class=owner)
        if isinstance(obj, JiKuaiInstance):
            attr = node.attr
            # M9-4 封装：以「私」开头的成员只允许经 `自身.` 访问。
            # 用命名约定而不是新关键字，避免动词/关键字表扩张影响无空格分词；
            # 判定看的是**语法上的接收者**（node.obj 是否为 `自身`），
            # 而不是运行时对象身份——后者无法区分「类内访问自己」与
            # 「类外恰好拿到同一个实例」。
            if attr.startswith('私') and not self._is_self_receiver(node.obj):
                raise JiKuaiError(
                    f"私有成员不可从外部访问：{obj.klass.name}.{attr}"
                    f"（以「私」开头的成员只能在类内经 `自身.` 使用）")
            # 字段优先
            if attr in obj.attrs:
                return obj.attrs[attr]

            method, owner = obj.find_method_with_owner(attr, obj.klass)
            if method is not None:
                if len(method.params) == 0 and auto_invoke:
                    return self._invoke_method(obj, method, [], env,
                                               defining_class=owner)   # M-01
                return BoundMethod(obj, method, env, defining_class=owner)  # M-02 / M-03
            # ADR-02：声明过但未初始化的字段 → 空(nil)；未声明的属性 → 报错
            if obj.is_declared_field(attr):
                return None
            raise JiKuaiError(f"对象 {obj.klass.name} 无属性/方法：{attr}")
        if isinstance(obj, dict):
            return obj.get(node.attr)
        raise JiKuaiError(f"无法访问 {node.attr}")

    def _invoke_method(self, instance, method_def, args, env, defining_class=None):
        """以 instance 为 `自身` 执行方法体，返回 `返回` 的值（无则 None）。

        ADR-22：方法体的父环境是**方法定义所在类**的 def_env，而非调用者。
        这使方法能看到定义它的模块里 `导入` / `定义` 的名字；继承来的方法
        用**父类**的 def_env（父类可能定义在另一个模块）。

        M10-1：把「方法定义所在类」写进 call_env（键 `__定义类__`），供 `父类`
        求值时定位继承链起点。调用方未显式传 defining_class 时按方法名沿链回查，
        与 `_method_scope` 的解析顺序一致，保证两者永远指向同一个类。
        """
        if defining_class is None:
            _m, defining_class = instance.find_method_with_owner(
                method_def.name, instance.klass)
        scope_parent = (defining_class.def_env if defining_class is not None
                        and defining_class.def_env else None)
        if scope_parent is None:
            scope_parent = self._method_scope(instance.klass,
                                              method_def.name, env)
        call_env = Environment(scope_parent)
        call_env.set('自身', instance)
        call_env.set(_DEFINING_CLASS_KEY, defining_class)
        for i, p in enumerate(method_def.params):
            call_env.set(p, args[i] if i < len(args) else None)
        try:
            self._eval_body(method_def.body, call_env)
        except ReturnSignal as r:
            return r.value
        return None

    def _eval_Super(self, node, env):
        """M10-1：求值 `父类`，产出 SuperProxy。

        起点 = 「当前执行的方法定义所在类」的父类，而不是 `自身.klass` 的父类。
        这一点是正确性关键：三层继承 孙←子←父，若按 `自身.klass.parent` 取，
        子类方法里的 `父类.X` 在孙实例上会解析回子类自己 → 无限递归。
        """
        try:
            instance = env.get('自身')
        except JiKuaiError:
            raise JiKuaiError("`父类` 只能在类的方法体内使用")
        if not isinstance(instance, JiKuaiInstance):
            raise JiKuaiError("`父类` 只能在类的方法体内使用")
        try:
            defining_class = env.get(_DEFINING_CLASS_KEY)
        except JiKuaiError:
            defining_class = None
        base = defining_class if defining_class is not None else instance.klass
        return SuperProxy(instance, base.parent if base is not None else None)

    def _is_self_receiver(self, node):
        """判断 MemberAccess 的接收者语法上是不是 `自身`。

        看的是**语法**（AST 节点是否是 `Ident('自身')`），而不是运行时对象身份。
        原因：类外代码 `赵狗.主人 = 赵狗` 之后 `赵狗.主人.私余额` 也拿到同一
        实例，如果按对象身份判断就会误放行；而 `自身` 只有在方法体内才会被
        parser 生成，所以「语法上是 `自身`」等价于「这行代码写在类内」。

        M10-1：`父类` 同理——它也只由 parser 在方法体内生成，所以
        `父类.私方法()` 属于类内访问，应当放行。
        """
        return (isinstance(node, Ident) and node.name == '自身') \
            or isinstance(node, Super)

    def _method_scope(self, klass, method_name, fallback_env):
        """ADR-22：沿继承链找到定义 method_name 的类，返回它的 def_env。

        解析顺序与 `JiKuaiInstance._find_method` 一致（最派生优先）。
        找不到（或该类没有 def_env，如手工构造的类）时退回 fallback_env，
        保持旧行为，不至于让方法调用直接失去作用域。
        """
        k = klass
        while k is not None:
            if method_name in k.methods:
                return k.def_env or fallback_env
            k = k.parent
        return fallback_env

    def _py_paren_required_error(self, py_module, node):
        """ADR-11：Python 桥函数缺括号的 SYNTAX 诊断（AC-94）。

        返回（不抛出）一个携带 `ErrorInfo(category=SYNTAX)` 的 `JiKuaiError`，
        由调用方 `raise`，便于在 `_member_lookup` 里表达"取值失败"语义。
        """
        qual = f"{py_module.name}.{node.attr}"
        detail = (f"Python 桥函数「{qual}」必须使用括号调用，"
                  f"例如 {qual}(参数)。免括号（元数驱动）写法只适用于中文动词。")
        return JiKuaiError(f"语法错误：{detail}", info=ErrorInfo(
            category=ErrorCategory.SYNTAX,
            message=detail,
            line=getattr(node, 'line', 0),
            col=getattr(node, 'col', 0),
            source_line=self._source_line(getattr(node, 'line', 0)),
        ))

    def _reject_bound_method(self, value, node):
        """DP-3：BoundMethod 不可赋值 / 传参 / 返回。

        M10-1：SuperProxy 同样受此约束。裸 `父类` 若作为值流转，后续没有任何
        操作能对它做有意义的事，早报错比让它渗进列表/字典里更好定位。
        """
        if isinstance(value, SuperProxy):
            detail = "`父类` 不能作为值使用，只能写成 `父类.方法名(参数)`"
            raise JiKuaiError(
                f"类型错误：{detail}",
                info=ErrorInfo(
                    category=ErrorCategory.TYPE,
                    message=detail,
                    line=getattr(node, 'line', 0),
                    col=getattr(node, 'col', 0),
                    source_line=self._source_line(getattr(node, 'line', 0)),
                ))
        if isinstance(value, BoundMethod):
            name = f"{value.instance.klass.name}.{value.method_def.name}"
            detail = f"方法不能作为值使用，请直接调用：{name}(参数)"
            raise JiKuaiError(
                f"类型错误：{detail}",
                info=ErrorInfo(
                    category=ErrorCategory.TYPE,
                    message=detail,
                    line=getattr(node, 'line', 0),
                    col=getattr(node, 'col', 0),
                    source_line=self._source_line(getattr(node, 'line', 0)),
                ),
            )
        return value

    def _eval_args(self, arg_nodes, env):
        """求值实参列表，并按 DP-3 拒绝 BoundMethod 作为参数传递。"""
        values = []
        for a in arg_nodes:
            values.append(self._reject_bound_method(self._eval_node(a, env), a))
        return values

    def _eval_Index(self, node, env):
        obj = self._eval_node(node.obj, env)
        idx = self._eval_node(node.index, env)
        # 字典按键取值（键不强转 int），序列按整数下标取值
        if isinstance(obj, dict):
            return obj[idx]
        return obj[int(idx)]

    def _eval_ListLit(self, node, env):
        return [self._eval_node(item, env) for item in node.items]

    def _eval_DictLit(self, node, env):
        result = {}
        for k_node, v_node in node.items:
            key = self._eval_node(k_node, env)
            # ADR-23：键必须可哈希（str/int/float/bool/None），不可哈希类型给中文诊断
            try:
                hash(key)
            except TypeError:
                raise JiKuaiError(
                    f"类型错误：字典的键不可哈希（类型 {type(key).__name__}，值 {key!r}）",
                    info=ErrorInfo(
                        category=ErrorCategory.TYPE,
                        message=f"字典的键必须是不可变类型（字符串/数字/布尔/空），"
                                f"当前键的类型是 {type(key).__name__}",
                        line=getattr(k_node, 'line', 0),
                        col=getattr(k_node, 'col', 0),
                        source_line=self._source_line(getattr(k_node, 'line', 0)),
                    ),
                )
            result[key] = self._eval_node(v_node, env)
        return result

    def _eval_Lambda(self, node, env):
        return ('func', FuncDef(name='<lambda>', params=node.params, body=node.body), env)

    def _resolve_ctor(self, klass):
        """ADR-02：沿继承链向上查找定义了构造器的类。

        规则：
        - klass.ctor_defined == True → 该类自己定义了构造器（可能为空体），返回它。
        - 否则递归查 klass.parent。
        - 一路到顶仍无 → 返回 None。
        """
        if klass is None:
            return None
        if klass.ctor_defined:
            return klass
        return self._resolve_ctor(klass.parent)

    def _eval_NewInstance(self, node, env):
        cls_name = node.class_name
        klass = self.classes.get(cls_name)
        if not klass:
            raise JiKuaiError(f"未定义的类：{cls_name}")
        instance = JiKuaiInstance(klass)
        # ADR-02：用 _resolve_ctor 代替直接判 klass.ctor_body
        ctor_class = self._resolve_ctor(klass)
        if ctor_class is not None and ctor_class.ctor_body:
            # ADR-22：构造器体以**构造器所在类**的 def_env 为父环境（词法作用域）；
            # 参数在调用者作用域求值（对齐 Python 求值时机），然后注入 ctor_env。
            parent_env = getattr(ctor_class, 'def_env', None) or env
            ctor_env = Environment(parent_env)
            ctor_env.set('自身', instance)
            for i, param in enumerate(ctor_class.ctor_params):
                val = self._eval_node(node.args[i], env) if i < len(node.args) else None
                ctor_env.set(param, val)
            self._eval_body(ctor_class.ctor_body, ctor_env)
        return instance

    # ======================== 辅助 ========================

    def _call_function(self, func, args, env):
        """调用函数（用户定义或类方法）。"""
        if isinstance(func, tuple) and func[0] == 'func':
            _, func_def, closure_env = func
            call_env = Environment(closure_env)
            for i, param in enumerate(func_def.params):
                call_env.set(param, args[i] if i < len(args) else None)
            try:
                self._eval_body(func_def.body, call_env)
            except ReturnSignal as r:
                return r.value
            return None
        if isinstance(func, FuncDef):
            # 方法调用（需要绑定 self）
            call_env = Environment(env)
            # 第一参数是 self（从 MemberAccess 场景来）
            for i, param in enumerate(func.params):
                call_env.set(param, args[i] if i < len(args) else None)
            try:
                self._eval_body(func.body, call_env)
            except ReturnSignal as r:
                return r.value
            return None
        if callable(func):
            return func(*args)
        raise JiKuaiError(f"不可调用的对象：{func}")


def _err(msg):
    raise JiKuaiError(msg)


# ---------------------------------------------------------------------------
# 标准库桥接：从 stdlib/ 目录的 Python 模块惰性加载中国国情函数
# ---------------------------------------------------------------------------

_STDLIB_CACHE = {}


def _load_stdlib_module(name):
    """惰性加载 stdlib/<name>.py，返回模块对象。"""
    if name in _STDLIB_CACHE:
        return _STDLIB_CACHE[name]
    import importlib.util
    # stdlib 目录：src/jikuai/../../stdlib
    here = os.path.dirname(os.path.abspath(__file__))
    stdlib_dir = os.path.join(here, '..', '..', 'stdlib')
    path = os.path.normpath(os.path.join(stdlib_dir, f'{name}.py'))
    spec = importlib.util.spec_from_file_location(f'jikuai_stdlib_{name}', path)
    if spec is None or spec.loader is None:
        raise JiKuaiError(f"无法加载标准库模块：{name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _STDLIB_CACHE[name] = module
    return module


def _stdlib_call(module_name, func_name):
    """返回一个调用 stdlib 模块函数的包装器（惰性加载）。"""
    def wrapper(*args):
        module = _load_stdlib_module(module_name)
        fn = getattr(module, func_name, None)
        if fn is None:
            raise JiKuaiError(f"标准库 {module_name} 无函数：{func_name}")
        result = fn(*args)
        # 农历元组转为列表，便于极快脚本使用
        if isinstance(result, tuple):
            return list(result)
        return result
    return wrapper
