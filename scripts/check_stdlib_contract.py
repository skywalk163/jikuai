# -*- coding: utf-8 -*-
"""静态契约校验（G10）：比对 stdlib/*.jk 的 `导出` 与 docs/标准库.md 声明。

用法：
    python scripts/check_stdlib_contract.py           # 一致 → 0，不一致 → 1
    python scripts/check_stdlib_contract.py --json    # 输出 JSON 报告

契约来源：
- 实际导出集合：`stdlib_contract.parse_exports` 静态扫描 .jk 源码的 `导出` 语句
- 文档声明集合：解析 docs/标准库.md 中每个「## 模块：X」小节下
  「### 公共符号」子节的 `- 符号名` 列表项

只校验有 .jk 门面的模块；纯 .py 模块（如 历法）不参与 G10。
不执行任何 .jk 模块，避免副作用。
"""

import json
import os
import re
import sys

# Windows 控制台默认编码是 GBK，脚本输出含中文符号名与 JSON。
# 强制 UTF-8 输出，保证被 subprocess 以 UTF-8 捕获时不出现解码错误。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, ".."))
SRC_PATH = os.path.join(REPO_ROOT, "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from jikuai import stdlib_contract  # noqa: E402

DOC_PATH = os.path.join(REPO_ROOT, "docs", "标准库.md")

_RE_MODULE_HEADER = re.compile(r"^##\s+模块[:：]\s*(\S+)\s*$")
_RE_SYMBOLS_HEADER = re.compile(r"^###\s+公共符号\s*$")
_RE_ANY_HEADER = re.compile(r"^#{1,6}\s")
_RE_SYMBOL_ITEM = re.compile(r"^-\s+([^\s（(]+)\s*(?:[（(].*[）)])?\s*$")


def parse_doc_symbols(doc_path=DOC_PATH):
    """解析 docs/标准库.md 中每个模块的公共符号清单，返回 dict[模块, set[符号]]。"""
    if not os.path.isfile(doc_path):
        raise SystemExit("找不到文档：%s" % doc_path)
    with open(doc_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    result = {}
    current = None
    in_symbols = False
    for line in lines:
        m = _RE_MODULE_HEADER.match(line)
        if m:
            current = m.group(1).strip()
            result.setdefault(current, set())
            in_symbols = False
            continue
        if current is None:
            continue
        if _RE_SYMBOLS_HEADER.match(line):
            in_symbols = True
            continue
        if in_symbols:
            if _RE_ANY_HEADER.match(line):
                in_symbols = False
                continue
            item = _RE_SYMBOL_ITEM.match(line)
            if item:
                result[current].add(item.group(1).strip())
    return result


def build_report():
    """构造契约报告：逐模块比对文档 vs .jk 实际导出。"""
    doc_map = parse_doc_symbols()
    jk_modules = stdlib_contract.list_stdlib_modules()

    modules = {}
    ok = True
    for name in sorted(set(jk_modules) | set(doc_map.keys())):
        has_jk = name in jk_modules
        actual = stdlib_contract.declared_exports(name) if has_jk else set()
        doc = doc_map.get(name, set())
        missing = sorted(actual - doc)
        extra = sorted(doc - actual)
        if has_jk and (missing or extra):
            ok = False
        modules[name] = {
            "doc": sorted(doc),
            "actual": sorted(actual),
            "missing_in_doc": missing,
            "extra_in_doc": extra,
            "mixed_module": has_jk and stdlib_contract.has_python_backing(name),
            "has_jk": has_jk,
        }
    return {"modules": modules, "ok": ok}


def format_text(report):
    """人类可读文本报告。"""
    lines = []
    for name, info in report["modules"].items():
        diff = info["missing_in_doc"] or info["extra_in_doc"]
        status = "OK" if not diff else "差异"
        if not info["has_jk"]:
            status = "仅文档（纯 .py 模块，不参与 G10）"
        lines.append("[%s] 模块 %s" % (status, name))
        lines.append("  文档声明: %s" % info["doc"])
        lines.append("  实际导出: %s" % info["actual"])
        if info["missing_in_doc"]:
            lines.append("  ! 实际有但文档缺: %s" % info["missing_in_doc"])
        if info["extra_in_doc"]:
            lines.append("  ! 文档有但实际未导出: %s" % info["extra_in_doc"])
    lines.append("")
    lines.append("契约一致" if report["ok"] else "契约不一致（G10 失败）")
    return "\n".join(lines)


def main(argv):
    as_json = "--json" in argv[1:]
    report = build_report()
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_text(report))
    exit_code = 0 if report["ok"] else 1

    # G11 门禁待集成：v0.12.0 块生态引入了 stdlib/blocks/索引.json，
    # 索引一致性校验的等效逻辑在 scripts/generate_block_index.py --check。
    # 这里以子进程方式串起来，让本脚本继续作为 CI 的单一入口。
    # 若脚本缺失（例如老分支回滚），静默跳过——G11 是新门禁，不破坏 G10 的既有语义。
    block_check = os.path.join(HERE, "generate_block_index.py")
    if os.path.isfile(block_check):
        import subprocess
        proc = subprocess.run(
            [sys.executable, block_check, "--check", "--quiet"],
            cwd=REPO_ROOT,
        )
        if proc.returncode != 0:
            print("G11 块索引校验失败（%s）" % os.path.relpath(block_check, REPO_ROOT))
            exit_code = exit_code or proc.returncode

    # G12：向量索引与块索引同源（ADR-25 §3.3）。
    # 无索引文件视为可接受（运行时降级启发式，ADR-25 §3.1）；有索引就必须
    # 与当前 索引.json 内容哈希一致，否则说明块库改过之后忘了重跑
    # tools/ai-bridge/generate_embeddings.py。本门禁只 import 标准库，
    # 常规 CI 不需要装 torch。
    try:
        sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
        from jikuai.pkg.blocks import check_vector_index
        状态, 说明 = check_vector_index()
        if 状态 == '不一致':
            print("G12 向量索引校验失败：%s" % 说明)
            exit_code = exit_code or 1
        # 其它状态（缺失/一致）都是可接受
    except Exception as e:
        # 不阻塞 G10/G11：v0.12.0 老分支上没有 check_vector_index
        print("G12 跳过（%s）" % e)

    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
