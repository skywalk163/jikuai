#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""commit message 卫生检查（W26 · v0.16.0）。

背景：v0.15.0 收官时 `a299768` 的 message 被写成字面量 `$(cat <<'EOF'`
—— Windows PowerShell 不解释 bash heredoc，整段作为 message 入库；
双远端已推，不追改历史，代价大于收益。本脚本挡新 commit，不追旧账。

用法：
    python scripts/check_commit_msg.py <path-to-COMMIT_EDITMSG>

也可以挂成 git hook（可选）：
    # .git/hooks/commit-msg（chmod +x）
    #!/usr/bin/env sh
    python scripts/check_commit_msg.py "$1"

拒条件（任一命中即退出 1）：
- message 为空或仅空白
- 出现 `$(` 之类 shell 命令替换的字面量（bash 未被 PowerShell 解释的信号）
- 出现 `<<'EOF'` / `<<EOF` / `<<"EOF"` 之类 heredoc 起始（PowerShell 场景）
- 首行没有可读文本（例如全是空白或全是标点）
"""
from __future__ import annotations

import re
import sys

# 命令替换：$(...)
_RE_CMD_SUBST = re.compile(r"\$\(")
# heredoc 起始：<<EOF / <<'EOF' / <<"EOF"，兼容常见 tag 名
_RE_HEREDOC = re.compile(r"<<-?\s*['\"]?[A-Za-z_][A-Za-z0-9_]*['\"]?")
# 首行必须有非空非纯标点内容
_RE_ANY_WORD = re.compile(r"[\w\u4e00-\u9fff]")


def check(text: str) -> list[str]:
    """返回问题描述列表，空表示通过。"""
    problems: list[str] = []
    # 去掉 `git commit` 会写入的注释行（`#` 开头）
    real_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    body = "\n".join(real_lines).strip()

    if not body:
        problems.append("message 为空")
        return problems

    if _RE_CMD_SUBST.search(body):
        problems.append("含 shell 命令替换字面量 `$(...)`（Windows/PowerShell 下 heredoc 常见炸法）")
    if _RE_HEREDOC.search(body):
        problems.append("含 heredoc 起始（如 `<<'EOF'`）——PowerShell 不解释，禁止使用；改用 `-F <文件>` 或多个 `-m`")

    first = next((ln for ln in real_lines if ln.strip()), "")
    if first and not _RE_ANY_WORD.search(first):
        problems.append("首行没有可读文本")

    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：check_commit_msg.py <COMMIT_EDITMSG>", file=sys.stderr)
        return 2
    path = argv[1]
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"读文件失败：{e}", file=sys.stderr)
        return 2

    problems = check(text)
    if not problems:
        return 0
    print("commit message 检查未通过：", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    print(
        "\nPowerShell 环境写多行 message 用："
        "\n  git commit -F <文件>      # 从文件读"
        "\n  git commit -m '标题' -m '正文'  # 多个 -m",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
