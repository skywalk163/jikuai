# 更新日志

本扩展遵循 [语义化版本](https://semver.org/lang/zh-CN/)，版本号与主包 `jikuai` 对齐。

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
