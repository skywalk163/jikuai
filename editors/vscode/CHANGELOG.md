# 更新日志

本扩展遵循 [语义化版本](https://semver.org/lang/zh-CN/)，版本号与主包 `jikuai` 对齐。

## [0.20.0] - 2026-08-12

版本号随主包 v0.20.0 对齐。**扩展侧无代码改动** —— 本轮主题是「HTTP 远程注册表
+ 包签名 + 可信跨机分发」，全在 `jk 包` CLI 与包管理内核，扩展不涉及。

### 服务端能力变化（扩展透传，无需改扩展代码）

- 包签名与验签落地（ADR-33）：`jk 包 发布 --签名 <别名>` 用 Ed25519 签校验和；
  `jk 包 装` 自动三道检查（完整性 / 签名 / 未签名过渡告警），TOFU 信任库 pin 公钥。
- 远程 HTTP 注册表（ADR-34）：`JIKUAI_REGISTRY=https://...` 即可从远程注册表装包，
  快照走 tar.gz 传输；支持 per-dependency 注册表覆盖与 Bearer token 鉴权。

## [0.19.0] - 2026-08-12

版本号随主包 v0.19.0 对齐。**扩展侧无代码改动** —— 本轮主题是「块包一体 +
生态冷启动」（第三方块能通过包管理装、能被检索/粘合用），全在服务端与
`jk 包`/`jk 块` CLI，扩展通过 `vscode-languageclient` 透传即可。

### 服务端能力变化（扩展透传，无需改扩展代码）

- 块包桥接落地（ADR-32）：`jk 包 装` 一个携带块的第三方包后，块能被
  `scan_blocks` / 检索 / 粘合发现——发现、执行、检索三根块根系统同时闭合。
- 步骤协议新增可选字段 `命名空间`：第三方块的导入路径从此带命名空间段
  （`从 blocks.<命名空间>.<领域>.<块> 导入 X`），LSP `极快.选块` 生成的方案
  会带上它。内置块无此字段，行为一字不变。

## [0.18.0] - 2026-08-11


版本号随主包 v0.18.0 对齐。**扩展侧无代码改动** —— 本轮 LSP 增强
（W54 多根 `definition`、W56 `_token_at` 走 lexer）全在服务端，扩展通过
`vscode-languageclient` 透传即可。

### 服务端能力变化（扩展透传，无需改扩展代码）

- `textDocument/definition` 的块路径解析扩到多根 workspace（W54）。此前只查
  `blocks_root()` 与文档自身目录，多根工程里跨根跳转会失败。
- `_token_at` 改为优先走 JiKuai lexer 分词（W56）。此前 `定义赵共享` 这种
  「关键字紧贴标识符」形态会被当成一个整体 token，**从定义处发起 rename
  拿不到符号**；现在 `定义`(KEYWORD) 与 `赵共享`(IDENT) 正确切开。

### 明确不做

- `textDocument/codeAction`：v0.18.0 W53 以 `docs/ADR-31-不做codeAction.md`
  **正式关闭**（四轮复审后结论：14 个诊断码无一满足「唯一机械修复」、唯一候选
  用例已被 `极快.选块` 覆盖、四轮零社区诉求）。重开条件见 ADR-31 §5。
  此前四个版本的 CHANGELOG 都写「留待下一轮复审」，本轮起不再逐轮挂账。

### 已知限制

- `.vsix` **已真机验证通过**（v0.18.0 W57）。用户验证环境：Node 工具链 + VS Code
  + `pip install -e .` + `pip install -e lsp\`。打包 `vsce package` 成功产出
  `jikuai-vscode-0.18.0.vsix`（14.63 KB）；安装后 LSP 拉起、hover / completion /
  definition / references / rename / signatureHelp / `极快.选块` 七项能力可见。
  两个 vsce warning（缺 `repository` 字段 + 缺独立 LICENSE 文件）不影响功能。

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
