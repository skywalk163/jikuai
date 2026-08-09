# 极快 JiKuai · 性能基准（T8）

## 快速开始

```bash
python benches/run_bench.py                # 跑全部基准
python benches/run_bench.py 斐波那契         # 只跑名字含 "斐波那契" 的
python benches/run_bench.py --轮数 10        # 覆盖默认 5 轮采样
python benches/run_bench.py --json out.json  # 详细数据落盘
python benches/run_bench.py --no-aot         # 强制只跑解释器（对比用）
```

## 设计原则

- **只测计算密集**：4 个基准全部纯整数 + 控制流，落在 AOT 子集内。字符串/
  列表 / OOP 目前不在 AOT 子集，测了也没得比。
- **两条独立子进程链**：解释器与 AOT 各自跑独立子进程，让 GC / pyc 缓存不
  影响下一次采样。子进程启动开销**都算进去**，因为用户看到的就是端到端时间。
- **中位数而非均值**：GC 抖动是长尾分布，均值会被拉偏。
- **正确性守护**：每个基准声明期望输出，两条链的 stdout 都要精确匹配。任何
  一条对不上就把该行标为"结果不可信"——性能基准最大的坑是"你测的根本不是
  同一件事"。
- **无 C 编译器降级**：AOT 一栏空着 + 明确原因，不假装"AOT = 解释器"，避免
  误导后续决策。

## 基准清单

| 名字        | 考察点                                     | 期望输出   |
|-------------|--------------------------------------------|------------|
| 斐波那契    | 递归压力：`fib(30)`，函数调用+栈行为        | 832040     |
| 求和_百万   | 范围 for + 全局累加器：Σ 1..10⁶            | 500000500  |
| Collatz     | while 循环 + 整数分支：1..10⁴ 累加步数     | 849666     |
| 嵌套循环    | 双重 for + 取模判定：25 万次迭代            | 2497500    |

## 输出解读

```
基准           解释器 (中位)   AOT (中位)     加速比    状态
--------------------------------------------------------------------
斐波那契       xxx.xx ms       xx.xx ms      xx.xx×    ok
```

- **解释器/AOT** 都是**端到端**子进程时间：含解释器启动、模块加载。二进制
  纯 CPU 用时看 `--json` 输出里 `samples` 的 min（best）。
- **加速比** = 解释器中位 / AOT 中位。CI 上有 gcc 后这个数字才有意义；
  本机无编译器时该列显示 `-`。
- **状态** 非 `ok` 的行请视为"不可信"：可能是编译失败、二进制崩溃、或两条链
  输出不一致。

## 加入 CI

`.github/workflows/ci.yml` 装了 gcc；在最后加一个 job 即可：

```yaml
- name: 性能基准
  run: python benches/run_bench.py --json bench-${{ matrix.python }}.json
- uses: actions/upload-artifact@v4
  with:
    name: bench-${{ matrix.python }}
    path: bench-*.json
```

这样每次 push 都有一份基准 JSON 可以纵向比对，SPU（single point of use）
够用，正式的回归门禁（"AOT 加速比不得低于历史 80%"之类）留待中央注册表
上线后再说——那时基准数字才能真正跨环境复现。
