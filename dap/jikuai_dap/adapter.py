# -*- coding: utf-8 -*-
"""极快语言 · DAP 适配器（M6-P3 · T-M6-D03 · ADR-20）。

单步调试 MVP。生命周期：
    initialize → launch → setBreakpoints → configurationDone → 运行
    → stopped 事件 → stackTrace / scopes / variables → next / continue
    → terminated

线程模型：
    - 主线程：读取并分发 DAP 请求，写响应/事件（所有写入过 _write_lock）。
    - 调试线程：跑 `Evaluator(hook=DebugHook(...))`。
    - `threading.Event` 做暂停/恢复同步。DebugHook.before_stmt 在调试线程里
      判定断点/单步，命中则发 stopped 事件并阻塞等待恢复指令。

能力边界（ADR-20）：范围外能力（条件断点 / evaluate / setVariable / 多线程 /
函数·数据·异常断点）一律返回 success=false，message 含「暂不支持」与 JK-E8001。

物理隔离：主包 `jikuai` 不 import 本模块；本模块 import `jikuai`。
"""

from __future__ import annotations

import sys
import threading
from typing import Any, Dict, List, Optional

from jikuai.service import TextDocumentStore, SessionHost
from jikuai.frontend import compile_source
from jikuai.evaluator import Evaluator, ExecHook, RMB, JiKuaiError
from jikuai.diagnostics.codes import JK_E8001

from . import transport


# 单线程模型固定线程 id / 帧 id / 作用域 variablesReference
THREAD_ID = 1
FRAME_ID = 1
LOCALS_REF = 1000


class TerminateDebug(BaseException):
    """请求终止调试执行的信号。

    继承 BaseException（而非 Exception），确保它不会被求值器里的
    `except Exception`（如 `_eval_Try` 的兜底捕获）或原生异常包装吞掉，
    可一路上抛到调试线程顶层，干净结束执行。
    """
    pass


# ─────────────────────────── 值格式化 ───────────────────────────

def format_value(v):
    """把运行时值格式化为调试面板展示字符串（中文友好）。

    人民币显示为 `￥xx.xx`；nil/bool 用中文；字符串加引号；其余用 repr。
    """
    if v is None:
        return "空"
    if isinstance(v, bool):
        return "真" if v else "假"
    if isinstance(v, RMB):
        return f"￥{v.amount}"      # AC-M6-05-03：人民币 ￥xx.xx
    if isinstance(v, str):
        return repr(v)
    return repr(v)


def value_type_name(v):
    """返回值的中文类型名，供 variables 的 type 字段展示。"""
    if v is None:
        return "空"
    if isinstance(v, bool):
        return "布尔"
    if isinstance(v, RMB):
        return "人民币"
    if isinstance(v, str):
        return "文本"
    if isinstance(v, int):
        return "整数"
    if isinstance(v, float):
        return "小数"
    if isinstance(v, list):
        return "列表"
    if isinstance(v, dict):
        return "字典"
    return type(v).__name__


# ─────────────────────────── 调试钩子 ───────────────────────────

class DebugHook(ExecHook):
    """执行钩子：在每条语句前判定断点/单步，命中则暂停等待恢复指令。

    运行在调试线程。所有与主线程共享的状态经 adapter 的锁/事件同步。
    """

    def __init__(self, adapter):
        self._adapter = adapter

    def before_stmt(self, node, env):
        adapter = self._adapter
        if adapter._terminate:
            raise TerminateDebug()

        line = getattr(node, "line", 0)
        should_stop = False
        reason = None

        mode = adapter._step_mode
        if adapter._pause_requested:
            should_stop, reason = True, "pause"
            adapter._pause_requested = False
        elif mode == "stepIn":
            should_stop, reason = True, "step"
        elif mode == "next":
            # step over：回到同层或更浅层时停（近似：env 链深度不深于暂停点）
            if adapter._env_depth(env) <= adapter._step_ref_depth:
                should_stop, reason = True, "step"
        elif mode == "stepOut":
            # step out：回到更浅层时停
            if adapter._env_depth(env) < adapter._step_ref_depth:
                should_stop, reason = True, "step"

        if not should_stop and line in adapter._breakpoints:
            should_stop, reason = True, "breakpoint"

        if should_stop:
            adapter._pause(node, env, reason)


# ─────────────────────────── DAP 适配器 ───────────────────────────

class DapAdapter:
    """DAP 协议状态机 + 会话编排。"""

    def __init__(self, instream, outstream):
        self._in = instream
        self._out = outstream
        self._write_lock = threading.Lock()
        self._seq = 0

        # 会话资产（ADR-20：复用 M5 service 层）
        self._store = TextDocumentStore()
        self._host = SessionHost(self._store)
        self._uri = None
        self._source_path = None
        self._source_text = ""

        # 断点：源码行号集合（1-based）
        self._breakpoints = set()

        # 执行/暂停状态
        self._debug_thread = None
        self._resume_event = threading.Event()
        self._step_mode = None    # continue / next / stepIn / stepOut / None
        self._step_ref_depth = 0
        self._pause_requested = False
        self._terminate = False

        # 暂停点快照
        self._paused = False
        self._cur_node = None
        self._cur_env = None
        self._running_done = False

    # ─────── 帧读写 ───────

    def _next_seq(self):
        self._seq += 1
        return self._seq

    def _send(self, message):
        message["seq"] = self._next_seq()
        with self._write_lock:
            transport.write_message(self._out, message)

    def _send_response(self, request, body=None, success=True, message=None):
        resp = {
            "type": "response",
            "request_seq": request.get("seq", 0),
            "success": success,
            "command": request.get("command", ""),
        }
        if body is not None:
            resp["body"] = body
        if message is not None:
            resp["message"] = message
        self._send(resp)

    def _send_event(self, event, body=None):
        evt = {"type": "event", "event": event}
        if body is not None:
            evt["body"] = body
        self._send(evt)

    def _reject_unsupported(self, request, capability):
        """范围外能力统一拒绝：success=false，message 含 JK-E8001 与「暂不支持」。"""
        msg = f"{JK_E8001} 调试能力暂不支持：{capability}"
        self._send_response(request, success=False, message=msg)

    # ─────── 主循环 ───────

    def serve(self):
        """阻塞读取并处理 DAP 请求，直到 EOF 或 disconnect。返回退出码。"""
        while True:
            try:
                msg = transport.read_message(self._in)
            except Exception:
                break
            if msg is None:
                break
            if msg.get("type") != "request":
                continue
            command = msg.get("command", "")
            handler = getattr(self, f"_on_{command}", None)
            if handler is None:
                self._reject_unsupported(msg, command)
                continue
            try:
                stop = handler(msg)
            except Exception as e:  # noqa: BLE001
                self._send_response(msg, success=False, message=f"内部错误：{e}")
                stop = False
            if stop:
                break
        # 收尾：确保调试线程不再阻塞
        self._terminate = True
        self._resume_event.set()
        t = self._debug_thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        return 0

    # ─────── 请求处理器 ───────

    def _on_initialize(self, request):
        caps = {
            "supportsConfigurationDoneRequest": True,
            "supportsEvaluateForHovers": False,
            "supportsSetVariable": False,
            "supportsConditionalBreakpoints": False,
            "supportsHitConditionalBreakpoints": False,
            "supportsFunctionBreakpoints": False,
            "supportsDataBreakpoints": False,
            "supportsExceptionOptions": False,
            "supportsStepBack": False,
            "supportsRestartFrame": False,
            "supportTerminateDebuggee": False,
        }
        self._send_response(request, body=caps)
        # DAP 约定：initialize 响应后发 initialized 事件
        self._send_event("initialized")
        return False

    def _on_launch(self, request):
        args = request.get("arguments", {}) or {}
        program = args.get("program")
        code = args.get("code") or args.get("source")
        if program:
            try:
                with open(program, "r", encoding="utf-8") as f:
                    self._source_text = f.read()
            except OSError as e:
                self._send_response(request, success=False, message=f"无法读取程序：{e}")
                return False
            self._source_path = program
            self._uri = program
        elif code is not None:
            self._source_text = code
            self._source_path = args.get("path", "inline://main.jk")
            self._uri = self._source_path
        else:
            self._send_response(request, success=False, message="launch 缺少 program 或 code")
            return False
        self._store.did_open(self._uri, self._source_text)
        self._send_response(request, body={})
        return False

    def _on_setBreakpoints(self, request):
        args = request.get("arguments", {}) or {}
        bps = args.get("breakpoints")
        # AC-M6-05-04：条件断点等范围外能力 → 拒绝
        if bps:
            for bp in bps:
                if bp.get("condition") or bp.get("hitCondition") or bp.get("logMessage"):
                    self._reject_unsupported(request, "条件断点 / 命中计数 / 日志断点")
                    return False
            lines = [int(bp.get("line", 0)) for bp in bps]
        else:
            lines = [int(n) for n in (args.get("lines") or [])]

        self._breakpoints = set(l for l in lines if l > 0)
        verified = [{"verified": True, "line": l} for l in lines]
        self._send_response(request, body={"breakpoints": verified})
        return False

    def _on_configurationDone(self, request):
        self._send_response(request, body={})
        self._start_execution()
        return False

    def _on_threads(self, request):
        self._send_response(request, body={
            "threads": [{"id": THREAD_ID, "name": "主线程"}]
        })
        return False

    def _on_stackTrace(self, request):
        line = getattr(self._cur_node, "line", 0) if self._cur_node is not None else 0
        frames = [{
            "id": FRAME_ID,
            "name": "主程序",
            "line": line,
            "column": 1,
            "source": {"name": self._source_name(), "path": self._source_path or ""},
        }]
        self._send_response(request, body={
            "stackFrames": frames,
            "totalFrames": len(frames),
        })
        return False

    def _on_scopes(self, request):
        scopes = [{
            "name": "局部变量",
            "variablesReference": LOCALS_REF,
            "expensive": False,
        }]
        self._send_response(request, body={"scopes": scopes})
        return False

    def _on_variables(self, request):
        args = request.get("arguments", {}) or {}
        ref = int(args.get("variablesReference", 0))
        variables = []
        if ref == LOCALS_REF and self._cur_env is not None:
            for name, val in self._collect_scope(self._cur_env).items():
                variables.append({
                    "name": name,
                    "value": format_value(val),
                    "type": value_type_name(val),
                    "variablesReference": 0,
                })
        self._send_response(request, body={"variables": variables})
        return False

    def _on_continue(self, request):
        self._step_mode = "continue"
        self._resume()
        self._send_response(request, body={"allThreadsContinued": True})
        return False

    def _on_next(self, request):
        self._step_mode = "next"
        self._step_ref_depth = self._env_depth(self._cur_env)
        self._resume()
        self._send_response(request, body={})
        return False

    def _on_stepIn(self, request):
        self._step_mode = "stepIn"
        self._resume()
        self._send_response(request, body={})
        return False

    def _on_stepOut(self, request):
        self._step_mode = "stepOut"
        self._step_ref_depth = self._env_depth(self._cur_env)
        self._resume()
        self._send_response(request, body={})
        return False

    def _on_pause(self, request):
        self._pause_requested = True
        self._send_response(request, body={})
        return False

    def _on_evaluate(self, request):
        # AC-M6-05-04：表达式求值不支持
        self._reject_unsupported(request, "表达式求值（evaluate）")
        return False

    def _on_setVariable(self, request):
        self._reject_unsupported(request, "设置变量（setVariable）")
        return False

    def _on_disconnect(self, request):
        self._terminate = True
        self._resume()
        self._send_response(request, body={})
        return True

    def _on_terminate(self, request):
        self._terminate = True
        self._resume()
        self._send_response(request, body={})
        return True

    # ─────── 执行与暂停 ───────

    def _start_execution(self):
        self._step_mode = "continue"
        self._debug_thread = threading.Thread(
            target=self._run_program, name="jikuai-debug", daemon=True)
        self._debug_thread.start()

    def _run_program(self):
        """调试线程主体：编译 + 求值。求值期间重定向 print 到 DAP output 事件。"""
        old_stdout = sys.stdout
        sys.stdout = _DapOutput(self)
        exit_code = 0
        try:
            result = compile_source(self._source_text, file=self._uri or "main.jk")
            ev = Evaluator(hook=DebugHook(self))
            ev.eval(result.ast, source=self._source_text)
        except TerminateDebug:
            exit_code = 0
        except JiKuaiError as e:
            sys.stdout = old_stdout
            self._send_event("output", {
                "category": "stderr",
                "output": f"运行错误：{e}\n",
            })
            exit_code = 1
        except Exception as e:  # noqa: BLE001
            sys.stdout = old_stdout
            self._send_event("output", {
                "category": "stderr",
                "output": f"内部错误：{e}\n",
            })
            exit_code = 1
        finally:
            sys.stdout = old_stdout
            self._paused = False
            self._running_done = True
            self._send_event("exited", {"exitCode": exit_code})
            self._send_event("terminated")

    def _pause(self, node, env, reason):
        """调试线程：记录暂停点，发 stopped 事件，阻塞等待恢复。"""
        self._cur_node = node
        self._cur_env = env
        self._paused = True
        self._resume_event.clear()
        self._send_event("stopped", {
            "reason": reason or "step",
            "threadId": THREAD_ID,
            "allThreadsStopped": True,
            # 协议扩展字段：直接带上暂停行，便于测试免一次 stackTrace 往返
            "line": getattr(node, "line", 0),
            "source": {"name": self._source_name(), "path": self._source_path or ""},
        })
        # 阻塞直到主线程恢复；带轮询以便 terminate 时也能醒来
        while not self._resume_event.wait(timeout=0.2):
            if self._terminate:
                break
        self._paused = False
        if self._terminate:
            raise TerminateDebug()

    def _resume(self):
        self._paused = False
        self._resume_event.set()

    # ─────── 辅助 ───────

    @staticmethod
    def _env_depth(env):
        d = 0
        while env is not None:
            d += 1
            env = getattr(env, "parent", None)
        return d

    @staticmethod
    def _collect_scope(env):
        """沿环境链收集可见变量（内层遮蔽外层），供 variables 展示。"""
        collected = {}
        chain = []
        e = env
        while e is not None:
            chain.append(e)
            e = getattr(e, "parent", None)
        for e in reversed(chain):
            for name, val in getattr(e, "vars", {}).items():
                collected[name] = val
        return collected

    def _source_name(self):
        path = self._source_path or "main.jk"
        for sep in ("/", "\\"):
            if sep in path:
                path = path.rsplit(sep, 1)[-1]
        return path


class _DapOutput:
    """把 `print` 的文本输出转成 DAP output 事件，避免污染 stdio 协议帧。"""

    def __init__(self, adapter):
        self._adapter = adapter

    def write(self, s):
        if s:
            self._adapter._send_event("output", {"category": "stdout", "output": s})
        return len(s)

    def flush(self):
        pass


def main(argv=None):
    """入口：走本机 stdio。二进制层用 sys.stdin/stdout.buffer。"""
    instream = sys.stdin.buffer
    outstream = sys.stdout.buffer
    adapter = DapAdapter(instream, outstream)
    return adapter.serve()


if __name__ == "__main__":
    sys.exit(main())
