# -*- coding: utf-8 -*-
"""极快语言 - REPL 增强（M2-2 / ADR-03、ADR-05；W10 补 `需求` 元命令）。

五块能力：
  1. 多行续行状态机（IDLE / CONTINUE / SELECTING）
  2. 历史持久化（~/.jikuai_history，readline 或 pyreadline3，缺失则静默降级）
  3. Tab 补全（关键字 ∪ 动词 ∪ 全局变量名 ∪ 元命令，startswith 匹配）
  4. `帮助` 命令（REPL 内特殊识别，不进求值器）
  5. `需求` 命令（W10 / ADR-25：自然语言检索块 → 数字选中 → 把导入行与调用行
     追加到当前编辑缓冲；生成口径与《块选择协议 v0》一致）

设计要点：所有判定逻辑都做成不依赖 stdin 的纯函数/纯方法，
便于测试直接调用（见 tests/test_jikuai.py 的 M2-2 用例、
tests/test_repl_需求.py 的 W10 用例）。
"""

import atexit
import os
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
PROMPT_SELECT = '选块> '

EXIT_WORDS = ('退出', 'exit', 'quit')
HELP_WORD = '帮助'
REQUIREMENT_WORD = '需求'

#: `需求` 一次展示的候选数上限（W10 / ADR-25）。
REQUIREMENT_TOP_K = 5

#: 无实参时调用行写的占位符。与《块选择协议 v0》「粘合器生成规则」第 4 条
#: 同一口径（`tools/ai-bridge/glue.py`）——W9 要跨通道复用同一份 schema，
#: 这里绝不另造一套占位约定。
REQUIREMENT_ARG_PLACEHOLDER = '?'

#: 调用行的结果变量名。沿用桥接的 `赵果N` 命名。
REQUIREMENT_RESULT_VAR = '赵果1'

#: 选块态下取消本次选择的输入（与空行等价）。
REQUIREMENT_CANCEL = '0'

#: 打开 REPL 神经检索路径的环境变量（W11 / ADR-25 §3.1）。
#: 不设默认走启发式——REPL 第一次跑不该等 10s 冷启动。
#: 与 `jikuai.ai.embed_client.ENV_NEURAL` 保持同名字面量，避免两处漂移。
REQUIREMENT_NEURAL_ENV = 'JIKUAI_AI_NEURAL'
_NEURAL_TRUTHY = frozenset({'1', 'true', 'yes', 'on', '开', 'neural', '神经'})


def _neural_enabled():
    """REPL 神经路径是否打开。刻意在这里就地判读环境变量（而不是每次都 import
    `embed_client` 去问），神经开关关着的 90% 情况下能跳过整个 subprocess 模块
    的 import，把 REPL 冷启动开销压到最低。"""
    return os.environ.get(REQUIREMENT_NEURAL_ENV, '').strip().lower() in _NEURAL_TRUTHY


# 状态机状态
STATE_IDLE = 'IDLE'
STATE_CONTINUE = 'CONTINUE'
STATE_SELECTING = 'SELECTING'

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


def requirement_usage():
    """`帮助 需求` 的用法说明（W10）。"""
    return '\n'.join([
        f"用法：{REQUIREMENT_WORD} <自然语言需求>",
        f"说明：{REQUIREMENT_WORD} 是 REPL 元命令（不进求值器），把一句自然语言",
        f"      需求映射为语义块候选清单（最多 {REQUIREMENT_TOP_K} 条）。",
        "流程：",
        f"  1. `{REQUIREMENT_WORD} 求个平均` → 列出候选（编号 · 块名 · 领域 · 描述）",
        "  2. 再输入编号 → 把下面两行追加到当前编辑缓冲：",
        "       从 blocks.<领域>.<块名> 导入 <导出名>。",
        f"       定义{REQUIREMENT_RESULT_VAR}=<导出名>(<实参>)。",
        f"     编号后可直接带实参，如 `1 列 1 2 3`；不带则写占位 "
        f"{REQUIREMENT_ARG_PLACEHOLDER}（需人工填参）。",
        f"  3. 输入 `{REQUIREMENT_CANCEL}` 或空行取消本次选块",
        f"示例：{REQUIREMENT_WORD} 求个平均",
        f"神经检索：设环境变量 {REQUIREMENT_NEURAL_ENV}=1 开启（默认走启发式，",
        "        避免首次检索等模型冷启动；sidecar 失败自动降级启发式）。",
    ])


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
        lines.append(f'输入 `{REQUIREMENT_WORD} <自然语言>` 按需求检索块，例如：'
                     f'{REQUIREMENT_WORD} 求个平均'
                     f'（`帮助 {REQUIREMENT_WORD}` 看详细用法）')
        lines.append('输入 退出 / exit / quit 结束会话')
        return '\n'.join(lines)
    if arg == REQUIREMENT_WORD:
        return requirement_usage()
    usage = verb_usage(arg)
    if usage is not None:
        return usage
    if arg in ALL_KEYWORDS:
        return f'"{arg}" 是关键字。输入 帮助 查看关键字总览。'
    return f'未找到 "{arg}"，试试 "帮助"'


# ---------------------------------------------------------------------------
# 需求命令：块候选 → 极快源码片段
# ---------------------------------------------------------------------------

def block_export_name(name, domain):
    """查一个块的导出名（W10）。查不到返回 None。

    走 `pkg.blocks.block_exports`——W7 引入的热路径快通道：同目录 `块.json`
    带 `导出` 就读元数据，否则回退正则扫 `.jk`。多导出时取字典序首个，与
    `tools/ai-bridge/select.resolve_export` 同一口径（现有 stdlib 块都是单导出）。
    """
    from .pkg.blocks import blocks_root, block_exports
    base = os.path.join(blocks_root(), domain, name)
    for jk in (os.path.join(base, name + '.jk'), os.path.join(base, 'main.jk')):
        if os.path.isfile(jk):
            exports = block_exports(jk)
            if exports:
                return sorted(exports)[0]
    return None


def requirement_snippet(hit, args=None):
    """把一条检索命中渲染成 `(导入行, 调用行)`。

    ADR-15 §3.7「导入用目录名、调用用导出名」，与《块选择协议 v0》的
    `步骤[].块` / `步骤[].导出名` 两字段一一对应::

        从 blocks.数据.均值 导入 中位。
        定义赵果1=中位(?)。

    `args` 为空时实参写占位符（协议：缺省则生成 `?` 并提示需人工填参）。
    导出名查不到时降级用块名——总比不给代码好，用户一眼看得出要改哪儿。
    """
    export = block_export_name(hit.name, hit.domain) or hit.name
    import_line = f'从 blocks.{hit.domain}.{hit.name} 导入 {export}。'
    actual = args.strip() if args and args.strip() else REQUIREMENT_ARG_PLACEHOLDER
    call_line = f'定义{REQUIREMENT_RESULT_VAR}={export}({actual})。'
    return import_line, call_line


def requirement_candidates_text(query, hits):
    """候选清单展示文本。编号从 1 起，版式对齐 `jk 块 search`。"""
    lines = [f'需求：{query}    {hits[0].path}']
    for i, h in enumerate(hits, 1):
        lines.append(f'  {i}. {h.name}（{h.domain}）  分数 {h.score:.4f}')
        lines.append(f'     {h.description}')
    lines.append(f'输入编号 1-{len(hits)} 选块，'
                 f'`{REQUIREMENT_CANCEL}` 或空行取消。')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Tab 补全
# ---------------------------------------------------------------------------

class CompletionEngine:
    """按前缀提供补全候选。

    候选源 = ALL_KEYWORDS ∪ VERB_ARITY.keys() ∪ evaluator.global_env.vars.keys()
    ∪ REPL 元命令（帮助 / 需求）。
    每次 candidates() 都重新读全局变量，保证 REPL 中新定义的名字立即可补全。
    """

    def __init__(self, evaluator=None):
        self.evaluator = evaluator
        self._static = (set(ALL_KEYWORDS) | set(VERB_ARITY.keys())
                        | {HELP_WORD, REQUIREMENT_WORD})

    def candidates(self, prefix):
        # M5 / T-M5-L04：候选计算下移到 jikuai.completion.repl_candidates。
        # 函数内 import 是刻意的：避免 completion 与 repl_session 模块级互引。
        from .completion import repl_candidates
        ev = self.evaluator
        extra = None
        if ev is not None:
            try:
                extra = set(ev.global_env.vars.keys())
            except AttributeError:
                extra = None
        return repl_candidates(prefix, extra_names=extra)

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
        # D-04 / ADR-06 / DEF-02：会话级用户定义名白名单，跨输入累积后注入 Lexer。
        # DEF-02 起改为 `(name, kind, owner_class)` 三元组集合，使类内 method/field
        # 在下一次分词时不被提升为会话全域（顶层仍走内建动词语义），而仅在 `.成员`
        # 松弛路径可命中。
        self._session_defs = set()
        # W10：`需求` 检索出的候选；选块态下等待用户输入编号
        self.pending_hits = []
        self.pending_query = ''

    # ---------- 纯逻辑：判定与解析 ----------

    @property
    def prompt(self):
        if self.state == STATE_SELECTING:
            return PROMPT_SELECT
        return PROMPT_CONTINUE if self.state == STATE_CONTINUE else PROMPT_IDLE

    def source(self):
        return '\n'.join(self.buffer)

    def reset(self):
        self.buffer = []
        self.state = STATE_IDLE
        self.pending_hits = []
        self.pending_query = ''

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

    @staticmethod
    def parse_requirement(line):
        """识别 `需求` / `需求 <自然语言>`（W10，骨架照 `parse_help`）。

        命中返回 `(True, query)`；只写 `需求` 时 query 为 None（打用法）。
        不是需求命令返回 `(False, None)`。
        """
        stripped = line.strip().rstrip('。')
        if stripped == REQUIREMENT_WORD:
            return True, None
        if stripped.startswith(REQUIREMENT_WORD):
            rest = stripped[len(REQUIREMENT_WORD):].strip()
            return True, (rest or None)
        return False, None

    @staticmethod
    def parse_selection(line, total):
        """解析选块态的一行输入，返回 `(种类, 序号, 实参)`。

        种类：
          'cancel'  空行 / `0` —— 取消本次选块
          'ok'      合法编号 1..total（实参可能为 None）
          'bad'     不是数字
          'range'   是数字但越界
        """
        stripped = line.strip()
        if not stripped or stripped == REQUIREMENT_CANCEL:
            return 'cancel', None, None
        parts = stripped.split(None, 1)
        head = parts[0].rstrip('。')
        args = parts[1].strip() if len(parts) > 1 else None
        try:
            index = int(head)
        except ValueError:
            return 'bad', None, None
        if index == 0:
            return 'cancel', None, None
        if index < 1 or index > total:
            return 'range', index, None
        return 'ok', index, (args or None)

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
          'select'    —— 已列出块候选，等待输入编号（W10）
          'skip'      —— 空行、无动作
        """
        if self.state == STATE_SELECTING:
            return self._feed_selecting(line)
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
        # W10：`需求` 与 `帮助` 同层，都是元命令，不进求值器
        req, query = self.parse_requirement(stripped)
        if req:
            return self._feed_requirement(query)
        self.buffer = [line]
        return self._try_run()

    def _feed_requirement(self, query):
        """`需求 <query>`：检索 top-K 候选并进入选块态（W10 / W11）。

        默认走启发式（不传 query_vector）——REPL 冷启动不该为一个可选元命令
        去加载几百 MB 的模型（模型冷启动实测 ~10s）。用户显式打开
        `JIKUAI_AI_NEURAL=1` 才 subprocess 拉一次 sidecar。

        任何 sidecar 失败都降级到启发式 + 一行 stderr 提示（W11 · ADR-25
        §3.1 分层兜底）——REPL 不会因为神经路径挂了就吐不出候选。
        """
        if not query:
            print(requirement_usage(), file=self.out)
            return 'idle'
        from .ai import retrieval
        # W11：`JIKUAI_AI_NEURAL=1` 打开神经路径。刻意用环境变量而非新元命令：
        # 「需求」已经是 REPL 里 CLI 味最重的入口了，再加 `神经` 元命令会把
        # 会话状态机膨胀一档；env var 也方便脚本化调用（IDE 里预置一次即可）。
        query_vector = None
        if _neural_enabled():
            from .ai import embed_client
            expected = embed_client.index_dim()
            vec, why = embed_client.fetch_query_vector(query, expected_dim=expected)
            if vec is None:
                # 文案前缀走常量，与 CLI（blocks_cli）/ Web（tools/web/server.py）同源。
                from .ai.embed_client import DEGRADE_PREFIX
                降级说明 = DEGRADE_PREFIX + why
                print(降级说明, file=self.err)
            else:
                query_vector = vec
        try:
            hits = retrieval.retrieve(query, top=REQUIREMENT_TOP_K,
                                      query_vector=query_vector)
        except retrieval.RetrievalError as e:
            print(f'检索失败：{e}', file=self.err)
            return 'idle'
        if not hits:
            print(f'没有匹配「{query}」的块。换个说法，或用 `帮助` 看内建动词。',
                  file=self.out)
            return 'idle'
        self.pending_hits = list(hits)
        self.pending_query = query
        self.state = STATE_SELECTING
        print(requirement_candidates_text(query, hits), file=self.out)
        return 'select'

    def _feed_selecting(self, line):
        """选块态：数字选中 → 把导入行与调用行追加到 buffer（W10）。"""
        total = len(self.pending_hits)
        kind, index, args = self.parse_selection(line, total)
        if kind == 'cancel':
            print('已取消选块', file=self.out)
            self.reset()
            return 'idle'
        if kind == 'bad':
            print(f'请输入 1-{total} 的编号（`{REQUIREMENT_CANCEL}` 或空行取消）',
                  file=self.err)
            return 'select'
        if kind == 'range':
            print(f'编号 {index} 超出范围，可选 1-{total}'
                  f'（`{REQUIREMENT_CANCEL}` 或空行取消）', file=self.err)
            return 'select'
        hit = self.pending_hits[index - 1]
        import_line, call_line = requirement_snippet(hit, args)
        # 追加到当前编辑缓冲，转续行态让用户接着写（本轮不求值）
        self.buffer.append(import_line)
        self.buffer.append(call_line)
        self.pending_hits = []
        self.pending_query = ''
        self.state = STATE_CONTINUE
        print(import_line, file=self.out)
        print(call_line, file=self.out)
        if REQUIREMENT_ARG_PLACEHOLDER in call_line:
            print(f'已追加到编辑缓冲；`{REQUIREMENT_ARG_PLACEHOLDER}` 是占位实参，'
                  f'需人工填参（空行放弃本次缓冲）', file=self.out)
        else:
            print('已追加到编辑缓冲（空行放弃本次缓冲）', file=self.out)
        return 'continue'

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
            # D-04 / DEF-02：注入会话级白名单，并把本次收集到的定义**签名**
            # （name, kind, owner_class）累积回会话——携带类归属才能在下一次
            # 分词时把类内 method/field 限定回类作用域。
            lexer = Lexer(src, external_defs=self._session_defs)
            tokens = lexer.tokenize()
            self._session_defs |= set(lexer.get_user_def_signatures())
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
                if self.state == STATE_SELECTING:
                    print('\n已取消选块', file=self.out)
                    self.reset()
                    continue
                print('\n再见！', file=self.out)
                break
            if self.feed(line) == 'exit':
                print('再见！', file=self.out)
                break