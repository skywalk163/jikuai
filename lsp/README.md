# jikuai-lsp — 极快语言 LSP 桩（v0.5.0 · M4）

极快（JiKuai）语言的 **Language Server Protocol** 实现，独立发行包。
当前处于 **M4 桩阶段**，作为 F1 冻结点「CLI + LSP 桩双消费者实证」的载体。

## 与主包的隔离关系（ADR-15）

- **物理隔离**：本包 `jikuai-lsp` 独立发行，主包 `jikuai` 不依赖 `jikuai-lsp`。
- **单向依赖**：`jikuai_lsp` 可以 `import jikuai`；反之禁止。
- **零副作用**：主包在不安装 `jikuai-lsp` 时行为完全不变（测试用例数与结果一致）。

## 安装

```bash
# 主包（前置依赖）
pip install -e .

# LSP 桩
pip install -e lsp/
```

或直接通过 PYTHONPATH 运行（测试即走此路径，无需 pip install）：

```powershell
$env:PYTHONPATH = "src;lsp"
python -m jikuai_lsp
```

## 启动

```bash
python -m jikuai_lsp   # 通过 stdio 与 LSP 客户端通信
```

服务器读取 stdin、写 stdout 的 JSON-RPC 消息（LSP 帧格式：
`Content-Length: N\r\n\r\n<UTF-8 JSON>`），日志走 stderr。

## M4 桩能力边界

| 功能 | 状态 | 说明 |
| --- | --- | --- |
| `initialize` | ✅ | 返回 `capabilities` + `serverInfo` |
| `initialized` | ✅ | 静默接收 |
| `shutdown` / `exit` | ✅ | 正常关闭链路，退出码 0 |
| `textDocument/didOpen` | ✅ | 缓存文档 + 推送 `publishDiagnostics` |
| `textDocument/didChange` | ✅ | Full sync；缓存更新 + 推送诊断 |
| `textDocument/didClose` | ✅ | 清缓存 + 推送空诊断 |
| **真实诊断投影** | ✅ | ParseError → `diagnostics.from_error_info` → `to_lsp_diagnostic` |
| `completionProvider` | ⏭️ M5 | M4 桩**未声明**（声明即承诺响应，规避契约风险） |
| `hoverProvider` | ⏭️ M5 | 同上 |
| 增量同步（Incremental） | ⏭️ M5 | 桩用 Full sync（`change=1`），文本每次整篇重传 |

## 能力声明（capabilities.py）

```python
SERVER_CAPABILITIES = {
    "textDocumentSync": {"openClose": True, "change": 1},  # 1 = Full
    "positionEncoding": "utf-16",
}
```

`positionEncoding = "utf-16"` 与主包 `diagnostics.adapters.to_lsp_diagnostic`
的列换算口径（0-based UTF-16 code unit）保持一致。

## 技术栈选型

**当前实现：自实现最小 JSON-RPC over stdio（`transport.py`）。**

原因：
1. 本机 pygls 为 2.x（`from pygls.lsp.server import LanguageServer`），
   与 M4 假设的 1.x API 不符。自实现可控性最高。
2. **物理隔离最干净**：运行期 `sys.modules` 无 `pygls`，
   与「主包不感知 LSP」的 ADR-15 契约完全一致。
3. LSP 底层帧格式极其简单，写测试子进程也是手写 client。
4. `pygls` 已在 `pyproject.toml` 的 `optional-dependencies` 中登记，
   M5 可平滑切换。

## M5 计划

- 基于 pygls 或继续自实现（届时二选一）重构 `server.py`。
- 补齐 `completionProvider` / `hoverProvider` / `definitionProvider`。
- 抽出 `service/TextDocumentStore` 至主包 L3 层，CLI 与 LSP 共享。
- 支持 Incremental sync + 增量诊断。

## 目录结构

```
lsp/
├── pyproject.toml
├── README.md
└── jikuai_lsp/
    ├── __init__.py          # __version__ / main 导出
    ├── __main__.py          # `python -m jikuai_lsp` 入口
    ├── server.py            # LSP 消息循环与生命周期
    ├── capabilities.py      # SERVER_CAPABILITIES 纯数据
    └── transport.py         # JSON-RPC over stdio 帧读写
```
