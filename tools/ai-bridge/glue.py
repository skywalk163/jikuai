# -*- coding: utf-8 -*-
"""块粘合合成器 —— 把「选块方案」JSON 合成为可运行极快源码。

配套 `select.py` 与 `协议.md`。选块器负责「选哪些块」，本文件负责「把它们
用极快语法串起来」。

两级能力：

  v0 `synthesize`  —— 模板合成。方案里手写 `参数`，缺省填 `?` 占位。
  v1 `TypeGraph`   —— 类型图驱动的**自动链式**（ADR-26 类型词表 + W3-W4）。
                      给定选块方案，按块的 `输入`/`输出` 类型自动推断
                      `赵果i → 赵果j` 的传参链；推不出的显式拒绝、给理由，
                      不静默硬塞。

命令行::

    python tools/ai-bridge/glue.py 方案.json            # 模板合成（尊重方案里的参数）
    python tools/ai-bridge/glue.py 方案.json --自动链式  # 类型图推断缺失的参数

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

__all__ = ['synthesize', 'result_var', 'TypeGraph', 'type_feeds', 'normalize_type']

#: 占位符：参数无法推断时写在实参位，配一行注释提示人工补。
_占位符 = '?'


def result_var(i):
    """第 i 步（0 基）的结果变量名。

    取 `赵果` + 序号。已验证词法原子：`tokenize('赵果1。')` → 单个 IDENT。
    前缀 `赵` 是块生态约定的「安全字头」——避免与内建动词冲突被切碎。
    """
    return '赵果%d' % (i + 1)


# ---------------------------------------------------------------------------
# 类型系统（ADR-26 类型词表）
# ---------------------------------------------------------------------------

_标量 = frozenset({'数', '字符串', '布尔', '函数', '任意'})
_容器 = frozenset({'列表', '字典', '元组'})
_联合 = '联合'


def normalize_type(t):
    """把类型标注归一成 `(种类, 载荷)` 元组，便于比较。

    - 标量字符串 `'数'` → `('数', None)`
    - 裸容器 `'列表'` → `('列表', ('任意-元素',))`（视为 列表<任意>）
    - 裸 `'字典'` → `('字典', (('任意',), ('任意',)))`
    - 结构化 `{'类型':'列表','元素类型':X}` → `('列表', (normalize(X),))`
    - 结构化 元组/字典/联合 类推
    - 缺省 / None → `('任意', None)`
    """
    if t is None:
        return ('任意', None)
    if isinstance(t, str):
        if t in _标量:
            return (t, None)
        if t == '列表':
            return ('列表', (('任意', None),))
        if t == '字典':
            return ('字典', (('任意', None), ('任意', None)))
        if t == '元组':
            return ('元组', None)          # 长度未知的裸元组
        return ('任意', None)              # 未知字符串保守当 任意
    if isinstance(t, dict):
        kind = t.get('类型')
        if kind == '列表':
            return ('列表', (normalize_type(t.get('元素类型')),))
        if kind == '字典':
            return ('字典', (normalize_type(t.get('键类型')),
                            normalize_type(t.get('值类型'))))
        if kind == '元组':
            return ('元组', tuple(normalize_type(x) for x in (t.get('元数') or [])))
        if kind == _联合:
            return (_联合, tuple(normalize_type(x) for x in (t.get('候选') or [])))
        return ('任意', None)
    return ('任意', None)


def _人读(nt):
    """把归一类型转回人类可读串，用于拒绝理由。"""
    kind, payload = nt
    if kind == '列表':
        return '列表<%s>' % _人读(payload[0])
    if kind == '字典':
        return '字典<%s,%s>' % (_人读(payload[0]), _人读(payload[1]))
    if kind == '元组':
        if payload is None:
            return '元组'
        return '元组[%s]' % ','.join(_人读(x) for x in payload)
    if kind == _联合:
        return '联合(%s)' % '|'.join(_人读(x) for x in payload)
    return kind


def type_feeds(src, dst):
    """判断「产出类型 src 能否喂给形参类型 dst」（自动链式的可喂性）。

    比纯子类型更严：**元组不自动喂列表**——固定形状返回值要人工拆包，
    这正是 `批量统计→升序` 该被拒的原因（WBS W3-W4 DoD）。

    `src` / `dst` 是 `normalize_type` 的输出。返回布尔。
    """
    sk, sp = src
    dk, dp = dst

    # 任意 是顶：形参收任意 → 放行；实参是任意（动态/共享常量）→ 放行
    if dk == '任意' or sk == '任意':
        return True

    # 联合：实参联合要求每个候选都能喂；形参联合要求实参能喂某个候选
    if sk == _联合:
        return all(type_feeds(c, dst) for c in sp)
    if dk == _联合:
        return any(type_feeds(src, c) for c in dp)

    # 元组 → 列表：显式拒绝（需人工拆包），即使元素同质
    if sk == '元组' and dk == '列表':
        return False

    if sk != dk:
        return False

    if sk in _标量:
        return True
    if sk == '列表':
        return type_feeds(sp[0], dp[0])
    if sk == '字典':
        return type_feeds(sp[0], dp[0]) and type_feeds(sp[1], dp[1])
    if sk == '元组':
        if sp is None or dp is None:
            return True                   # 裸元组兜底放行
        if len(sp) != len(dp):
            return False
        return all(type_feeds(a, b) for a, b in zip(sp, dp))
    return False


# ---------------------------------------------------------------------------
# 类型图：自动链式传参
# ---------------------------------------------------------------------------

class TypeGraph:
    """类型图驱动的自动链式粘合。

    节点 = 已产出变量（附类型）；边 = 「变量 X 的类型可喂步骤 Y 的第 k 个入参」。
    `plan(steps, 共享)` 按步骤顺序推断每步的实参链，返回三元组：

        (实参方案, 未匹配, 拒绝理由)

    - `实参方案`：`list[list[str] | None]`，每步一份实参名列表；某步整体推不出则为 None
    - `未匹配`：`list[(步序, 入参名, 原因)]`，逐个说清哪个槽没填上
    - `拒绝理由`：`list[str]`，人读的「为什么没链上」——尤其是类型近似但不可喂
      （如 元组[数,数,数,数] 非 列表<数>，需人工拆包），不静默。
    """

    def __init__(self, root=None):
        from jikuai.pkg import blocks
        self._meta = {}
        for m in blocks.scan_blocks(root):
            self._meta[m.name] = {
                '输入': [dict(x) for x in m.inputs],
                '输出': dict(m.output),
            }

    def _block_inputs(self, 名):
        info = self._meta.get(名)
        return info['输入'] if info else []

    def _block_output_type(self, 名):
        info = self._meta.get(名)
        if not info or not info['输出']:
            return ('任意', None)
        return normalize_type(info['输出'].get('类型'))

    def plan(self, steps, 共享=None):
        # 候选变量池：共享常量（类型未知 → 任意）先入池，随后每步产出追加
        池 = []          # list of (变量名, 归一类型)
        for c in (共享 or []):
            if c.get('名'):
                池.append((c['名'], ('任意', None)))

        实参方案 = []
        未匹配 = []
        拒绝理由 = []

        for i, s in enumerate(steps):
            名 = s.get('块')
            inputs = self._block_inputs(名)
            这步实参 = []
            这步齐全 = True

            # 若方案已手写参数，尊重之（类型图只补缺失的），但仍校验可喂性
            手写 = s.get('参数')

            for k, slot in enumerate(inputs):
                形参类型 = normalize_type(slot.get('类型'))
                slot名 = slot.get('名', '参数%d' % (k + 1))

                if 手写 is not None and k < len(手写):
                    这步实参.append(str(手写[k]))
                    continue

                # 在池中找可喂的变量：最近产出优先（倒序）
                命中 = None
                近似拒绝 = []
                for 变量名, 变量类型 in reversed(池):
                    if type_feeds(变量类型, 形参类型):
                        命中 = 变量名
                        break
                    # 「近似不可喂」：记下具体类型冲突，避免拒绝理由静默。
                    # 元组喂列表单独出提示——固定形状要人工拆包（W3-W4 DoD 硬用例）。
                    if 变量类型[0] == '任意':
                        continue                       # 任意 万能兜底，不算冲突
                    if 变量类型[0] == '元组' and 形参类型[0] == '列表':
                        近似拒绝.append(
                            '%s 的输出是 %s，非 %s；需人工拆包后再喂步骤%d「%s」的入参「%s」'
                            % (变量名, _人读(变量类型), _人读(形参类型),
                               i + 1, 名, slot名))
                    else:
                        近似拒绝.append(
                            '%s 的输出是 %s，无法喂步骤%d「%s」的入参「%s」（形参需 %s）'
                            % (变量名, _人读(变量类型), i + 1, 名,
                               slot名, _人读(形参类型)))

                if 命中 is not None:
                    这步实参.append(命中)
                else:
                    这步齐全 = False
                    这步实参.append(None)
                    未匹配.append((i + 1, slot名, '无类型兼容的已产出变量可喂'))
                    拒绝理由.extend(近似拒绝)

            # 该步产出入池，供后续步骤消费
            池.append((result_var(i), self._block_output_type(名)))
            实参方案.append(这步实参 if 这步齐全 else None)

        return 实参方案, 未匹配, 拒绝理由


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


def synthesize(方案, 自动链式=False, root=None):
    """把选块方案（`协议.md` 定义的 JSON）合成为极快源码字符串。

    参数校验：`方案` 必须是 dict 且含非空 `步骤`；每步须有 `块/领域/导出名`。
    `自动链式=True` 时，对**缺 `参数`** 的步骤用 `TypeGraph` 按类型推断实参链；
    推不出的仍落 `?` 占位并把拒绝理由写进注释。

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

    自动实参 = None
    拒绝理由 = []
    if 自动链式:
        图 = TypeGraph(root=root)
        自动实参, _未匹配, 拒绝理由 = 图.plan(steps, 方案.get('共享'))

    lines = ['-- 由 极快 AI 桥接（选块 + 粘合%s）自动合成'
             % ('，类型图链式' if 自动链式 else '')]
    if 方案.get('需求'):
        lines.append('-- 需求：' + str(方案['需求']))
    if 拒绝理由:
        lines.append('--')
        lines.append('-- 类型图未能自动链上的传参（需人工处理）：')
        for r in 拒绝理由:
            lines.append('--   * ' + r)
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
        if 参数 is not None:
            实参 = ' '.join(str(p) for p in 参数)
        elif 自动实参 is not None and 自动实参[i] is not None:
            实参 = ' '.join(自动实参[i])
        else:
            lines.append('-- 需人工填参：%s 的入参未指定（下一行的 %s 占位）'
                         % (s['导出名'], _占位符))
            实参 = _占位符
        lines.append('定义%s=%s(%s)。' % (var, s['导出名'], 实参))

    # 4) 打印
    lines.append('')
    打印列表 = 方案.get('打印') or 结果变量
    for 名 in 打印列表:
        lines.append('打印 %s。' % 名)

    return '\n'.join(lines).rstrip() + '\n'


def _cli(argv=None):
    p = argparse.ArgumentParser(description='极快块粘合合成器')
    p.add_argument('方案', help='选块方案 JSON 文件路径')
    p.add_argument('--自动链式', '--auto', action='store_true',
                   help='用类型图推断缺 参数 的步骤实参链（ADR-26）')
    args = p.parse_args(argv)
    with open(args.方案, 'r', encoding='utf-8') as f:
        方案 = json.load(f)
    sys.stdout.write(synthesize(方案, 自动链式=args.自动链式))
    return 0


if __name__ == '__main__':
    raise SystemExit(_cli())
