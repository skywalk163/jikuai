# -*- coding: utf-8 -*-
"""极快语言 - CLI 入口与 REPL。"""

import sys
from .lexer import tokenize
from .parser import parse, ParseError
from .evaluator import Evaluator, JiKuaiError
from .errors import ErrorFormatter


VERSION = "0.4.1"
BANNER = f"""
╔══════════════════════════════════╗
║   极快 JiKuai v{VERSION}                  ║
║   极简·极速·极中国                ║
║   输入 退出 或 Ctrl+C 退出        ║
╚══════════════════════════════════╝
"""


def run_source(source, evaluator=None):
    """编译并执行极快源代码。返回最终结果。"""
    if evaluator is None:
        evaluator = Evaluator()
    evaluator._current_source = source
    tokens = tokenize(source)
    ast = parse(tokens)
    return evaluator.eval(ast, source=source)


def run_file(filepath):
    """执行 .jk 文件。"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
    except FileNotFoundError:
        print(f"错误：找不到文件 {filepath}", file=sys.stderr)
        sys.exit(1)
    except UnicodeDecodeError:
        print(f"错误：文件编码不是 UTF-8", file=sys.stderr)
        sys.exit(1)

    import os
    evaluator = Evaluator()
    evaluator._current_file = os.path.abspath(filepath)
    evaluator._current_source = source
    try:
        run_source(source, evaluator)
    except JiKuaiError as e:
        if getattr(e, 'info', None) is not None:
            print(ErrorFormatter.format(e.info), file=sys.stderr)
        else:
            print(f"运行错误：{e}", file=sys.stderr)
        sys.exit(1)
    except ParseError as e:
        if getattr(e, 'info', None) is not None:
            print(ErrorFormatter.format(e.info), file=sys.stderr)
        else:
            print(f"语法错误：{e}", file=sys.stderr)
        sys.exit(1)


def repl():
    """启动交互式 REPL（增强版：多行 / 历史 / 补全 / 帮助）。"""
    from .repl_session import ReplSession
    session = ReplSession()
    session.run(banner=BANNER)


def main():
    """CLI 主入口。"""
    if len(sys.argv) < 2:
        repl()
    elif sys.argv[1] in ('-h', '--help', '帮助'):
        print(f"极快语言 v{VERSION}")
        print("用法：")
        print("  jk              进入交互式 REPL")
        print("  jk <文件.jk>    执行文件")
        print("  jk -h           显示帮助")
        print("  jk -v           显示版本")
    elif sys.argv[1] in ('-v', '--version', '版本'):
        print(f"极快 v{VERSION}")
    else:
        run_file(sys.argv[1])


if __name__ == '__main__':
    main()

