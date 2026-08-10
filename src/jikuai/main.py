# -*- coding: utf-8 -*-
"""极快语言 - CLI 入口与 REPL。"""

import sys
from ._version import __version__
from .lexer import tokenize
from .parser import parse, ParseError
from .evaluator import Evaluator, JiKuaiError
from .errors import ErrorFormatter
from .frontend import compile_source
from .diagnostics import NullSink, make_default_sink, render_all_text


# 兼容旧引用（`from jikuai.main import VERSION`）。v0.16.0 W25 起单一真源在
# `_version.__version__`；此别名仅作过渡，等确认无外部依赖后可清理。
VERSION = __version__
BANNER = f"""
╔══════════════════════════════════╗
║   极快 JiKuai v{VERSION}                 ║
║   极简·极速·极中国                ║
║   输入 退出 或 Ctrl+C 退出        ║
╚══════════════════════════════════╝
"""


def run_source(source, evaluator=None, file=None):
    """编译并执行极快源代码。返回最终结果。

    v0.5.0（ADR-17）：编译走 `frontend.compile_source`（两遍分词 + 静态诊断）。
    收集到的**警告/提示**类诊断打印到 stderr，不影响返回值与退出码——
    警告不是错误，程序照常执行。`JIKUAI_DIAGNOSTICS=off` 时不打印。
    """
    if evaluator is None:
        evaluator = Evaluator()
    evaluator._current_source = source

    result = compile_source(source, file=file)
    _report_diagnostics(result.diagnostics, source)
    return evaluator.eval(result.ast, source=source)


def _report_diagnostics(diagnostics, source):
    """把非错误级诊断渲染到 stderr。错误级诊断由异常路径负责，这里不重复输出。"""
    if not diagnostics:
        return
    if isinstance(make_default_sink(), NullSink):   # JIKUAI_DIAGNOSTICS=off
        return
    non_fatal = [d for d in diagnostics if d.severity != "错误"]
    if not non_fatal:
        return
    print(render_all_text(non_fatal, source_lines=source.split('\n')),
          file=sys.stderr)



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
        run_source(source, evaluator, file=os.path.abspath(filepath))
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
        print("  jk 包 <子命令>  包管理（初始化/添加/装/列表/运行）")
        print("  jk 块 <子命令>  块生态（列表/查找/选/详情/校验/索引）")
        print("  jk -h           显示帮助")
        print("  jk -v           显示版本")
    elif sys.argv[1] in ('-v', '--version', '版本'):
        print(f"极快 v{VERSION}")
    elif sys.argv[1] in ('包', 'pkg', '包管理'):
        from .pkg.cli import run as pkg_run
        sys.exit(pkg_run(sys.argv[2:]))
    elif sys.argv[1] in ('块', 'blocks', 'block'):
        from .pkg import blocks_cli
        sys.exit(blocks_cli.run(sys.argv[2:]))
    else:
        run_file(sys.argv[1])


if __name__ == '__main__':
    main()

