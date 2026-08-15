<#
.SYNOPSIS
    干净 venv 里验收极快 wheel：装完真能 `导入 数学`、选块、跑分词。（W117 · v0.24.0）

.DESCRIPTION
    这是唯一能抓住「本机 editable 恰好还能回溯到旧位置」这种假绿的一步——
    BACKLOG §10 那次事故（PyPI 0.4.1 wheel 里零个 stdlib）在本机 editable 下
    完全看不出来。所以每次发版前都要在**全新** venv、非 editable 下跑一遍。

    做六件事：构建 wheel → 装进全新 venv → 四条验收命令 → 确认 stdlib 落在
    site-packages（不是回落源码树）→ 验 JIKUAI_STDLIB 覆盖口 → 清理。
    任一步失败即 exit 1。

.PARAMETER Wheel
    已有 wheel 路径。省略则现构建（清掉 dist 后 `python -m build --wheel`）。

.PARAMETER Keep
    保留临时目录（默认跑完删）。排障时用。

.EXAMPLE
    powershell -File scripts\verify_wheel_e2e.ps1
    powershell -File scripts\verify_wheel_e2e.ps1 -Wheel dist\jikuai-0.24.0-py3-none-any.whl -Keep
#>
param(
    [string]$Wheel = "",
    [switch]$Keep
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 本脚本**必须存成带 BOM 的 UTF-8**。PowerShell 5.1 读无 BOM 的 .ps1 时按 ANSI
# （中文系统上是 GBK）解码，脚本里的中文全变乱码，直接解析报「function 声明中
# 缺少 function 主体」。注意这跟 `pyproject.toml` 的坑正好相反——那边有 BOM 就
# 解析失败。规矩是按文件类型分的，不是「一律别写 BOM」。
#
# 另一处：`jk` 的 stdout 一旦被管道捕获（非 tty），CPython 就按 locale 编码写
# （中文 Windows 上是 GBK），而 PowerShell 按上面设的 UTF-8 解，于是抓到乱码，
# 让「分词结果里有没有『个人所得税』」这类断言假红。钉死子进程的 IO 编码：
$env:PYTHONIOENCODING = "utf-8"

# 仓库根 = 本脚本所在目录的上一级
$repo = Split-Path -Parent $PSScriptRoot
$e = Join-Path $env:TEMP "jk_e2e"
$enc = New-Object System.Text.UTF8Encoding($false)   # 无 BOM，否则 .jk 词法器会噎住

$失败 = @()
function 记失败($msg) { $script:失败 += $msg; Write-Host "  [失败] $msg" -ForegroundColor Red }

# --- Step 1: 构建 + 装进全新 venv ---------------------------------------
Write-Host "[1/5] 构建 wheel、建全新 venv、安装" -ForegroundColor Cyan
Remove-Item -Recurse -Force $e -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $e | Out-Null

if (-not $Wheel) {
    Remove-Item -Recurse -Force (Join-Path $repo "dist") -ErrorAction SilentlyContinue
    Push-Location $repo
    python -m build --wheel | Out-Null
    Pop-Location
    $Wheel = (Get-ChildItem (Join-Path $repo "dist\*.whl") | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
}
if (-not (Test-Path $Wheel)) { Write-Error "找不到 wheel：$Wheel"; exit 1 }
Write-Host "  wheel = $Wheel"

python -m venv "$e\venv"
$py = "$e\venv\Scripts\python.exe"
$jk = "$e\venv\Scripts\jk.exe"
& "$e\venv\Scripts\pip.exe" install -q --no-deps $Wheel
if ($LASTEXITCODE -ne 0) { 记失败 "pip install 失败" }

# --- Step 2: 四条验收命令 -----------------------------------------------
Write-Host "[2/5] 四条验收命令" -ForegroundColor Cyan

[IO.File]::WriteAllText("$e\hello.jk", '打印 "你好，极快"。', $enc)
& $jk "$e\hello.jk"
if ($LASTEXITCODE -ne 0) { 记失败 "hello.jk 退出码非 0" }

[IO.File]::WriteAllText("$e\seg.jk", "从 分词 导入 分词。`r`n打印 分词(`"个人所得税起征点`")。", $enc)
$seg = (& $jk "$e\seg.jk" | Out-String)
if ($LASTEXITCODE -ne 0) { 记失败 "seg.jk 退出码非 0" }
# 真分词至少把「个人所得税」切成一个词；逐字退化会切成单字
if ($seg -notmatch "个人所得税") { 记失败 "分词没切出「个人所得税」，疑似退化为逐字：$seg" }

& $jk 块 选 "月薪两万个税多少" --json | Out-File -Encoding utf8 "$e\sel.json"
if ($LASTEXITCODE -ne 0) { 记失败 "块 选 退出码非 0" }
$cand = & $py -c "import json,io; d=json.load(io.open(r'$e\sel.json',encoding='utf-8-sig')); c=d['候选']; import sys; sys.exit(0 if any(x['名称']=='个税' for x in c) else 3)"
if ($LASTEXITCODE -ne 0) { 记失败 "块 选 候选里没有『个税』" }

# `jk 包 列表` 需要 cwd 有 包.json——在初始化后的目录里跑才是真验收
New-Item -ItemType Directory -Force "$e\pkg" | Out-Null
Push-Location "$e\pkg"
& $jk 包 初始化 | Out-Null
& $jk 包 列表 | Out-Null
if ($LASTEXITCODE -ne 0) { 记失败 "包 初始化+列表 退出码非 0" }
Pop-Location

# --- Step 3: stdlib 落在 site-packages，未回落源码树 ---------------------
Write-Host "[3/5] stdlib 定位在 site-packages" -ForegroundColor Cyan
$check = @"
import os, sys
from jikuai import resources
d = resources.stdlib_dir()
ok = ('site-packages' in d.replace('\\','/')) and os.path.isfile(resources.stdlib_path('分词词典.txt'))
print(d)
sys.exit(0 if ok else 3)
"@
[IO.File]::WriteAllText("$e\loc.py", $check, $enc)
& $py "$e\loc.py"
if ($LASTEXITCODE -ne 0) { 记失败 "stdlib 未定位到 site-packages 或缺分词词典" }

# --- Step 4: JIKUAI_STDLIB 覆盖口 ---------------------------------------
Write-Host "[4/5] JIKUAI_STDLIB 覆盖口" -ForegroundColor Cyan
$srcStdlib = Join-Path $repo "src\jikuai\stdlib"
$env:JIKUAI_STDLIB = $srcStdlib
$ov = (& $py -c "from jikuai import resources; print(resources.stdlib_dir())" | Out-String).Trim()
Remove-Item Env:\JIKUAI_STDLIB
if ($ov -ne (Resolve-Path $srcStdlib).Path) { 记失败 "JIKUAI_STDLIB 覆盖没生效：$ov" }

# --- Step 5: 清理 + 汇总 -------------------------------------------------
Write-Host "[5/5] 清理" -ForegroundColor Cyan
if (-not $Keep) { Remove-Item -Recurse -Force $e -ErrorAction SilentlyContinue }
else { Write-Host "  保留：$e" }

if ($失败.Count -gt 0) {
    Write-Host "`nwheel e2e 验收失败（$($失败.Count) 项）" -ForegroundColor Red
    exit 1
}
Write-Host "`nwheel e2e 验收全绿" -ForegroundColor Green
exit 0
