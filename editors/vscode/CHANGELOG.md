# 更新日志

本扩展遵循 [语义化版本](https://semver.org/lang/zh-CN/)，版本号与主包 `jikuai` 对齐。

## [0.17.0] - 2026-08-11

版本号随主包对齐。本条目为 v0.18.0 W58 回归时补录——v0.17.0 发布时 `package.json`
已升到 `0.17.0`，但本 CHANGELOG 漏更（G15 只校验 pyproject/根 CHANGELOG/package.json
三处，未覆盖扩展 CHANGELOG），由 `test_changelog_has_current_version_entry` 逮出。

### 扩展侧变化

- 版本号随主包 v0.17.0 对齐（LSP 服务端本轮上线 `textDocument/references` 与
  `textDocument/rename`，扩展通过 `vscode-languageclient` 透传，无需扩展侧改代码）。

### 已知限制

- 沿用 v0.16.0：`.vsix` 仍未在装了 Node 工具链的机器上真机验证；四点 DoD
  （诊断 / hover / F12 / 选块）为「代码就绪待人工验证」。v0.18.0 W57 处理。

## [0.16.0] - 2026-08-10

版本号随主包对齐（此前长期停在 `0.6.0`，与主包 v0.7.0-v0.15.0 脱节九个版本）。G15 门禁现已校验本文件版本号与主包一致。

### 新增

- 命令面板 `极快: 选块`：`Ctrl+Shift+P` 唤起 → 输入框收需求 → 经 LSP
  `workspace/executeCommand: 极快.选块` 检索 → QuickPick 展示候选
  （名称 / 领域 / L层级 / 分数 / 描述）→ 选中后把
  `从 blocks.<领域>.<块名> 导入 <导出名>。` 插入编辑器光标处（无活动编辑器时
  退回复制到剪贴板）。命令**无条件注册**，LSP 未启动时给出可操作提示，不静默失败。
- `build.ps1` 一键打包脚本：前置检查 node/npm → `npm install` →
  `npm run compile` → `npx @vscode/vsce package`，中文输出 + 失败可操作提示。
- 完整安装教程与常见问题见仓库 `docs/LSP-使用.md`。

### 已知限制

- 命令面板选块插入的 `导入 <导出名>` 段：LSP 候选协议当前不含「导出名」字段，
  块目录名与真实导出名不同时先以块名兜底，需手动订正（已回传架构侧补协议）。
- **`.vsix` 首版随发布未产出**：主开发机无 Node.js 工具链，`build.ps1` 与
  `extension.ts` 为纸面产物未经真实执行。装了 Node 的机器上 `cd editors/vscode; .\build.ps1`
  一步出包；四点 DoD（诊断/hover/F12/选块）为「代码就绪待人工验证」。

## [0.6.0] - 2026-08-08

首个版本（M5-P2 支线：VS Code 扩展）。

### 新增

- 注册 `jikuai` 语言，关联 `.jk` 后缀。
- TextMate 语法高亮（`source.jikuai`）：注释（`#` / `--`）、字符串（`"..."` / `"…"`）、
  双字关键字、内建动词、副词（皆/只/归）、常量（真/假/空）、数字（阿拉伯 + 中文）、
  人民币字面量（￥/¥）、内建类型名。
- 通过 `vscode-languageclient` 以 stdio 方式拉起 `python -m jikuai_lsp`，
  提供实时诊断 / 补全（`.` `，` 触发）/ hover。
- 语言配置：中文/半角括号对的自动闭合与环绕，行注释 `--`。
- 配置项 `极快.pythonPath`（默认 `python`）、`极快.lsp.enabled`（默认 `true`）。
- LSP 启动失败时降级为「仅语法高亮」，并提示 `pip install -e lsp/`。

### 边界

- 暂不含调试（断点 / 单步），规划于 M6。
