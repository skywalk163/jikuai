"""G21 · 三包发行元数据门禁（纯标准库，不需要 twine）。

为什么不用 `twine check`
------------------------
`twine check` 在 gitea 那台 FreeBSD runner 上**装不上**：twine → readme_renderer →
`nh3`，而 nh3 是 Rust 扩展、PyPI 上没有 FreeBSD 轮子，pip 只能回退到源码构建，于是要
maturin + Rust 工具链，实测直接失败：

    Unsupported platform: 311 / Rust not found, installing into a temporary directory
    ERROR: Failed to build 'nh3' when installing build dependencies for nh3

（查过 readme_renderer 42.0/43.0/44.0/45.0 的 requires_dist，**全都**钉 `nh3>=0.2.14`，
没有「换个版本就不用 Rust」这条路。）

所以本脚本承接 `twine check` 里**我们真正需要**的那部分，用标准库重写：

- W118 那次给 lsp/dap 钉 `jikuai>=0.24.0` 下界，是手改的 —— 掉了没人会发现，直到
  有人装了配不上的组合。
- v0.24 发版当天才发现元数据问题，代价是补发。
- 「三包版号一致」G15 只管**源码里**的四处投影，管不到**构建产物**里的版号 ——
  dist/ 里留着上一版的 wheel 照样能过 G15，然后被 twine upload 一起发出去。

**没覆盖的那部分要说清**：`twine check` 还会用 readme_renderer 真渲染一遍
long_description，判断它在 PyPI 页面上能不能正常显示。本脚本**不做这件事**（那正是
需要 nh3 的部分）。这一项由发版流程里人做的 **TestPyPI 预演**兜住 —— 传上去打开页面
看一眼，比渲染器更可信。

用法
----
    python scripts/check_dist_metadata.py                    # 默认查三包各自的 dist/
    python scripts/check_dist_metadata.py dist lsp/dist      # 指定目录
    python scripts/check_dist_metadata.py --要求成对          # 再要求 wheel+sdist 都在
    python scripts/check_dist_metadata.py --quiet

`--要求成对` 只给**发版流程**用。常规 CI 里主包是 `python -m build --wheel` 单独构的
（G20 与 wheel e2e 复用同一个 wheel，不必多花一次 sdist 的时间），那时缺 sdist 是预期。
发版时三包都要 sdist + wheel 全套，缺一种就等于少发一条安装路径。

退出码 0 全过 / 1 有违规 / 2 用法或环境问题（找不到产物等）。

"""

from __future__ import annotations

import argparse
import email.parser
import pathlib
import re
import sys
import tarfile
import zipfile

仓库根 = pathlib.Path(__file__).resolve().parent.parent

# 三包 → 它的产物目录。dist 名里的连字符会被规范化成下划线，两种都认。
包表 = {
    "jikuai": "dist",
    "jikuai-lsp": "lsp/dist",
    "jikuai-dap": "dap/dist",
}

# lsp / dap 必须对主包钉**下界**。W118 的教训：PyPI 上 jikuai 0.4.1 是个装完不可用的
# 坏包，没有下界的话依赖解析可能挑中它。
须钉主包下界 = {"jikuai-lsp", "jikuai-dap"}

# 允许两种写法：新 setuptools 出 `jikuai>=0.24.0`，老版本出 `jikuai (>=0.24.0)`。
# 只认一种就会在 setuptools 版本不同的机器上假红 —— 这一轮已经栽过两次同类跟头
# （W125 只认 gcc、W126 中文变量名），凡是「产物长相依赖工具版本」的地方都放宽。
_下界模式 = re.compile(r"^jikuai\s*(\[[^\]]*\])?\s*\(?\s*>=\s*(\d+\.\d+(\.\d+)?)")


def 读版本号() -> str:
    """从单一真源 src/jikuai/_version.py 读版本号，不 import 整个包。"""
    文本 = (仓库根 / "src" / "jikuai" / "_version.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', 文本)
    if not m:
        raise RuntimeError("src/jikuai/_version.py 里找不到 __version__")
    return m.group(1)


def 取wheel元数据(路径: pathlib.Path) -> str:
    with zipfile.ZipFile(路径) as z:
        名单 = [n for n in z.namelist()
                if n.endswith(".dist-info/METADATA") and n.count("/") == 1]
        if not 名单:
            raise RuntimeError(f"{路径.name} 里没有 *.dist-info/METADATA")
        return z.read(名单[0]).decode("utf-8", "replace")


def 取sdist元数据(路径: pathlib.Path) -> str:
    with tarfile.open(路径, "r:gz") as t:
        名单 = [n for n in t.getnames()
                if n.endswith("/PKG-INFO") and n.count("/") == 1]
        if not 名单:
            raise RuntimeError(f"{路径.name} 里没有顶层 PKG-INFO")
        f = t.extractfile(名单[0])
        assert f is not None
        return f.read().decode("utf-8", "replace")


def 查一个产物(路径: pathlib.Path, 包名: str, 期望版本: str) -> list[str]:
    """返回违规说明列表，空列表 = 通过。"""
    问题: list[str] = []
    原文 = (取wheel元数据(路径) if 路径.suffix == ".whl"
            else 取sdist元数据(路径))
    元 = email.parser.Parser().parsestr(原文)

    名 = (元.get("Name") or "").strip()
    版 = (元.get("Version") or "").strip()
    规范名 = 名.replace("_", "-").lower()
    if 规范名 != 包名:
        问题.append(f"Name={名!r}，期望 {包名!r}")
    if 版 != 期望版本:
        # 最常见的成因不是「写错了」，而是 dist/ 里躺着上一版的产物没清掉。
        问题.append(f"Version={版!r}，期望 {期望版本!r}"
                    f"（dist/ 里可能残留旧产物，构建前该 rm -rf）")

    # long_description 有内容时必须声明类型，否则 PyPI 会按 plain text 渲染成一坨。
    if (元.get_payload() or "").strip() or 元.get("Description"):
        if not (元.get("Description-Content-Type") or "").strip():
            问题.append("有 long_description 但缺 Description-Content-Type")

    if 包名 in 须钉主包下界:
        依赖 = [d.strip() for d in 元.get_all("Requires-Dist") or []]
        主包依赖 = [d for d in 依赖 if d.split()[0].split("[")[0].split(">")[0]
                    .split("=")[0].split("<")[0].strip().lower() == "jikuai"]
        if not 主包依赖:
            问题.append("Requires-Dist 里没有对 jikuai 的依赖")
        elif not any(_下界模式.match(d) for d in 主包依赖):
            问题.append(f"对 jikuai 的依赖没钉 >= 下界：{主包依赖}"
                        f"（W118：PyPI 上 0.4.1 是坏包，无下界可能被解析选中）")
    return 问题


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="G21 · 三包发行元数据门禁")
    p.add_argument("目录", nargs="*", help="产物目录，默认三包各自的 dist/")
    p.add_argument("--要求成对", action="store_true",
                   help="额外要求每个目录里 wheel 与 sdist 都在（发版流程用）")
    p.add_argument("--quiet", action="store_true", help="只在失败时输出")
    args = p.parse_args(argv)

    期望版本 = 读版本号()
    if not args.quiet:
        print(f"G21 · 三包发行元数据门禁（期望版号 {期望版本}）")

    # 目录 → 包名。指定了目录就按指定的查，否则查三包全部。
    目录到包 = {v: k for k, v in 包表.items()}
    待查 = [(目录到包.get(d.replace("\\", "/"), None), 仓库根 / d)
            for d in (args.目录 or list(包表.values()))]

    退出码 = 0
    查过 = 0
    for 包名, 目录 in 待查:
        if 包名 is None:
            print(f"  [错误] 不认识的产物目录 {目录}，认得的是 {list(包表.values())}")
            return 2
        if not 目录.is_dir():
            print(f"  [错误] {包名}：产物目录不存在 {目录}")
            return 2

        产物 = sorted(list(目录.glob("*.whl")) + list(目录.glob("*.tar.gz")))
        if not 产物:
            print(f"  [错误] {包名}：{目录} 里没有 .whl / .tar.gz")
            return 2
        # 三包同发，缺一种格式就等于少发一种安装路径 —— 但只在发版流程里卡，
        # 常规 CI 主包是 --wheel 单构的，那时缺 sdist 是预期。
        if args.要求成对:
            if not any(x.suffix == ".whl" for x in 产物):
                print(f"  [失败] {包名}：只有 sdist，缺 wheel")
                退出码 = 1
            if not any(x.name.endswith(".tar.gz") for x in 产物):
                print(f"  [失败] {包名}：只有 wheel，缺 sdist")
                退出码 = 1

        for 文件 in 产物:
            查过 += 1
            try:
                问题 = 查一个产物(文件, 包名, 期望版本)
            except Exception as e:                      # 坏归档也算违规
                print(f"  [失败] {文件.name}：读元数据失败 —— {e}")
                退出码 = 1
                continue
            if 问题:
                退出码 = 1
                for 条 in 问题:
                    print(f"  [失败] {文件.name}：{条}")
            elif not args.quiet:
                print(f"  [通过] {文件.name}")

    if 退出码 == 0 and not args.quiet:
        print(f"G21 通过：{查过} 个产物元数据合规。")
        print("注意：long_description 能否在 PyPI 页面正常渲染**不在本门禁范围**"
              "（那需要 readme_renderer→nh3→Rust，FreeBSD runner 上装不上），"
              "由发版流程里人做的 TestPyPI 预演兜住。")
    return 退出码


if __name__ == "__main__":
    sys.exit(main())
