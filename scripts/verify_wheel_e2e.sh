#!/bin/sh
# 干净 venv 里验收极快 wheel（POSIX 版）· v0.25.0 W121
#
# 这是 scripts/verify_wheel_e2e.ps1 的等价物。为什么要有两份：
#   .ps1 是 PowerShell 5.1 专用（BOM 要求、$LASTEXITCODE、Join-Path），
#   gitea 那台 FreeBSD host runner 上没有 pwsh，跑不了。而这一步恰恰是
#   **唯一能抓住「editable 恰好还能回溯到旧位置」这种假绿的检查**——
#   BACKLOG §10 那次事故（PyPI 0.4.1 wheel 里零个 stdlib 文件）在本机
#   editable 下完全看不出来。只有 .ps1 版就等于这一步永远进不了 CI。
#
# 两份要保持行为等价。改了一份就改另一份，验收项清单见下面五步。
#
# 用法：
#   sh scripts/verify_wheel_e2e.sh                # 现构建 wheel 再验
#   sh scripts/verify_wheel_e2e.sh dist/xxx.whl   # 验已有 wheel
#   保留临时目录排障：JK_E2E_KEEP=1 sh scripts/verify_wheel_e2e.sh
#
# 退出码 0 全绿 / 1 任一项失败。

set -u

# `jk` 的 stdout 被管道捕获（非 tty）时 CPython 按 locale 编码写，FreeBSD 上
# locale 可能是 C/POSIX，中文会炸成 UnicodeEncodeError。钉死为 UTF-8。
PYTHONIOENCODING=utf-8
export PYTHONIOENCODING

仓库根=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
e=${TMPDIR:-/tmp}/jk_e2e
wheel=${1:-}

失败数=0
记失败() {
    失败数=$((失败数 + 1))
    echo "  [失败] $1"
}

# --- Step 1: 构建 + 装进全新 venv ---------------------------------------
echo "[1/5] 构建 wheel、建全新 venv、安装"
rm -rf "$e"
mkdir -p "$e"

if [ -z "$wheel" ]; then
    rm -rf "$仓库根/dist"
    ( cd "$仓库根" && python -m build --wheel >/dev/null ) || {
        echo "python -m build 失败（是否漏装 build 包？）"
        exit 1
    }
    # 取最新的那个 .whl
    wheel=$(ls -1t "$仓库根"/dist/*.whl 2>/dev/null | head -n 1)
fi
if [ ! -f "$wheel" ]; then
    echo "找不到 wheel：$wheel"
    exit 1
fi
echo "  wheel = $wheel"

python -m venv "$e/venv"
py="$e/venv/bin/python"
jk="$e/venv/bin/jk"
"$e/venv/bin/pip" install -q --no-deps "$wheel" || 记失败 "pip install 失败"

# --- Step 2: 四条验收命令 -----------------------------------------------
echo "[2/5] 四条验收命令"

# .jk 文件不能带 BOM——词法器会噎住。heredoc 天然无 BOM。
cat > "$e/hello.jk" <<'JK'
打印 "你好，极快"。
JK
"$jk" "$e/hello.jk" || 记失败 "hello.jk 退出码非 0"

cat > "$e/seg.jk" <<'JK'
从 分词 导入 分词。
打印 分词("个人所得税起征点")。
JK
seg=$("$jk" "$e/seg.jk" 2>&1) || 记失败 "seg.jk 退出码非 0"
echo "  分词输出：$seg"
# 真词典随包发行时「个人所得税」会整词切出；词典缺失会退化成逐字切，
# 而**退出码照样 0**——所以必须断言内容，不能只看退出码。
case "$seg" in
    *个人所得税*) : ;;
    *) 记失败 "分词没切出「个人所得税」，疑似退化为逐字：$seg" ;;
esac

"$jk" 块 选 "月薪两万个税多少" --json > "$e/sel.json" || 记失败 "块 选 退出码非 0"
"$py" - "$e/sel.json" <<'PY' || 记失败 "块 选 候选里没有『个税』"
import io, json, sys
with io.open(sys.argv[1], encoding='utf-8-sig') as f:
    d = json.load(f)
sys.exit(0 if any(x['名称'] == '个税' for x in d['候选']) else 3)
PY

# `jk 包 列表` 要求 cwd 有 包.json——在初始化后的目录里跑才是真验收
mkdir -p "$e/pkg"
( cd "$e/pkg" && "$jk" 包 初始化 >/dev/null && "$jk" 包 列表 >/dev/null ) \
    || 记失败 "包 初始化+列表 退出码非 0"

# --- Step 3: stdlib 落在 site-packages，未回落源码树 ---------------------
echo "[3/5] stdlib 定位在 site-packages"
"$py" - <<'PY' || 记失败 "stdlib 未定位到 site-packages 或缺分词词典"
import os, sys
from jikuai import resources
d = resources.stdlib_dir()
print('  stdlib_dir =', d)
ok = ('site-packages' in d.replace('\\', '/')) and \
     os.path.isfile(resources.stdlib_path('分词词典.txt'))
sys.exit(0 if ok else 3)
PY

# --- Step 4: JIKUAI_STDLIB 覆盖口 ---------------------------------------
echo "[4/5] JIKUAI_STDLIB 覆盖口"
源stdlib="$仓库根/src/jikuai/stdlib"
ov=$(JIKUAI_STDLIB="$源stdlib" "$py" -c \
    "from jikuai import resources; print(resources.stdlib_dir())")
期望=$(CDPATH= cd -- "$源stdlib" && pwd)
if [ "$ov" != "$期望" ]; then
    记失败 "JIKUAI_STDLIB 覆盖没生效：$ov（期望 $期望）"
fi

# --- Step 5: 清理 + 汇总 -------------------------------------------------
echo "[5/5] 清理"
if [ "${JK_E2E_KEEP:-}" = "1" ]; then
    echo "  保留：$e"
else
    rm -rf "$e"
fi

if [ "$失败数" -ne 0 ]; then
    echo ""
    echo "wheel e2e 验收失败（$失败数 项）"
    exit 1
fi
echo ""
echo "wheel e2e 验收全绿"
exit 0
