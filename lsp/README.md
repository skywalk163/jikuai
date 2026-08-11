# jikuai-lsp — 极快语言 Language Server（v0.17.0）

极快（JiKuai）语言的 **Language Server Protocol** 实现，独立发行包。
自实现最小 JSON-RPC over stdio（`transport.py`），**零第三方运行时依赖**。

## 与主包的隔离关系（ADR-15）

- **物理隔离**：本包 `jikuai-lsp` 独立发行，主包 `jikuai` 不依赖 `jikuai-lsp`。
- **单向依赖**：`jikuai_lsp` 可以 `import jikuai`；反之禁止。
- **零副作用**：主包在不安装 `jikuai-lsp` 时行为完全不变。
  `tests/test_v0_5_0_lsp_stub.py::test_physical_isolation_import_jikuai_alone`
  在子进程里守这条线：`import jikuai` 后 `sys.modules` 不得出现
  `jikuai_lsp` 或 `pygls`。
- **主包惰性导入**：`server._ensure_jikuai()` 在首次用到时才导入
  `jikuai.service` / `jikuai.completion` / `jikuai.diagnostics`。

## 安装

```bash
pip install -e .        # 主包（前置依赖）
pip install -e lsp/     # LSP 服务器
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

服务器读 stdin、写 stdout 的 JSON-RPC 消息（LSP 帧格式：
`Content-Length: N\r\n\r\n<UTF-8 JSON>`），日志走 stderr。

## 已实现能力

| 方法 / 能力 | 说明 |
| --- | --- |
| `initialize` | 返回 `capabilities` + `serverInfo`（name=`jikuai-lsp`） |
| `initialized` | 静默接收 |
| `shutdown` / `exit` | 正常关闭链路，退出码 0 |
| `textDocument/didOpen` | 缓存文档 + 推送 `publishDiagnostics` |
| `textDocument/didChange` | **Incremental sync**（`change=2`）；无 `range` 的变更按全文替换兜底 |
| `textDocument/didClose` | 清缓存 + 推送空诊断 |
| `textDocument/publishDiagnostics` | 服务端 push。走 `service.SessionHost.compile_and_diagnose`，ParseError → `diagnostics.from_error_info` → `to_lsp_diagnostic` |
| `textDocument/completion` | 内建动词 / 关键字 / 用户名字 / `模块.成员`；触发字符 `.` 与 `，` |
| `textDocument/hover` | 内建动词与关键字的中文 Markdown 说明；其他 token 返回 `null` |
| `textDocument/definition` | `导入` / `从 … 导入` 语句里的点分块路径 → 块目录 URI；不命中返回 `null` |
| `textDocument/documentSymbol` | 单文件 AST 遍历，提取函数（`函数`）/类（`类`）/导入（`导入`）三类顶层符号，含 `range` 与 `selectionRange`（W32） |
| `textDocument/signatureHelp` | 光标位于内建动词调用范围内 → 返回签名与 `activeParameter`；复用 `completion.verb_arity_text` 元数查询；触发字符空格（W32） |
| `textDocument/references` | 跨文件引用查找，走 `service/symbol_index.py` 的反向引用图；按 `uri` + 行号稳定排序；按 LSP 规范处理 `context.includeDeclaration`（v0.17.0 W40） |
| `textDocument/prepareRename` | 光标不在可改名符号上直接返回 `null`（明确反馈，不静默无操作）（v0.17.0 W41） |
| `textDocument/rename` | 返回跨文件 `WorkspaceEdit`。新名先过 `check_export_atomicity`（首字百家姓 + 单 IDENT），非原子名拒；块导出名一律拒（见「已知缺口」）（v0.17.0 W41） |
| `workspace/workspaceFolders` | `initialize` 解析并记录多根（v0.17.0 W38，ADR-29） |
| `workspace/executeCommand` | 命令 `极快.选块`：`{需求, top?}` → `{需求, 候选[]}`，与 `jk 块 选 --json` 同构 |

### 位置口径

`positionEncoding = "utf-16"`。所有列换算经 `jikuai.service.position`
的 `utf16_to_codepoint` / `codepoint_to_utf16`，与
`diagnostics.adapters.to_lsp_diagnostic` 的 0-based UTF-16 code unit
口径一致。中文源码里这一步不能省——汉字在 UTF-16 里是 1 个单元，
但 emoji 与部分生僻字是 2 个。

### `极快.选块` 命令契约

请求：

```json
{
  "command": "极快.选块",
  "arguments": [{"需求": "把一批数字求和再算平均", "top": 3}]
}
```

响应（字段定义唯一真源在 `src/jikuai/service/schema.py`）：

```json
{
  "需求": "把一批数字求和再算平均",
  "候选": [
    {"名称": "求和", "领域": "数据", "层级": 0,
     "描述": "…", "分数": 6.1234, "路径": "[启发式]"}
  ]
}
```

- `需求` 缺失或为空 → JSON-RPC 错误（`-32602` InvalidParams）。
- 未知 command → JSON-RPC 错误（`-32601` MethodNotFound），**不静默返回 null**。
- 默认走启发式检索（纯标准库，不起子进程）。神经路径需调用方提供查询向量，
  LSP 通道当前不暴露该入口。

## 已知缺口

> v0.17.0 W40-W41 已落地 `references` + `rename`（prepareProvider）；
> v0.18.0 W53 以 **ADR-31 明确关闭 `codeAction`**（四轮复审后不做）；
> v0.18.0 W56 修掉 `_token_at` 切不开 `定义X` / `赋值X` 的老边界。

| 缺口 | 现状 |
| --- | --- |
| `textDocument/codeAction` | **明确不做**（v0.18.0 W53 · `docs/ADR-31-不做codeAction.md`）。v0.15.0/v0.16.0/v0.17.0/v0.18.0 四轮复审：14 个诊断码无一满足「唯一机械修复」、唯一候选用例已被 `极快.选块` 覆盖、四轮零社区诉求。重开条件见 ADR-31 §5 |
| `textDocument/rename`（块导出名） | v0.17.0 W41 明确**拒绝**改块导出名——改它要连 `块.json` 与 G13 全局唯一门禁一起改，超出 LSP 层职责。手工改：动 `.jk` + `块.json` 后重跑 `scripts/generate_block_index.py` |
| 启动时全量扫工作区 | 未做。当前只在 `didOpen` / `didChange` 时增量索引；未打开的文件里的引用查不到。工作区大文件多时，用户逐个打开就会补齐。留待做后台异步全量扫 |
| `foldingRange` | 未实现 |
| 增量诊断 | `didChange` 走增量同步，但诊断仍是整篇重编译 |
| 多根 workspace | `initialize` 已解析 `workspaceFolders`（v0.17.0 W38）；`definition` 的块路径解析已在 v0.18.0 W54 扩到多根 |
| pull-based 诊断 | `diagnosticProvider: false`。诊断只走服务端 push |
| 补全空前缀 | LSP 口径下空前缀返回 `[]`（不列全表），与 REPL 的 Tab 行为**故意不同** |

## 能力声明（capabilities.py）

`SERVER_CAPABILITIES` 是**纯数据 dict**，测试直接断言，不反射运行期对象。
`freeze_signature()` 返回按 key 递归排序的规范化字典，是对外契约的唯一判据；
锁死在 `tests/test_lsp_capabilities_freeze.py`。

契约变更历史：

| 版本 | 变更 |
| --- | --- |
| v0.6.0 M5（F3 冻结点） | `textDocumentSync`(Full) + `completionProvider` + `hoverProvider` |
| v0.15.0 W14 | 新增 `definitionProvider`；`textDocumentSync.change` 1 → 2 |
| v0.15.0 W15 | 新增 `executeCommandProvider`（`极快.选块`） |
| v0.16.0 W32 | 新增 `documentSymbolProvider`、`signatureHelpProvider`（触发字符空格） |
| v0.17.0 W38 | 新增 `workspace.workspaceFolders.supported = True`（ADR-29） |
| v0.17.0 W40 | 新增 `referencesProvider` |
| v0.17.0 W41 | 新增 `renameProvider: {prepareProvider: true}` |

## 技术栈选型

**自实现最小 JSON-RPC over stdio（`transport.py`），不用 pygls。**

1. 本机 pygls 为 2.x（`from pygls.lsp.server import LanguageServer`），
   与早期设计假设的 1.x API 不符。
2. **物理隔离最干净**：运行期 `sys.modules` 无 `pygls`，与
   「主包不感知 LSP」的 ADR-15 契约一致。
3. LSP 帧格式极简，测试子进程也是手写 client，收发完全可控。
4. `pygls` 保留在 `pyproject.toml` 的 `optional-dependencies`，
   将来若要切换有退路。

## 测试

```powershell
python -m pytest tests/test_v0_5_0_lsp_stub.py -q          # 生命周期 + 诊断 + 隔离
python -m pytest tests/test_lsp_completion.py -q           # completion
python -m pytest tests/test_lsp_hover.py -q                # hover
python -m pytest tests/test_lsp_definition.py -q           # definition
python -m pytest tests/test_lsp_incremental_sync.py -q     # 增量同步
python -m pytest tests/test_lsp_execute_command.py -q      # 极快.选块
python -m pytest tests/test_lsp_document_symbol.py -q      # documentSymbol（W32）
python -m pytest tests/test_lsp_signature_help.py -q       # signatureHelp（W32）
python -m pytest tests/test_lsp_capabilities_freeze.py -q  # 能力冻结契约
```

所有 LSP 测试都起真子进程（`python -m jikuai_lsp`）走真实协议对话，
共享 helper 在 `tests/lsp_helpers.py`。

## 目录结构

```
lsp/
├── pyproject.toml
├── README.md
└── jikuai_lsp/
    ├── __init__.py          # __version__ / main 导出
    ├── __main__.py          # `python -m jikuai_lsp` 入口
    ├── server.py            # LSP 消息循环、生命周期与各 handler
    ├── capabilities.py      # SERVER_CAPABILITIES 纯数据 + freeze_signature
    └── transport.py         # JSON-RPC over stdio 帧读写
```
