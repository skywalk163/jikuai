# -*- coding: utf-8 -*-
"""块粘合合成器 v0 —— 把「选块方案」JSON 合成为可运行极快源码。

配套 `select.py` 与 `协议.md`。选块器负责「选哪些块」，本文件负责「把它们
用极快语法串起来」。生成规则见 `协议.md` §粘合器生成规则，要点：

  1. 每步一行 `从 blocks.<领域>.<块> 导入 <导出名>。`（去重）
  2. `共享` 常量先落地 `定义<名>=<值>。`
  3. 步骤逐个 `定义赵果N=<导出名>(<参数>)。`，无参数则填 `?` 占位并注释
  4. `打印` 列表（默认每步结果变量）

命令行::

    python tools/ai-bridge/glue.py 方案.json

尽力生成能直接跑的代码；无法自动推断的参数用 `?` 占位并注释「需人工填参」。
"""

import argparse
import json
import os
import sys

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

__all__ = ['synthesize', 'result_var']

#: 占位符：参数无法推断时写在实参位，配一行注释提示人工补。
_占位符 = '?'


def result_var(i):
    """第 i 步（0 基）的结果变量名。

    取 `赵果` + 序号。已验证词法原子：`tokenize('赵果1。')` → 单个 IDENT。
    前缀 `赵` 是块生态约定的「安全字头」——避免与内建动词冲突被切碎。
    """
    return '赵果%d' % (i + 1)


def _导入行(steps):
    """生成去重后的 `从 ... 导入 ...` 行。顺序按步骤首次出现，稳定可测。"""
    seen = set()
    lines = []
    for s in steps:
        key = (s['领域'], s['块'], s['导出名'])
        if key in seen:
            continue
        seen.add(key)
        lines.append('从 blocks.%s.%s 导入 %s。' % key)
    return lines


def synthesize(方案):
    """把选块方案（`协议.md` 定义的 JSON）合成为极快源码字符串。

    参数校验：`方案` 必须是 dict 且含非空 `步骤`；每步须有 `块/领域/导出名`。
    返回：以换行分隔、末尾带单个换行的极快源码。
    """
    if not isinstance(方案, dict):
        raise ValueError('方案必须是 JSON 对象（dict）')
    steps = 方案.get('步骤') or []
    if not steps:
        raise ValueError('方案缺少非空的 `步骤` 字段')
    for i, s in enumerate(steps):
        for 字段 in ('块', '领域', '导出名'):
            if not s.get(字段):
                raise ValueError('步骤 %d 缺少必填字段「%s」' % (i + 1, 字段))

    lines = ['-- 由 极快 AI 桥接 v0（选块 + 粘合）自动合成']
    if 方案.get('需求'):
        lines.append('-- 需求：' + str(方案['需求']))
    lines.append('')

    # 1) 导入
    lines.extend(_导入行(steps))

    # 2) 共享常量 / 输入
    共享 = 方案.get('共享') or []
    if 共享:
        lines.append('')
        for item in 共享:
            if not item.get('名') or item.get('值') is None:
                raise ValueError('共享项须含「名」与「值」：%r' % (item,))
            lines.append('定义%s=%s。' % (item['名'], item['值']))

    # 3) 步骤调用
    lines.append('')
    结果变量 = []
    for i, s in enumerate(steps):
        var = result_var(i)
        结果变量.append(var)
        if s.get('说明'):
            lines.append('-- 步骤 %d：%s' % (i + 1, s['说明']))
        参数 = s.get('参数')
        if 参数 is None:
            lines.append('-- 需人工填参：%s 的入参未指定（下一行的 %s 占位）'
                         % (s['导出名'], _占位符))
            实参 = _占位符
        else:
            实参 = ' '.join(str(p) for p in 参数)
        lines.append('定义%s=%s(%s)。' % (var, s['导出名'], 实参))

    # 4) 打印
    lines.append('')
    打印列表 = 方案.get('打印') or 结果变量
    for 名 in 打印列表:
        lines.append('打印 %s。' % 名)

    return '\n'.join(lines).rstrip() + '\n'


def _cli(argv=None):
    p = argparse.ArgumentParser(description='极快块粘合合成器 v0')
    p.add_argument('方案', help='选块方案 JSON 文件路径')
    args = p.parse_args(argv)
    with open(args.方案, 'r', encoding='utf-8') as f:
        方案 = json.load(f)
    sys.stdout.write(synthesize(方案))
    return 0


if __name__ == '__main__':
    raise SystemExit(_cli())
