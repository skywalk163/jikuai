# -*- coding: utf-8 -*-
"""极快语言 - REPL 增强（M2-2 / ADR-03、ADR-05）。

四块能力：
  1. 多行续行状态机（IDLE / CONTINUE）
  2. 历史持久化（~/.jikuai_history，readline 或 pyreadline3，缺失则静默降级）
  3. Tab 补全（关键字 ∪ 动词 ∪ 全局变量名，startswith 匹配）
  4. `帮助` 命令（REPL 内特殊识别，不进求值器）

设计要点：所有判定逻辑都做成不依赖 stdin 的纯函数/纯方法，
便于测试直接调用（见 tests/test_jikuai.py 的 M2-2 用例）。
"""

import atexit
import sys
from pathlib import Path

from .keywords import ALL_KEYWORDS, VERB_ARITY, ADVERBS
from .lexer import tokenize, Lexer
from .parser import parse, ParseError, UnexpectedEOFError
from .evaluator import Evaluator, JiKuaiError, ReturnSignal, BreakSignal, ContinueSignal
from .errors import ErrorFormatter


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

HISTORY_FILE = '.jikuai_history'
HISTORY_LENGTH = 2000

PROMPT_IDLE = '极快> '
PROMPT_CONTINUE = '...   '

EXIT_WORDS = ('退出', 'exit', 'quit')
HELP_WORD = '帮助'

# 状态机状态
STATE_IDLE = 'IDLE'
STATE_CONTINUE = 'CONTINUE'

# 动词分类（用于 `帮助` 的分类简介）
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


# ---------------------------------------------------------------------------
# 帮助命令
# ---------------------------------------------------------------------------

def verb_usage(name):
    """返回单个动词的用法说明；name 不是已知动词时返回 None。"""
    if name not in VERB_ARITY:
        return None
    arity = VERB_ARITY[name]
    lines = []
    if name in ADVERBS:
        lines.append(f"用法：列 ...，{name}<动词> [初值]")
        lines.append(f"说明：{name} 是副词（高阶操作），作用于管道左侧的列表")
        return '\n'.join(lines)
    if arity == -1:
        lines.append(f"用法：{name} arg1 arg2 ... argN   （可变参数）")
    elif arity == 0:
        lines.append(f"用法：{name}")
    else:
        args = ' '.join(f"arg{i + 1}" for i in range(arity))
        lines.append(f"用法：{name} {args}")
    if arity == 2:
        lines.append(f"中缀示例：arg1 {name} arg2")
    return '\n'.join(lines)


def help_text(arg=None):
    """`帮助` / `帮助 <名字>` 的输出文本。"""
    if not arg:
        lines = ['极快语言帮助', '']
        lines.append('关键字：' + ' '.join(sorted(ALL_KEYWORDS)))
        lines.append('')
        lines.append('动词分类：')
        for title, verbs in VERB_CATEGORIES:
            lines.append(f"  {title}：" + ' '.join(verbs))
        lines.append(f"  副词：" + ' '.join(sorted(ADVERBS)))
        lines.append('')
        lines.append('输入 `帮助 <动词名>` 查看单个动词用法，例如：帮助 加')
        lines.append('输入 退出 / exit / quit 结束会话')
        return '\n'.join(lines)
    usage = verb_usage(arg)
    if usage is not None:
        return usage
    if arg in ALL_KEYWORDS:
        return f'"{arg}" 是关键字。输入 帮助 查看关键字总览。'
    return f'未找到 "{arg}"，试试 "帮助"'


# ---------------------------------------------------------------------------
# Tab 补全
# ---------------------------------------------------------------------------

class CompletionEngine:
    """按前缀提供补全候选。

    候选源 = ALL_KEYWORDS ∪ VERB_ARITY.keys() ∪ evaluator.global_env.vars.keys()
    每次 candidates() 都重新读全局变量，保证 REPL 中新定义的名字立即可补全。
    """

    def __init__(self, evaluator=None):
        self.evaluator = evaluator
        self._static = set(ALL_KEYWORDS) | set(VERB_ARITY.keys()) | {HELP_WORD}

    def candidates(self, prefix):
        pool = set(self._static)
        ev = self.evaluator
        if ev is not None:
            try:
                pool |= set(ev.global_env.vars.keys())
            except AttributeError:
                pass
        if not prefix:
            return sorted(pool)
        return sorted(w for w in pool if w.startswith(prefix))

    # readline 回调协议：completer(text, state)
    def complete(self, text, state):
        if state == 0:
            self._matches = self.candidates(text)
        try:
            return self._matches[state]
        except IndexError:
            return None


# ---------------------------------------------------------------------------
# readline / 历史（降级安全）
# ---------------------------------------------------------------------------

def _import_readline():
    """依次尝试 readline、pyreadline3（Windows）；都没有则返回 None。"""
    try:
        import readline
        return readline
    except ImportError:
        pass
    try:
        import pyreadline3.rlmain  # noqa: F401  触发注册
        import readline
        return readline
    except Exception:
        pass
    try:
        from pyreadline3 import Readline
        return Readline()
    except Exception:
        return None


def history_path():
    return Path.home() / HISTORY_FILE


def setup_readline(evaluator=None):
    """装配历史持久化与 Tab 补全。任一步不支持都静默降级，不抛异常。

    返回实际使用的 readline 模块（或 None 表示完全降级）。
    """
    rl = _import_readline()
    if rl is None:
        return None

    # 历史长度
    try:
        rl.set_history_length(HISTORY_LENGTH)
    except Exception:
        pass

    # 读入既有历史（文件不存在则跳过）
    path = history_path()
    try:
        if path.exists():
            rl.read_history_file(str(path))
    except Exception:
        pass

    # 退出时写回
    def _save():
        try:
            rl.write_history_file(str(history_path()))
        except Exception:
            pass
    atexit.register(_save)

    # Tab 补全
    try:
        engine = CompletionEngine(evaluator)
        rl.set_completer(engine.complete)
        try:
            rl.set_completer_delims(' \t\n')
        except Exception:
            pass
        rl.parse_and_bind('tab: complete')
    except Exception:
        pass

    return rl


# ---------------------------------------------------------------------------
# 多行状态机
# ---------------------------------------------------------------------------

class ReplSession:
    """REPL 会话：多行缓冲 + 求值 + 错误输出。"""

    def __init__(self, evaluator=None, out=None, err=None):
        self.evaluator = evaluator if evaluator is not None else Evaluator()
        self.buffer = []
        self.state = STATE_IDLE
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr
        # D-04 / ADR-06：会话级用户定义名白名单，跨输入累积后注入 Lexer，
        # 使上一次输入里定义的方法/字段名在后续输入的调用点不被切碎。
        self._session_defs = set()

    # ---------- 纯逻辑：判定与解析 ----------

    @property
    def prompt(self):
        return PROMPT_CONTINUE if self.state == STATE_CONTINUE else PROMPT_IDLE

    def source(self):
        return '\n'.join(self.buffer)

    def reset(self):
        self.buffer = []
        self.state = STATE_IDLE

    @staticmethod
    def parse_help(line):
        """识别 `帮助` / `帮助 X`。命中返回 (True, arg)，否则 (False, None)。"""
        stripped = line.strip().rstrip('。')
        if stripped == HELP_WORD:
            return True, None
        if stripped.startswith(HELP_WORD):
            rest = stripped[len(HELP_WORD):].strip()
            if rest:
                return True, rest
        return False, None

    def needs_continuation(self, source):
        """R-1（ADR-03 修正）：**parser 权威**判定 source 是否"尚未写完"。

        真值表（唯一一套判定，不与 lexer 计数并存）：

        | parse(buffer) 结果          | 判定   | REPL 行为            |
        |-----------------------------|--------|----------------------|
        | 抛 UnexpectedEOFError       | 未闭合 | 显示 `... ` 继续收集 |
        | 成功                        | 完整   | 立即执行             |
        | 抛其他语法错误              | 真错误 | 报中文诊断，清缓冲   |

        唯一的 lexer 层例外：未闭合字符串在 tokenize 阶段就抛错，
        parser 根本拿不到 token，故一并归入"未闭合"。
        """
        from .errors import ErrorCategory
        try:
            parse(tokenize(source, external_defs=self._session_defs))
        except UnexpectedEOFError:
            return True
        except JiKuaiError as e:
            info = getattr(e, 'info', None)
            if (info is not None
                    and getattr(info, 'category', None) == ErrorCategory.LEXER
                    and '未闭合的字符串' in (info.message or '')):
                return True
            return False
        except Exception:
            return False
        return False

    # ---------- 副作用：喂一行、必要时求值 ----------

    def feed(self, line):
        """喂入一行输入。返回状态字符串：

          'exit'      —— 用户要求退出
          'idle'      —— 本行处理完毕（可能已求值/已报错），回到 IDLE
          'continue'  —— 需要续行
          'skip'      —— 空行、无动作
        """
        if self.state == STATE_IDLE:
            return self._feed_idle(line)
        return self._feed_continue(line)

    def _feed_idle(self, line):
        stripped = line.strip()
        if not stripped:
            return 'skip'
        if stripped in EXIT_WORDS:
            return 'exit'
        hit, arg = self.parse_help(stripped)
        if hit:
            print(help_text(arg), file=self.out)
            return 'idle'
        self.buffer = [line]
        return self._try_run()

    def _feed_continue(self, line):
        if not line.strip():
            # R-2 / D-02 修正：`...` 续行态下的空行 → 取消整个多行缓冲，
            # 清空已输入内容并回主提示符（否则除 Ctrl+C 别无退出方式）。
            print('已取消多行输入', file=self.out)
            self.reset()
            return 'idle'
        self.buffer.append(line)
        return self._try_run()

    def _try_run(self):
        src = self.source()
        if self.needs_continuation(src):
            self.state = STATE_CONTINUE
            return 'continue'
        try:
            # D-04：注入会话级白名单，并把本次收集到的定义名累积回会话
            lexer = Lexer(src, external_defs=self._session_defs)
            tokens = lexer.tokenize()
            self._session_defs |= set(lexer.get_user_defs())
            self.evaluator._current_source = src
            result = self.evaluator.eval(parse(tokens), source=src)
            if result is not None:
                print(self.evaluator._format_value(result), file=self.out)
        except (JiKuaiError, ParseError) as e:
            self._report(e)
        except ReturnSignal:
            # D-05 兜底：eval 顶层已拦截，此处防止其他路径逃逸
            print('语法错误：「返回」只能在函数或方法体内使用。', file=self.err)
        except BreakSignal:
            print('语法错误：「跳出」只能在循环体内使用。', file=self.err)
        except ContinueSignal:
            print('语法错误：「跳过」只能在循环体内使用。', file=self.err)
        except Exception as e:
            print(f"内部错误：{e}", file=self.err)
        self.reset()
        return 'idle'

    def _report(self, e):
        info = getattr(e, 'info', None)
        if info is not None:
            print(ErrorFormatter.format(info), file=self.err)
        elif isinstance(e, ParseError):
            print(f"语法错误：{e}", file=self.err)
        else:
            print(f"错误：{e}", file=self.err)

    # ---------- 交互主循环 ----------

    def run(self, banner=None):
        if banner:
            print(banner, file=self.out)
        setup_readline(self.evaluator)
        while True:
            try:
                line = input(self.prompt)
            except EOFError:
                print('\n再见！', file=self.out)
                break
            except KeyboardInterrupt:
                # Ctrl+C：放弃当前缓冲，不退出会话
                if self.state == STATE_CONTINUE:
                    print('\n已取消多行输入', file=self.out)
                    self.reset()
                    continue
                print('\n再见！', file=self.out)
                break
            if self.feed(line) == 'exit':
                print('再见！', file=self.out)
                break
