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
# **变量名一律 ASCII（W126 修）**：POSIX shell 的 name 产生式是
# [A-Za-z_][A-Za-z0-9_]*，中文标识符不合法。FreeBSD /bin/sh 会把
# `仓库根=/x` 当成**命令**去执行，报 `仓库根=/x: not found` 然后带着空值往下走，
# 而且**前面几步照样打出成功日志**——比直接报错更难查。孪生的 .ps1 里用中文变量名
# 没问题（PowerShell 支持 Unicode 标识符），所以这条不能靠「照抄另一份」推出来。
# 注释和 echo 里的中文不受影响，只有标识符受限。
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

repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
e=${TMPDIR:-/tmp}/jk_e2e
wheel=${1:-}

fails=0
note_fail() {
    fails=$((fails + 1))
    echo "  [失败] $1"
}

# --- Step 1: 构建 + 装进全新 venv ---------------------------------------
echo "[1/5] 构建 wheel、建全新 venv、安装"
rm -rf "$e"
mkdir -p "$e"

if [ -z "$wheel" ]; then
    rm -rf "$repo/dist"
    ( cd "$repo" && python -m build --wheel >/dev/null ) || {
        echo "python -m build 失败（是否漏装 build 包？）"
        exit 1
    }
    # 取最新的那个 .whl
    wheel=$(ls -1t "$repo"/dist/*.whl 2>/dev/null | head -n 1)
fi
if [ ! -f "$wheel" ]; then
    echo "找不到 wheel：$wheel"
    exit 1
fi
echo "  wheel = $wheel"

python -m venv "$e/venv"
py="$e/venv/bin/python"
jk="$e/venv/bin/jk"
"$e/venv/bin/pip" install -q --no-deps "$wheel" || note_fail "pip install 失败"

# --- Step 2: 四条验收命令 -----------------------------------------------
echo "[2/5] 四条验收命令"

# .jk 文件不能带 BOM——词法器会噎住。heredoc 天然无 BOM。
cat > "$e/hello.jk" <<'JK'
打印 "你好，极快"。
JK
"$jk" "$e/hello.jk" || note_fail "hello.jk 退出码非 0"

cat > "$e/seg.jk" <<'JK'
从 分词 导入 分词。
打印 分词("个人所得税起征点")。
JK
seg=$("$jk" "$e/seg.jk" 2>&1) || note_fail "seg.jk 退出码非 0"
echo "  分词输出：$seg"
# 真词典随包发行时「个人所得税」会整词切出；词典缺失会退化成逐字切，
# 而**退出码照样 0**——所以必须断言内容，不能只看退出码。
case "$seg" in
    *个人所得税*) : ;;
    *) note_fail "分词没切出「个人所得税」，疑似退化为逐字：$seg" ;;
esac

"$jk" 块 选 "月薪两万个税多少" --json > "$e/sel.json" || note_fail "块 选 退出码非 0"
"$py" - "$e/sel.json" <<'PY' || note_fail "块 选 候选里没有『个税』"
import io, json, sys
with io.open(sys.argv[1], encoding='utf-8-sig') as f:
    d = json.load(f)
sys.exit(0 if any(x['名称'] == '个税' for x in d['候选']) else 3)
PY

# `jk 包 列表` 要求 cwd 有 包.json——在初始化后的目录里跑才是真验收
mkdir -p "$e/pkg"
( cd "$e/pkg" && "$jk" 包 初始化 >/dev/null && "$jk" 包 列表 >/dev/null ) \
    || note_fail "包 初始化+列表 退出码非 0"

# --- Step 3: stdlib 落在 site-packages，未回落源码树 ---------------------
echo "[3/5] stdlib 定位在 site-packages"
"$py" - <<'PY' || note_fail "stdlib 未定位到 site-packages 或缺分词词典"
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
src_stdlib="$repo/src/jikuai/stdlib"
if [ ! -d "$src_stdlib" ]; then
    note_fail "源码树里没有 $src_stdlib，无法验覆盖口"
else
    ov=$(JIKUAI_STDLIB="$src_stdlib" "$py" -c \
        "from jikuai import resources; print(resources.stdlib_dir())")
    expected=$(CDPATH= cd -- "$src_stdlib" && pwd)
    echo "  stdlib_dir(覆盖后) = $ov"
    if [ "$ov" != "$expected" ]; then
        note_fail "JIKUAI_STDLIB 覆盖没生效：$ov（期望 $expected）"
    fi
fi

# --- Step 5: 清理 + 汇总 -------------------------------------------------
echo "[5/5] 清理"
if [ "${JK_E2E_KEEP:-}" = "1" ]; then
    echo "  保留：$e"
else
    rm -rf "$e"
fi

if [ "$fails" -ne 0 ]; then
    echo ""
    echo "wheel e2e 验收失败（$fails 项）"
    exit 1
fi
echo ""
echo "wheel e2e 验收全绿"
exit 0
