# -*- coding: utf-8 -*-
"""极快语言 · 补全引擎（v0.6.0 · M5 · T-M5-L04）。

从 `repl_session.CompletionEngine` 提取的纯函数补全核心，服务两个消费者：
  - REPL：`repl_candidates` 走 startswith，空前缀返回全池（行为不变）。
  - LSP：`complete(source, line, column)`，1-based 码点位置，空前缀返回 []。
补全路径只做静态文本分析，绝不加载/执行任何 .jk / .py 模块。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set

from .keywords import ADVERBS, ALL_KEYWORDS, VERB_ARITY

HELP_WORD = '帮助'
REQUIREMENT_WORD = '需求'

CATEGORY_KEYWORD = '关键字'
CATEGORY_VERB = '内建动词'
CATEGORY_USER = '用户名字'
CATEGORY_ORDER: Dict[str, int] = {
    CATEGORY_KEYWORD: 0, CATEGORY_VERB: 1, CATEGORY_USER: 2}

KIND_KEYWORD = 14
KIND_FUNCTION = 3
KIND_VARIABLE = 6
KIND_MODULE = 9

VERB_CATEGORIES = [
    ('算术', ['加', '减', '乘', '除', '取余', '幂', '整除', '负', '绝对值']),
    ('比较', ['等于', '不等于', '大于', '小于', '大于等于', '小于等于']),
    ('逻辑', ['且', '或', '非']),
    ('列表', ['列', '长度', '首个', '其余', '末个', '追加', '连接',
              '包含', '反转', '排序', '去重', '取值', '范围']),
    ('聚合', ['求和', '最大', '最小', '平均']),
    ('字符串', ['拼接', '分割', '替换', '子串', '大写', '小写',
                '转字符串', '转整数', '转小数']),
    ('输入输出', ['打印', '输入']),
    ('中国特色', ['人民币', '大写金额', '汉字数字']),
    ('国情校验', ['校验身份证', '提取身份证信息', '校验手机号', '判断运营商',
                  '校验银行卡', '校验车牌', '校验社会信用代码']),
    ('中国历法', ['公历转农历', '干支纪年', '生肖', '农历完整日期']),
]
_VERB_CATEGORY_OF: Dict[str, str] = {}
for _t, _vs in VERB_CATEGORIES:
    for _v in _vs:
        _VERB_CATEGORY_OF[_v] = _t


@dataclass(frozen=True)
class CompletionItem:
    """一条补全候选（label/类别/LSP kind/单行说明）。"""
    label: str
    category: str = CATEGORY_USER
    kind: int = KIND_VARIABLE
    detail: str = ''

    @property
    def sort_text(self) -> str:
        return f"{CATEGORY_ORDER.get(self.category, 9)}{self.label}"

    def to_lsp(self) -> Dict:
        item = {'label': self.label, 'kind': self.kind,
                'sortText': self.sort_text, 'insertText': self.label}
        if self.detail:
            item['detail'] = self.detail
        return item

def verb_arity_text(name: str) -> str:
    """把动词元数渲染为中文短语。"""
    arity = VERB_ARITY.get(name)
    if arity is None:
        return ''
    if name in ADVERBS:
        return '副词（高阶操作）'
    if arity == -1:
        return '可变元数（1 个或多个参数）'
    if arity == 0:
        return '零元'
    return f'{arity} 元'


def verb_detail(name: str) -> str:
    """补全候选的单行 detail。"""
    if name not in VERB_ARITY:
        return ''
    cat = _VERB_CATEGORY_OF.get(name, '其他')
    return f'内建动词 · {cat} · {verb_arity_text(name)}'


def verb_documentation(name: str) -> Optional[str]:
    """内建动词的多行中文说明（Markdown），供 LSP hover；非动词返回 None。"""
    if name not in VERB_ARITY:
        return None
    arity = VERB_ARITY[name]
    cat = _VERB_CATEGORY_OF.get(name, '其他')
    lines = [f'**{name}** —— 内建动词（{cat}）', '']
    if name in ADVERBS:
        lines.append('元数：副词（高阶操作，作用于管道左侧的列表）')
        lines.append('')
        lines.append(f'用法：`列 ...，{name}<动词> [初值]`')
        return '\n'.join(lines)
    lines.append(f'元数：{verb_arity_text(name)}')
    lines.append('')
    if arity == -1:
        lines.append(f'用法：`{name} arg1 arg2 ... argN`')
    elif arity == 0:
        lines.append(f'用法：`{name}`')
    else:
        args = ' '.join(f'arg{i + 1}' for i in range(arity))
        lines.append(f'用法：`{name} {args}`')
    if arity == 2:
        lines.append('')
        lines.append(f'中缀写法：`arg1 {name} arg2`')
    return '\n'.join(lines)


def keyword_documentation(name: str) -> Optional[str]:
    """关键字的 hover 说明；非关键字返回 None。"""
    if name not in ALL_KEYWORDS:
        return None
    return f'**{name}** —— 极快语言关键字'


_BOUNDARY_CHARS = set(' \t\r\n')
_BOUNDARY_CHARS |= set('。，、：:；;=（）()【】[]「」{}<>+-*/%!?"\'`|&^~@$#\\')


def extract_prefix(line_text: str, cursor_column: int) -> str:
    """取光标左侧补全前缀（cursor_column 为 1-based 码点列）。`.` 不算边界。"""
    if not line_text:
        return ''
    end = max(0, min(cursor_column - 1, len(line_text)))
    start = end
    while start > 0 and line_text[start - 1] not in _BOUNDARY_CHARS:
        start -= 1
    return line_text[start:end]


def _blank(m):
    return ' ' * len(m.group(0))


def _mask_source(source: str) -> str:
    """掩码字符串字面量与行注释。"""
    s = re.sub(r'"[^"\n]*"', _blank, source)
    s = re.sub(r"'[^'\n]*'", _blank, s)
    s = re.sub(r'--[^\n]*', _blank, s)
    s = re.sub(r'#[^\n]*', _blank, s)
    return s


def user_defined_names(source: str) -> Set[str]:
    """从源码静态推导用户定义名（走 lexer 预扫描白名单，异常返回空集）。"""
    try:
        from .lexer import Lexer
        return set(Lexer(source).get_user_defs())
    except Exception:
        return set()


_RE_IMPORT = re.compile(r'导入\s+([^\s。作]+)(?:\s*作为\s*([^\s。]+))?')
_RE_FROM_IMPORT = re.compile(r'从\s+([^\s。]+)\s+导入\s+([^。]+)')


def import_aliases(source: str) -> Dict[str, str]:
    """别名 -> 模块名。"""
    masked = _mask_source(source)
    out: Dict[str, str] = {}
    for m in _RE_IMPORT.finditer(masked):
        module = m.group(1).strip()
        alias = (m.group(2) or module).strip()
        if alias:
            out[alias] = module
    return out


def imported_symbols(source: str) -> Dict[str, str]:
    """导入引入的符号 -> 说明文本。"""
    masked = _mask_source(source)
    result: Dict[str, str] = {}
    for m in _RE_FROM_IMPORT.finditer(masked):
        module = m.group(1).strip()
        for name in m.group(2).split():
            n = name.strip()
            if n:
                result[n] = f'来自模块 {module}'
    for m in _RE_IMPORT.finditer(masked):
        module = m.group(1).strip()
        alias = (m.group(2) or module).strip()
        if not alias or alias in result:
            continue
        result[alias] = f'模块 {module}'
    return result


def module_exports(module_name: str) -> Set[str]:
    """读 stdlib 模块导出集合（纯文本静态解析）；读不到返回空集。"""
    try:
        from .stdlib_contract import declared_exports
        return set(declared_exports(module_name))
    except Exception:
        return set()


def candidates(pool: Iterable[str], prefix: str) -> List[str]:
    """REPL 口径：空前缀返回全池，否则 startswith 匹配。"""
    names = set(pool)
    if not prefix:
        return sorted(names)
    return sorted(w for w in names if w.startswith(prefix))


def static_pool(source: str) -> Dict[str, CompletionItem]:
    """源码级候选池。"""
    pool: Dict[str, CompletionItem] = {}
    for kw in ALL_KEYWORDS:
        pool[kw] = CompletionItem(kw, CATEGORY_KEYWORD, KIND_KEYWORD, '关键字')
    for verb in VERB_ARITY:
        if verb not in pool:
            pool[verb] = CompletionItem(
                verb, CATEGORY_VERB, KIND_FUNCTION, verb_detail(verb))
    aliases = set(import_aliases(source))
    for name, note in imported_symbols(source).items():
        if name not in pool:
            pool[name] = CompletionItem(
                name, CATEGORY_USER,
                KIND_MODULE if name in aliases else KIND_FUNCTION, note)
    for name in user_defined_names(source):
        if name not in pool:
            pool[name] = CompletionItem(name, CATEGORY_USER, KIND_VARIABLE, '用户定义')
    return pool


def _sorted_items(items: Iterable[CompletionItem]) -> List[CompletionItem]:
    return sorted(items, key=lambda it: (CATEGORY_ORDER.get(it.category, 9), it.label))


def complete(source: str, cursor_line: int, cursor_column: int) -> List[CompletionItem]:
    """LSP 口径补全：空前缀/行越界返回 []；别名.成员 只返回该模块导出。"""
    lines = source.splitlines()
    idx = cursor_line - 1
    if idx < 0 or idx >= len(lines):
        return []
    raw = extract_prefix(lines[idx], cursor_column)
    if not raw:
        return []
    if '.' in raw:
        alias, _, member_prefix = raw.rpartition('.')
        module = import_aliases(source).get(alias)
        if module is None:
            return []
        return _sorted_items(
            CompletionItem(n, CATEGORY_USER, KIND_FUNCTION, f'{module} 的导出')
            for n in module_exports(module) if n.startswith(member_prefix))
    pool = static_pool(source)
    return _sorted_items(it for lb, it in pool.items() if lb.startswith(raw))


def complete_lsp(source: str, cursor_line: int, cursor_column: int) -> List[Dict]:
    """complete 的 LSP 字典投影。"""
    return [it.to_lsp() for it in complete(source, cursor_line, cursor_column)]


REPL_STATIC_POOL: Set[str] = (set(ALL_KEYWORDS) | set(VERB_ARITY.keys())
                              | {HELP_WORD, REQUIREMENT_WORD})


def repl_candidates(prefix: str, extra_names: Optional[Iterable[str]] = None) -> List[str]:
    """REPL Tab 补全候选：静态池 + 运行期全局变量名（含空前缀列全表）。"""
    pool = set(REPL_STATIC_POOL)
    if extra_names:
        pool |= set(extra_names)
    return candidates(pool, prefix)