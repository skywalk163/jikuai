# -*- coding: utf-8 -*-
"""协议-三通道 CI 门禁 G16（v0.18.0 · W55）。

**为什么这个门禁**：v0.17.0 复盘发现 `docs/协议-三通道.md` 的 Web 端点章节
从 v0.15.0 W20 起没跟着代码更新，六个 W31/W46 新端点是靠一次手工同步（W47）
才追上来的。同一份契约同时躺在 doc 与 code 里，早晚会漂开一次；漂开的问题
就是 CI 该抓的问题。

**做法**：**双向 diff**——文档里出现但代码里没有 → 红；代码里有但文档里没有 → 红。
按四种 HTTP 方法（POST/GET/PUT/DELETE）分别做，防止「新增 PUT 端点却写成
POST 端点」这类形状漂移。

**为什么不用「服务端声明清单」**：路由不是集中在一张 dict 上——`_POST路由`
是真字典；GET/PUT/DELETE 是 if/elif 分支里的字符串常量与前缀常量
（`_方案列路径` / `_方案id前缀`）。抽象成统一表得动 server.py 的结构，代价
比抽象带来的收益大。这里用 AST 扫方法体收集字符串，能吃现有结构；如果
将来 server.py 真做出统一路由表，本脚本换实现也简单。

**id 占位符归一**：`/api/方案/<id>` 在 code 侧是 `_方案id前缀 + <id>` 的前缀
匹配；doc 侧写作 `<id>`。两边都归一成 `/api/方案/<id>` 再比。

用法：
    python scripts/check_protocol_doc.py            扫描 + 比对
    python scripts/check_protocol_doc.py --quiet    只有差异才输出

退出码：0=一致 / 1=有差异或读文件失败。
"""

import argparse
import ast
import os
import re
import sys

# Windows 控制台默认 GBK。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
_DOC_PATH = os.path.join(_REPO_ROOT, 'docs', '协议-三通道.md')
_SERVER_PATH = os.path.join(_REPO_ROOT, 'tools', 'web', 'server.py')

#: 支持的 HTTP 方法（顺序 = 输出报告的顺序）。
_METHODS = ('POST', 'GET', 'PUT', 'DELETE')

#: 章节标题——`三、通道 × schema 对应关系`（含 `## ` 前缀）。定位起点后一直
#: 读到下一个同级或更高级标题为止。
_SECTION_HEAD = '## 三、'

#: 端点识别：`METHOD /api/<...>`。允许中文段（`/api/方案/存` 里的「方案」「存」）
#: 与拉丁段（`/api/blocks`）。终止字符里带上反引号、星号、空格、行尾——
#: 端点常常写在 markdown 的 `**POST /api/xxx**` 内联代码里。
_ENDPOINT_IN_DOC = re.compile(
    r'\b(POST|GET|PUT|DELETE)\s+(/api/[^\s`*\)\|]+)'
)

#: doc 里的 `<id>`（或任意 `<...>`）占位归一成统一形态，与 server.py 的
#: `_方案id端点`（`/api/方案/<id>`）口径一致。
_PLACEHOLDER = re.compile(r'<[^>]*>')


def _归一路径(p):
    """把任意 `<...>` 占位统一成 `<id>`，并去掉尾部标点。"""
    p = _PLACEHOLDER.sub('<id>', p)
    return p.rstrip('。，、,;；.')


def _读文件(path):
    if not os.path.isfile(path):
        print('错误：文件不存在：%s' % path, file=sys.stderr)
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------------------
# 从 doc 提取端点
# ---------------------------------------------------------------------------

def _取小节(text, head):
    """截取 `## <head>` 起、到下一个 `## ` 止的段落文本。找不到返回 None。"""
    start = text.find(head)
    if start < 0:
        return None
    # 下一个同级标题；` ##` / `\n## ` 都算
    tail = text.find('\n## ', start + len(head))
    return text[start:tail] if tail > 0 else text[start:]


def _doc端点(doc文本):
    """从 `## 三、` 小节里抽取 (方法, 路径) 集合。

    只吃 `**METHOD /api/xxx**`、`METHOD /api/xxx`、表格里 `` `METHOD /api/xxx` ``
    这几种形态——它们的共同点是「方法名 + 空格 + /api/ 开头」。
    """
    小节 = _取小节(doc文本, _SECTION_HEAD)
    if 小节 is None:
        raise ValueError('未在 %s 找到「%s」小节'
                         % (os.path.relpath(_DOC_PATH, _REPO_ROOT),
                            _SECTION_HEAD))
    endpoints = set()
    for m in _ENDPOINT_IN_DOC.finditer(小节):
        method = m.group(1)
        path = _归一路径(m.group(2))
        endpoints.add((method, path))
    return endpoints


# ---------------------------------------------------------------------------
# 从 server.py 提取端点
# ---------------------------------------------------------------------------

def _求值(node, 常量表):
    """把 AST 节点求成字符串（只支持字面量、Name 引用、字符串 `+` 拼接）。

    支撑 `_方案id前缀 + '<id>'` 这类表达式——server.py 的路由清单用它组装
    单资源端点。求不出（含非字符串运算）返回 None，由调用方跳过。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return 常量表.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        左 = _求值(node.left, 常量表)
        右 = _求值(node.right, 常量表)
        if 左 is not None and 右 is not None:
            return 左 + 右
    return None


def _取序列(node, 常量表):
    """把 List/Tuple/Dict 的元素/键求成字符串列表（跳过求不出的项）。"""
    元素 = []
    if isinstance(node, (ast.List, ast.Tuple)):
        元素 = node.elts
    elif isinstance(node, ast.Dict):
        元素 = node.keys
    out = []
    for e in 元素:
        v = _求值(e, 常量表)
        if v is not None and v.startswith('/api/'):
            out.append(v)
    return out


def _server端点(server源码):
    """解析 server.py，返回按方法归类的端点集合。

    识别口径（W55 后 server.py 已把四方法的路由收敛成模块级清单，见那里的
    `_POST路由` / `_GET路由` / `_PUT路由` / `_DELETE路由`）：直接对这四个模块级
    赋值做**字面量求值**，`_方案id前缀 + '<id>'` 这类拼接也能解。这比扫方法体
    稳——方法体里混着守卫串、排除串、404 文案，区分不了「服务」与「排除」。
    """
    tree = ast.parse(server源码)

    常量表 = {}     # 模块级字符串常量
    路由声明 = {}    # 方法 → AST 节点

    方法名映射 = {
        '_POST路由': 'POST', '_GET路由': 'GET',
        '_PUT路由': 'PUT', '_DELETE路由': 'DELETE',
    }

    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        name = node.targets[0].id
        # 按源码顺序求值：`_方案id端点 = _方案id前缀 + '<id>'` 这类拼接依赖
        # 先出现的常量，顺序遍历刚好满足。
        值 = _求值(node.value, 常量表)
        if 值 is not None:
            常量表[name] = 值
        if name in 方法名映射:
            路由声明[方法名映射[name]] = node.value

    端点 = {m: set() for m in _METHODS}
    for m in _METHODS:
        决 = 路由声明.get(m)
        if 决 is None:
            raise ValueError('server.py 里未找到 %s 的路由清单模块级定义'
                             % m)
        for p in _取序列(决, 常量表):
            端点[m].add(_归一路径(p))
    return 端点


# ---------------------------------------------------------------------------
# 归一 & 差异
# ---------------------------------------------------------------------------

def _归一(集合):
    """归一 `<id>` 占位：`/api/方案/<id>` 保持原样；扁平路径 str 保持原样。"""
    return set(集合)


def _比对(doc端点表, code端点表):
    """按方法比对，返回 `{方法: (doc独有, code独有)}`。"""
    doc按方法 = {m: set() for m in _METHODS}
    for method, path in doc端点表:
        if method in doc按方法:
            doc按方法[method].add(path)

    差异 = {}
    for m in _METHODS:
        doc集 = _归一(doc按方法[m])
        code集 = _归一(code端点表.get(m, set()))
        差异[m] = (sorted(doc集 - code集), sorted(code集 - doc集))
    return 差异


def _报告(差异, quiet):
    """输出人读报告，返回是否发现差异（True=有）。"""
    有差异 = any(only_doc or only_code for only_doc, only_code in 差异.values())
    if not 有差异:
        if not quiet:
            print('G16 通过：`docs/协议-三通道.md` 的 Web 端点与 '
                  '`tools/web/server.py` 一致')
        return False

    print('错误：G16 协议文档同步失败——文档与代码的 Web 端点集不一致',
          file=sys.stderr)
    for m in _METHODS:
        only_doc, only_code = 差异[m]
        if not only_doc and not only_code:
            continue
        print('  [%s]' % m, file=sys.stderr)
        for p in only_doc:
            print('    - 文档写了但代码没有：%s' % p, file=sys.stderr)
        for p in only_code:
            print('    - 代码有但文档没写：%s' % p, file=sys.stderr)
    print('  修复：更新 docs/协议-三通道.md 的「三、通道 × schema 对应关系」小节'
          '，或调整 tools/web/server.py 的路由（择一）', file=sys.stderr)
    return True


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='check_protocol_doc',
        description='CI 门禁 G16：docs/协议-三通道.md ↔ tools/web/server.py 端点一致性')
    parser.add_argument('--quiet', action='store_true',
                        help='一致时静默，只在有差异时输出')
    parser.add_argument('--doc', default=_DOC_PATH, help='协议文档路径')
    parser.add_argument('--server', default=_SERVER_PATH, help='服务端源码路径')
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    doc文本 = _读文件(args.doc)
    server源码 = _读文件(args.server)
    if doc文本 is None or server源码 is None:
        return 1

    try:
        doc端点表 = _doc端点(doc文本)
        code端点表 = _server端点(server源码)
    except ValueError as e:
        print('错误：解析失败：%s' % e, file=sys.stderr)
        return 1

    差异 = _比对(doc端点表, code端点表)
    return 1 if _报告(差异, args.quiet) else 0


if __name__ == '__main__':
    sys.exit(main())
