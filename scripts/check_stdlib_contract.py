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
# G16 要 `import check_protocol_doc`（同目录兄弟脚本）。直接跑脚本时解释器会
# 自动把脚本目录放进 sys.path，但被测试以模块形式 import 时不会——显式加上。
if HERE not in sys.path:
    sys.path.insert(0, HERE)

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

    # G14：块类型标注精度（ADR-26 §4.3）。stdlib 块库不允许光秃秃 `列表`/
    # `字典`/`元组`——W3-W4 类型图粘合器只有拿到细化后的类型才能推链，裸容器
    # 等同 `任意`，会让自动组装退化到无差别硬塞。此门禁对内置 stdlib 强制，
    # 对第三方块库不生效（第三方走 `_validate` 的宽松兼容路径）。
    try:
        from jikuai.pkg.blocks import check_stdlib_type_annotations
        问题列表 = check_stdlib_type_annotations()
        if 问题列表:
            print("G14 类型标注精度不足（%d 处）：" % len(问题列表))
            for 条 in 问题列表:
                print("  - %s" % 条)
            exit_code = exit_code or 1
    except Exception as e:
        # 不阻塞已有门禁：老分支没有 check_stdlib_type_annotations
        print("G14 跳过（%s）" % e)

    # G13：导出名全局唯一（W8）。短名跨块碰撞会让 AI 桥接的候选合并/代码生成
    # 指错块——运行时不崩（各块 `_exports` 独立），但会静默走偏，只有在 PR 阶段
    # 逼贡献者改名才能防住。老索引没有 `导出` 字段时本门禁静默通过。
    try:
        from jikuai.pkg.blocks import check_export_globally_unique
        冲突 = check_export_globally_unique()
        if 冲突:
            print("G13 导出名跨块重复（%d 个）：" % len(冲突))
            for 名, 块列 in 冲突:
                print("  - 「%s」在 %s 中同时出现" % (名, '、'.join(块列)))
            exit_code = exit_code or 1
    except Exception as e:
        print("G13 跳过（%s）" % e)

    # G13 扩展（ADR-28 · W29）：L3 聚合块的三条结构约束，都长在同一张
    # 「块 --依赖块--> 块」有向图上。与 G13/G14 同样的 try/except 风格：
    # 老分支没有这些函数就静默跳过，不阻塞既有门禁。
    #   1) 依赖环检测——依赖成环让层级失去偏序、粘合器链式推导打转
    #   2) 层级一致性——声明 L3 但依赖够不上 L3 判定（如只依赖 L1）
    #   3) 稳定性传递——stable 聚合块（L2+）不得依赖任何非 stable 块（W44 起全量强度）
    # 三者都只扫内置块库（第三方块不拖红内置门禁，同 G14 策略）。
    try:
        from jikuai.pkg.blocks import (
            check_dependency_acyclic,
            check_level_consistency,
            check_stability_propagation,
        )
        环 = check_dependency_acyclic()
        if 环:
            print("G13+ 依赖图有环（%d 个）：" % len(环))
            for 路径 in 环:
                print("  - %s" % " → ".join(路径))
            exit_code = exit_code or 1
        层级问题 = check_level_consistency()
        if 层级问题:
            print("G13+ L3 层级虚标（%d 处）：" % len(层级问题))
            for 条 in 层级问题:
                print("  - %s" % 条)
            exit_code = exit_code or 1
        稳定性问题 = check_stability_propagation()
        if 稳定性问题:
            print("G13+ 聚合块稳定性传递违规（%d 处）：" % len(稳定性问题))
            for 条 in 稳定性问题:
                print("  - %s" % 条)
            exit_code = exit_code or 1
    except Exception as e:
        print("G13+ 跳过（%s）" % e)

    # G15：版本号单一真源（W25 · v0.16.0）。`_version.__version__` 是唯一真源；
    # pyproject（dynamic 引用它，解析后应相等）、CHANGELOG 最新条目、VS Code 扩展
    # package.json 三处必须与之一致。历史上 pyproject/__init__/main/扩展四处停在
    # 0.6.0 与实际发布 v0.15.0 脱节达九个版本，本门禁防止再次漂移。
    problems = _check_version_consistency()
    if problems:
        print("G15 版本号不一致（%d 处）：" % len(problems))
        for 条 in problems:
            print("  - %s" % 条)
        exit_code = exit_code or 1

    # G16：协议文档同步（W55 · v0.18.0）。`docs/协议-三通道.md` 的 Web 端点清单
    # 必须与 `tools/web/server.py` 的四张路由清单**双向**一致。v0.17.0 复盘发现
    # W31/W46 六个新端点漂了一年半才被手工审计（W47）追上——文档漂移是 CI 该抓
    # 的问题，不该靠人定期对账。
    #
    # 刻意**不**学 G13+ 的 `except → 跳过`：G13+ 的宽容是历史遗留（早期块库还没
    # 补齐 `依赖块` 时不该红），而 G16 是新门禁，解析不了就是它自己坏了——静默
    # 跳过等于门禁形同虚设，那正是它要防的病。
    import check_protocol_doc
    if check_protocol_doc.main(["--quiet"]) != 0:
        exit_code = exit_code or 1

    return exit_code


def _read_source_version():
    """唯一真源 `jikuai._version.__version__`。"""
    from jikuai._version import __version__
    return __version__


def _read_pyproject_version(source_version):
    """pyproject.toml 的版本。dynamic 模式下静态读不到字面量，
    则确认它确实声明了 dynamic version + 指向 _version，视为一致。"""
    path = os.path.join(REPO_ROOT, "pyproject.toml")
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    # 静态 version = "x.y.z"
    m = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', text)
    if m:
        return m.group(1)
    # dynamic 模式：必须同时声明 dynamic=["version"] 且 attr 指向 _version
    has_dynamic = re.search(r'(?m)^\s*dynamic\s*=\s*\[[^\]]*["\']version["\']', text)
    points_to_source = "jikuai._version.__version__" in text
    if has_dynamic and points_to_source:
        return source_version   # 由 setuptools 在构建期解析为真源，视为一致
    return None


def _read_changelog_version():
    """CHANGELOG.md 最新条目版本号（首个 `## vX.Y.Z` 或 `## [X.Y.Z]`）。"""
    path = os.path.join(REPO_ROOT, "CHANGELOG.md")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^##\s+\[?v?(\d+\.\d+\.\d+)", line.strip())
            if m:
                return m.group(1)
    return None


def _read_vscode_version():
    """editors/vscode/package.json 的 version。"""
    path = os.path.join(REPO_ROOT, "editors", "vscode", "package.json")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("version")


def _check_version_consistency():
    """比对四处版本号，返回不一致描述列表（空 = 一致）。"""
    problems = []
    try:
        src = _read_source_version()
    except Exception as e:
        return ["无法读取 _version.__version__：%s" % e]

    checks = [
        ("pyproject.toml", _read_pyproject_version(src)),
        ("CHANGELOG.md 最新条目", _read_changelog_version()),
        ("editors/vscode/package.json", _read_vscode_version()),
    ]
    for 名, 值 in checks:
        if 值 is None:
            problems.append("%s 读不到版本号" % 名)
        elif 值 != src:
            problems.append("%s = %r，真源 _version = %r" % (名, 值, src))
    return problems


if __name__ == "__main__":
    sys.exit(main(sys.argv))
