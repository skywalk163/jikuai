# -*- coding: utf-8 -*-
"""切分质量基准评测（v0.23.0 W112，ADR-38 §2 验收标准3）。

拿 `切分基准.json` 里人工标注的期望切分，量当前 `stdlib/分词` 的**边界准确率**
与**词级 F1**，并与「无词典基线」（逐字切）对照，证明词典扩容带来可测的提升。

**量的是分词质量本身，不碰任何检索指标**（ADR-38 非目标）。

边界 F1：把切分看作在字序列上放「词边界」。设句长 n，边界位置有 n-1 个内部间隙
（末尾边界所有切法都相同，不计）。预测边界集合与期望边界集合求 P/R/F1。

词级 F1：把切分看作 (起点,词) 的集合，求 P/R/F1。词级更严格。

用法：python tools/dict/切分质量评测.py
"""
import json
import importlib.util
import os

仓库根 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
基准文件 = os.path.join(仓库根, "tools", "dict", "切分基准.json")


def 加载分词():
    path = os.path.join(仓库根, "stdlib", "分词.py")
    spec = importlib.util.spec_from_file_location("seg_bench", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def 词序列转边界(词序列):
    """把词序列转成内部边界位置集合（每个词的右端字符下标，末尾那个不计）。"""
    边界 = set()
    pos = 0
    for w in 词序列:
        pos += len(w)
        边界.add(pos)
    边界.discard(sum(len(w) for w in 词序列))  # 去掉句末边界
    return 边界


def 词序列转跨度(词序列):
    跨度 = set()
    pos = 0
    for w in 词序列:
        跨度.add((pos, w))
        pos += len(w)
    return 跨度


def prf(预测, 期望):
    if not 预测 and not 期望:
        return 1.0, 1.0, 1.0
    命中 = len(预测 & 期望)
    p = 命中 / len(预测) if 预测 else 0.0
    r = 命中 / len(期望) if 期望 else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def 逐字切(文本):
    """无词典基线：每个字符单独成词（B2 半角串仍整体，与真实兜底一致的下界近似）。"""
    return list(文本)


def 评测():
    seg = 加载分词()
    data = json.load(open(基准文件, encoding="utf-8"))
    用例 = data["用例"]

    def 汇总(切法):
        b_tp = b_fp = b_fn = 0
        w_tp = w_fp = w_fn = 0
        全对 = 0
        for c in 用例:
            got = 切法(c["文本"])
            exp = c["期望"]
            gb, eb = 词序列转边界(got), 词序列转边界(exp)
            b_tp += len(gb & eb); b_fp += len(gb - eb); b_fn += len(eb - gb)
            gw, ew = 词序列转跨度(got), 词序列转跨度(exp)
            w_tp += len(gw & ew); w_fp += len(gw - ew); w_fn += len(ew - gw)
            if got == exp:
                全对 += 1

        def f(tp, fp, fn):
            p = tp / (tp + fp) if (tp + fp) else 1.0
            r = tp / (tp + fn) if (tp + fn) else 1.0
            return 2 * p * r / (p + r) if (p + r) else 0.0, p, r

        bf, bp, br = f(b_tp, b_fp, b_fn)
        wf, wp, wr = f(w_tp, w_fp, w_fn)
        return {
            "边界F1": bf, "边界P": bp, "边界R": br,
            "词级F1": wf, "词级P": wp, "词级R": wr,
            "整句全对": 全对, "总数": len(用例),
        }

    词典结果 = 汇总(seg.segment)
    基线结果 = 汇总(逐字切)

    print("== 切分质量基准（%d 句，词典 %d 条）==" % (len(用例), seg.dictionary_size()))
    print()
    print("%-10s %8s %8s %8s %8s" % ("", "边界F1", "词级F1", "整句全对", "词级P/R"))
    print("%-10s %8.3f %8.3f %6d/%d  %.3f/%.3f" % (
        "扩容词典", 词典结果["边界F1"], 词典结果["词级F1"],
        词典结果["整句全对"], 词典结果["总数"],
        词典结果["词级P"], 词典结果["词级R"]))
    print("%-10s %8.3f %8.3f %6d/%d  %.3f/%.3f" % (
        "逐字基线", 基线结果["边界F1"], 基线结果["词级F1"],
        基线结果["整句全对"], 基线结果["总数"],
        基线结果["词级P"], 基线结果["词级R"]))
    print()
    print("边界F1 提升：%.3f → %.3f（+%.3f）" % (
        基线结果["边界F1"], 词典结果["边界F1"],
        词典结果["边界F1"] - 基线结果["边界F1"]))
    print("词级F1 提升：%.3f → %.3f（+%.3f）" % (
        基线结果["词级F1"], 词典结果["词级F1"],
        词典结果["词级F1"] - 基线结果["词级F1"]))
    print()
    print("-- 未全对的句子（供人工复核标注是否合理）--")
    for c in 用例:
        got = seg.segment(c["文本"])
        if got != c["期望"]:
            print("  文本:", c["文本"])
            print("    期望:", " / ".join(c["期望"]))
            print("    实得:", " / ".join(got))
    return 0


if __name__ == "__main__":
    raise SystemExit(评测())
