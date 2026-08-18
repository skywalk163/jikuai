# -*- coding: utf-8 -*-
"""G20 · wheel 内容门禁（v0.24.0 W116 · ADR-39 §5）。

用法：
    python scripts/check_wheel_contents.py            # 构建 wheel 再校验
    python scripts/check_wheel_contents.py <路径.whl>  # 校验已有 wheel

退出码 0 全绿 / 1 有问题。

**为什么不进 `check_stdlib_contract.py` 主流程**：那个文件里 G10–G19 全是纯静态
检查，秒级返回；本门禁要跑 `python -m build`，量级差两个数量级，还多一个 `build`
包依赖。所以它独立成脚本，由 `check_stdlib_contract.py` 在输出末尾提示一句，
免得被静默忘掉。
"""

import os
import posixpath
import subprocess
import sys
import zipfile

# Windows 控制台默认 GBK，条目名全是中文，强制 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

#: 块元数据 json 的下界。112 是 v0.23.0 的块数；只设下界不设上界会重演
#: 「守卫绿≠守卫在守」，故同时设一个宽松上界哨。
块JSON下界 = 112
块JSON上界 = 500

#: 块背衬 `.py` 的精确条数（ADR-16 §3.3 混合模块）。W114 执行期实测 14 个；
#: v0.26.0（W130-W145）制造域 Chat BI 引入 21 个块背衬（表载入/连接/产量汇总/
#: 缺陷率/质量体检/投影/选取/排序/取前N/分组汇总/达成率均值/达成率权重/
#: 单车电耗现成/单车电耗重算/能耗汇总/缺陷汇总/延期汇总/延期排行/停线汇总/
#: 班间对比/表元信息），14 → 35。
#: **这条是本门禁存在的核心理由之一**：原验收线（3 个具名文件 + 块 json 数 +
#: 无 pyc）在漏掉全部 14 个背衬 .py 时会全绿，而 `圆分`（`财务/保留分/保留分.py`）
#: 被 13 个财务块引用（含 `个税`/`增值税`），漏了就是旗舰块运行期炸。
#: 用等值而非下界：块背衬数变了就该有人来改这个数并解释一句。
#: v0.26.0 实测代价：这个数没跟着改，gitea run 49（ci.yml）/ 50（release.yml）
#: 双红——`tests/test_wheel_contents.py` 的合成条目断言全绿也拦不住，
#: 故该文件同时补了一条「常量 ↔ 源码树」对账测试，把漂移提前到本地 pytest。
块背衬PY数 = 35

#: 不许随包发行的产物名前缀。W114 实测：块自测在源码树里写出的 9 个
#: `临时_测试*.txt` 真的进过 wheel。根因（自测污染源码树）W115 已修，
#: 但门禁要独立守——根因修了不等于以后没别的东西漏进来。
禁发前缀 = ('临时_测试',)


def 必需条目():
    """wheel 里必须出现的具名条目。"""
    return (
        'jikuai/stdlib/分词词典.txt',
        'jikuai/stdlib/blocks/向量索引.bin',
        'jikuai/stdlib/blocks/索引.json',
        # 混合模块背衬的代表样本，见 块背衬PY数 的注释
        'jikuai/stdlib/blocks/财务/保留分/保留分.py',
    )


def 校验wheel条目(条目):
    """对条目名列表做断言，返回问题描述列表（空 = 全绿）。"""
    问题 = []
    有 = set(条目)
    for 必需 in 必需条目():
        if 必需 not in 有:
            问题.append('wheel 里缺必需资源：%s' % 必需)

    泄漏 = [n for n in 条目 if n.endswith('.pyc') or '__pycache__' in n]
    if 泄漏:
        问题.append('wheel 里泄漏了 .pyc / __pycache__（%d 个），首个：%s'
                    % (len(泄漏), 泄漏[0]))

    禁发 = [n for n in 条目
            if posixpath.basename(n).startswith(禁发前缀)]
    if 禁发:
        问题.append('wheel 里混进了不该发行的产物（%d 个），首个：%s——'
                    '前缀名单见 禁发前缀' % (len(禁发), 禁发[0]))

    块json = [n for n in 条目
              if n.startswith('jikuai/stdlib/blocks/')
              and n.endswith('.json')
              and n.count('/') >= 5]
    if len(块json) < 块JSON下界:
        问题.append('块元数据 json 只有 %d 个，低于下界 %d'
                    % (len(块json), 块JSON下界))
    if len(块json) > 块JSON上界:
        问题.append('块元数据 json 有 %d 个，超过上界哨 %d——'
                    '块库真的长这么多了就上调这个数，别删这条哨'
                    % (len(块json), 块JSON上界))

    块py = [n for n in 条目
            if n.startswith('jikuai/stdlib/blocks/')
            and n.endswith('.py')
            and n.count('/') >= 5]
    if len(块py) != 块背衬PY数:
        问题.append('块背衬 .py 有 %d 个，期望 %d 个——'
                    '漏了它们旗舰块会在运行期炸；块背衬真变了就改这个常量'
                    % (len(块py), 块背衬PY数))
    return 问题


def 构建wheel(仓库根, 输出目录):
    """构建 wheel，返回文件路径。"""
    subprocess.run([sys.executable, '-m', 'build', '--wheel',
                    '--outdir', 输出目录, 仓库根], check=True)
    whls = [f for f in os.listdir(输出目录) if f.endswith('.whl')]
    if not whls:
        raise RuntimeError('构建后 %s 里没有 .whl' % 输出目录)
    whls.sort(key=lambda f: os.path.getmtime(os.path.join(输出目录, f)))
    return os.path.join(输出目录, whls[-1])


def main(argv):
    if len(argv) > 1:
        wheel = argv[1]
    else:
        仓库根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        wheel = 构建wheel(仓库根, os.path.join(仓库根, 'dist'))
    with zipfile.ZipFile(wheel) as z:
        条目 = z.namelist()
    问题 = 校验wheel条目(条目)
    if 问题:
        print('G20 wheel 内容门禁：失败（%s）' % wheel)
        for p in 问题:
            print('  - %s' % p)
        return 1
    print('G20 wheel 内容门禁：通过（%s，%d 个条目）' % (wheel, len(条目)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
