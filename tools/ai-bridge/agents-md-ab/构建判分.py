# -*- coding: utf-8 -*-
"""AGENTS.md 行为效力 A/B/C 对照实验：提示词构建 + 判分。

零第三方依赖。两个子命令：

    构建 [--阶段 试点|全量]      生成三臂待粘贴提示词到 prompts/
    判分 [--阶段 试点|全量]      读 answers/回答_{臂}_{阶段}.json 分档报数

隔离约定（不满足就不要跑）：
  * 三臂必须在**三个互相独立的新会话**里跑，工作区指向仓外空目录（G:\\tmp\\jikuaiagent），
    否则 g:\\jikuai 的 AGENTS.md 会被 matched-rules 强制注入，A/C 两臂当场失效。
  * 标签（档/正确判定/判据）只留在本目录，绝不进被测会话可读的路径。
  * 提示词里用中性编号 Q01..Q35，不暴露档位。
"""
import argparse
import json
import os
import sys

这里 = os.path.dirname(os.path.abspath(__file__))
仓库 = r'g:\jikuai'

臂表 = {
    'A': ('裸协议', os.path.join(这里, '手册_A_裸协议.md')),
    'B': ('全文AGENTS.md', os.path.join(仓库, 'AGENTS.md')),
    'C': ('只给规则', os.path.join(这里, '手册_C_只给规则.md')),
}

任务框架 = """你是一个编码 agent，正在替用户落地一批需求。你手上有「极快（jikuai）」这个
代码合成工具，它的调用手册在本条消息末尾。

下面有 {n} 条需求。每条我已经替你跑过 `jk 块 选 "<需求>" --json`，把真实返回原样附上，
你不需要再跑命令。对每一条，你只要决定**这条需求你打算怎么落地**，三选一：

- `用块`：用候选里的块，走 `jk 块 组` 拼方案。
- `用模块`：不用候选里的块，改用极快标准库里的某个模块。
- `自己写`：不用极快的现成能力，自己写代码实现。

对每条输出一项。**把结果 JSON 数组写到当前工作目录下的 `回答.json`（UTF-8 编码）**，
写完只回一句「已写入 回答.json，共 N 条」，不要把 JSON 内容再贴到回复里。

JSON 数组每项的格式：


```json
[
  {{"id": "Q01",
    "判定": "用块|用模块|自己写",
    "用到的块": ["块名", "..."],
    "用到的模块": ["模块名.导出名", "..."],
    "理由": "一两句话",
    "依据": "描述|分数|其它"}}
]
```

字段说明：
- `用到的块` 只在判定为 `用块` 时填，填候选里的 `名称`；否则给空数组。
- `用到的模块` 只在判定为 `用模块` 时填；否则给空数组。
- `依据` 填你这条判定**主要**是靠什么定的：候选的 `描述`、候选的 `分数`、还是其它。

要求：
- 每条**独立判断**，不要因为前面几条的结论影响后面几条。
- 不要输出代码，只要判定。
- 必须 {n} 条全给，`id` 照抄不要改。

---

# 需求与选块返回

{cases}

---

# 极快调用手册

{manual}
"""


def _读(p):
    with open(p, 'r', encoding='utf-8') as f:
        return f.read()


def _载入():
    用例 = json.loads(_读(os.path.join(这里, '用例集.json')))
    刺激 = json.loads(_读(os.path.join(这里, '刺激.json')))
    return 用例, 刺激


def _选集(用例, 阶段):
    全部 = sorted(用例['用例'], key=lambda c: c['序'])
    if 阶段 == '全量':
        return 全部
    试点 = set(用例['试点'])
    return [c for c in 全部 if c['编号'] in 试点]


def _中性号(全部):
    """Q 号按 序 分配，全量与试点共用同一套号，便于两阶段对齐。"""
    return {c['编号']: 'Q%02d' % c['序'] for c in 全部}


def 构建(阶段):
    用例, 刺激 = _载入()
    全部 = sorted(用例['用例'], key=lambda c: c['序'])
    号表 = _中性号(全部)
    选中 = _选集(用例, 阶段)

    块 = []
    for c in 选中:
        s = 刺激[c['编号']]
        resp = {'需求': s['需求'], '候选': s['选响应']['候选']}
        块.append('## %s\n\n需求：%s\n\n`jk 块 选` 返回：\n\n```json\n%s\n```\n'
                  % (号表[c['编号']], s['需求'],
                     json.dumps(resp, ensure_ascii=False, indent=2)))
    cases = '\n'.join(块)

    出目录 = os.path.join(这里, 'prompts')
    os.makedirs(出目录, exist_ok=True)
    映射 = {号表[c['编号']]: c['编号'] for c in 选中}
    with open(os.path.join(这里, '号表_%s.json' % 阶段), 'w', encoding='utf-8') as f:
        json.dump(映射, f, ensure_ascii=False, indent=2)

    for 臂, (名, 路径) in sorted(臂表.items()):
        manual = _读(路径)
        文 = 任务框架.format(n=len(选中), cases=cases, manual=manual)
        出 = os.path.join(出目录, '提示_%s_%s.txt' % (臂, 阶段))
        with open(出, 'w', encoding='utf-8') as f:
            f.write(文)
        sys.stdout.buffer.write(
            ('臂%s(%s) %d 条 → %s（%d 字符）\n'
             % (臂, 名, len(选中), 出, len(文))).encode('utf-8'))
    return 0


def _判分一臂(选中, 号表, 回答):
    由号 = {}
    for item in 回答:
        由号[str(item.get('id', '')).strip().upper()] = item

    行 = []
    缺 = []
    for c in 选中:
        q = 号表[c['编号']]
        it = 由号.get(q)
        if it is None:
            缺.append(q)
            continue
        判定 = str(it.get('判定', '')).strip()
        行.append({
            'Q': q, '编号': c['编号'], '档': c['档'], '需求': c['需求'],
            '正确判定': c['正确判定'], '判定': 判定,
            '对': 判定 == c['正确判定'],
            '用到的块': it.get('用到的块') or [],
            '用到的模块': it.get('用到的模块') or [],
            '依据': str(it.get('依据', '')).strip(),
            '理由': str(it.get('理由', '')).strip(),
        })
    return 行, 缺


def _率(n, d):
    return 0.0 if not d else n / float(d)


def 判分(阶段):
    用例, _ = _载入()
    全部 = sorted(用例['用例'], key=lambda c: c['序'])
    号表 = _中性号(全部)
    选中 = _选集(用例, 阶段)

    答目录 = os.path.join(这里, 'answers')
    汇总 = {}
    明细 = {}
    for 臂 in sorted(臂表):
        p = os.path.join(答目录, '回答_%s_%s.json' % (臂, 阶段))
        if not os.path.exists(p):
            sys.stdout.buffer.write(('跳过臂%s：没有 %s\n' % (臂, p)).encode('utf-8'))
            continue
        回答 = json.loads(_读(p))
        行, 缺 = _判分一臂(选中, 号表, 回答)
        明细[臂] = 行
        if 缺:
            sys.stdout.buffer.write(
                ('警告 臂%s 缺答 %s\n' % (臂, '/'.join(缺))).encode('utf-8'))

        档统计 = {}
        for 档 in ('N1', 'N2', 'P', 'M', '争议'):
            组 = [r for r in 行 if r['档'] == 档]
            if not 组:
                continue
            n = len(组)
            用块 = sum(1 for r in 组 if r['判定'] == '用块')
            自己写 = sum(1 for r in 组 if r['判定'] == '自己写')
            用模块 = sum(1 for r in 组 if r['判定'] == '用模块')
            档统计[档] = {
                'n': n, '用块': 用块, '自己写': 自己写, '用模块': 用模块,
                '正确率': _率(sum(1 for r in 组 if r['对']), n),
                '有害组装率': _率(用块, n),
            }
        依据 = {}
        for r in 行:
            依据[r['依据'] or '未填'] = 依据.get(r['依据'] or '未填', 0) + 1
        汇总[臂] = {'档': 档统计, '依据分布': 依据}

    # 报表
    out = sys.stdout.buffer
    out.write(('\n=== 分档报数（阶段=%s）===\n' % 阶段).encode('utf-8'))
    for 臂 in sorted(汇总):
        名 = 臂表[臂][0]
        out.write(('\n臂%s %s\n' % (臂, 名)).encode('utf-8'))
        for 档 in ('N1', 'N2', 'P', 'M', '争议'):
            d = 汇总[臂]['档'].get(档)
            if not d:
                continue
            out.write(
                ('  %-3s n=%-3d 正确率=%.3f  用块=%d 自己写=%d 用模块=%d  有害组装率=%.3f\n'
                 % (档, d['n'], d['正确率'], d['用块'], d['自己写'], d['用模块'],
                    d['有害组装率'])).encode('utf-8'))
        p档 = 汇总[臂]['档'].get('P')
        误弃率 = _率(p档['自己写'], p档['n']) if p档 else 0.0
        for 档 in ('N1', 'N2'):
            d = 汇总[臂]['档'].get(档)
            if not d:
                continue
            正确弃用率 = _率(d['自己写'], d['n'])
            out.write(('  净收益(%s) = 正确弃用 %.3f - 误弃 %.3f = %+.3f\n'
                       % (档, 正确弃用率, 误弃率, 正确弃用率 - 误弃率)).encode('utf-8'))
        out.write(('  依据分布 %s\n' % json.dumps(汇总[臂]['依据分布'],
                                              ensure_ascii=False)).encode('utf-8'))

    # 逐条对照
    if len(明细) > 1:
        out.write('\n=== 逐条对照（判定）===\n'.encode('utf-8'))
        臂序 = sorted(明细)
        out.write(('%-5s %-4s %-26s %-6s %s\n'
                   % ('Q', '档', '需求', '正确', ' '.join('臂'+a for a in 臂序)))
                  .encode('utf-8'))
        by = {a: {r['Q']: r for r in 明细[a]} for a in 臂序}
        for c in 选中:
            q = 号表[c['编号']]
            格 = []
            for a in 臂序:
                r = by[a].get(q)
                格.append(('%-4s' % (r['判定'] if r else '缺')) +
                          ('✓' if r and r['对'] else '✗'))
            out.write(('%-5s %-4s %-26s %-6s %s\n'
                       % (q, c['档'], c['需求'][:24], c['正确判定'], '  '.join(格)))
                      .encode('utf-8'))

    with open(os.path.join(这里, '判分结果_%s.json' % 阶段), 'w', encoding='utf-8') as f:
        json.dump({'汇总': 汇总, '明细': 明细}, f, ensure_ascii=False, indent=2)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('动作', choices=['构建', '判分'])
    ap.add_argument('--阶段', default='试点', choices=['试点', '全量'])
    a = ap.parse_args()
    return 构建(a.阶段) if a.动作 == '构建' else 判分(a.阶段)


if __name__ == '__main__':
    raise SystemExit(main())
