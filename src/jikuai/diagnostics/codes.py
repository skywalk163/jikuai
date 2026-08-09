# -*- coding: utf-8 -*-
"""极快语言 · 诊断错误码表（ADR-14 F1 冻结契约）。

错误码格式：``JK-{E|W}{段位:1 位}{序号:3 位}``。

    E = 错误（严重）        W = 警告（可继续，需注意）

段位定义（千位）：
    0xxx  词法
    1xxx  语法 / 解析
    2xxx  名称 / 作用域
    3xxx  元数 / 参数
    4xxx  类型 / 运行时
    5xxx  模块 / 导入
    6xxx  互操作（pybridge）
    7xxx  AOT
    8xxx  调试
    9xxx  内部 / 契约

**硬约束**：错误码一经发布只增不改不复用。修订消息模板可以，废弃后
号段留空，禁止重号复用其他语义（这是 CLI / LSP / 用户脚本三方
可依赖的稳定契约）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from ..errors import ErrorCategory
from .model import Severity, SEVERITY_ERROR, SEVERITY_HINT, SEVERITY_WARNING


# ---------------------------------------------------------------------------
# 词法（0xxx）
# ---------------------------------------------------------------------------

JK_E0001 = "JK-E0001"   # 非法字符

# ---------------------------------------------------------------------------
# 语法 / 解析（1xxx）
# ---------------------------------------------------------------------------

JK_E1001 = "JK-E1001"   # 意外的文件结束
JK_W1001 = "JK-W1001"   # 副词后接非内建动词，按原值透传（US-M4-01）

# ---------------------------------------------------------------------------
# 名称 / 作用域（2xxx）
# ---------------------------------------------------------------------------

JK_E2001 = "JK-E2001"   # 未定义的名称
JK_E2002 = "JK-E2002"   # 未知的内建动词（含拼写建议）（US-M4-02）

# ---------------------------------------------------------------------------
# 元数 / 参数（3xxx）
# ---------------------------------------------------------------------------

JK_E3001 = "JK-E3001"   # 动词元数不符

# ---------------------------------------------------------------------------
# 类型 / 运行时（4xxx）
# ---------------------------------------------------------------------------

JK_E4001 = "JK-E4001"   # 类型不匹配
JK_E4002 = "JK-E4002"   # 不可实例化抽象类 / 接口（T6，命名约定 抽/协 开头）
JK_E4003 = "JK-E4003"   # 具体类未实现全部抽象方法（T6）

# ---------------------------------------------------------------------------
# 模块 / 导入（5xxx）
# ---------------------------------------------------------------------------

JK_E5001 = "JK-E5001"   # 找不到模块（消息必须包含模块名）（AC-M4-03-03）
JK_E5002 = "JK-E5002"   # 模块未导出该名字（AC-M4-03-02）

# ---------------------------------------------------------------------------
# 互操作（6xxx）
# ---------------------------------------------------------------------------

JK_E6001 = "JK-E6001"   # 命中 pybridge 拒绝清单
JK_W6001 = "JK-W6001"   # pybridge 非沙箱信任提示

# ---------------------------------------------------------------------------
# AOT（7xxx）
# ---------------------------------------------------------------------------

JK_E7001 = "JK-E7001"   # 超出 AOT 受支持子集（AC-M6-06-02）

# ---------------------------------------------------------------------------
# 调试（8xxx）
# ---------------------------------------------------------------------------

JK_E8001 = "JK-E8001"   # 调试能力暂不支持（AC-M6-05-04）

# ---------------------------------------------------------------------------
# 内部 / 契约（9xxx）
# ---------------------------------------------------------------------------

JK_W9001 = "JK-W9001"   # 两遍分词未收敛，已回退首遍结果（ADR-17 兜底）


# ---------------------------------------------------------------------------
# 元数据表
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CodeInfo:
    """错误码元数据。"""
    code: str
    severity: Severity
    category: ErrorCategory
    template: str           # 消息模板，供 reporter 参考；`{}` 为占位
    since: str              # 首次出现版本
    doc_anchor: str = ""    # 文档锚点，供 hover 与 error 打点引用


CODE_TABLE: Dict[str, CodeInfo] = {
    JK_E0001: CodeInfo(JK_E0001, SEVERITY_ERROR, ErrorCategory.LEXER,
                       "非法字符 {char!r}", "v0.5.0"),
    JK_E1001: CodeInfo(JK_E1001, SEVERITY_ERROR, ErrorCategory.SYNTAX,
                       "意外的文件结束：{context}", "v0.5.0"),
    JK_W1001: CodeInfo(JK_W1001, SEVERITY_WARNING, ErrorCategory.SYNTAX,
                       "副词 {adverb!r} 内部遇到未知动词 {inner!r}，"
                       "将按原值透传，不产生预期效果", "v0.5.0"),
    JK_E2001: CodeInfo(JK_E2001, SEVERITY_ERROR, ErrorCategory.NAME,
                       "未定义的名称：{name}", "v0.5.0"),
    JK_E2002: CodeInfo(JK_E2002, SEVERITY_ERROR, ErrorCategory.NAME,
                       "未知的内建动词：{name}", "v0.5.0"),
    JK_E3001: CodeInfo(JK_E3001, SEVERITY_ERROR, ErrorCategory.TYPE,
                       "动词 {verb!r} 需要 {expected} 个参数，"
                       "但收到 {actual} 个", "v0.5.0"),
    JK_E4001: CodeInfo(JK_E4001, SEVERITY_ERROR, ErrorCategory.TYPE,
                       "类型不匹配：{detail}", "v0.5.0"),
    JK_E4002: CodeInfo(JK_E4002, SEVERITY_ERROR, ErrorCategory.TYPE,
                       "不可实例化抽象类/接口：{cls}", "v0.10.0"),
    JK_E4003: CodeInfo(JK_E4003, SEVERITY_ERROR, ErrorCategory.TYPE,
                       "类 {cls} 未实现抽象方法：{methods}", "v0.10.0"),
    JK_E5001: CodeInfo(JK_E5001, SEVERITY_ERROR, ErrorCategory.RUNTIME,
                       "找不到模块：{module}", "v0.5.0"),
    JK_E5002: CodeInfo(JK_E5002, SEVERITY_ERROR, ErrorCategory.RUNTIME,
                       "模块 {module} 未导出：{name}", "v0.5.0"),
    JK_E6001: CodeInfo(JK_E6001, SEVERITY_ERROR, ErrorCategory.RUNTIME,
                       "命中 pybridge 拒绝清单：{target}", "v0.5.0"),
    JK_W6001: CodeInfo(JK_W6001, SEVERITY_WARNING, ErrorCategory.RUNTIME,
                       "pybridge 非沙箱环境，请仅执行可信 Python 代码", "v0.5.0"),
    JK_E7001: CodeInfo(JK_E7001, SEVERITY_ERROR, ErrorCategory.RUNTIME,
                       "超出 AOT 受支持子集：{feature}", "v0.7.0"),
    JK_E8001: CodeInfo(JK_E8001, SEVERITY_ERROR, ErrorCategory.RUNTIME,
                       "调试能力暂不支持：{capability}", "v0.7.0"),
    JK_W9001: CodeInfo(JK_W9001, SEVERITY_WARNING, ErrorCategory.RUNTIME,
                       "两遍分词未收敛，已回退首遍结果；请检查类块识别边界",
                       "v0.5.0"),
}


def info_for(code: str) -> CodeInfo:
    """按码取元数据；未登记时抛 KeyError（内部契约违反）。"""
    return CODE_TABLE[code]


# ---------------------------------------------------------------------------
# 段位校验（供 G9 契约测试使用）
# ---------------------------------------------------------------------------

def segment_of(code: str) -> int:
    """返回错误码所属段位（千位数字）。非法码抛 ValueError。"""
    if not code.startswith("JK-") or len(code) != 8 or code[3] not in ("E", "W"):
        raise ValueError(f"错误码格式非法：{code!r}")
    try:
        num = int(code[4:])
    except ValueError as e:
        raise ValueError(f"错误码序号非法：{code!r}") from e
    return num // 1000


def is_valid_code(code: str) -> bool:
    """判定字符串是否为合法错误码格式（不校验是否已登记）。"""
    try:
        segment_of(code)
    except ValueError:
        return False
    return True
