# 极快 JiKuai · VS Code 扩展一键打包脚本（v0.16.0 W33）
#
# 用途：在本目录（editors/vscode/）执行 `npm install` + `vsce package`，
#       产出 `jikuai-vscode-<版本>.vsix`，供 VS Code「从 VSIX 安装」。
#
# 为什么用 PowerShell 而不是 Makefile：Windows 是本项目的主开发环境，
# 仓库里其它验证命令（PYTHONPATH 设置、pytest 调用）也一律 PowerShell 口径。
#
# 用法：
#   cd editors/vscode
#   .\build.ps1              # 完整打包
#   .\build.ps1 -SkipInstall # node_modules 已就绪时跳过 npm install
#
# 安全考虑：
#   - 只调用本机已装的 node/npm/npx，不下载任何脚本执行；
#   - `vsce` 通过 npx 临时拉取（不写进 dependencies，也不需要全局装）；
#   - 不需要任何令牌：只做 `package`（本地打包），**不做** `publish`
#     （发布到 Marketplace 需要 PAT，那是独立的人工动作，不放进构建脚本）。

[CmdletBinding()]
param(
    # 跳过 npm install（node_modules 已存在且依赖没变时用，省一两分钟）
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'

function 说(  [string]$文) { Write-Host $文 }
function 步(  [string]$文) { Write-Host "==> $文" -ForegroundColor Cyan }
function 好(  [string]$文) { Write-Host "[成功] $文" -ForegroundColor Green }
function 警(  [string]$文) { Write-Host "[注意] $文" -ForegroundColor Yellow }
function 糟(  [string]$文) { Write-Host "[失败] $文" -ForegroundColor Red }

# 始终以脚本所在目录为工作目录，避免从仓库根执行时 npm 找不到 package.json
$本目录 = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $本目录

try {
    说 '=========================================='
    说 '  极快 JiKuai · VS Code 扩展打包'
    说 '=========================================='
    说 "工作目录：$本目录"
    说 ''

    # ---- 0. 前置检查：package.json ----
    if (-not (Test-Path -LiteralPath 'package.json')) {
        糟 '当前目录没有 package.json，这不是扩展根目录。'
        说 '  请确认在 editors/vscode/ 下执行本脚本。'
        exit 1
    }

    # ---- 1. 前置检查：node / npm ----
    步 '检查 Node.js 与 npm'
    $node = Get-Command node -ErrorAction SilentlyContinue
    $npm = Get-Command npm -ErrorAction SilentlyContinue

    if (-not $node) {
        糟 '本机未找到 node。'
        说 '  打包扩展必须有 Node.js（vsce 是 Node 工具链）。'
        说 '  处理办法（任选一）：'
        说 '    1) 装 LTS 版 Node.js：https://nodejs.org/zh-cn/download'
        说 '    2) 用 winget：winget install OpenJS.NodeJS.LTS'
        说 '    3) 不想装 Node：跳过 .vsix，直接在 VS Code 里按 F5 起'
        说 '       「扩展开发宿主」调试本扩展（无需打包，但需要 node 编译 TS，'
        说 '       所以这条路同样要 Node —— 打包这一步绕不开它）。'
        说 ''
        说 '  注意：LSP / DAP 侧是纯 Python，不需要 Node；只有打包 .vsix 需要。'
        exit 1
    }
    if (-not $npm) {
        糟 '找到了 node 但没找到 npm。'
        说 '  多为 Node 安装不完整或 PATH 缺 npm 所在目录。'
        说 "  node 位置：$($node.Source)"
        说 '  建议重装 Node.js LTS（安装器会一并配好 npm）。'
        exit 1
    }

    $node版本 = (& node --version) 2>$null
    $npm版本 = (& npm --version) 2>$null
    好 "node $node版本 / npm $npm版本"
    说 ''

    # ---- 2. 依赖安装 ----
    if ($SkipInstall) {
        警 '按 -SkipInstall 跳过 npm install。'
        if (-not (Test-Path -LiteralPath 'node_modules')) {
            糟 'node_modules 不存在，-SkipInstall 无法继续。'
            说 '  去掉 -SkipInstall 重跑本脚本。'
            exit 1
        }
    }
    else {
        步 'npm install（typescript / @types/* / vscode-languageclient）'
        & npm install
        if ($LASTEXITCODE -ne 0) {
            糟 "npm install 失败（退出码 $LASTEXITCODE）。"
            说 '  常见原因与处理：'
            说 '    - 网络不通 / 公司代理：设镜像 npm config set registry https://registry.npmmirror.com'
            说 '    - 残留的坏缓存：npm cache clean --force 后重试'
            说 '    - node_modules 半残：删掉 node_modules 与 package-lock.json 后重试'
            exit 1
        }
        好 '依赖安装完成。'
    }
    说 ''

    # ---- 3. 编译 TypeScript ----
    # vsce package 会走 `vscode:prepublish`（= npm run compile），这里先单独编一次，
    # 好让 TS 报错单独暴露出来，而不是混在打包日志尾部。
    步 'npm run compile（tsc -p ./ → out/extension.js）'
    & npm run compile
    if ($LASTEXITCODE -ne 0) {
        糟 "TypeScript 编译失败（退出码 $LASTEXITCODE）。"
        说 '  上面的 tsc 报错就是原因；修 src/extension.ts 后重跑。'
        exit 1
    }
    好 'out/extension.js 已产出。'
    说 ''

    # ---- 4. 打包 .vsix ----
    步 'npx @vscode/vsce package（首次会临时下载 vsce，需要网络）'
    & npx --yes @vscode/vsce package
    if ($LASTEXITCODE -ne 0) {
        糟 "vsce package 失败（退出码 $LASTEXITCODE）。"
        说 '  常见原因与处理：'
        说 '    - 拉不到 @vscode/vsce：换镜像（见上）或先 npm i -D @vscode/vsce 再跑 npx vsce package'
        说 '    - 报 missing repository/LICENSE 字段：按 vsce 提示补 package.json 字段'
        说 '    - 报 activationEvents/commands 不匹配：核对 contributes.commands 与 src/extension.ts'
        exit 1
    }
    说 ''

    # ---- 5. 报告产物 ----
    $包 = Get-ChildItem -LiteralPath $本目录 -Filter '*.vsix' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($包) {
        好 "已产出：$($包.Name)（$([math]::Round($包.Length / 1KB, 1)) KB）"
        说 ''
        说 '安装方式（任选一）：'
        说 "  A) 命令行：code --install-extension `"$($包.FullName)`""
        说 '  B) VS Code 图形界面：扩展面板 → 右上角 ⋯ → 「从 VSIX 安装…」→ 选上面的文件'
        说 ''
        说 '装完记得先备齐 LSP：在仓库根执行 pip install -e lsp/'
        说 '完整教程见 docs/LSP-使用.md'
    }
    else {
        警 'vsce 退出码为 0 但目录下没找到 .vsix，请翻上面的日志确认。'
        exit 1
    }
}
finally {
    Pop-Location
}
