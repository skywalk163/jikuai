# -*- coding: utf-8 -*-
"""极快语言 - 模块加载器 (M2-1)。

职责：
- 按优先级搜索 .jk 模块：当前文件目录 → stdlib/ → JIKUAI_PATH（环境变量）
- 在独立的模块级 Environment 中执行 .jk 文件
- 只有 `导出` 的名字对外可见
- 相同绝对路径的模块只加载执行一次（缓存）
- 循环导入检测：加载栈中出现重复则抛 JiKuaiError（含"循环导入"字样）
- 拒绝含路径分隔符 / `..` 的模块名
"""

import os
from typing import List, Optional


#: 混合模块（`X.jk` + 同名 `X.py`）的 Python 实现缓存：abspath -> module
_PY_BACKING_CACHE = {}


def _load_python_backing(jk_path):
    """加载 `.jk` 模块的同名 `.py` 实现（ADR-16 §3.3 混合模块）。

    - 以 `importlib.util.spec_from_file_location` 隔离加载，**不污染 sys.path**
    - 找不到同名 `.py` 时返回 None（纯 `.jk` 模块的常态）
    - 同一路径只加载一次（进程级缓存）
    """
    py_path = jk_path[:-3] + '.py' if jk_path.endswith('.jk') else None
    if not py_path or not os.path.isfile(py_path):
        return None
    key = os.path.abspath(py_path)
    if key in _PY_BACKING_CACHE:
        return _PY_BACKING_CACHE[key]

    import importlib.util
    stem = os.path.splitext(os.path.basename(py_path))[0]
    spec = importlib.util.spec_from_file_location(
        f'jikuai_stdlib_backing_{stem}', key)
    if spec is None or spec.loader is None:
        from .evaluator import JiKuaiError
        raise JiKuaiError(f"无法加载模块的 Python 实现：{stem}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _PY_BACKING_CACHE[key] = module
    return module


def _inject_python_backing(module_env, backing):
    """把 Python 实现的公共可调用对象注入 `.jk` 模块环境。

    ADR-16 §3.3：`.py` 是内部实现，`.jk` 是唯一门面。注入后 `.jk` 只需用
    `导出` 声明哪些名字对外可见——未 `导出` 的注入名依旧被
    `ModuleValue.get()` 拦截（JK-E5002），访问控制不受影响。

    注入策略（保守，避免污染）：
    - 跳过下划线开头的私有名
    - 跳过 `__all__` 之外的名字（若 `.py` 声明了 `__all__`）
    - 只注入可调用对象，且必须是该 `.py` 自身定义的（`__module__` 相符），
      避免把 `import` 进来的第三方函数一并暴露
    - 已在 `.jk` 中定义的同名变量优先（不覆盖）
    """
    public = getattr(backing, '__all__', None)
    names = public if public is not None else dir(backing)
    own_module_name = getattr(backing, '__name__', None)
    for name in names:
        if name.startswith('_'):
            continue
        value = getattr(backing, name, None)
        if not callable(value):
            continue
        # 只暴露本文件定义的函数/类，不透传 import 进来的符号
        if getattr(value, '__module__', own_module_name) != own_module_name:
            continue
        if name in module_env.vars:
            continue
        module_env.vars[name] = value



class ModuleValue:
    """运行时模块对象。只暴露 `导出` 的名字，通过 MemberAccess 访问。"""

    __slots__ = ('name', '_env', '_exports', '_evaluator')

    def __init__(self, name, env, exports, evaluator):
        self.name = name
        self._env = env
        self._exports = set(exports)
        self._evaluator = evaluator

    def get(self, attr):
        """获取导出名对应的值。未导出或不存在则抛错。"""
        from .evaluator import JiKuaiError
        from .diagnostics import codes
        if attr not in self._exports:
            raise JiKuaiError(f"[{codes.JK_E5002}] 模块 {self.name} 未导出：{attr}")
        # 优先查模块环境；否则允许 fallback 到内建动词（用于薄封装）
        if attr in self._env.vars:
            return self._env.vars[attr]
        if attr in self._evaluator.verbs:
            return self._evaluator.verbs[attr]
        raise JiKuaiError(f"[{codes.JK_E5002}] 模块 {self.name} 导出的名字未定义：{attr}")

    def __repr__(self):
        return f"<模块:{self.name}>"


class ModuleLoader:
    """管理 .jk 模块的解析、执行、缓存与循环导入检测。"""

    def __init__(self, evaluator):
        self.evaluator = evaluator
        self._cache = {}       # abspath -> ModuleValue
        self._loading = []     # 加载栈（abspath 列表）用于循环检测

    def _search_paths(self, current_file):
        paths = []
        if current_file:
            paths.append(os.path.dirname(os.path.abspath(current_file)))
        here = os.path.dirname(os.path.abspath(__file__))
        stdlib_dir = os.path.normpath(os.path.join(here, '..', '..', 'stdlib'))
        paths.append(stdlib_dir)
        env_path = os.environ.get('JIKUAI_PATH', '')
        if env_path:
            for p in env_path.split(os.pathsep):
                if p:
                    paths.append(p)
        return paths

    def resolve(self, module_name, current_file=None):
        """将模块名解析为绝对路径。找不到则抛错。"""
        from .evaluator import JiKuaiError
        from .diagnostics import codes
        # 安全检查：拒绝路径分隔符和 ..
        if ('/' in module_name or '\\' in module_name
                or '..' in module_name or module_name.startswith('.')):
            raise JiKuaiError(f"非法模块名（含路径分隔符或 ..）：{module_name}")

        for d in self._search_paths(current_file):
            path = os.path.join(d, module_name + '.jk')
            if os.path.isfile(path):
                return os.path.abspath(path)
        raise JiKuaiError(f"[{codes.JK_E5001}] 找不到模块：{module_name}")

    def load(self, module_name, current_file=None):
        """加载模块并返回 ModuleValue。"""
        from .evaluator import JiKuaiError, Environment
        from .lexer import tokenize
        from .parser import parse

        path = self.resolve(module_name, current_file)

        if path in self._cache:
            return self._cache[path]

        if path in self._loading:
            chain = ' -> '.join(self._loading + [path])
            raise JiKuaiError(f"循环导入检测：{chain}")

        self._loading.append(path)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                source = f.read()
            tokens = tokenize(source)
            ast = parse(tokens)
            # 模块级独立环境（无父级：不继承调用方的用户名字）
            module_env = Environment()
            module_exports = set()

            # ADR-16 §3.3：混合模块 —— 若存在同名 .py 实现，则先加载并把公共
            # 可调用对象注入模块环境。`.py` 是内部实现，`.jk` 是唯一门面；
            # 未 `导出` 的注入名依旧被 ModuleValue.get() 拦截（JK-E5002）。
            backing = _load_python_backing(path)
            if backing is not None:
                _inject_python_backing(module_env, backing)

            # 切换 evaluator 的模块上下文
            ev = self.evaluator
            prev_env = ev._current_module_env
            prev_exports = ev._current_exports
            prev_file = ev._current_file
            prev_source = ev._current_source
            ev._current_module_env = module_env
            ev._current_exports = module_exports
            ev._current_file = path
            ev._current_source = source
            try:
                ev._eval_body(ast.body, module_env)
            finally:
                ev._current_module_env = prev_env
                ev._current_exports = prev_exports
                ev._current_file = prev_file
                ev._current_source = prev_source

            mod = ModuleValue(module_name, module_env, module_exports, ev)
            self._cache[path] = mod
            return mod
        finally:
            self._loading.pop()
