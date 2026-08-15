# -*- coding: utf-8 -*-
"""极快分词词典生成器（v0.23.0 W109，ADR-38）。

从三个来源合并出 `stdlib/分词词典.txt`：
  1. 必留种子集：`tools/dict/种子词.txt`（现有 565 条，无条件全留）
  2. 通用底座：jieba `dict.txt`，按词频降序取 top N（默认 50000，见 ADR-38 §3.1）
  3. 域内增强：THUOCL `caijing` / `law` 两个词表全量

过滤规则（ADR-38 §3/§4）：只收 **2 ≤ 长度 ≤ 8 且全为汉字** 的词。
合并顺序 = 种子 ∪ 底座 ∪ 增强，并**断言 seed - merged == ∅**（§3.2 必留约束）。

产物（写到 stdlib/）：
  - `分词词典.txt`         明文，一行一词，码点升序，结尾单换行
  - `分词词典.元信息.json`  每源 URL/license/取用规则/条数 + 合并 sha256
  - `分词词典来源.md`       人读授权（转录两份 MIT + THUOCL 引用格式）

上游源缓存在 `tools/dict/上游/`（不入仓）。有缓存就不联网；缺失时按 URL 下载
（raw.githubusercontent 偶发超时，带 retry）。

用法：
  python tools/dict/重生成词典.py            # 用缓存/联网，产出三件套
  python tools/dict/重生成词典.py --topn 50000
  python tools/dict/重生成词典.py --check     # 只校验 stdlib 产物与当前源一致，不写

本脚本是**离线工具**，不属于运行时；`stdlib/分词.py` 只读产物，不 import 本脚本。
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

# --- 路径 ---
仓库根 = Path(__file__).resolve().parents[2]
种子文件 = 仓库根 / "tools" / "dict" / "种子词.txt"
上游目录 = 仓库根 / "tools" / "dict" / "上游"
词典输出 = 仓库根 / "stdlib" / "分词词典.txt"
元信息输出 = 仓库根 / "stdlib" / "分词词典.元信息.json"
来源输出 = 仓库根 / "stdlib" / "分词词典来源.md"

词长下限 = 2
词长上限 = 8  # ADR-38 §4：FMM 窗口上界，丢 0.15% 长词换约 28% 速度

# --- 上游源定义 ---
源定义 = {
    "jieba": {
        "本地名": "jieba.dict.txt",
        "url": "https://raw.githubusercontent.com/fxsjy/jieba/master/jieba/dict.txt",
        "license": "MIT",
        "版权": "Copyright (c) 2013 Sun Junyi",
        "取用": "按第二列词频降序取 top N（本次 N 见 组成.topn）",
    },
    "THUOCL_caijing": {
        "本地名": "THUOCL_caijing.txt",
        "url": "https://raw.githubusercontent.com/thunlp/THUOCL/master/data/THUOCL_caijing.txt",
        "license": "MIT",
        "版权": "Copyright (c) 2018 THUNLP",
        "取用": "全量过滤后取",
    },
    "THUOCL_law": {
        "本地名": "THUOCL_law.txt",
        "url": "https://raw.githubusercontent.com/thunlp/THUOCL/master/data/THUOCL_law.txt",
        "license": "MIT",
        "版权": "Copyright (c) 2018 THUNLP",
        "取用": "全量过滤后取",
    },
}


def 合法词(w):
    """ADR-38 §3/§4 过滤：2 ≤ 长度 ≤ 8 且全为汉字。"""
    return (词长下限 <= len(w) <= 词长上限
            and all("\u4e00" <= c <= "\u9fff" for c in w))


def 取源文本(键):
    """读上游源。优先本地缓存；缺失则下载并写入缓存（带 retry）。"""
    信息 = 源定义[键]
    路径 = 上游目录 / 信息["本地名"]
    if 路径.exists() and 路径.stat().st_size > 1000:
        return 路径.read_text(encoding="utf-8", errors="replace")
    上游目录.mkdir(parents=True, exist_ok=True)
    最后异常 = None
    for 次 in range(5):
        try:
            数据 = urllib.request.urlopen(信息["url"], timeout=300).read()
            路径.write_bytes(数据)
            return 数据.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 - 下载失败重试
            最后异常 = e
            print("  重试 %s (%d/5): %s" % (键, 次 + 1, type(e).__name__),
                  file=sys.stderr)
            time.sleep(3)
    raise RuntimeError("下载 %s 失败：%s" % (键, 最后异常))


def 读种子():
    if not 种子文件.exists():
        raise RuntimeError("缺少必留种子文件 %s" % 种子文件)
    词 = set()
    for 行 in 种子文件.read_text(encoding="utf-8").splitlines():
        行 = 行.strip()
        if not 行 or 行.startswith("#"):
            continue
        词.add(行)
    return 词


def 抽jieba底座(文本, topn):
    条目 = []
    for 行 in 文本.splitlines():
        列 = 行.split()
        if len(列) < 2:
            continue
        try:
            频 = int(列[1])
        except ValueError:
            continue
        if 合法词(列[0]):
            条目.append((频, 列[0]))
    条目.sort(reverse=True)
    return [w for _, w in 条目[:topn]]


def 抽THUOCL(文本):
    词 = set()
    for 行 in 文本.splitlines():
        列 = 行.split()
        if 列 and 合法词(列[0]):
            词.add(列[0])
    return 词


def 构建(topn):
    种子 = 读种子()

    jieba文本 = 取源文本("jieba")
    底座 = set(抽jieba底座(jieba文本, topn))

    财经 = 抽THUOCL(取源文本("THUOCL_caijing"))
    法律 = 抽THUOCL(取源文本("THUOCL_law"))

    合并 = 种子 | 底座 | 财经 | 法律

    # ADR-38 §3.2：必留种子不可被频率截断
    缺失 = 种子 - 合并
    if 缺失:
        raise AssertionError(
            "必留种子有 %d 条未进入词典（ADR-38 §3.2 违约）：%s"
            % (len(缺失), " ".join(sorted(缺失)[:20])))

    统计 = {
        "种子": len(种子),
        "jieba底座": len(底座),
        "THUOCL_caijing": len(财经),
        "THUOCL_law": len(法律),
        "topn": topn,
    }
    return 合并, 统计


def 词典字节(词集):
    """码点升序、一行一词、结尾单换行。排序固定以保证 sha256 可复现。"""
    return ("\n".join(sorted(词集)) + "\n").encode("utf-8")


def 生成元信息(词集, 统计):
    字节 = 词典字节(词集)
    源列表 = []
    for 键, 信息 in 源定义.items():
        源列表.append({
            "名称": 键,
            "url": 信息["url"],
            "license": 信息["license"],
            "版权": 信息["版权"],
            "取用规则": 信息["取用"],
        })
    return {
        "说明": "由 tools/dict/重生成词典.py 生成，勿手改。见 docs/ADR-38-中文分词词典.md。",
        "算法": "正向最大匹配（FMM）",
        "词长范围": [词长下限, 词长上限],
        "词条数": len(词集),
        "sha256": hashlib.sha256(字节).hexdigest(),
        "组成": 统计,
        "上游源": 源列表,
    }


来源正文模板 = """# 极快分词词典 · 来源与授权

`stdlib/分词词典.txt` 是**生成产物**，由 `tools/dict/重生成词典.py` 从下列公开词库
合并而成。合并规则见 [`docs/ADR-38-中文分词词典.md`](../docs/ADR-38-中文分词词典.md)。
机读的来源与校验和见同目录 `分词词典.元信息.json`。

当前词典：**{词条数} 条**，sha256 `{sha256}`。

组成：
- 必留种子 {种子} 条（`tools/dict/种子词.txt`，极快自有）
- jieba `dict.txt` 通用底座 top{topn} = {jieba底座} 条
- THUOCL 财经 {THUOCL_caijing} 条 + 法律 {THUOCL_law} 条

---

## 一、jieba（`fxsjy/jieba`）

- 用途：通用词底座（`dict.txt`，按词频取 top{topn}）。
- 授权：MIT。Copyright (c) 2013 Sun Junyi。

```
The MIT License (MIT)
Copyright (c) 2013 Sun Junyi

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

**残留风险（如实记录）**：jieba 的 `dict.txt` 上游语料来源（人民日报语料等）jieba
自身未逐条声明。我们依赖其仓库的 MIT 授权 + 署名，这是业界通行做法（jieba 已被
Debian 收录分发），但不等于逐词条可追溯授权。若上游澄清出更严格条件，处置方式是
换源重跑本脚本——词典是生成产物，替换底座不影响 FMM 代码。

## 二、THUOCL（`thunlp/THUOCL`，清华大学开放中文词库）

- 用途：域内增强（`caijing` 财经 + `law` 法律）。
- 授权：MIT。Copyright (c) 2018 THUNLP。README 另有明文：「面向国内外大学、研究所、
  企业、机构以及个人免费开放，**可用于研究与商业**」。

```
MIT License
Copyright (c) 2018 THUNLP

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

**引用声明（THUOCL README 要求）**：使用了清华大学开放中文词库。

> 韩世依, 张钰晖, 马云山, 涂存超, 郭志芃, 刘知远, 孙茂松. THUOCL：清华大学开放中文词库. 2016.
>
> Shiyi Han, Yuhui Zhang, Yunshan Ma, Cunchao Tu, Zhipeng Guo, Zhiyuan Liu,
> Maosong Sun. THUOCL: Tsinghua Open Chinese Lexicon. 2016.
"""


def 写来源(元信息, 统计):
    正文 = 来源正文模板.format(
        词条数=元信息["词条数"],
        sha256=元信息["sha256"],
        种子=统计["种子"],
        topn=统计["topn"],
        jieba底座=统计["jieba底座"],
        THUOCL_caijing=统计["THUOCL_caijing"],
        THUOCL_law=统计["THUOCL_law"],
    )
    来源输出.write_text(正文, encoding="utf-8", newline="\n")


def main():
    解析器 = argparse.ArgumentParser(description="生成极快分词词典")
    解析器.add_argument("--topn", type=int, default=50000,
                        help="jieba 底座按词频取的条数（默认 50000，见 ADR-38 §3.1）")
    解析器.add_argument("--check", action="store_true",
                        help="只校验 stdlib 产物与当前源一致，不写文件")
    参数 = 解析器.parse_args()

    词集, 统计 = 构建(参数.topn)
    元信息 = 生成元信息(词集, 统计)
    字节 = 词典字节(词集)

    if 参数.check:
        if not 词典输出.exists():
            print("[FAIL] %s 不存在" % 词典输出)
            return 1
        现有 = 词典输出.read_bytes()
        现有哈希 = hashlib.sha256(现有).hexdigest()
        if 现有哈希 != 元信息["sha256"]:
            print("[FAIL] 词典 sha256 不一致：现有 %s，重算 %s"
                  % (现有哈希[:16], 元信息["sha256"][:16]))
            return 1
        print("[OK] 词典与当前源一致：%d 条，sha256 %s"
              % (元信息["词条数"], 元信息["sha256"][:16]))
        return 0

    词典输出.write_bytes(字节)
    元信息输出.write_text(
        json.dumps(元信息, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    写来源(元信息, 统计)

    print("词典已生成：%d 条 / %d 字节 / sha256 %s"
          % (元信息["词条数"], len(字节), 元信息["sha256"][:16]))
    print("组成：", 统计)
    print("产物：")
    for p in (词典输出, 元信息输出, 来源输出):
        print("  ", p.relative_to(仓库根))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
