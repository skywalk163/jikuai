# -*- coding: utf-8 -*-
"""极快语言 - Token 定义。"""

from enum import Enum, auto


class TokenType(Enum):
    # 字面量
    NUMBER = auto()       # 数字（含中文数字转换后的值）
    STRING = auto()       # 字符串
    MONEY = auto()        # 人民币金额 ￥99.90
    # 标识符与关键字
    IDENT = auto()        # 标识符（百家姓开头或英文）
    KEYWORD = auto()      # 关键字
    VERB = auto()         # 动词（带元数）
    ADVERB = auto()       # 副词（皆/只/归）
    # 标点
    PERIOD = auto()       # 。
    COMMA = auto()        # ，管道
    COLON = auto()        # ：
    DOT = auto()          # . 成员访问
    EQUALS = auto()       # =
    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    LBRACE = auto()
    RBRACE = auto()
    # 布局
    NEWLINE = auto()
    INDENT = auto()
    DEDENT = auto()
    EOF = auto()


class Token:
    __slots__ = ('type', 'value', 'line', 'col', 'arity')

    def __init__(self, type_, value, line=0, col=0, arity=0):
        self.type = type_
        self.value = value
        self.line = line
        self.col = col
        self.arity = arity   # 仅动词有意义

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, 行{self.line}, 元数{self.arity})"
