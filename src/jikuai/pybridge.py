# -*- coding: utf-8 -*-
"""极快语言 - Python 桥（ADR-10 / ADR-11）· v0.4.0 M2。

本文件是极快 ↔ Python 双向互操作的**唯一**实现点。evaluator 通过窄接口
（`py_import` / `PyModule` / `PyCallable`）调用，不感知 importlib 细节。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
方向一 · 极快 → Python（out-bound）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  导入 蟒:math。          → env['math'] = PyModule(math)
  打印 math.sqrt(16)。    → PyCallable(math.sqrt)(16) → 4.0

  ADR-11：Python 函数**必须括号调用**，不注册进 `VERB_ARITY`，不进免括号
  元数路径。裸 `math.sqrt`（无括号）在 evaluator 的 `_member_lookup`
  （auto_invoke=True）处抛 SYNTAX 中文诊断。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
方向二 · Python → 极快（in-bound / embed）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  import jikuai
  mod = jikuai.load("script.jk")   → JKModule
  mod.某函数(3) / mod.某变量 / mod.某类(...)

  AC-104：`import jikuai` 不触发 load、不改全局 mutable state；
  同脚本 `load` 两次得到**独立**模块对象（每次新建 Evaluator，无缓存）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
类型映射表（不可变契约）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  整数 ↔ int          小数 ↔ float        字符串 ↔ str
  布尔 ↔ bool         空   ↔ None
  列表 ↔ list（递归） 字典 ↔ dict（key 仅允许字符串/整数）
  RMB  →  Decimal     反向需 `人民币(...)` 显式包装（Decimal 原样过界）
  JiKuaiInstance → opaque object
  JiKuaiClass    不跨界   ┐ 触发诊断（category=TYPE，与 DP-3 一致）
  BoundMethod    禁止跨界 ┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
安全边界（v0.6.0 · ADR-21 · 必读）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
**pybridge 不提供完整沙箱隔离，仅基于黑名单做缓解。**

  拒绝清单（`DENY_LIST`，黑名单机制）：
    os.system / subprocess.Popen / builtins.eval / builtins.exec

  已知绕过路径（黑名单的固有局限，不是缺陷）：
    - `importlib.import_module("os").system(...)` 等间接引用
    - `getattr(__builtins__, "e" + "val")` 等动态名字构造
    - 任何未列入清单的危险 API（清单是枚举而非语义分析）

  适用场景：
    运行**你自己编写的**或**来源可信的** Python 代码。

  禁用场景（明确不支持）：
    - 执行不受信任的第三方代码 / 用户上传的代码
    - 多租户环境下的代码隔离
    - 面向公网的代码执行服务

  若必须承载不可信输入：**pybridge 不能作为安全边界**，须在其外叠加
  进程级或容器级隔离（如独立子进程 + seccomp / 容器 + 只读挂载）。

  其他既有约束：
    - `load` 拒绝 `..` 路径穿越与绝对路径逃逸（`_validate_script_path`）
    - 跨语言异常携带 `ErrorInfo(category=RUNTIME)`，含 Python 类型名 +
      原始 message，绝对路径被 `_scrub_paths` 抹去

  完整声明见 `docs/安全边界.md` 与 `docs/ADR-21-pybridge安全边界.md`。
"""


import importlib
import os
import re
from decimal import Decimal


# ═══════════════════════════════════════════════════════════════════════
# 安全拒绝清单
# ═══════════════════════════════════════════════════════════════════════

#: 架构定的默认拒绝清单（模块名, 属性名）。命中即抛 RUNTIME 诊断。
#: ⚠️ 这是**黑名单**，不是沙箱；绕过风险见 docs/互操作.md「安全边界」一节。
DENY_LIST = frozenset({
    ('os', 'system'),
    ('subprocess', 'Popen'),
    ('builtins', 'eval'),
    ('builtins', 'exec'),
})


def _is_denied(module_name, attr):
    """(模块名, 属性名) 是否命中默认拒绝清单。"""
    return (module_name, attr) in DENY_LIST


def _denied_error(module_name, attr):
    from .errors import ErrorCategory, ErrorInfo
    from .evaluator import JiKuaiError
    msg = (f"安全限制：Python 桥默认拒绝 {module_name}.{attr}。"
           f"如确需使用，请在宿主 Python 侧显式封装后再暴露给极快。")
    return JiKuaiError(f"运行错误：{msg}", info=ErrorInfo(
        category=ErrorCategory.RUNTIME, message=msg, line=0, col=0))


# ═══════════════════════════════════════════════════════════════════════
# 路径脱敏（AC-101：不泄漏堆栈绝对路径）
# ═══════════════════════════════════════════════════════════════════════

_PLACEHOLDER = '<路径已隐去>'

# Windows 盘符绝对路径：C:\a\b 或 C:/a/b
_WIN_ABS = re.compile(r'[A-Za-z]:[\\/][^\s\'"),;]*')
# POSIX 绝对路径：/a/b（至少两段，避免把 "/" 或 "a/b" 误判）
_POSIX_ABS = re.compile(r'(?<![\w.])/(?:[^\s/\'"),;]+/)+[^\s/\'"),;]*')


def _scrub_paths(text):
    """把消息中的绝对路径替换为占位符。相对路径与文件名保留。"""
    if not text:
        return text
    text = _WIN_ABS.sub(_PLACEHOLDER, text)
    text = _POSIX_ABS.sub(_PLACEHOLDER, text)
    return text


# ═══════════════════════════════════════════════════════════════════════
# 类型映射
# ═══════════════════════════════════════════════════════════════════════

def _cross_border_error(detail):
    """跨界被禁类型的诊断（category=TYPE，与 DP-3 的 BoundMethod 拒绝一致）。

    注：ADR 文案称之为 SEMANTIC 诊断；`errors.ErrorCategory` 中没有 SEMANTIC
    成员，DP-3 既有实现用 TYPE，本处沿用 TYPE 以保持 M1 枚举不变。
    """
    from .errors import ErrorCategory, ErrorInfo
    from .evaluator import JiKuaiError
    return JiKuaiError(f"类型错误：{detail}", info=ErrorInfo(
        category=ErrorCategory.TYPE, message=detail, line=0, col=0))


def _check_dict_key(key):
    """字典 key 仅允许字符串/整数（契约）。bool 是 int 子类，显式排除。"""
    if isinstance(key, bool) or not isinstance(key, (str, int)):
        raise _cross_border_error(
            f"字典键跨语言只支持字符串与整数，收到：{type(key).__name__}")
    return key


def jk_to_py(v):
    """极快值 → Python 值（递归）。"""
    from .evaluator import RMB, BoundMethod, JiKuaiClass, JiKuaiInstance

    # 空 / 布尔 / 整数 / 小数 / 字符串：直通（bool 必须先于 int 判定）
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    # 列表 → list（递归）
    if isinstance(v, list):
        return [jk_to_py(x) for x in v]
    # 字典 → dict（key 仅字符串/整数）
    if isinstance(v, dict):
        return {_check_dict_key(k): jk_to_py(val) for k, val in v.items()}
    # RMB → Decimal
    if isinstance(v, RMB):
        return v.amount
    # BoundMethod 禁止跨界（DP-3）
    if isinstance(v, BoundMethod):
        raise _cross_border_error(
            f"绑定方法不能跨语言传递："
            f"{v.instance.klass.name}.{v.method_def.name}。请在极快侧调用后传结果。")
    # JiKuaiClass 不跨界
    if isinstance(v, JiKuaiClass):
        raise _cross_border_error(f"极快类「{v.name}」不能跨语言传递。")
    # JiKuaiInstance → opaque object（原样传递，Python 侧不解构）
    if isinstance(v, JiKuaiInstance):
        return v
    # Decimal / Python 原生对象（含桥回传的 opaque）：直通
    return v


def py_to_jk(v):
    """Python 值 → 极快值（递归）。

    Decimal 不自动变 RMB —— 契约要求反向由极快侧 `人民币(...)` 显式包装。
    未列入映射表的 Python 对象作为 opaque object 原样返回，`打印` 时经
    `_format_value` → `str(...)` 得到可读字符串（AC-93）。
    """
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    # list / tuple → 极快列表（tuple 无对应类型，统一编组为列表）
    if isinstance(v, (list, tuple)):
        return [py_to_jk(x) for x in v]
    if isinstance(v, dict):
        return {_check_dict_key(k): py_to_jk(val) for k, val in v.items()}
    return v


# ═══════════════════════════════════════════════════════════════════════
# 异常翻译
# ═══════════════════════════════════════════════════════════════════════

def translate_exception(exc):
    """Python 异常 → `JiKuaiError`（category=RUNTIME）。

    AC-101 文案：`Python 侧异常：<类名>：<message>`，绝对路径已脱敏。
    已经是 `JiKuaiError` 的（例如从极快侧穿过 Python 回调再抛回）原样返回，
    避免二次包装丢失原始中文诊断。
    """
    from .errors import ErrorCategory, ErrorInfo
    from .evaluator import JiKuaiError

    if isinstance(exc, JiKuaiError):
        return exc

    detail = _scrub_paths(str(exc))
    message = f"Python 侧异常：{type(exc).__name__}：{detail}"
    return JiKuaiError(f"运行错误：{message}", info=ErrorInfo(
        category=ErrorCategory.RUNTIME, message=message, line=0, col=0))


# ═══════════════════════════════════════════════════════════════════════
# 方向一：极快 → Python
# ═══════════════════════════════════════════════════════════════════════

class PyCallable:
    """跨界可调用体。ADR-11：只能被括号调用，不进 `VERB_ARITY`。"""

    __slots__ = ('_fn', '_qualname')

    def __init__(self, fn, qualname):
        self._fn = fn
        self._qualname = qualname

    @property
    def qualname(self):
        return self._qualname

    def __call__(self, *jk_args):
        """类型映射（入参）→ 调用 → 异常翻译 → 类型映射（返回值）。"""
        py_args = [jk_to_py(a) for a in jk_args]
        try:
            result = self._fn(*py_args)
        except BaseException as exc:            # noqa: BLE001 —— 必须全捕获后翻译
            raise translate_exception(exc) from None
        return py_to_jk(result)

    def __repr__(self):
        return f"<Python函数:{self._qualname}>"


class PyModule:
    """Python 模块的极快侧句柄。成员访问经拒绝清单与类型映射。"""

    __slots__ = ('_mod', '_name')

    def __init__(self, mod, name):
        object.__setattr__(self, '_mod', mod)
        object.__setattr__(self, '_name', name)

    @property
    def name(self):
        return self._name

    def member(self, attr):
        """取模块成员：命中拒绝清单抛诊断；可调用包 PyCallable；否则 py→jk。

        供 evaluator 的 `_member_lookup` 调用（与 `ModuleValue.get` 同角色，
        但用独立名字避免与 Python 模块里名为 `get` 的成员相冲突）。
        """
        import types

        from .errors import ErrorCategory, ErrorInfo
        from .evaluator import JiKuaiError

        if _is_denied(self._name, attr):
            raise _denied_error(self._name, attr)

        try:
            val = getattr(self._mod, attr)
        except AttributeError:
            msg = f"Python 模块 {self._name} 无此属性：{attr}"
            raise JiKuaiError(f"运行错误：{msg}", info=ErrorInfo(
                category=ErrorCategory.RUNTIME, message=msg,
                line=0, col=0)) from None

        # 子模块（`os.path`）继续包成 PyModule，保持逐段的拒绝清单检查点
        if isinstance(val, types.ModuleType):
            return PyModule(val, f"{self._name}.{attr}")
        if callable(val):
            return PyCallable(val, f"{self._name}.{attr}")
        return py_to_jk(val)

    def __repr__(self):
        return f"<蟒模块:{self._name}>"


def py_import(name, top_level=False):
    """`导入 蟒:name。` 的运行时实现。返回 `PyModule`。

    Args:
        name: Python 模块名（可含 `.`，如 `os.path`）
        top_level: 若为 True，返回顶层模块的 PyModule（对齐 Python
            `import x.y` 的绑定语义：`x` 在 caller 作用域可见）。
            evaluator 侧的 `_eval_Import` 根据是否有 `作为 别名` 决定。

    AC-96：导入失败抛中文 `ErrorInfo` 诊断，不透出 Python `ImportError` 原文。
    """
    import sys

    from .errors import ErrorCategory, ErrorInfo
    from .evaluator import JiKuaiError

    if not name or not all(part.isidentifier() for part in name.split('.')):
        msg = f"非法的 Python 模块名：{name}"
        raise JiKuaiError(f"运行错误：{msg}", info=ErrorInfo(
            category=ErrorCategory.RUNTIME, message=msg, line=0, col=0))

    try:
        mod = importlib.import_module(name)
    except BaseException as exc:               # noqa: BLE001
        # 含 ImportError / ModuleNotFoundError，也含模块 import 期抛的任意异常
        if isinstance(exc, ImportError):
            msg = f"找不到 Python 模块：{name}"
        else:
            msg = (f"导入 Python 模块 {name} 时出错："
                   f"{type(exc).__name__}：{_scrub_paths(str(exc))}")
        raise JiKuaiError(f"运行错误：{msg}", info=ErrorInfo(
            category=ErrorCategory.RUNTIME, message=msg,
            line=0, col=0)) from None

    if top_level and '.' in name:
        top = name.split('.')[0]
        return PyModule(sys.modules[top], top)
    return PyModule(mod, name)


# ═══════════════════════════════════════════════════════════════════════
# 方向二：Python → 极快（embed）
# ═══════════════════════════════════════════════════════════════════════

class JiKuaiCallError(Exception):
    """占位基类，实际在 `_embed_error_class()` 中替换为 JiKuaiError 子类。

    定义在函数里以避免模块导入期反向依赖 evaluator（evaluator 会 import 本文件）。
    """


_EMBED_ERROR_CLS = None


def _embed_error_class():
    """惰性构造 `JiKuaiError` 的子类，供 Python 侧捕获（AC-100）。"""
    global _EMBED_ERROR_CLS
    if _EMBED_ERROR_CLS is None:
        from .evaluator import JiKuaiError

        class JiKuaiEmbedError(JiKuaiError):
            """极快侧错误跨界到 Python 侧的表示。保留中文文案与 `ErrorInfo`。"""

        _EMBED_ERROR_CLS = JiKuaiEmbedError
    return _EMBED_ERROR_CLS


def _reraise_to_python(exc):
    """极快侧 `JiKuaiError` → Python 侧 `JiKuaiError` 子类（保留 info）。"""
    cls = _embed_error_class()
    err = cls(str(exc), info=getattr(exc, 'info', None))
    raise err from None


def _validate_script_path(path, base_dir):
    """安全：拒绝绝对路径与 `..` 穿越，确保解析结果落在 base_dir 内。

    Returns: 脚本绝对路径。
    Raises: ValueError（由 `load` 转成中文诊断）。消息里的路径经 `_scrub_paths`
        脱敏，避免把宿主目录结构回显给调用方。
    """
    text = str(path)
    shown = _scrub_paths(text)
    if os.path.isabs(text) or _WIN_ABS.match(text):
        raise ValueError(f"拒绝绝对路径：{shown}")
    normalized = text.replace('\\', '/')
    if '..' in normalized.split('/'):
        raise ValueError(f"拒绝路径穿越（含 ..）：{shown}")

    root = os.path.abspath(base_dir)
    target = os.path.abspath(os.path.join(root, text))
    # 二次兜底：symlink / 大小写等造成的逃逸
    try:
        inside = os.path.commonpath([root, target]) == root
    except ValueError:
        inside = False          # 跨盘符（Windows）
    if not inside:
        raise ValueError(f"路径逃逸出根目录：{shown}")
    return target


class JKCallable:
    """极快函数在 Python 侧的句柄。入参 py→jk，返回值 jk→py。"""

    __slots__ = ('_func', '_evaluator', '_name')

    def __init__(self, func, evaluator, name):
        self._func = func
        self._evaluator = evaluator
        self._name = name

    def __call__(self, *py_args):
        from .evaluator import JiKuaiError
        jk_args = [py_to_jk(a) for a in py_args]
        try:
            result = self._evaluator._call_function(
                self._func, jk_args, self._evaluator.global_env)
        except JiKuaiError as exc:
            _reraise_to_python(exc)
        return jk_to_py(result)

    def __repr__(self):
        return f"<极快函数:{self._name}>"


class JKBoundMethod:
    """极快实例方法在 Python 侧的句柄。"""

    __slots__ = ('_instance', '_method_def', '_evaluator')

    def __init__(self, instance, method_def, evaluator):
        self._instance = instance
        self._method_def = method_def
        self._evaluator = evaluator

    def __call__(self, *py_args):
        from .evaluator import JiKuaiError
        jk_args = [py_to_jk(a) for a in py_args]
        try:
            result = self._evaluator._invoke_method(
                self._instance, self._method_def, jk_args,
                self._evaluator.global_env)
        except JiKuaiError as exc:
            _reraise_to_python(exc)
        return jk_to_py(result)

    def __repr__(self):
        return (f"<极快方法:{self._instance.klass.name}."
                f"{self._method_def.name}>")


class JKInstance:
    """极快实例在 Python 侧的句柄。属性读字段，方法返回可调用句柄。"""

    __slots__ = ('_instance', '_evaluator')

    def __init__(self, instance, evaluator):
        object.__setattr__(self, '_instance', instance)
        object.__setattr__(self, '_evaluator', evaluator)

    @property
    def 极快实例(self):
        """暴露底层 `JiKuaiInstance`（opaque object 回传用）。"""
        return self._instance

    def __getattr__(self, attr):
        from .evaluator import JiKuaiError
        if attr.startswith('_'):
            raise AttributeError(attr)
        inst = self._instance
        if attr in inst.attrs:
            return jk_to_py(inst.attrs[attr])
        method = inst._find_method(attr, inst.klass)
        if method is not None:
            if len(method.params) == 0:
                # 与极快侧 M-01「0 参方法访问即调用」保持一致
                try:
                    return jk_to_py(self._evaluator._invoke_method(
                        inst, method, [], self._evaluator.global_env))
                except JiKuaiError as exc:
                    _reraise_to_python(exc)
            return JKBoundMethod(inst, method, self._evaluator)
        if inst.is_declared_field(attr):
            return None
        raise AttributeError(f"极快对象 {inst.klass.name} 无属性/方法：{attr}")

    def __setattr__(self, attr, value):
        if attr.startswith('_'):
            object.__setattr__(self, attr, value)
            return
        self._instance.attrs[attr] = py_to_jk(value)

    def __repr__(self):
        return f"<极快实例:{self._instance.klass.name}>"


class JKClass:
    """极快类在 Python 侧的句柄。调用即实例化（AC-99）。"""

    __slots__ = ('_klass', '_evaluator')

    def __init__(self, klass, evaluator):
        self._klass = klass
        self._evaluator = evaluator

    @property
    def name(self):
        return self._klass.name

    def __call__(self, *py_args):
        from .evaluator import Environment, JiKuaiError, JiKuaiInstance
        ev = self._evaluator
        instance = JiKuaiInstance(self._klass)
        ctor_class = ev._resolve_ctor(self._klass)
        if ctor_class is not None and ctor_class.ctor_body:
            ctor_env = Environment(ev.global_env)
            ctor_env.set('自身', instance)
            for i, param in enumerate(ctor_class.ctor_params):
                ctor_env.set(param,
                             py_to_jk(py_args[i]) if i < len(py_args) else None)
            try:
                ev._eval_body(ctor_class.ctor_body, ctor_env)
            except JiKuaiError as exc:
                _reraise_to_python(exc)
        return JKInstance(instance, ev)

    def __repr__(self):
        return f"<极快类:{self._klass.name}>"


class JKModule:
    """`jikuai.load()` 的返回值。只暴露脚本 `导出` 的名字。"""

    __slots__ = ('_name', '_path', '_module', '_evaluator')

    def __init__(self, name, path, module_value, evaluator):
        object.__setattr__(self, '_name', name)
        object.__setattr__(self, '_path', path)
        object.__setattr__(self, '_module', module_value)
        object.__setattr__(self, '_evaluator', evaluator)

    @property
    def 名字(self):
        return self._name

    def 导出名(self):
        """返回本模块导出名的排序列表（便于宿主自省）。"""
        return sorted(self._module._exports)

    def __dir__(self):
        return list(super().__dir__()) + self.导出名()

    def __getattr__(self, attr):
        from .evaluator import (BoundMethod, JiKuaiClass, JiKuaiError,
                                JiKuaiInstance)
        if attr.startswith('_'):
            raise AttributeError(attr)
        try:
            value = self._module.get(attr)
        except JiKuaiError as exc:
            # 未导出 / 未定义：转成 AttributeError 更符合 Python 直觉，
            # 但保留中文原因文本，便于宿主打印。
            raise AttributeError(str(exc)) from None

        ev = self._evaluator
        # 函数：('func', FuncDef, closure_env)
        if isinstance(value, tuple) and value and value[0] == 'func':
            return JKCallable(value, ev, attr)
        if isinstance(value, JiKuaiClass):
            return JKClass(value, ev)
        if isinstance(value, JiKuaiInstance):
            return JKInstance(value, ev)
        if isinstance(value, BoundMethod):
            raise _cross_border_error("绑定方法不能跨语言传递。")
        if callable(value):
            # 内建动词的薄封装导出
            return JKCallable(value, ev, attr)
        return jk_to_py(value)

    def __repr__(self):
        return f"<极快模块:{self._name}>"


def load(path, base_dir=None):
    """加载 `.jk` 脚本并返回 Python 侧句柄 `JKModule`。

    Args:
        path: **相对**脚本路径（相对 `base_dir`）。绝对路径与 `..` 一律拒绝。
        base_dir: 解析根目录，默认当前工作目录。

    Returns:
        `JKModule` —— 每次调用都是**全新**对象（AC-104：无缓存、无全局状态）。

    Raises:
        `jikuai.JiKuaiError` 子类：路径非法、文件缺失、脚本执行出错。
    """
    from .errors import ErrorCategory, ErrorInfo
    from .evaluator import Environment, Evaluator, JiKuaiError
    from .lexer import tokenize
    from .module_loader import ModuleValue
    from .parser import parse

    cls = _embed_error_class()

    def _fail(message):
        raise cls(f"运行错误：{message}", info=ErrorInfo(
            category=ErrorCategory.RUNTIME, message=message,
            line=0, col=0)) from None

    root = base_dir if base_dir is not None else os.getcwd()
    try:
        abspath = _validate_script_path(path, root)
    except ValueError as exc:
        _fail(f"jikuai.load 路径检查失败：{exc}")

    if not os.path.isfile(abspath):
        _fail(f"找不到极快脚本：{path}")

    try:
        with open(abspath, 'r', encoding='utf-8') as f:
            source = f.read()
    except UnicodeDecodeError:
        _fail(f"极快脚本编码不是 UTF-8：{path}")

    # 每次 load 都用**独立** Evaluator，保证两次 load 互不干扰（AC-104）
    evaluator = Evaluator()
    module_env = Environment()
    exports = set()
    evaluator._current_module_env = module_env
    evaluator._current_exports = exports
    evaluator._current_file = abspath
    evaluator._current_source = source

    try:
        evaluator._eval_body(parse(tokenize(source)).body, module_env)
    except JiKuaiError as exc:
        _reraise_to_python(exc)

    name = os.path.splitext(os.path.basename(abspath))[0]
    module_value = ModuleValue(name, module_env, exports, evaluator)
    return JKModule(name, abspath, module_value, evaluator)
