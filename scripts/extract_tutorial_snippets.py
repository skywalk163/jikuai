# -*- coding: utf-8 -*-
"""教程代码片段抽取器（T-M6-T04）。

解析 docs/教程/*.md 中的 fenced code block（```jikuai ... ```）与其紧邻的
HTML 注释标注，产出一份「可执行片段清单」，供 CI（tests/test_v0_7_0_tutorial.py）
逐片段执行。

标注约定（写在代码块正上方，允许中间有空行）：

    <!-- run: true -->
    <!-- expect: 8 -->
    ```jikuai
    打印 加 3 5。
    ```

- `<!-- run: true -->`     → 该片段纳入 CI 执行；缺省视为纯展示，不执行
- `<!-- expect: <文本> -->` → 断言归一化 stdout 等于该文本；可写多条，
                              依出现顺序拼成多行期望输出；不写则只校验退出码 0

命令行：
    python scripts/extract_tutorial_snippets.py          # 人类可读清单
    python scripts/extract_tutorial_snippets.py --json   # JSON 输出

无论是否有片段，正常解析都以退出码 0 结束。
"""

import glob
import json
import os
import re
import sys

# Windows 控制台默认 GBK，本脚本输出含中文与 JSON，强制 UTF-8，
# 保证被 subprocess 以 UTF-8 捕获时不出现解码错误。
# （沿用 scripts/check_stdlib_contract.py 的既有做法。）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, ".."))
DOCS_DIR = os.path.join(REPO_ROOT, "docs", "教程")

_RE_RUN = re.compile(r"^\s*<!--\s*run:\s*true\s*-->\s*$")
_RE_EXPECT = re.compile(r"^\s*<!--\s*expect:\s?(.*?)\s*-->\s*$")
_RE_FENCE_OPEN = re.compile(r"^\s*```+\s*([A-Za-z0-9_+-]*)\s*$")
_RE_FENCE_CLOSE = re.compile(r"^\s*```+\s*$")

# 只把这个语言标签的代码块视为极快源码
JK_LANGS = {"jikuai", "jk", "极快"}


def _new_pending():
    return {"run": False, "expect": []}


def extract_from_text(text, source_name):
    """从单个 md 文本抽取片段，返回片段字典列表。"""
    lines = text.split("\n")
    snippets = []
    pending = _new_pending()
    i = 0
    n = len(lines)
    idx_in_file = 0
    while i < n:
        line = lines[i]

        m_run = _RE_RUN.match(line)
        if m_run:
            pending["run"] = True
            i += 1
            continue

        m_expect = _RE_EXPECT.match(line)
        if m_expect:
            pending["expect"].append(m_expect.group(1))
            i += 1
            continue

        m_fence = _RE_FENCE_OPEN.match(line)
        if m_fence:
            lang = m_fence.group(1).lower()
            # 收集到闭合围栏
            body = []
            i += 1
            while i < n and not _RE_FENCE_CLOSE.match(lines[i]):
                body.append(lines[i])
                i += 1
            # 跳过闭合围栏本身
            if i < n:
                i += 1
            if lang in JK_LANGS:
                idx_in_file += 1
                expect = "\n".join(pending["expect"]) if pending["expect"] else None
                snippets.append({
                    "source": source_name,
                    "index": idx_in_file,
                    "id": "%s#%d" % (source_name, idx_in_file),
                    "lang": lang,
                    "code": "\n".join(body),
                    "run": bool(pending["run"]),
                    "expect": expect,
                })
            # 无论是否极快块，围栏都会消费掉挂起标注
            pending = _new_pending()
            continue

        # 空行：保留挂起标注（允许标注与围栏之间空一行）
        if line.strip() == "":
            i += 1
            continue

        # 其它正文：重置挂起标注
        pending = _new_pending()
        i += 1

    return snippets


def extract_snippets(docs_dir=DOCS_DIR):
    """遍历 docs/教程/*.md，返回全部片段字典列表（含纯展示片段）。"""
    result = []
    if not os.path.isdir(docs_dir):
        return result
    for path in sorted(glob.glob(os.path.join(docs_dir, "*.md"))):
        name = os.path.basename(path)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        result.extend(extract_from_text(text, name))
    return result


def runnable_snippets(docs_dir=DOCS_DIR):
    """只返回标了 run: true 的片段。"""
    return [s for s in extract_snippets(docs_dir) if s["run"]]


def _format_text(snippets):
    runnable = [s for s in snippets if s["run"]]
    lines = []
    lines.append("教程目录：%s" % DOCS_DIR)
    lines.append("片段总数：%d（可运行 %d，纯展示 %d）"
                 % (len(snippets), len(runnable), len(snippets) - len(runnable)))
    lines.append("")
    for s in runnable:
        first = s["code"].split("\n", 1)[0]
        exp = "（校验退出码 0）" if s["expect"] is None else ("expect=%r" % s["expect"])
        lines.append("[run] %-24s %s" % (s["id"], exp))
        lines.append("      首行：%s" % first)
    return "\n".join(lines)


def main(argv):
    as_json = "--json" in argv[1:]
    snippets = extract_snippets()
    if as_json:
        print(json.dumps(snippets, ensure_ascii=False, indent=2))
    else:
        print(_format_text(snippets))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
