# -*- coding: utf-8 -*-
"""自动链式基准（W3-W4）—— 跑 `评测集-链式.json`，报三项指标。

指标定义：

- **自动链式命中率** = 标注 `应链` 且类型图把末步实参完全推对 / `应链` 总数
- **语义荒谬率**     = 标注 `应拒` 却被硬链上 / `应拒` 总数（必须为 0）
- **拒绝质量**       = 标注 `应拒` 且给出非空、含预期关键词的理由 / `应拒` 总数

`边界` 用例单独报：期望「未匹配非空、但不是类型冲突」（拒绝理由应为空），
用来防止把「缺标量入参」误报成「类型不兼容」。

用法::

    python tools/ai-bridge/bench_glue.py
    python tools/ai-bridge/bench_glue.py --json
"""

import argparse
import json
import os
import sys

_HERE = os.path.abspath(os.path.dirname(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass

import glue  # noqa: E402

评测集路径 = os.path.join(_HERE, '评测集-链式.json')


def 载入(path=评测集路径):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)['用例']


def 跑一条(图, 用例):
    """返回 `(通过, 详情串)`。判定口径按 `期望` 分三支。"""
    steps = 用例['步骤']
    实参, 未匹配, 拒绝 = 图.plan(steps, 用例.get('共享'))
    期望 = 用例['期望']
    末 = 实参[-1]

    if 期望 == '应链':
        想要 = (用例.get('校验') or {}).get('链入参')
        if 末 is None:
            return False, '应链但末步未链上（未匹配=%s，拒绝=%s）' % (未匹配, 拒绝)
        if 想要 is not None and 末 != 想要:
            return False, '应链但实参不符：得到 %s，期望 %s' % (末, 想要)
        return True, '链上 %s' % 末

    if 期望 == '应拒':
        if 末 is not None:
            return False, '语义荒谬：被硬链成 %s' % 末
        if not 拒绝:
            return False, '拒对了但没给理由（未匹配=%s）' % (未匹配,)
        关键 = (用例.get('校验') or {}).get('拒绝含')
        if 关键 and not any(关键 in r for r in 拒绝):
            return False, '理由未含关键词「%s」：%s' % (关键, 拒绝)
        return True, '拒绝且有理由：%s' % 拒绝[0]

    # 边界：应当报「有槽没填上」，且不能把它误报成「元组需人工拆包」
    # （后者是元组专属提示；缺标量入参给出类型不符的说明是有用信息，不算失败）
    if not 未匹配:
        return False, '边界用例却全链上了：%s' % (实参,)
    误报 = [r for r in 拒绝 if '人工拆包' in r]
    if 误报:
        return False, '边界用例被误报成元组拆包问题：%s' % 误报
    return True, '缺入参 %d 处，无元组拆包误报' % len(未匹配)


def 跑全量(path=评测集路径):
    图 = glue.TypeGraph()
    用例表 = 载入(path)
    结果 = []
    for c in 用例表:
        通过, 详情 = 跑一条(图, c)
        结果.append({'id': c['id'], '期望': c['期望'], '需求': c.get('需求', ''),
                    '通过': 通过, '详情': 详情})

    def 组(期望):
        return [r for r in 结果 if r['期望'] == 期望]

    应链, 应拒, 边界 = 组('应链'), 组('应拒'), 组('边界')
    荒谬 = [r for r in 应拒 if not r['通过'] and '语义荒谬' in r['详情']]

    def 率(通过数, 总数):
        return (通过数 / 总数) if 总数 else 0.0

    return {
        '用例数': len(结果),
        '自动链式命中率': 率(sum(1 for r in 应链 if r['通过']), len(应链)),
        '语义荒谬率': 率(len(荒谬), len(应拒)),
        '拒绝质量': 率(sum(1 for r in 应拒 if r['通过']), len(应拒)),
        '边界通过率': 率(sum(1 for r in 边界 if r['通过']), len(边界)),
        '分组规模': {'应链': len(应链), '应拒': len(应拒), '边界': len(边界)},
        '明细': 结果,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description='自动链式基准（W3-W4）')
    p.add_argument('--json', action='store_true', help='输出 JSON 报告')
    p.add_argument('--评测集', default=评测集路径)
    args = p.parse_args(argv)

    报告 = 跑全量(args.评测集)
    if args.json:
        print(json.dumps(报告, ensure_ascii=False, indent=2))
        return 0

    print('评测集：%d 条（应链 %d / 应拒 %d / 边界 %d）'
          % (报告['用例数'], 报告['分组规模']['应链'],
             报告['分组规模']['应拒'], 报告['分组规模']['边界']))
    print('自动链式命中率：%.1f%%（门槛 ≥60%%）' % (报告['自动链式命中率'] * 100))
    print('语义荒谬率：    %.1f%%（门槛 =0%%）' % (报告['语义荒谬率'] * 100))
    print('拒绝质量：      %.1f%%' % (报告['拒绝质量'] * 100))
    print('边界通过率：    %.1f%%' % (报告['边界通过率'] * 100))
    失败 = [r for r in 报告['明细'] if not r['通过']]
    if 失败:
        print('\n未通过 %d 条：' % len(失败))
        for r in 失败:
            print('  [%s/%s] %s —— %s' % (r['id'], r['期望'], r['需求'], r['详情']))
    达标 = (报告['自动链式命中率'] >= 0.60 and 报告['语义荒谬率'] == 0.0)
    print('\n%s' % ('达标' if 达标 else '未达标（命中率 <60% 或存在语义荒谬）'))
    return 0 if 达标 else 1


if __name__ == '__main__':
    raise SystemExit(main())
