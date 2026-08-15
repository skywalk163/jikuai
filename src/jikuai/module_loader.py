# -*- coding: utf-8 -*-
"""极快语言 - 模块加载器 (M2-1)。

职责：
- 按优先级搜索 .jk 模块：当前文件目录 → 极快_包/（M8 包管理） →
  stdlib/ → JIKUAI_PATH（环境变量）
- 在独立的模块级 Environment 中执行 .jk 文件
- 只有 `导出` 的名字对外可见
- 相同绝对路径的模块只加载执行一次（缓存）
- 循环导入检测：加载栈中出现重复则抛 JiKuaiError（含"循环导入"字样）
- 拒绝含路径分隔符 / `..` 的模块名
"""

import os
from typing import List, Optional


#: 已安装依赖目录名。与 `jikuai.pkg.installer.PACKAGES_DIR` 保持一致；
#: 这里用字面量而不是 import，避免核心加载路径依赖包管理子包。
PACKAGES_DIR = '极快_包'

#: 包清单文件名，与 `jikuai.pkg.manifest.MANIFEST_NAME` 一致。
PKG_MANIFEST = '包.json'

#: 项目根查找结果缓存：起始目录 -> 项目根（或 None）。
#: 每次 `导入` 都向上走文件系统开销可观，而项目根在一次运行内不会变。
_PROJECT_ROOT_CACHE = {}



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
        # M8 包管理：把 `导入 甲` 解析到项目根的 极快_包/甲.jk。
        # 优先级排在脚本同目录之后、stdlib 之前——本地脚本可覆盖依赖，
        # 而依赖不能遮蔽内置标准库（stdlib 兜底在最后）。
        pkg_dir = self._packages_dir(current_file)
        if pkg_dir:
            paths.append(pkg_dir)
            # ADR-32 §2.3 执行侧：把已装块包的块根「父目录」加进搜索路径，
            # 让 `从 blocks.<命名空间>.<领域>.<块> 导入 ...` 能解析到第三方
            # 块包携带的块。块根语义是「blocks/ 那一级」，而 dotpath 解析要的
            # 是它的父目录（`<父>/blocks/...`）。挂在 stdlib 之前——第三方块
            # 可被内置块遮蔽，与发现侧「内置优先」一致。
            for parent in self._block_root_parents(pkg_dir):
                paths.append(parent)
        # W115（v0.24.0 · ADR-39）：stdlib 是包内资源，定位收敛到 resources。
        from . import resources
        paths.append(resources.stdlib_dir())
        env_path = os.environ.get('JIKUAI_PATH', '')
        if env_path:
            for p in env_path.split(os.pathsep):
                if p:
                    paths.append(p)
        return paths

    #: 已装块根索引文件名。与 `jikuai.pkg.installer.BLOCK_ROOTS_INDEX` 一致；
    #: 这里用字面量而非 import，避免核心加载路径依赖包管理子包（同 PACKAGES_DIR）。
    _BLOCK_ROOTS_INDEX = '.块根.json'
    _BLOCK_ROOTS_INDEX_VERSION = 1

    def _block_root_parents(self, pkg_dir):
        """读 `极快_包/.块根.json`，返回每个块根的**父目录**（供 dotpath 解析）。

        直接读文件、不 import pkg——与 `PACKAGES_DIR` 字面量同理，核心加载
        路径不该拉起包管理子包（含 git 的 sources）。文件缺失 / 版本不符
        返回空。

        **W86 安全审计（v0.21.0）**：`路径` 字段必须是相对路径、无 `..`
        段、归一后仍落在 `pkg_dir` 之内。同 `pkg.installer.安全块根路径`
        的规则（不 import 避免依赖倒置）。索引里越界的条目静默跳过——不
        因为一个可选索引挡住 `导入`，与既有版本不符处理同强度。
        """
        import json
        index_path = os.path.join(pkg_dir, self._BLOCK_ROOTS_INDEX)
        if not os.path.isfile(index_path):
            return []
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        if (not isinstance(data, dict)
                or data.get('索引版本') != self._BLOCK_ROOTS_INDEX_VERSION):
            return []
        base_abs = os.path.abspath(pkg_dir)
        parents = []
        seen = set()
        for 条 in data.get('块根') or []:
            if not isinstance(条, dict):
                continue
            rel = 条.get('路径')
            if not isinstance(rel, str) or not rel:
                continue
            unified = rel.replace('\\', '/')
            if (unified.startswith('/')
                    or os.path.isabs(rel) or os.path.isabs(unified)):
                continue
            if any(seg == '..' for seg in unified.split('/')):
                continue
            块根 = os.path.abspath(os.path.join(base_abs, *[
                seg for seg in unified.split('/') if seg not in ('', '.')]))
            try:
                common = os.path.commonpath([base_abs, 块根])
            except ValueError:
                continue
            if common != base_abs or 块根 == base_abs:
                continue
            parent = os.path.dirname(块根)
            if parent in seen:
                continue
            seen.add(parent)
            if os.path.isdir(parent):
                parents.append(parent)
        return parents

    def _packages_dir(self, current_file):
        """从当前文件所在目录向上找 `包.json`，返回其同级 `极快_包/`。

        没有清单或没有依赖目录时返回 None——包管理是可选的，纯脚本
        用户不装依赖也不该受影响。项目根按 (起点) 缓存，避免每次
        `导入` 都爬一遍文件系统。
        """
        start = (os.path.dirname(os.path.abspath(current_file))
                 if current_file else os.getcwd())
        root = _PROJECT_ROOT_CACHE.get(start, ...)
        if root is ...:
            root = self._find_project_root(start)
            _PROJECT_ROOT_CACHE[start] = root
        if root is None:
            return None
        pkg_dir = os.path.join(root, PACKAGES_DIR)
        return pkg_dir if os.path.isdir(pkg_dir) else None

    @staticmethod
    def _find_project_root(start):
        """向上逐级找含 `包.json` 的目录，返回其路径；找不到返回 None。"""
        here = os.path.abspath(start)
        while True:
            if os.path.isfile(os.path.join(here, PKG_MANIFEST)):
                return here
            parent = os.path.dirname(here)
            if parent == here:
                return None
            here = parent

    @staticmethod
    def _dotpath_to_parts(module_name):
        """将点分模块名拆为路径片段列表。

        仅当模块名含 `.` 时才视为点分路径；否则返回 None 表示扁平模块名。
        """
        if '.' not in module_name:
            return None
        return module_name.split('.')

    def _resolve_dotpath(self, parts, current_file):
        """按三级优先级解析点分路径模块。

        优先级（每个搜索路径下依次尝试）：
        1. <dir>/a/b/c.jk          —— 扁平文件
        2. <dir>/a/b/c/c.jk        —— 同名主文件的目录形式
        3. <dir>/a/b/c/main.jk     —— 约定入口（兜底）
        """
        rel_dir = os.path.join(*parts[:-1]) if len(parts) > 1 else ''
        leaf = parts[-1]
        for d in self._search_paths(current_file):
            base = os.path.join(d, rel_dir) if rel_dir else d
            # 策略 1：扁平文件
            candidate = os.path.join(base, leaf + '.jk')
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
            # 策略 2：同名主文件（目录形式）
            candidate = os.path.join(base, leaf, leaf + '.jk')
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
            # 策略 3：main.jk 兜底
            candidate = os.path.join(base, leaf, 'main.jk')
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
        return None

    def try_resolve(self, module_name, current_file=None):
        """`resolve()` 的静默变体：找不到 / 非法名字返回 None，不抛错。

        供 v0.13.0 W1「导入声明反哺 lexer 白名单」在 Pass1 之后的静态
        扫描使用——一个坏的 `导入` 不能让整次分词 fatal，运行时的
        `evaluator._eval_Import` 仍会调用 `resolve()` 精确报错。
        """
        try:
            return self.resolve(module_name, current_file)
        except Exception:
            return None

    def resolve(self, module_name, current_file=None):
        """将模块名解析为绝对路径。找不到则抛错。

        支持两种形式：
        - 扁平模块名（不含 `.`）：沿用既有逻辑
        - 点分路径（如 `blocks.数据.读取文件`）：按 ADR-15 块生态规则解析
        """
        from .evaluator import JiKuaiError
        from .diagnostics import codes
        # 安全检查：拒绝路径分隔符和 ..（双点）以及以 . 开头的模块名
        if ('/' in module_name or '\\' in module_name
                or '..' in module_name or module_name.startswith('.')):
            raise JiKuaiError(f"非法模块名（含路径分隔符或 ..）：{module_name}")

        # 点分路径解析（ADR-15 块生态）
        parts = self._dotpath_to_parts(module_name)
        if parts is not None:
            result = self._resolve_dotpath(parts, current_file)
            if result:
                return result
            raise JiKuaiError(f"[{codes.JK_E5001}] 找不到模块：{module_name}")

        # 扁平模块名：既有逻辑不变
        for d in self._search_paths(current_file):
            path = os.path.join(d, module_name + '.jk')
            if os.path.isfile(path):
                return os.path.abspath(path)

        # M8：已安装的包是**目录**（极快_包/甲/），入口由它自己的
        # 包.json「入口」字段决定（缺省 main.jk）。放在扁平文件查找之后，
        # 保证同名单文件模块仍然优先——升级到包管理不会改变既有行为。
        pkg_entry = self._resolve_package_entry(module_name, current_file)
        if pkg_entry:
            return pkg_entry

        raise JiKuaiError(f"[{codes.JK_E5001}] 找不到模块：{module_name}")

    def _resolve_package_entry(self, module_name, current_file):
        """把包名解析为 `极快_包/<包名>/<入口>` 的绝对路径。

        入口取自该包的 `包.json`；清单缺失或不可读时退回 `main.jk`——
        依赖目录被手工改坏不该让整个 `导入` 报出 JSON 解析错误，
        照常按约定入口试一次，真找不到再报「找不到模块」。
        """
        pkg_dir = self._packages_dir(current_file)
        if not pkg_dir:
            return None
        root = os.path.join(pkg_dir, module_name)
        if not os.path.isdir(root):
            return None

        entry = 'main.jk'
        manifest_path = os.path.join(root, PKG_MANIFEST)
        if os.path.isfile(manifest_path):
            try:
                import json
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    entry = json.load(f).get('入口') or entry
            except (OSError, ValueError, AttributeError):
                pass
        # 入口只允许是包内相对路径，禁止靠 `..` 逃出包目录
        candidate = os.path.normpath(os.path.join(root, entry))
        if os.path.commonpath([os.path.abspath(root),
                               os.path.abspath(candidate)]) != os.path.abspath(root):
            return None
        return os.path.abspath(candidate) if os.path.isfile(candidate) else None

    def load(self, module_name, current_file=None):
        """加载模块并返回 ModuleValue。"""
        from .evaluator import JiKuaiError, Environment
        from .frontend import parse_with_import_whitelist


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
            # v0.13.0 W1：模块体也可能 `导入` 别的块（L2 聚合 L1），同样反哺
            # 白名单以免把被依赖块的导出名切碎。
            tokens, ast = parse_with_import_whitelist(source, file=path)
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
