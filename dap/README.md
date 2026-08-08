# jikuai-dap · 极快语言调试适配器（M6-P3 MVP）

独立发行包 `jikuai-dap`，与主包 `jikuai` 物理隔离：主包不 import 本包，
本包依赖 `jikuai`（求值器 + service 层）。

## 快速开始

```bash
python -m jikuai_dap
```

进程通过本机 stdio 与调试客户端（VS Code 等）按 DAP 协议交互，不监听网络端口。

## 能力边界（ADR-20 · v0.7.0）

- 支持：行断点、`next`/`stepIn`/`stepOut`、`continue`/`pause`、`stackTrace`
  （最小帧）、`scopes`+`variables`、单线程 `threads`。
- 不支持（返回 `JK-E8001`「调试能力暂不支持」）：条件断点、`evaluate`、
  `setVariable`、多线程/多会话、函数/数据/异常断点。

详见仓库 `docs/调试.md`。
