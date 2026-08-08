"""ADR-06 X2 性能量测（D-06 触发条件 T1 的数据来源）。

对比三种编译路径在同一批真实源码上的耗时：
  A 原路径      tokenize + parse（v0.4.1 行为）
  B frontend    compile_source（两遍分词 + 静态诊断，含"无类跳过 Pass2"优化）
  C frontend    compile_source，强制单遍（JIKUAI_LEGACY_ADR06=1）

消噪：预热 1 轮，正式取 N 轮的中位数（不用均值，避免 GC / 调度尖峰拉偏）。
"""

import glob
import os
import statistics
import sys
import time

sys.path.insert(0, 'src')

from jikuai.frontend import compile_source
from jikuai.lexer import tokenize
from jikuai.parser import parse

FILES = sorted(glob.glob('examples/**/*.jk', recursive=True)) + \
        sorted(glob.glob('stdlib/*.jk'))
SOURCES = []
for path in FILES:
    with open(path, encoding='utf-8') as fh:
        SOURCES.append((path, fh.read()))

CLASS_FILES = [p for p, s in SOURCES if any(
    ln.lstrip().startswith('类') for ln in s.split('\n'))]

N = 60


def path_a():
    for _, src in SOURCES:
        parse(tokenize(src))


def path_b():
    for path, src in SOURCES:
        compile_source(src, file=path)


def bench(fn):
    fn()  # 预热
    samples = []
    for _ in range(N):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return statistics.median(samples) * 1000


print('样本文件数: %d（其中含类定义 %d 个: %s）'
      % (len(SOURCES), len(CLASS_FILES),
         ', '.join(os.path.basename(p) for p in CLASS_FILES)))
print('取样轮数: %d（中位数）' % N)

a = bench(path_a)
b = bench(path_b)

os.environ['JIKUAI_LEGACY_ADR06'] = '1'
c = bench(path_b)
del os.environ['JIKUAI_LEGACY_ADR06']

print()
print('A 原路径 tokenize+parse        : %7.2f ms' % a)
print('B frontend 两遍(含跳过优化)     : %7.2f ms  相对 A %+.1f%%' % (b, (b / a - 1) * 100))
print('C frontend 强制单遍(LEGACY=1)   : %7.2f ms  相对 A %+.1f%%' % (c, (c / a - 1) * 100))
print()
print('两遍机制本身的净开销 (B - C)     : %7.2f ms  (%+.1f%% of A)'
      % (b - c, (b - c) / a * 100))
print('静态诊断 check_program 的开销    : 含在 B 与 C 中（两者都跑）')
