# -*- coding: utf-8 -*-
"""极快语言 · 编译前端编排（v0.5.0 · ADR-17 两遍分词）。

`compile_source` 把「分词 → 解析 → 静态诊断」串成一条链，并实现 ADR-06 X2
的两遍分词闭环：

    Pass1: tokenize(source) 用行文本启发式定位类块 → parse → AST
    判定: AST 不含 ClassDef，或 JIKUAI_LEGACY_ADR06=1 → 直接用 Pass1（单遍）
    Pass2: 从 Pass1 AST 提取权威 ClassRegionTable → tokenize(source, class_regions=表)
           → 比较两遍 token 序列
             收敛（结构等价）→ 复用 Pass1 AST（无需再 parse）
             未收敛        → emit JK-W9001，回退取 Pass1 AST（不崩）
    最后: check_program(ast, sink) 收集静态诊断（如副词透传 JK-W1001）

性能（Spike-ADR06 结论）：无条件两遍会使编译阶段 +87%，故这里以
「AST 无 ClassDef 即跳过 Pass2」把开销压到接近 0——绝大多数脚本不含类。

v0.13.0 W1 · 导入声明反哺 lexer 白名单（ADR-15 §3.7 / v0.12.0 复盘 §3.1）：
在 Pass1 之后追加一条独立的 Pass2 路径——扫顶层 `导入` → 静默解析目标 `.jk`
→ `pkg.blocks.extract_exports` 提取导出名 → 过滤出「源码里出现过 **且**
单独喂 lexer 时不是单 IDENT」的名字 → 作为 `external_defs` 注入重新分词并
**重新 parse**（token 序列变了不能复用 Pass1 AST）。

    与类区间路径的关键差别：白名单路径**不做收敛判定**。白名单命中的正常
    结果就是两遍 token 序列不同，按收敛判不过就回退等于把功能整个抵消。

回退开关：`JIKUAI_IMPORT_WHITELIST=off`（`JIKUAI_LEGACY_ADR06=1` 一并关闭）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .ast_nodes import ClassDef, Import, Node, Program
from .diagnostics import ListSink, codes
from .diagnostics.model import Diagnostic, Position, Span
from .diagnostics.sink import DiagnosticSink
from .diagnostics.static_check import check_program
from .lexer import tokenize
from .parser import parse


@dataclass
class CompileResult:
    """一次编译的产物。"""
    tokens: list
    ast: Program
    diagnostics: List[Diagnostic] = field(default_factory=list)
    two_pass: bool = False          # 是否真的走了第二遍
    converged: bool = True          # 两遍是否收敛（未走两遍时恒 True）


# ---------------------------------------------------------------------------
# 行偏移与类区间
# ---------------------------------------------------------------------------

def _line_offsets(source: str) -> List[int]:
    """返回每一行（0-based 行号）的起始字符偏移。

    与 `lexer._heuristic_class_regions` 使用同一算法，保证两遍分词的区间
    坐标系一致（掩码保持长度不变，故掩码源码与原始源码偏移相同）。
    """
    offsets = []
    p = 0
    for line in source.split('\n'):
        offsets.append(p)
        p += len(line) + 1
    return offsets


def _max_line_in_subtree(node: Node) -> int:
    """递归求一个 AST 子树中出现的最大行号（用于推导类块终点）。"""
    best = getattr(node, 'line', 0) or 0
    for value in vars(node).values():
        if isinstance(value, Node):
            best = max(best, _max_line_in_subtree(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Node):
                    best = max(best, _max_line_in_subtree(item))
                elif isinstance(item, tuple):
                    for sub in item:
                        if isinstance(sub, Node):
                            best = max(best, _max_line_in_subtree(sub))
                        elif isinstance(sub, list):
                            for n in sub:
                                if isinstance(n, Node):
                                    best = max(best, _max_line_in_subtree(n))
        elif isinstance(value, dict):
            for v in value.values():
                if isinstance(v, Node):
                    best = max(best, _max_line_in_subtree(v))
    return best


def _find_class_defs(program: Program) -> List[ClassDef]:
    """收集 AST 中所有 ClassDef（当前语言类只在顶层，稳妥起见仍递归）。"""
    found: List[ClassDef] = []

    def walk(node: Node) -> None:
        if isinstance(node, ClassDef):
            found.append(node)
        for value in vars(node).values():
            if isinstance(value, Node):
                walk(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, Node):
                        walk(item)
            elif isinstance(value, dict):
                for v in value.values():
                    if isinstance(v, Node):
                        walk(v)

    walk(program)
    return found


def build_class_region_table(
    program: Program, source: str
) -> List[Tuple[int, int]]:
    """从 Pass1 AST 提取权威类块字符区间 [(start_char, end_char), ...]。

    这是 ADR-06 X2 的核心：用 parser 的结构化结果替代 lexer 的行文本启发式。
    区间起点 = `类` 关键字所在行的行首偏移；终点 = ClassDef.end_line 那一行的行末
    （含该行内容长度），覆盖到类块结束的独立 `。` 行。

    若 ClassDef.line/end_line 为 0（parser 未标注行号），回退到源码文本搜索。
    """
    offsets = _line_offsets(source)
    lines = source.split('\n')
    total_lines = len(lines)
    regions: List[Tuple[int, int]] = []

    for cls in _find_class_defs(program):
        start_line = getattr(cls, 'line', 0) or 0    # 1-based
        end_line = getattr(cls, 'end_line', 0) or 0  # 1-based

        if start_line == 0:
            # parser 未标注行号：回退到文本搜索找到 `类 <name>` 所在行
            for idx, ln in enumerate(lines):
                if ln.lstrip().startswith('类') and cls.name in ln:
                    start_line = idx + 1
                    break
            if start_line == 0:
                continue

        if end_line == 0:
            end_line = start_line

        start_idx = start_line - 1
        end_idx = min(end_line - 1, total_lines - 1)
        if 0 <= start_idx < len(offsets) and 0 <= end_idx < total_lines:
            start_char = offsets[start_idx]
            end_char = offsets[end_idx] + len(lines[end_idx])
            regions.append((start_char, end_char))

    regions.sort()
    return regions


# ---------------------------------------------------------------------------
# token 结构等价比较
# ---------------------------------------------------------------------------

def _token_signature(tokens) -> List[tuple]:
    """token 序列的结构签名：只看 (type, value)，忽略位置，用于收敛判定。"""
    sig = []
    for t in tokens:
        ttype = getattr(t, 'type', None)
        tval = getattr(t, 'value', None)
        sig.append((ttype, tval))
    return sig


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _legacy_single_pass() -> bool:
    return os.environ.get('JIKUAI_LEGACY_ADR06') == '1'


def _import_whitelist_enabled() -> bool:
    """v0.13.0 W1 · 导入声明反哺白名单默认开启，off 强制关闭作为回退开关。"""
    return os.environ.get('JIKUAI_IMPORT_WHITELIST', 'on').lower() != 'off'


_INVALID_MODULE_CHARS = ('/', '\\', '..')

# v0.13.0 W1 性能缓存。反哺通路每次编译都要「解析模块路径 + 读 .jk + 正则提
# 取导出名 + 判定名字是否词法原子」，无缓存时含导入文件的编译开销实测 +138%。
# 三张表都是进程级、无上限（键空间被源码里的模块名/导出名天然限住）。
# 失效策略各不相同，见各自函数：
#   _RESOLVE_CACHE     只缓存命中；未命中不缓存（W4 修，见 _cached_resolve）
#   _EXPORTS_CACHE     按 (mtime_ns, size) 指纹校验
#   _NEEDS_HELP_CACHE  不需失效（原子性判定是 lexer 的纯函数）
_RESOLVE_CACHE: dict = {}       # (module, 调用方目录) -> path（只存命中）
_EXPORTS_CACHE: dict = {}       # path -> ((mtime_ns, size), frozenset[导出名])
_NEEDS_HELP_CACHE: dict = {}    # 名字 -> 是否需要白名单救（非单 IDENT）


def _cached_resolve(loader, module: str, file: Optional[str]) -> Optional[str]:
    """解析模块路径，**只缓存成功结果**。

    v0.13.0 W4：原实现把 `None` 也写进缓存，于是 LSP / REPL 这类长驻进程里
    「先写 `导入 甲`（此时 `甲.jk` 还不存在）→ 再新建 `甲.jk`」之后，导入仍然
    永久解析不到——负结果没有任何失效手段。文件的**出现**无法靠指纹校验发现
    （没有文件可 stat），所以正确做法是不缓存未命中。

    代价可控：未命中意味着这个 `导入` 拿不到导出名、跳过白名单反哺，本来就是
    少数路径（`蟒:` 模块、笔误、尚未创建的文件）；重解析的成本是若干次
    `os.path.isfile`，与 `_EXPORTS_CACHE` 省下的整文件读 + 正则相比可忽略。
    """
    key = (module, os.path.dirname(os.path.abspath(file)) if file else None)
    hit = _RESOLVE_CACHE.get(key)
    if hit is not None:
        return hit
    path = loader.try_resolve(module, current_file=file)
    if path is not None:
        _RESOLVE_CACHE[key] = path
    return path


def _cached_exports(path: str) -> frozenset:
    """读取模块导出名，按 (mtime, size) 指纹缓存。指纹取不到就当无导出。"""
    from .pkg.blocks import extract_exports
    try:
        st = os.stat(path)
        fingerprint = (st.st_mtime_ns, st.st_size)
    except OSError:
        return frozenset()
    hit = _EXPORTS_CACHE.get(path)
    if hit is not None and hit[0] == fingerprint:
        return hit[1]
    try:
        exports = frozenset(extract_exports(path))
    except Exception:
        exports = frozenset()
    _EXPORTS_CACHE[path] = (fingerprint, exports)
    return exports


def _needs_whitelist_help(name: str) -> bool:
    """这个名字单独喂给 lexer 时能否成为单个 IDENT？不能才需要白名单救。

    过滤掉「本来就原子」的名字有两重收益：
      1. 性能——生产库 52 个块的导出名全是原子的，过滤后白名单为空、
         直接跳过 Pass2，含导入文件回到零开销。
      2. 安全——把原子名塞进白名单反而可能在别处切碎更长的标识符
         （源码里的 `汇总额` 会被 `汇总` 抢先命中成 `汇总`+`额`）。
    """
    hit = _NEEDS_HELP_CACHE.get(name)
    if hit is None:
        from .pkg.blocks import check_export_atomicity
        try:
            atomic, _ = check_export_atomicity(name)
        except Exception:
            atomic = True                       # 判不了就别乱注入
        hit = not atomic
        _NEEDS_HELP_CACHE[name] = hit
    return hit


def _collect_imports(program: Program) -> List[Import]:
    """收集顶层 `导入` 节点。

    `导入` 在语法上只能作为顶层语句出现（`parser._parse_import` /
    `_parse_from_import` 都只挂在语句派发上），所以扫 `program.body` 就够——
    不做 `vars(node)` 全量递归遍历，那是每次编译一次额外的整树走查。
    """
    return [s for s in getattr(program, 'body', ()) if isinstance(s, Import)]



def _collect_import_whitelist(
    program: Program, source: str, file: Optional[str]
) -> frozenset:
    """v0.13.0 W1 · 扫描 Pass1 AST 的所有 `导入`，静默提取被导入模块的
    `导出` 名，返回**需要反哺**的白名单集合。

    只保留同时满足两条的名字：
      1. 在调用方源码里出现过（没出现的名字注入了也无意义）
      2. 单独喂 lexer 时不是单个 IDENT（原子名不需要救，注入反而有害）

    失败原则：任何单个 Import 解析 / 读文件 / 提取失败 → 跳过该项，不抛错。
    动态模块名 / 蟒:xxx / 空模块名 / 含路径分隔符 → 跳过。
    返回空集合 → 上层无需触发 Pass2。
    """
    imports = _collect_imports(program)
    if not imports:
        return frozenset()

    from .module_loader import ModuleLoader      # 延迟导入避环

    loader = ModuleLoader(evaluator=None)
    names: set = set()
    for imp in imports:
        if getattr(imp, 'kind', 'jk') != 'jk':
            continue
        mod = getattr(imp, 'module', None) or ''
        if not mod or mod.startswith('.') \
                or any(bad in mod for bad in _INVALID_MODULE_CHARS):
            continue
        path = _cached_resolve(loader, mod, file)
        if not path:
            continue
        # `从 X 导入 A B` 也取模块的全部导出：白名单只影响调用方分词、不影响
        # 运行时可见性，超集安全；下面的两道过滤会把无关名字筛掉。
        for name in _cached_exports(path):
            if name in source and _needs_whitelist_help(name):
                names.add(name)
    return frozenset(names)



def _emit_w9001(sink: DiagnosticSink, file: Optional[str], note: str) -> None:
    """发出 JK-W9001（两遍分词未收敛兜底警告）。"""
    meta = codes.CODE_TABLE[codes.JK_W9001]
    pos = Position(1, 1)
    sink.emit(Diagnostic(
        code=codes.JK_W9001,
        severity=meta.severity,
        category=meta.category,
        message=meta.template,
        span=Span(start=pos, end=pos, file=file),
        notes=(note,),
    ))


def parse_with_import_whitelist(source: str, file: Optional[str] = None):
    """分词 + 解析，带「导入声明反哺白名单」，但**不做**静态诊断与类区间两遍。

    供 `module_loader.load()` 加载模块体时使用——L2 块聚合 L1 块时，被加载
    的模块自己也 `导入` 别的块，同样需要反哺才不会把导出名切碎。返回
    `(tokens, ast)`。反哺后二次解析失败则保守回退首遍结果。
    """
    tokens = tokenize(source)
    ast = parse(tokens)
    if not _import_whitelist_enabled() or _legacy_single_pass():
        return tokens, ast
    whitelist = _collect_import_whitelist(ast, source, file)
    if not whitelist:
        return tokens, ast
    tokens2 = tokenize(source, external_defs=whitelist)
    if _token_signature(tokens) == _token_signature(tokens2):
        return tokens2, ast
    try:
        return tokens2, parse(tokens2)
    except Exception:
        return tokens, ast


def compile_source(
    source: str,
    file: Optional[str] = None,
    sink: Optional[DiagnosticSink] = None,
) -> CompileResult:
    """两遍分词编排 + 静态诊断。返回 CompileResult。

    sink 为 None 时内部用 ListSink 收集，并把 drain 结果填入 CompileResult。
    传入外部 sink 时诊断直接 emit 给它，CompileResult.diagnostics 为空列表
    （由调用方自行 drain）。
    """
    own_sink = sink is None
    active_sink: DiagnosticSink = ListSink() if own_sink else sink

    # ---- Pass 1 ----
    tokens1 = tokenize(source)
    ast1 = parse(tokens1)

    two_pass = False
    converged = True
    final_tokens = tokens1
    final_ast = ast1

    class_defs = _find_class_defs(ast1)

    # v0.13.0 W1：导入声明反哺 lexer 白名单。扫描 Pass1 AST 的 `导入`，
    # 静默提取被导入模块的导出名，Pass2 注入 external_defs 防止调用方源码
    # 里引用这些名字时被免空格分词器切碎。
    import_whitelist = frozenset()
    if _import_whitelist_enabled() and not _legacy_single_pass():
        import_whitelist = _collect_import_whitelist(ast1, source, file)

    if import_whitelist:
        # ---- Pass 2 · 白名单路径 ----
        # 这条路**不做收敛判定**：白名单命中的正常结果就是「两遍 token 序列
        # 不同」（原本被切碎的导出名聚合成整体 IDENT），照类区间那套逻辑判
        # 不收敛就回退，等于把本功能整个抵消。Pass2 严格更正确，直接采纳。
        # 代价是 token 序列变了必须**重新 parse**——不能复用 Pass1 AST。
        two_pass = True
        table = build_class_region_table(ast1, source) if class_defs else None
        tokens2 = tokenize(source, external_defs=import_whitelist,
                           class_regions=table)
        if _token_signature(tokens1) == _token_signature(tokens2):
            final_tokens = tokens2          # 无名字被切碎，AST 等价，复用
        else:
            try:
                final_ast = parse(tokens2)
                final_tokens = tokens2
            except Exception:
                # 反哺白名单反而让 parse 崩了：保守回退 Pass1，发 JK-W9001。
                converged = False
                _emit_w9001(
                    active_sink, file,
                    "导入声明反哺白名单后二次解析失败，已回退首遍结果；"
                    "如遇异常可设 JIKUAI_IMPORT_WHITELIST=off 关闭反哺。")
    elif class_defs and not _legacy_single_pass():
        # ---- Pass 2 · 类区间路径（v0.5.0 ADR-06 X2，行为不变）----
        two_pass = True
        table = build_class_region_table(ast1, source)
        tokens2 = tokenize(source, class_regions=table)
        if _token_signature(tokens1) == _token_signature(tokens2):
            converged = True
            final_tokens = tokens2
        else:
            # 未收敛：发 JK-W9001 兜底，取 Pass1 结果继续（不崩）
            converged = False
            final_tokens = tokens1
            _emit_w9001(
                active_sink, file,
                "两遍分词的类块边界判定不一致，已回退首遍结果；"
                "如遇异常可设 JIKUAI_LEGACY_ADR06=1 强制单遍。")

    # ---- 静态诊断 ----
    check_program(final_ast, active_sink, file=file)

    diagnostics: List[Diagnostic] = []
    if own_sink and isinstance(active_sink, ListSink):
        diagnostics = active_sink.drain()

    return CompileResult(
        tokens=final_tokens,
        ast=final_ast,
        diagnostics=diagnostics,
        two_pass=two_pass,
        converged=converged,
    )



def diagnose(source: str, file: Optional[str] = None) -> List[Diagnostic]:
    """便捷函数：编译并返回稳定排序后的诊断列表（不执行）。"""
    return compile_source(source, file=file).diagnostics
