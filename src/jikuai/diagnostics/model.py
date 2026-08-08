# -*- coding: utf-8 -*-
"""极快语言 · 诊断内核数据模型（ADR-14 F1 冻结契约）。

所有类型均为 `frozen=True` 不可变对象，便于安全地传递、比较与哈希，
并使 `ListSink.drain()` 的排序结果具备决定性（AC-M4-01-03 可复现性基石）。

坐标系统：
    - `line` / `column` 均为 **1-based**；`column` 沿用现有 `errors.ErrorInfo.col`
      口径，即 Unicode 码点序号（不是 UTF-16 也不是字节）。
    - LSP Range 需要 0-based UTF-16 位置——转换由 L3 `service/` 层负责，
      本层保持码点口径不变。
    - `Span.end` 遵循 exclusive 语义（半开区间），便于 LSP 直接投影。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Tuple

# 现有 ErrorCategory 继续沿用；本层不重定义分类枚举，
# 仅在 diagnostics 生成时使用其成员（含本周期追加的 4 个新成员）。
from ..errors import ErrorCategory  # noqa: F401（对外重导出供消费者使用）


Severity = Literal["错误", "警告", "提示"]

SEVERITY_ERROR: Severity = "错误"
SEVERITY_WARNING: Severity = "警告"
SEVERITY_HINT: Severity = "提示"

# LSP DiagnosticSeverity 规范：Error=1 / Warning=2 / Information=3 / Hint=4。
# 极快侧只区分三档，映射如下（由 L3 service 层在投影时使用）。
_LSP_SEVERITY_MAP = {
    SEVERITY_ERROR: 1,
    SEVERITY_WARNING: 2,
    SEVERITY_HINT: 3,
}


def to_lsp_severity(severity: Severity) -> int:
    """把极快 Severity 中文枚举投影为 LSP 数字级别。"""
    return _LSP_SEVERITY_MAP[severity]


@dataclass(frozen=True)
class Position:
    """源码位置。line / column 均为 1-based，column 是 Unicode 码点序号。"""

    line: int
    column: int

    def __post_init__(self):
        if self.line < 1:
            raise ValueError(f"Position.line 必须 >= 1，收到 {self.line}")
        if self.column < 1:
            raise ValueError(f"Position.column 必须 >= 1，收到 {self.column}")


@dataclass(frozen=True)
class Span:
    """源码区间。end 采用 exclusive 语义（LSP Range 兼容）。

    file 为 None 时表示未绑定具体文件（如 REPL 输入或嵌入 API 内联源码）。
    """

    start: Position
    end: Position
    file: Optional[str] = None

    def __post_init__(self):
        # end 允许与 start 相等（零宽 Span，用于 EOF 或插入点诊断），
        # 但不允许 end 在 start 之前。
        if (self.end.line, self.end.column) < (self.start.line, self.start.column):
            raise ValueError(
                f"Span.end 不能早于 Span.start：{self.start} → {self.end}"
            )

    @classmethod
    def point(cls, line: int, column: int, file: Optional[str] = None) -> "Span":
        """构造零宽 Span，用于仅有单点位置的诊断。"""
        p = Position(line, column)
        return cls(start=p, end=p, file=file)


@dataclass(frozen=True)
class Suggestion:
    """建议候选。`distance` 用于排序（如拼写纠错的编辑距离）。

    `replace` 可选：若非 None，表示这是一条可自动应用的替换建议
    （LSP CodeAction 场景），本周期先只填 None。
    """

    text: str
    distance: int = 0
    replace: Optional[Span] = None


@dataclass(frozen=True)
class Diagnostic:
    """一条结构化诊断。

    字段契约（F1 冻结）：
        code        稳定错误码，形如 "JK-E2002" / "JK-W1001"。见 codes.py。
        severity    "错误" / "警告" / "提示" 三档。
        category    沿用 ErrorCategory，含本周期新增的 4 个成员。
        message     人类可读中文消息正文；不含位置前缀（前缀由 reporter 加）。
        span        触发位置区间（含结束位置）。
        subject     触发主体名字（可选），如副词名、动词名、模块名、符号名。
        suggestions 建议候选元组，按 distance 升序、字典序次之。
        notes       附加说明的字符串元组（如"参考 docs/xxx.md"）。

    `sort_key()` 提供决定性排序，保证 ListSink.drain() 输出可复现。
    """

    code: str
    severity: Severity
    category: ErrorCategory
    message: str
    span: Span
    subject: Optional[str] = None
    suggestions: Tuple[Suggestion, ...] = field(default_factory=tuple)
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        # 轻量结构校验：不做业务判断，只挡明显错误。
        if not self.code or not self.code.startswith("JK-"):
            raise ValueError(f"Diagnostic.code 非法：{self.code!r}")
        if self.severity not in (SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_HINT):
            raise ValueError(f"Diagnostic.severity 非法：{self.severity!r}")

    def sort_key(self) -> tuple:
        """决定性排序键：(file, line, column, code)。

        file 为 None 时用空串占位以保持类型稳定，避免 None 与 str 无法比较。
        """
        file_key = self.span.file or ""
        return (file_key, self.span.start.line, self.span.start.column, self.code)
