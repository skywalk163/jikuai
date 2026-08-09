# -*- coding: utf-8 -*-
"""极快 AOT · 驱动器（T-M6-A03 · ADR-19）。

命令行：

    python -m jikuai_aot <文件.jk> [-o 输出路径] [--emit-c] [--keep-temp]

流程（顺序不可调换）：

    读源 → compile_source → 前端错误级诊断拦截
         → subset_gate.check ── 不通过 ──▶ 打印 JK-E7001 + exit(1)，**零产物**
         → codegen.generate_c
         → --emit-c ? 落 .c : 调 C 编译器 → 校验 → 落二进制

「禁止静默产出错误产物」（AC-M6-06-03）的落地方式有两层：
    1. 门禁不通过时，**在创建任何临时目录之前**就返回，物理上不可能有产物；
    2. 通过后也先写临时目录、校验非空、再原子性拷到目标路径，避免半成品。

对外表述（D-07 第 4 条）：本驱动只有 C 编译器一条后端。若后续改走
PyInstaller / Nuitka，产物只得称「打包分发」，不得称「原生二进制编译」。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from jikuai.diagnostics import ListSink
from jikuai.diagnostics.model import Diagnostic
from jikuai.frontend import compile_source

from . import codegen, subset_gate

#: 退出码约定。0 成功；其余非 0，且**保证没有产物落盘**。
EXIT_OK = 0
EXIT_SUBSET = 1        # 命中 JK-E7001，超出受支持子集
EXIT_FRONTEND = 1      # 前端（词法/语法）错误
EXIT_USAGE = 2         # 文件读不到 / 编码不对
EXIT_TOOLCHAIN = 3     # 缺 C 编译器，且没加 --emit-c
EXIT_BUILD = 4         # C 编译器返回非 0

#: 探测顺序。`cl` 放最后：MSVC 需要 vcvars 环境，命中率最低。
_CC_CANDIDATES = ("gcc", "clang", "cc", "cl")


@dataclass
class BuildOptions:
    """一次构建的输入参数。"""
    source_file: str
    output_path: Optional[str] = None
    emit_c: bool = False
    keep_temp: bool = False
    cc: Optional[str] = None          # 手动指定编译器；None = 自动探测


@dataclass
class BuildResult:
    """一次构建的结构化结果。测试直接断言这些字段，不去 parse 文本。"""
    exit_code: int
    c_source: Optional[str] = None
    c_path: Optional[str] = None
    binary_path: Optional[str] = None
    compiler: Optional[str] = None
    diagnostics: Tuple[Diagnostic, ...] = ()
    message: str = ""
    stdout_text: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_OK


# ---------------------------------------------------------------------------
# C 工具链探测
# ---------------------------------------------------------------------------

def detect_c_compiler(prefer: Optional[str] = None) -> Optional[str]:
    """探测可用的 C 编译器，返回可执行文件绝对路径；找不到返回 None。

    优先级：显式 `prefer` → 环境变量 `CC` → gcc / clang / cc / cl。
    """
    candidates: List[str] = []
    if prefer:
        candidates.append(prefer)
    env_cc = os.environ.get("CC", "").strip()
    if env_cc:
        candidates.append(env_cc)
    candidates.extend(_CC_CANDIDATES)

    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    return None


def _is_msvc(cc: str) -> bool:
    """区分 MSVC 的 cl.exe 与 clang（名字前缀会撞车）。"""
    base = os.path.basename(cc).lower()
    return base.startswith("cl") and not base.startswith("clang")


def compile_c(c_path: str, out_path: str, cc: str) -> Tuple[int, str]:
    """调用 C 编译器。返回 (返回码, 合并后的编译器输出)。

    gcc/clang 用 `-std=c11 -O2 ... -lm`；MSVC 用 `/nologo /utf-8`。
    """
    if _is_msvc(cc):
        cmd: Sequence[str] = [cc, "/nologo", "/utf-8", c_path,
                              "/Fe:" + out_path]
    else:
        cmd = [cc, "-std=c11", "-O2", c_path, "-o", out_path, "-lm"]

    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.path.dirname(os.path.abspath(c_path)) or None,
        )
    except OSError as exc:
        return 127, "无法启动 C 编译器 {}：{}".format(cc, exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ---------------------------------------------------------------------------
# 诊断渲染
# ---------------------------------------------------------------------------

def render_diagnostics(diags: Sequence[Diagnostic]) -> str:
    """渲染诊断为「文件:行:列: 码 级别: 消息」+ 缩进说明。

    有意不复用 `diagnostics.reporter`：AOT 是实验性旁挂工具，保持自己的
    极简渲染可以避免把主包的输出格式变成 AOT 的隐式契约。
    """
    lines: List[str] = []
    for d in diags:
        pos = d.span.start
        where = "{}:{}:{}".format(d.span.file or "<输入>", pos.line, pos.column)
        lines.append("{}: {} {}: {}".format(where, d.code, d.severity, d.message))
        for note in d.notes:
            lines.append("    说明：{}".format(note))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build(opts: BuildOptions) -> BuildResult:
    """执行一次构建。**不调用 sys.exit**，便于测试直接驱动。"""
    # ---- 1. 读源 ----
    try:
        with open(opts.source_file, "r", encoding="utf-8") as fh:
            source = fh.read()
    except FileNotFoundError:
        return BuildResult(EXIT_USAGE,
                           message="错误：找不到文件 {}".format(opts.source_file))
    except IsADirectoryError:
        return BuildResult(EXIT_USAGE,
                           message="错误：{} 是目录".format(opts.source_file))
    except UnicodeDecodeError:
        return BuildResult(EXIT_USAGE,
                           message="错误：文件编码不是 UTF-8：{}".format(opts.source_file))

    abs_source = os.path.abspath(opts.source_file)

    # ---- 2. 前端编译 ----
    front = compile_source(source, file=abs_source)
    fatal = [d for d in front.diagnostics if d.severity == "错误"]
    if fatal:
        return BuildResult(EXIT_FRONTEND,
                           diagnostics=tuple(fatal),
                           message=render_diagnostics(fatal))

    # ---- 3. 子集门禁（不可降级项）----
    sink = ListSink()
    passed = subset_gate.check(front.ast, sink, file=abs_source)
    diags = tuple(sink.drain())
    if not passed:
        # 关键：此刻还没创建过任何文件或临时目录 → 物理上零产物
        return BuildResult(
            EXIT_SUBSET,
            diagnostics=diags,
            message=render_diagnostics(diags)
                    + "\n\nAOT 是实验性功能，受支持子集见 docs/AOT.md；未产出任何文件。",
        )

    # ---- 4. C codegen ----
    try:
        c_source = codegen.generate_c(front.ast)
    except codegen.CodegenError as exc:
        return BuildResult(EXIT_SUBSET,
                           diagnostics=diags,
                           message="错误：C 代码生成失败：{}".format(exc))

    # ---- 5. --emit-c：不需要编译器就能验证整条链路 ----
    if opts.emit_c:
        if opts.output_path is None:
            # 默认写到标准输出，避免在用户源码树里悄悄多出 .c 文件
            return BuildResult(EXIT_OK, c_source=c_source,
                               diagnostics=diags, stdout_text=c_source)
        try:
            _atomic_write_text(opts.output_path, c_source)
        except OSError as exc:
            return BuildResult(EXIT_BUILD,
                               message="错误：写出 C 中间码失败：{}".format(exc))
        return BuildResult(EXIT_OK, c_source=c_source, c_path=opts.output_path,
                           diagnostics=diags,
                           message="已生成 C 中间码：{}".format(opts.output_path))

    # ---- 6. 调 C 编译器 ----
    cc = detect_c_compiler(opts.cc)
    if cc is None:
        return BuildResult(
            EXIT_TOOLCHAIN,
            c_source=c_source,
            diagnostics=diags,
            message=("错误：未检测到 C 编译器（已尝试 {}）。\n"
                     "      可先用 --emit-c 拿到 C 中间码，或安装 MinGW-w64 / MSVC "
                     "后重试。".format(" / ".join(_CC_CANDIDATES))),
        )

    tmp_dir = tempfile.mkdtemp(prefix="jikuai_aot_")
    try:
        tmp_c = os.path.join(tmp_dir, "program.c")
        with open(tmp_c, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(c_source)

        exe_name = "program.exe" if os.name == "nt" else "program"
        tmp_exe = os.path.join(tmp_dir, exe_name)

        rc, cc_output = compile_c(tmp_c, tmp_exe, cc)
        if rc != 0:
            return BuildResult(EXIT_BUILD, c_source=c_source, compiler=cc,
                               diagnostics=diags,
                               message="错误：C 编译器 {} 返回 {}：\n{}".format(cc, rc, cc_output))

        # 产物校验：存在且非空，才允许落盘（避免半成品）
        if not os.path.isfile(tmp_exe) or os.path.getsize(tmp_exe) == 0:
            return BuildResult(EXIT_BUILD, c_source=c_source, compiler=cc,
                               diagnostics=diags,
                               message="错误：C 编译器未产出有效可执行文件")

        out_bin = opts.output_path or _default_binary_path(opts.source_file)
        _atomic_copy(tmp_exe, out_bin)

        c_path = None
        if opts.keep_temp:
            c_path = _default_c_path(opts.source_file)
            _atomic_write_text(c_path, c_source)

        return BuildResult(
            EXIT_OK, c_source=c_source, c_path=c_path, binary_path=out_bin,
            compiler=cc, diagnostics=diags,
            message="已产出二进制：{}（{} 字节，编译器 {}）".format(
                out_bin, os.path.getsize(out_bin), cc),
        )
    finally:
        if not opts.keep_temp:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 落盘工具
# ---------------------------------------------------------------------------

def _default_binary_path(source_file: str) -> str:
    base, _ = os.path.splitext(source_file)
    return base + ".exe" if os.name == "nt" else base


def _default_c_path(source_file: str) -> str:
    base, _ = os.path.splitext(source_file)
    return base + ".c"


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


def _atomic_write_text(path: str, text: str) -> None:
    """先写同目录临时文件再 replace，避免中断时留下截断的半成品。"""
    _ensure_parent(path)
    parent = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".jkaot_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _atomic_copy(src: str, dst: str) -> None:
    """同上，但用于二进制产物。

    坑：`shutil.copyfile` 只搬内容不搬权限；而 `tempfile.mkstemp` 在 POSIX 上
    以 mode 0600 建文件——`os.replace` 之后目标文件缺**可执行位**，Linux
    上直接 `PermissionError [Errno 13]`。Windows 靠扩展名判断可执行性所以
    没暴露这个坑，但 CI 是 Ubuntu，之前 T2a 的 8 条 e2e 用例全部 Errno 13
    死在这里。修法是让临时文件继承源产物（gcc 已带 0755）的 mode。
    """
    _ensure_parent(dst)
    parent = os.path.dirname(os.path.abspath(dst))
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".jkaot_", suffix=".tmp")
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        shutil.copymode(src, tmp)   # 关键：把源的可执行位传下去
        os.replace(tmp, dst)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m jikuai_aot",
        description="极快 AOT（实验性 / Experimental）："
                    "把受支持子集的 .jk 编译为 C 中间码或原生二进制。",
        epilog="警告：实验性功能，不保证生产稳定性，CLI 与产物格式可能变更。",
    )
    parser.add_argument("source", help="极快源文件（.jk）")
    parser.add_argument("-o", "--output", default=None,
                        help="输出路径。--emit-c 时为 .c 路径（缺省打到标准输出）；"
                             "否则为可执行文件路径（缺省与源文件同名）")
    parser.add_argument("--emit-c", action="store_true",
                        help="只生成 C 中间码，不调用 C 编译器")
    parser.add_argument("--keep-temp", action="store_true",
                        help="保留中间产物（.c），便于排障")
    parser.add_argument("--cc", default=None,
                        help="指定 C 编译器（缺省读 CC 环境变量，再按 "
                             "gcc/clang/cc/cl 探测）")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口。返回值即退出码。"""
    args = build_arg_parser().parse_args(argv)

    result = build(BuildOptions(
        source_file=args.source,
        output_path=args.output,
        emit_c=args.emit_c,
        keep_temp=args.keep_temp,
        cc=args.cc,
    ))

    if result.stdout_text:
        sys.stdout.write(result.stdout_text)
        if not result.stdout_text.endswith("\n"):
            sys.stdout.write("\n")

    if result.message:
        stream = sys.stdout if result.ok else sys.stderr
        print(result.message, file=stream)

    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())