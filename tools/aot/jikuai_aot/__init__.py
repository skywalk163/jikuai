# -*- coding: utf-8 -*-
"""极快 AOT 跨端编译试验（M6-P4 · Experimental / Could 级 · ADR-19）。

**重要：这是 v0.7.0 支线的实验性产物，不进入 v0.7.0 兼容承诺。**
对外一律标注 Experimental；CLI 参数与产物格式可能在后续版本变更。

对外表述（遵守交付总监 D-07 第 4 条）：
    - 走 C 编译器路径 → 「原生二进制编译」
    - 走 PyInstaller / Nuitka 路径 → 「打包分发」，不得称「原生二进制编译」

物理隔离：
    - 主包 jikuai **不依赖** jikuai_aot
    - 主包 533 用例在不安装 AOT 相关额外依赖时必须全绿
"""

from .subset_gate import (
    SUPPORTED_VERBS,
    UNSUPPORTED_NODE_TYPES,
    check,
    describe_subset,
    is_supported,
    unsupported_reasons,
)
from .codegen import generate_c, CodegenError
from .driver import (
    BuildOptions,
    BuildResult,
    build,
    detect_c_compiler,
    main,
)

__version__ = "0.7.0.dev0+aot"
__experimental__ = True

__all__ = [
    "SUPPORTED_VERBS",
    "UNSUPPORTED_NODE_TYPES",
    "check",
    "describe_subset",
    "is_supported",
    "unsupported_reasons",
    "generate_c",
    "CodegenError",
    "BuildOptions",
    "BuildResult",
    "build",
    "detect_c_compiler",
    "main",
    "__version__",
    "__experimental__",
]