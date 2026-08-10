# 极快 JiKuai · VS Code 扩展 + LSP 使用指南

面向使用者：把极快语言的编辑体验（诊断 / 补全 / hover / 定义跳转 / 大纲 /
签名帮助 / 命令面板选块 / 断点调试）装进 VS Code。

> 这份文档补齐了 v0.15.0 遗留的 W16 欠账（原计划的「装扩展教程」），
> 并覆盖 v0.16.0 W32/W33 新增能力。

---

## 三步装好

### 第 1 步：装 LSP Server（提供诊断 / 补全 / hover / 跳转 / 大纲 / 签名 / 选块）

在仓库根目录执行：

```powershell
pip install -e .        # 主包 jikuai（前置依赖）
pip install -e lsp/     # LSP 服务器 jikuai-lsp
```

装完自检（正常会阻塞等待客户端，`Ctrl+C` 退出即可）：

```powershell
python -m jikuai_lsp
```

> 不想 `pip install` 也行：设 `PYTHONPATH` 亦可跑通（测试即走此路径）：
> ```powershell
> $env:PYTHONPATH = "src;lsp"
> python -m jikuai_lsp
> ```
> 但装进 VS Code 用时，`极快.pythonPath` 指向的解释器必须能 `import jikuai_lsp`，
> 所以推荐 `pip install -e lsp/` 而不是临时 PYTHONPATH。

### 第 2 步：打包扩展 `.vsix`

```powershell
cd editors/vscode
.\build.ps1
```

`build.ps1` 会：检查 node/npm → `npm install` → `npm run compile`（tsc）→
`npx @vscode/vsce package` → 报告产出的 `.vsix` 路径。

> **需要 Node.js**：打包扩展是 Node 工具链（vsce）的活，LSP/DAP 侧是纯 Python
> 不需要 Node。本机没装 Node 时 `build.ps1` 会明确报错并给安装指引
> （`winget install OpenJS.NodeJS.LTS` 或 https://nodejs.org ）。

### 第 3 步：VS Code「从 VSIX 安装」

- 图形界面：扩展面板 → 右上角 `⋯` → **从 VSIX 安装…** → 选第 2 步产出的 `.vsix`。
- 或命令行：
  ```powershell
  code --install-extension jikuai-vscode-0.16.0.vsix
  ```

装完打开任意 `.jk` 文件即可激活。

---

## 装完应该能用的能力清单

| 能力 | 触发方式 | 底层来源 |
| --- | --- | --- |
| 语法高亮 | 打开 `.jk` 文件 | TextMate 语法（不依赖 LSP） |
| 实时诊断 | 编辑时自动 | LSP `publishDiagnostics`（服务端 push） |
| 自动补全 | 键入 `.` 或 `，` 触发 | LSP `textDocument/completion` |
| Hover 悬浮说明 | 鼠标停在内建动词/关键字上 | LSP `textDocument/hover` |
| **F12 定义跳转** | 光标在 `导入` / `从…导入` 的块路径上按 F12 | LSP `textDocument/definition` |
| **大纲 Outline** | 侧栏「大纲」视图 / `Ctrl+Shift+O` | LSP `textDocument/documentSymbol`（W32） |
| **签名帮助** | 动词后打空格 | LSP `textDocument/signatureHelp`（W32） |
| **命令面板选块** | `Ctrl+Shift+P` → 「极快: 选块」 | LSP `workspace/executeCommand: 极快.选块`（W33） |
| **断点调试（DAP）** | 在 `.jk` 上打断点，F5 启动 | `python -m jikuai_dap`（M6-P3） |

### 命令面板选块的用法

1. `Ctrl+Shift+P` 打开命令面板，输入「极快: 选块」回车；
2. 弹出的输入框里描述需求（例如「把一批数字求和再算平均」），回车；
3. 候选以列表形式弹出，每条显示 `名称（领域）` / `L层级 · 分数` / `描述`；
4. 选中一条，扩展把 `从 blocks.<领域>.<块名> 导入 <导出名>。` 插入到当前编辑器
   光标处；若当前没有活动编辑器，则复制到剪贴板。

> **已知限制**：LSP 选块响应的候选目前不携带「导出名」字段，块目录名与真实
> 导出名不一致时（例如「个税」块导出「缴税」），插入语句的 `导入 X` 段会先用
> 块名兜底，需你手动改成真实导出名。彻底修复要在候选协议里补 `导出名` 字段，
> 已作为实现反馈回传架构侧，计划在后续版本闭合。

---

## 常见问题

### Q1. 如何配置 Python 路径？

扩展用 `极快.pythonPath`（默认 `python`）作为解释器，执行
`<pythonPath> -m jikuai_lsp`（LSP）与 `<pythonPath> -m jikuai_dap`（调试）。

在 `settings.json`：

```json
{
  "极快.pythonPath": "C:\\path\\to\\venv\\Scripts\\python.exe",
  "极快.lsp.enabled": true
}
```

多解释器场景下，调试还能在 `launch.json` 的单个配置里用 `pythonPath` 覆盖全局设置。

### Q2. `jikuai_lsp` 没装会怎样（降级行为）？

- **语法高亮照常**：它是 TextMate 语法，不依赖 LSP。
- **诊断 / 补全 / hover / 跳转 / 大纲 / 签名 / 选块全部关闭**：扩展启动 LSP 失败时
  弹一次警告「未找到 jikuai_lsp，请先 pip install -e lsp/（已降级为仅语法高亮 +
  调试）」，然后安静降级，不会反复弹窗，也不会让扩展崩溃。
- 想临时只要高亮：把 `极快.lsp.enabled` 设为 `false`。

### Q3. 中文输入法下补全触发不了？

补全触发字符是 `.` 与 `，`（全角逗号），签名帮助触发字符是空格：

- 打点分成员（如 `模块.成员`）时用**半角句点** `.` 触发补全；
- 管道 / 参数分隔用**全角逗号** `，` 触发补全 —— 注意是全角 `，` 不是半角 `,`；
  中文输入法下逗号默认就是全角，正常打即可。
- 动词调用（如 `加 1 2`）打完动词名敲**空格**触发签名帮助。

若发现打了触发字符没反应，多半是 LSP 没起来（见 Q2 / Q4）。

### Q4. LSP 启动失败怎么看日志？

1. VS Code 菜单 → **查看 → 输出**（`Ctrl+Shift+U`）；
2. 右上角下拉选 **「极快 语言服务」** 通道；
3. 里面是 LSP 服务端 stderr 的日志。常见错误：
   - `ModuleNotFoundError: No module named 'jikuai_lsp'` → 该解释器没装 LSP，
     回到第 1 步 `pip install -e lsp/`，或修正 `极快.pythonPath`。
   - `No module named 'jikuai'` → 主包没装，先 `pip install -e .`。
   - 解释器路径不对 → 确认 `极快.pythonPath` 指向的可执行文件真实存在。

---

## 目录结构

```
editors/vscode/
├── package.json                 # 扩展清单（语言 / 语法 / 命令 / 调试贡献点）
├── tsconfig.json                # TS 编译配置
├── build.ps1                    # 一键打包脚本（W33）
├── language-configuration.json  # 注释 / 括号对 / 自动闭合
├── syntaxes/
│   └── 极快.tmLanguage.json      # TextMate 语法（scope: source.jikuai）
└── src/
    └── extension.ts             # 激活入口：LSP client + DAP + 选块命令
```

LSP 服务端能力全景见 [`lsp/README.md`](../lsp/README.md)。
