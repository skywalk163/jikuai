# -*- coding: utf-8 -*-
"""远程注册表服务端 · 审计日志（ADR-35 §2.6）。

**极简 append-only JSONL**：一次请求一行，一行一次 `open/write/flush/close`。

为什么不用 `logging`
-------------------
`logging.FileHandler` 默认按行缓冲，但**按行**是行末换行才刷；崩溃时可能丢
最后几行。审计日志的价值就在「崩了前一秒的记录也要留下」，所以直接
`open('a')` + `write` + `flush` + `close` —— 一次 syscall 的代价换一条不丢
的记录，值。

为什么**失败也要记**
--------------------
连续的越权尝试是入侵信号（同一远端连续 401/403）。只记成功等于把最有用的
部分丢掉。审计日志的读者是运维，不是「谁发布成功了」的账本。

**不写敏感字段**：token 明文或哈希都不写、私钥/公钥字节不写。`签名者` 已经
足够定位「是谁」，凭证材料不该从日志侧泄漏（ADR-35 §2.6）。
"""

import json
import os

__all__ = ['append_entry']


def append_entry(path, entry):
    """把一条审计记录追加到 `path`（一行 JSON + `\\n`）。

    - `path`：绝对/相对文件路径。父目录不存在则先建。
    - `entry`：`dict`，UTF-8 无 ASCII 转义序列化。

    **不吞异常**：写审计日志失败通常意味着磁盘/权限出了问题，让调用侧
    以 500 收敛。相对地，这函数不做任何字段校验——审计层不该定义业务模型。
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, separators=(',', ':')) + '\n'
    with open(path, 'a', encoding='utf-8', newline='\n') as f:
        f.write(line)
        f.flush()
