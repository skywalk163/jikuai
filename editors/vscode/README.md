# 极快 JiKuai · VS Code 扩展

为「极快 JiKuai」中文编程语言提供 VS Code 编辑体验：**语法高亮** + **LSP**（实时校验 / 补全 / hover）。

- 版本：`0.6.0`（与主包 `jikuai` 版本对齐）
- 语言 ID：`jikuai`，文件后缀：`.jk`
- Grammar scope：`source.jikuai`

## 能力边界

| 能力 | M5（当前） | M6（规划） |
| --- | --- | --- |
| 语法高亮（TextMate） | ✅ | — |
| 实时诊断（publishDiagnostics） | ✅（经 LSP） | — |
| 自动补全（`.` / `，` 触发） | ✅（经 LSP） | — |
| Hover 悬浮提示 | ✅（经 LSP） | — |
| 断点 / 单步调试 | ❌ | ✅ 计划补充 |

> 语法高亮不依赖 LSP。即使未安装 `jikuai_lsp`，高亮仍可用；诊断 / 补全 / hover 会自动降级关闭。

## 安装

### 1. 安装 LSP Server（提供诊断 / 补全 / hover）

在仓库根目录执行：

```bash
pip install -e lsp/
```

安装后应能运行：

```bash
python -m jikuai_lsp   # 走 stdio，正常情况下会阻塞等待客户端
```

### 2. 安装扩展（vsix）

```bash
code --install-extension jikuai-vscode-0.6.0.vsix
```

## 从源码构建

> 需要本机具备 Node.js 与 npm；打包需要 `@vscode/vsce`。

```bash
cd editors/vscode
npm install          # 安装 typescript / @types/* / vscode-languageclient
npm run compile      # tsc -p ./  ->  产出 out/extension.js
npx @vscode/vsce package   # 生成 jikuai-vscode-0.6.0.vsix
```

## 配置项

| 配置键 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `极快.pythonPath` | string | `python` | 启动 LSP 用的 Python 解释器路径，执行 `<pythonPath> -m jikuai_lsp` |
| `极快.lsp.enabled` | boolean | `true` | 是否启用 LSP；关闭后仅保留语法高亮 |

在 `settings.json` 中示例：

```json
{
  "极快.pythonPath": "python3",
  "极快.lsp.enabled": true
}
```

## 故障排查

- 弹出「未找到 jikuai_lsp，请先 pip install -e lsp/」：说明配置的解释器下没有 `jikuai_lsp` 模块。请确认 `极快.pythonPath` 指向的解释器已执行过 `pip install -e lsp/`。
- 高亮正常但无补全 / 诊断：多为 LSP 未启动，查看输出面板「极快 语言服务」通道。

## 目录结构

```
editors/vscode/
├── package.json                 # 扩展清单（语言 / 语法 / 配置贡献点）
├── tsconfig.json                # TS 编译配置（ES2020 / commonjs / strict）
├── language-configuration.json  # 注释 / 括号对 / 自动闭合
├── syntaxes/
│   └── 极快.tmLanguage.json      # TextMate 语法（scope: source.jikuai）
└── src/
    └── extension.ts             # 激活入口：拉起 LanguageClient
```
