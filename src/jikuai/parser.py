# -*- coding: utf-8 -*-
"""极快语言 - 元数驱动语法分析器（Parser）。

核心设计：
  - 动词根据元数（arity）自动向右吞噬固定数量的参数
  - 中文逗号 `，` 为管道操作符，前一步的结果作为下一步的隐式第一参数
  - 副词（皆/只/归）抓取后续 Call 构建高阶操作
  - 句号 `。` 终止语句
"""

from .tokens import Token, TokenType
from .keywords import (
    ALL_KEYWORDS, KW_DEFINE, KW_ASSIGN, KW_IF, KW_THEN, KW_ELIF, KW_ELSE,
    KW_WHILE, KW_FOR, KW_IN, KW_FROM, KW_TO, KW_REPEAT, KW_TIMES,
    KW_BREAK, KW_CONTINUE, KW_FUNC, KW_PARAM, KW_RETURN,
    KW_CLASS, KW_EXTENDS, KW_CTOR, KW_METHOD, KW_NEW, KW_SELF, KW_SUPER,
    KW_TRY, KW_CATCH, KW_FINALLY, KW_THROW,
    KW_IMPORT, KW_EXPORT, KW_FILE, KW_AS,
    KW_TRUE, KW_FALSE, KW_NIL, VERB_ARITY, ADVERBS
)
from .ast_nodes import *
from .errors import ErrorInfo, ErrorCategory


class ParseError(Exception):
    def __init__(self, msg, token=None):
        self.token = token
        line = token.line if token else 0
        col = token.col if token else 0
        self.info = ErrorInfo(
            category=ErrorCategory.SYNTAX,
            message=msg,
            line=line,
            col=col,
        )
        super().__init__(f"第{token.line}行: {msg}" if token else msg)


class UnexpectedEOFError(ParseError):
    """输入在语法结构中途耗尽——期望更多 token。

    REPL 用它区分"这行还没写完，等续行"与"这行真的写错了"。
    """
    pass



class Parser:
    """极快语法分析器。"""

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def _loc(self, node, tok):
        """将 token 的行列信息附加到 AST 节点上。"""
        node.line = tok.line
        node.col = tok.col
        return node

    def parse(self):
        """解析为 Program AST。"""
        stmts = []
        while not self._at_end():
            self._skip_newlines()
            if self._at_end():
                break
            stmt = self._parse_statement()
            if stmt is not None:
                stmts.append(stmt)
        return Program(body=stmts)

    # ======================== 辅助方法 ========================

    def _cur(self):
        return self.tokens[self.pos]

    def _at_end(self):
        return self.pos >= len(self.tokens) or self._cur().type == TokenType.EOF

    def _advance(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _peek(self, offset=0):
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return Token(TokenType.EOF, '', 0, 0)

    def _match_type(self, *types):
        if not self._at_end() and self._cur().type in types:
            return self._advance()
        return None

    def _match_keyword(self, *keywords):
        """匹配关键字 token，value 在指定关键字集合中。"""
        if (not self._at_end() and self._cur().type == TokenType.KEYWORD
                and self._cur().value in keywords):
            return self._advance()
        return None

    def _match_kw_set(self, kw_set):
        if (not self._at_end() and self._cur().type == TokenType.KEYWORD
                and self._cur().value in kw_set):
            return self._advance()
        return None

    def _expect_type(self, type_, msg=""):
        tok = self._match_type(type_)
        if not tok:
            text = msg or f"期望 {type_.name}"
            # 输入耗尽（EOF）：抛 UnexpectedEOFError，供 REPL 判定"还需续行"
            if self._at_end():
                raise UnexpectedEOFError(text, self._cur())
            raise ParseError(text, self._cur())
        return tok

    def _skip_newlines(self):
        while not self._at_end() and self._cur().type == TokenType.NEWLINE:
            self._advance()

    def _skip_period(self):
        """跳过可选的句号。"""
        self._match_type(TokenType.PERIOD)

    def _require_block_close(self):
        """R-1（ADR-03 修正）：块结构的收尾。

        - 若已到 EOF 而块未看到闭合的 `。` → 抛 `UnexpectedEOFError`，
          让 REPL 明确知道"输入还没写完"。
        - 若当前是 `。` → 消费。
        - 其它情况保持宽松（等同 `_skip_period`），不阻碍相邻语句。
        """
        if self._at_end():
            raise UnexpectedEOFError("块未闭合，期望 。", self._cur())
        self._match_type(TokenType.PERIOD)

    # ======================== 语句解析 ========================

    def _parse_statement(self):
        tok = self._cur()

        # 关键字驱动的语句
        if tok.type == TokenType.KEYWORD:
            val = tok.value
            if val in KW_DEFINE:
                return self._parse_define()
            if val in KW_ASSIGN:
                return self._parse_assign()
            if val in KW_IF:
                return self._parse_if()
            if val in KW_WHILE:
                return self._parse_while()
            if val in KW_FOR:
                return self._parse_for()
            if val in KW_REPEAT:
                return self._parse_repeat()
            if val in KW_FUNC:
                return self._parse_funcdef()
            if val in KW_CLASS:
                return self._parse_classdef()
            if val in KW_RETURN:
                return self._parse_return()
            if val in KW_BREAK:
                self._advance()
                self._skip_period()
                return Break()
            if val in KW_CONTINUE:
                self._advance()
                self._skip_period()
                return Continue()
            if val in KW_TRY:
                return self._parse_try()
            if val in KW_THROW:
                return self._parse_throw()
            if val in KW_IMPORT:
                return self._parse_import()
            if val in KW_FROM:
                return self._parse_from_import()
            if val in KW_EXPORT:
                return self._parse_export()
            if val in KW_NEW:
                return self._parse_new_expr_as_stmt()

        # 表达式语句（管道 / 动词调用 / 赋值等）
        expr = self._parse_pipeline()
        # 裸赋值：目标 = 值（支持 自身.属性 = 值、列表[i] = 值、变量 = 值）
        if (not self._at_end() and self._cur().type == TokenType.EQUALS
                and isinstance(expr, (Ident, MemberAccess, Index))):
            self._advance()
            value = self._parse_pipeline()
            self._skip_period()
            return Assign(target=expr, value=value)
        self._skip_period()
        return expr

    # ---------- 定义 ----------

    def _parse_define(self):
        tok = self._cur()
        self._advance()  # 消费 "定义"
        name_tok = self._expect_type(TokenType.IDENT, "定义后期望标识符")
        name = name_tok.value
        self._match_type(TokenType.EQUALS)
        # 检查是否为函数定义：定义X = 函数 接收 ...
        if (not self._at_end() and self._cur().type == TokenType.KEYWORD
                and self._cur().value in KW_FUNC):
            return self._parse_funcdef_with_name(name, tok)
        value = self._parse_pipeline()
        self._skip_period()
        return self._loc(Define(name=name, value=value), tok)

    def _parse_assign(self):
        tok = self._cur()
        self._advance()  # 消费 "赋值"/"设为"
        name_tok = self._expect_type(TokenType.IDENT, "赋值后期望标识符")
        self._match_type(TokenType.EQUALS)
        value = self._parse_pipeline()
        self._skip_period()
        target = self._loc(Ident(name_tok.value), name_tok)
        return self._loc(Assign(target=target, value=value), tok)

    # ---------- 条件 ----------

    def _parse_if(self):
        self._advance()  # 消费 "如果"
        cond = self._parse_expression()
        self._match_kw_set(KW_THEN)  # 可选 "那么"
        self._match_type(TokenType.COLON)  # 可选 ：
        self._skip_newlines()
        then_body = self._parse_block()

        elif_branches = []
        while self._match_kw_set(KW_ELIF):
            elif_cond = self._parse_expression()
            self._match_kw_set(KW_THEN)
            self._match_type(TokenType.COLON)
            self._skip_newlines()
            elif_body = self._parse_block()
            elif_branches.append((elif_cond, elif_body))

        else_body = None
        if self._match_kw_set(KW_ELSE):
            self._match_type(TokenType.COLON)
            self._skip_newlines()
            else_body = self._parse_block()

        self._require_block_close()
        return If(cond=cond, then_branch=then_body,
                  elif_branches=elif_branches, else_branch=else_body)

    # ---------- 循环 ----------

    def _parse_while(self):
        self._advance()  # 消费 "当"
        cond = self._parse_expression()
        self._match_type(TokenType.COLON)
        self._skip_newlines()
        body = self._parse_block()
        self._require_block_close()
        return While(cond=cond, body=body)

    def _parse_for(self):
        self._advance()  # 消费 "遍历"
        var_tok = self._expect_type(TokenType.IDENT, "遍历后期望变量名")
        self._match_kw_set(KW_IN)  # 消费 "于"
        iterable = self._parse_expression()
        self._match_type(TokenType.COLON)
        self._skip_newlines()
        body = self._parse_block()
        self._require_block_close()
        return For(var=var_tok.value, iterable=iterable, body=body)

    def _parse_repeat(self):
        self._advance()  # 消费 "重复"
        count = self._parse_expression()
        self._match_kw_set(KW_TIMES)  # 可选 "次"
        self._match_type(TokenType.COLON)
        self._skip_newlines()
        body = self._parse_block()
        self._require_block_close()
        return Repeat(count=count, body=body)

    # ---------- 函数 ----------

    def _parse_funcdef(self):
        tok = self._cur()
        self._advance()  # 消费 "函数"
        name_tok = self._expect_type(TokenType.IDENT, "函数后期望名称")
        return self._parse_funcdef_with_name(name_tok.value, tok)

    def _parse_funcdef_with_name(self, name, start_tok=None):
        # 消费可能的 "函数" 关键字（如果从 define 路径来的话）
        if (not self._at_end() and self._cur().type == TokenType.KEYWORD
                and self._cur().value in KW_FUNC):
            self._advance()
        params = []
        # 接收 参数列表
        if self._match_kw_set(KW_PARAM):
            params = self._parse_param_list()
        self._match_type(TokenType.COLON)
        self._skip_newlines()
        body = self._parse_block()
        self._require_block_close()
        node = FuncDef(name=name, params=params, body=body)
        if start_tok is not None:
            self._loc(node, start_tok)
        return node

    def _parse_param_list(self):
        """解析参数列表：标识符序列，逗号分隔或空格分隔。"""
        params = []
        while not self._at_end():
            tok = self._match_type(TokenType.IDENT)
            if not tok:
                break
            params.append(tok.value)
            self._match_type(TokenType.COMMA)
        return params

    # ---------- 类 ----------

    def _parse_classdef(self):
        self._advance()  # 消费 "类"
        name_tok = self._expect_type(TokenType.IDENT, "类后期望类名")
        parent = None
        if self._match_kw_set(KW_EXTENDS):
            parent_tok = self._expect_type(TokenType.IDENT, "继承后期望父类名")
            parent = parent_tok.value
        self._match_type(TokenType.COLON)
        self._skip_newlines()

        ctor_params = []
        ctor_body = []
        ctor_defined = False
        methods = {}

        # 解析类体
        while not self._at_end() and self._cur().type != TokenType.PERIOD:
            self._skip_newlines()
            if self._at_end() or self._cur().type == TokenType.PERIOD:
                break
            if self._cur().type == TokenType.KEYWORD:
                val = self._cur().value
                if val in KW_CTOR:
                    self._advance()
                    # 只要出现 构造 关键字就视为显式定义（即使 body 为空），
                    # 用于 ADR-02 的继承链回溯：显式空构造器不再回溯父类。
                    ctor_defined = True
                    if self._match_kw_set(KW_PARAM):
                        ctor_params = self._parse_param_list()
                    self._match_type(TokenType.COLON)
                    self._skip_newlines()
                    ctor_body = self._parse_block()
                    self._require_block_close()
                elif val in KW_METHOD:
                    self._advance()
                    m_name_tok = self._expect_type(TokenType.IDENT, "方法后期望名称")
                    m_params = []
                    if self._match_kw_set(KW_PARAM):
                        m_params = self._parse_param_list()
                    self._match_type(TokenType.COLON)
                    self._skip_newlines()
                    m_body = self._parse_block()
                    self._require_block_close()
                    methods[m_name_tok.value] = FuncDef(
                        name=m_name_tok.value, params=m_params, body=m_body
                    )
                else:
                    break
            else:
                break
        # ADR-06 X2（v0.5.0）：记录类块收尾 `。` 所在行，供 frontend 构造
        # 权威 ClassRegionTable。取 `_require_block_close` 消费前的当前 token 行。
        close_line = self._cur().line if not self._at_end() else 0
        self._require_block_close()
        node = ClassDef(name=name_tok.value, parent=parent,
                        ctor_params=ctor_params, ctor_body=ctor_body,
                        methods=methods, ctor_defined=ctor_defined)
        # 类名 token 与 `类` 关键字同行，故用它标注类块起始行
        self._loc(node, name_tok)
        node.end_line = close_line or node.line
        return node

    # ---------- 异常 ----------

    def _parse_try(self):
        self._advance()  # 消费 "尝试"
        self._match_type(TokenType.COLON)
        self._skip_newlines()
        body = self._parse_block()
        self._require_block_close()

        catch_var = None
        catch_body = None
        if self._match_kw_set(KW_CATCH):
            var_tok = self._match_type(TokenType.IDENT)
            if var_tok:
                catch_var = var_tok.value
            self._match_type(TokenType.COLON)
            self._skip_newlines()
            catch_body = self._parse_block()
            self._require_block_close()

        finally_body = None
        if self._match_kw_set(KW_FINALLY):
            self._match_type(TokenType.COLON)
            self._skip_newlines()
            finally_body = self._parse_block()
            self._require_block_close()

        return Try(body=body, catch_var=catch_var,
                   catch_body=catch_body, finally_body=finally_body)

    def _parse_throw(self):
        self._advance()
        value = self._parse_expression()
        self._skip_period()
        return Throw(value=value)

    def _parse_return(self):
        self._advance()
        value = None
        if not self._at_end() and self._cur().type not in (TokenType.PERIOD, TokenType.NEWLINE, TokenType.EOF):
            value = self._parse_pipeline()
        self._skip_period()
        return Return(value=value)

    # ---------- 导入 ----------

    #: ADR-10：Python 桥导入前缀。lexer 把 `蟒:math` 整体产出为一个 IDENT，
    #: 因此这里只需按前缀切分，不需要额外 token 类型。
    PY_IMPORT_PREFIXES = ('蟒:', '蟒：')

    @classmethod
    def _split_python_prefix(cls, text):
        """`蟒:math` → `('python', 'math')`；普通模块名 → `('jk', 原文)`。"""
        for prefix in cls.PY_IMPORT_PREFIXES:
            if text.startswith(prefix):
                return 'python', text[len(prefix):]
        return 'jk', text

    def _read_module_name(self, message):
        """读取一个模块名，支持点分路径（ADR-15 块生态）。

        lexer 在 `.` 处切出独立的 DOT token，所以 `blocks.数据.读取文件`
        到达 parser 时是 `IDENT DOT IDENT DOT IDENT`。这里把它们重新拼成
        一个字符串交给 `module_loader.resolve()`，由后者做目录映射。

        点分路径只是 **解析器行为**，不是新语法：`导入` 关键字、`.` token、
        标识符规则全都沿用既有定义（ADR-15 §3.3）。

        `蟒:os.path` 这类 Python 桥导入由 lexer 整体成词为单个 IDENT，
        不会走到这里的 DOT 循环，语义不受影响（ADR-10）。

        返回 `(首个 token, 完整模块名)`——首个 token 供报错定位使用。
        """
        # 模块名的每一段可以是任何"词形"token（IDENT/VERB/ADVERB/KEYWORD），
        # 因为块名可能恰好与内建动词/关键字重名（如 `求和`→VERB，`排序`→VERB）。
        # lexer 无法预知"导入"后面的上下文，parser 在此松弛接受条件。
        _MOD_NAME_TYPES = (TokenType.IDENT, TokenType.VERB,
                           TokenType.ADVERB, TokenType.KEYWORD)
        cur = self._cur()
        if cur.type not in _MOD_NAME_TYPES:
            raise ParseError(message, cur)
        first = self._advance()
        parts = [first.value]
        while self._cur().type == TokenType.DOT:
            self._advance()                      # 消费 `.`
            seg = self._cur()
            if seg.type not in _MOD_NAME_TYPES:
                raise ParseError("点分模块名后期望路径片段", seg)
            self._advance()
            parts.append(seg.value)
        return first, '.'.join(parts)

    def _parse_import(self):
        tok = self._cur()
        self._advance()  # 消费 "导入"
        mod_tok, raw_module = self._read_module_name("导入后期望模块名")
        kind, module = self._split_python_prefix(raw_module)
        if kind == 'python' and not module:
            raise ParseError("「蟒:」后期望 Python 模块名", mod_tok)
        alias = None
        if self._match_kw_set(KW_AS):
            alias_tok = self._expect_type(TokenType.IDENT, "作为后期望别名")
            alias = alias_tok.value
        self._skip_period()
        # Python 侧默认绑定顶层名（`蟒:os.path` → `os`）
        default_alias = module.split('.')[0] if kind == 'python' else None
        return self._loc(
            Import(module=module,
                   alias=alias if alias is not None else default_alias,
                   kind=kind),
            tok)

    def _parse_from_import(self):
        """解析 从 模块 导入 名字1 名字2。"""
        tok = self._cur()
        self._advance()  # 消费 "从"
        mod_tok, raw_module = self._read_module_name("从后期望模块名")
        kind, module = self._split_python_prefix(raw_module)
        if kind == 'python':
            # ADR-10：`从 蟒:X 导入 名字` 未纳入本期契约（名字级导入会绕开
            # PyModule 拒绝清单检查点）。显式拒绝，不静默降级。
            raise ParseError(
                "暂不支持「从 蟒:模块 导入 名字」；请写「导入 蟒:模块。」后用"
                "「模块.函数(参数)」调用", mod_tok)
        if not self._match_kw_set(KW_IMPORT):
            raise ParseError("从...期望 导入", self._cur())
        names = []
        while (not self._at_end() and self._cur().type in
               (TokenType.IDENT, TokenType.VERB, TokenType.ADVERB)):
            names.append(self._advance().value)
            self._match_type(TokenType.COMMA)
        if not names:
            raise ParseError("导入后期望名字列表", self._cur())
        self._skip_period()
        return self._loc(Import(module=module, names=names, alias=None), tok)

    def _parse_export(self):
        """解析 导出 名字1 名字2。"""
        tok = self._cur()
        self._advance()  # 消费 "导出"
        names = []
        while (not self._at_end() and self._cur().type in
               (TokenType.IDENT, TokenType.VERB, TokenType.ADVERB)):
            names.append(self._advance().value)
            self._match_type(TokenType.COMMA)
        if not names:
            raise ParseError("导出后期望名字列表", self._cur())
        self._skip_period()
        return self._loc(Export(names=names), tok)

    # ---------- 新建 ----------

    def _parse_new_expr_as_stmt(self):
        expr = self._parse_new_expr()
        self._skip_period()
        return expr

    def _parse_new_expr(self):
        self._advance()  # 消费 "新建"
        cls_tok = self._expect_type(TokenType.IDENT, "新建后期望类名")
        args = []
        # 括号调用
        if self._match_type(TokenType.LPAREN):
            args = self._parse_call_args_paren()
        return NewInstance(class_name=cls_tok.value, args=args)

    # ======================== 表达式解析 ========================

    def _parse_pipeline(self):
        """解析管道表达式（逗号分隔的调用链）。"""
        first = self._parse_expression()
        if self._at_end() or self._cur().type != TokenType.COMMA:
            return first
        stages = [first]
        while self._match_type(TokenType.COMMA):
            self._skip_newlines()
            stages.append(self._parse_expression())
        return Pipeline(stages=stages)

    def _parse_expression(self):
        """解析单个表达式。"""
        return self._parse_verb_or_primary()

    def _parse_verb_or_primary(self):
        """如果当前是动词，元数驱动吞噬参数；如果是副词，构造高阶；否则是 primary。"""
        tok = self._cur()

        # 副词处理
        if tok.type == TokenType.ADVERB:
            return self._parse_adverb()

        # 动词处理
        if tok.type == TokenType.VERB:
            return self._parse_verb_call()

        # 新建表达式
        if tok.type == TokenType.KEYWORD and tok.value in KW_NEW:
            return self._parse_new_expr()

        # primary 后可能跟中缀动词
        left = self._parse_primary()
        # 中缀模式：primary VERB primary（对于二元动词）
        while (not self._at_end() and self._cur().type == TokenType.VERB
               and self._cur().arity == 2):
            verb_tok = self._advance()
            right = self._parse_primary()
            left = self._loc(Call(verb=verb_tok.value, args=[left, right]), verb_tok)
        return left

    def _parse_verb_call(self):
        """解析前缀动词调用：verb arg1 arg2 ...（按元数吞噬）。"""
        verb_tok = self._advance()
        arity = verb_tok.arity
        args = []
        if arity == -1:
            # 可变元数：吞噬直到句号/逗号/行尾/右括号
            while not self._at_end() and self._cur().type not in (
                    TokenType.PERIOD, TokenType.COMMA, TokenType.NEWLINE,
                    TokenType.RPAREN, TokenType.RBRACKET, TokenType.RBRACE, TokenType.EOF,
                    TokenType.COLON):
                if self._cur().type == TokenType.ADVERB:
                    break
                args.append(self._parse_argument())
        elif arity > 0:
            for _ in range(arity):
                if self._at_end() or self._cur().type in (
                        TokenType.PERIOD, TokenType.COMMA, TokenType.NEWLINE,
                        TokenType.RPAREN, TokenType.RBRACKET, TokenType.RBRACE, TokenType.EOF,
                        TokenType.COLON):
                    break
                args.append(self._parse_argument())
        return self._loc(Call(verb=verb_tok.value, args=args), verb_tok)

    def _parse_argument(self):
        """解析动词的单个参数。参数本身可能是嵌套动词调用。

        v0.3.2（D-10 · 方案 A）：读完 primary/verb-call 之后，允许**中缀二元合并**，
        使 `打印 郑数 加 2` 解析为 `打印(加(郑数, 2))`，与用户直觉一致；
        `列 A B 加 C` 解析为 `列(A, 加(B, C))`。此前该写法在 evaluator 层
        泄漏 Python 异常文本，非可读的中文诊断。
        """
        tok = self._cur()
        if tok.type == TokenType.VERB:
            left = self._parse_verb_call()
        elif tok.type == TokenType.ADVERB:
            left = self._parse_adverb()
        else:
            left = self._parse_primary()
        # 中缀二元合并循环（与 _parse_verb_or_primary 的中缀分支同源）
        while (not self._at_end() and self._cur().type == TokenType.VERB
               and self._cur().arity == 2):
            verb_tok = self._advance()
            right = self._parse_primary()
            left = self._loc(Call(verb=verb_tok.value, args=[left, right]), verb_tok)
        return left

    def _parse_adverb(self):
        """解析副词：皆/只/归 + 动词调用。

        副词供给动词的第一个参数（被映射/过滤/归约的元素），
        因此内部动词只需消费 (元数-1) 个显式参数。
        """
        adv_tok = self._advance()
        if not self._at_end() and self._cur().type == TokenType.VERB:
            verb_tok = self._advance()
            arity = verb_tok.arity
            args = []
            # 副词占用一个参数槽，其余由源码提供
            need = max(arity - 1, 0) if arity > 0 else 0
            for _ in range(need):
                if self._at_end() or self._cur().type in (
                        TokenType.PERIOD, TokenType.COMMA, TokenType.NEWLINE,
                        TokenType.RPAREN, TokenType.RBRACKET, TokenType.RBRACE, TokenType.EOF,
                        TokenType.COLON):
                    break
                args.append(self._parse_argument())
            inner = Call(verb=verb_tok.value, args=args)
        else:
            inner = self._parse_primary()
        return AdverbCall(adverb=adv_tok.value, inner=inner)

    def _parse_primary(self):
        """解析基本表达式。"""
        tok = self._cur()

        if tok.type == TokenType.NUMBER:
            self._advance()
            return NumberLit(value=tok.value)

        if tok.type == TokenType.STRING:
            self._advance()
            return StringLit(value=tok.value)

        if tok.type == TokenType.MONEY:
            self._advance()
            return MoneyLit(value=tok.value)

        if tok.type == TokenType.KEYWORD:
            if tok.value in KW_TRUE:
                self._advance()
                return BoolLit(value=True)
            if tok.value in KW_FALSE:
                self._advance()
                return BoolLit(value=False)
            if tok.value in KW_NIL:
                self._advance()
                return NilLit()
            if tok.value in KW_SELF:
                self._advance()
                return self._parse_postfix(Ident(name='自身'))
            # M10-1：`父类.方法名(参数)`。裸 `父类` 也走 postfix，若后面没有
            # `.成员` 则最终由 evaluator 报「父类不能作为值使用」。
            if tok.value in KW_SUPER:
                self._advance()
                return self._parse_postfix(self._loc(Super(), tok))

        if tok.type == TokenType.IDENT:
            self._advance()
            return self._parse_postfix(self._loc(Ident(name=tok.value), tok))

        # 列表字面量 【1, 2, 3】
        if tok.type == TokenType.LBRACKET:
            return self._parse_list_literal()

        # 字典字面量 「"键": 值」 / {"键": 值}
        if tok.type == TokenType.LBRACE:
            return self._parse_dict_literal()

        # 括号分组
        if tok.type == TokenType.LPAREN:
            self._advance()
            expr = self._parse_pipeline()
            self._expect_type(TokenType.RPAREN, "缺少右括号")
            return expr

        # 如果都不匹配，跳过并返回 Nil
        self._advance()
        return NilLit()

    def _parse_postfix(self, node):
        """解析标识符后的后缀操作：.属性、[索引]、()调用。"""
        while not self._at_end():
            if self._cur().type == TokenType.DOT:
                dot_tok = self._advance()
                # ADR-补丁：`.` 之后允许 IDENT / VERB / KEYWORD 作为成员名。
                # 中文动词/关键字若恰好被用作模块导出名（如 `正则.匹配`），不该被
                # 语法分析卡住——把它们当作字面名字对待，与 Python `obj.class` 允许
                # 保留字作为属性名的取舍一致。
                cur = self._cur()
                if cur.type in (TokenType.IDENT, TokenType.VERB,
                                TokenType.KEYWORD, TokenType.ADVERB):
                    attr_tok = self._advance()
                else:
                    attr_tok = self._expect_type(TokenType.IDENT, "成员访问后期望属性名")
                node = self._loc(MemberAccess(obj=node, attr=attr_tok.value), dot_tok)
            elif self._cur().type == TokenType.LBRACKET:
                bracket_tok = self._advance()
                index_expr = self._parse_expression()
                self._expect_type(TokenType.RBRACKET, "索引缺少 ]")
                node = self._loc(Index(obj=node, index=index_expr), bracket_tok)
            elif self._cur().type == TokenType.LPAREN:
                paren_tok = self._advance()
                args = self._parse_call_args_paren()
                node = self._loc(FuncCall(func=node, args=args), paren_tok)
            else:
                break
        return node

    def _parse_list_literal(self):
        self._advance()  # 消费 [
        items = []
        self._skip_newlines()
        while not self._at_end() and self._cur().type != TokenType.RBRACKET:
            items.append(self._parse_expression())
            self._match_type(TokenType.COMMA)
            self._skip_newlines()
        self._expect_type(TokenType.RBRACKET, "列表缺少 ]")
        return ListLit(items=items)

    def _parse_dict_literal(self):
        """解析字典字面量：`{"键": 值, "键2": 值2}` / `「"键": 值」`。

        - 键与值都是**表达式**（不含逗号管道）：逗号在字典内是条目分隔符，
          需要管道作为值时请用括号包起来。
        - 条目之间允许逗号和/或换行分隔，末尾逗号可省略。
        - `{}` 为空字典。
        """
        brace_tok = self._advance()  # 消费 {
        items = []
        self._skip_newlines()
        while not self._at_end() and self._cur().type != TokenType.RBRACE:
            key = self._parse_expression()
            self._expect_type(TokenType.COLON, "字典条目缺少键值分隔符 ：")
            self._skip_newlines()
            value = self._parse_expression()
            items.append((key, value))
            self._match_type(TokenType.COMMA)
            self._skip_newlines()
        self._expect_type(TokenType.RBRACE, "字典缺少 }")
        return self._loc(DictLit(items=items), brace_tok)

    def _parse_call_args_paren(self):
        """解析括号内的参数列表。"""
        args = []
        while not self._at_end() and self._cur().type != TokenType.RPAREN:
            args.append(self._parse_expression())
            self._match_type(TokenType.COMMA)
        self._expect_type(TokenType.RPAREN, "函数调用缺少右括号")
        return args

    # ======================== 块解析 ========================

    def _parse_block(self):
        """解析语句块（到句号或减少缩进）。简化版：收集到遇到句号/否则/捕获/最终。"""
        stmts = []
        while not self._at_end():
            self._skip_newlines()
            if self._at_end():
                break
            # 块终止条件
            tok = self._cur()
            if tok.type == TokenType.PERIOD:
                break
            if tok.type == TokenType.KEYWORD and tok.value in (
                    KW_ELSE | KW_ELIF | KW_CATCH | KW_FINALLY | KW_CTOR | KW_METHOD):
                break
            stmt = self._parse_statement()
            if stmt is not None:
                stmts.append(stmt)
        return stmts


def parse(tokens):
    """从 token 列表解析为 AST。"""
    return Parser(tokens).parse()
