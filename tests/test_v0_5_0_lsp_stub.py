# -*- coding: utf-8 -*-
"""v0.5.0 · M4-P4 支线 · LSP 桩协议级测试（G11 前身）。

覆盖任务：
    T-M4-L01  包骨架与独立分发
    T-M4-L02  server 桩（initialize / didOpen / didChange / shutdown / exit）
    T-M4-L03  协议级测试（脱离 VS Code，subprocess + 手写 LSP 客户端）

**硬门禁**：任何一条依赖不可用时必须 skip，不得 fail、不得 error、
不得在 collect 阶段崩。用 pytest.importorskip 或 try/except 早期守护。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

# ---------------------------------------------------------------------------
# 路径守护：让主包与 LSP 桩包都可被本进程与子进程 import
# ---------------------------------------------------------------------------

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, 'src')
_LSP = os.path.join(_ROOT, 'lsp')
sys.path.insert(0, _SRC)
sys.path.insert(0, _LSP)


# ---------------------------------------------------------------------------
# 依赖可用性守护：不可用则整个模块 skip（绝不 fail）
# ---------------------------------------------------------------------------

def _lsp_available():
    """检查 jikuai_lsp 是否可导入。不可用则本模块所有用例 skip。"""
    try:
        import importlib
        importlib.import_module('jikuai_lsp')
        importlib.import_module('jikuai_lsp.transport')
        importlib.import_module('jikuai_lsp.capabilities')
        importlib.import_module('jikuai_lsp.server')
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _lsp_available(),
    reason="jikuai_lsp 依赖不可用（如 pygls 缺失或路径未就绪），跳过 LSP 桩测试",
)


# ---------------------------------------------------------------------------
# 帧读写辅助（子进程侧走同款帧格式，见 lsp/jikuai_lsp/transport.py）
# ---------------------------------------------------------------------------

def _write_frame(stream, obj):
    """把 dict 序列化并按 LSP 帧格式写入 stream。"""
    body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    header = f"Content-Length: {len(body)}\r\n\r\n".encode('ascii')
    stream.write(header)
    stream.write(body)
    stream.flush()


def _read_frame(stream, timeout: float = 5.0):
    """从 stream 读取一条 LSP 帧；超时或流关闭返回 None。

    通过 select 或简易 deadline 控制超时，避免测试卡死。
    Windows 上 select 不支持文件句柄，故用逐行阻塞读取 + 进程 poll 兜底。
    """
    deadline = time.time() + timeout
    headers = {}
    # ---- 读头部 ----
    while True:
        if time.time() > deadline:
            return None
        line = stream.readline()
        if not line:
            return None
        try:
            decoded = line.decode('ascii', errors='replace')
        except Exception:
            return None
        if decoded in ('\r\n', '\n', '\r'):
            break
        if ':' in decoded:
            k, v = decoded.split(':', 1)
            headers[k.strip().lower()] = v.strip()
    # ---- 读体 ----
    length = int(headers.get('content-length', '0') or '0')
    if length <= 0:
        return None
    body = b''
    remaining = length
    while remaining > 0:
        if time.time() > deadline:
            return None
        chunk = stream.read(remaining)
        if not chunk:
            return None
        body += chunk
        remaining -= len(chunk)
    return json.loads(body.decode('utf-8'))


def _read_until(stream, predicate, max_msgs: int = 10, timeout: float = 5.0):
    """连续读消息直到 predicate(msg) 为真；返回该消息，其余消息忽略。

    用于跳过中间的日志/无关通知，稳定地等到目标响应或目标通知。
    """
    for _ in range(max_msgs):
        msg = _read_frame(stream, timeout=timeout)
        if msg is None:
            return None
        if predicate(msg):
            return msg
    return None


# ---------------------------------------------------------------------------
# 子进程夹具
# ---------------------------------------------------------------------------

@pytest.fixture
def lsp_process():
    """启动 `python -m jikuai_lsp` 子进程，测试结束后确保关闭。

    通过 PYTHONPATH 让子进程找到主包与 LSP 桩包（无需 pip install -e）。
    """
    env = os.environ.copy()
    # 保留原有 PYTHONPATH 并追加 src / lsp
    existing = env.get('PYTHONPATH', '')
    parts = [_SRC, _LSP]
    if existing:
        parts.append(existing)
    env['PYTHONPATH'] = os.pathsep.join(parts)
    # 避免子进程 Python 输出编码在 Windows 下出错
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'

    proc = subprocess.Popen(
        [sys.executable, '-m', 'jikuai_lsp'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        # bufsize=0 让 stdout 无缓冲，帧到达即可读
        bufsize=0,
    )
    try:
        yield proc
    finally:
        # 优雅关闭：若还活着，尝试发 exit 通知，然后强杀
        if proc.poll() is None:
            try:
                _write_frame(proc.stdin, {"jsonrpc": "2.0", "method": "exit"})
            except Exception:
                pass
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

def _initialize(proc, msg_id=1):
    """发送 initialize 请求并返回响应体。"""
    _write_frame(proc.stdin, {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "initialize",
        "params": {
            "processId": None,
            "rootUri": None,
            "capabilities": {},
        },
    })
    return _read_until(
        proc.stdout,
        lambda m: m.get('id') == msg_id and 'result' in m,
        max_msgs=10,
    )


def _initialized(proc):
    _write_frame(proc.stdin, {
        "jsonrpc": "2.0",
        "method": "initialized",
        "params": {},
    })


def test_initialize_returns_capabilities(lsp_process):
    """T-M4-L03-01：initialize 往返成功，capabilities 非空且含 textDocumentSync。"""
    resp = _initialize(lsp_process)
    assert resp is not None, "未收到 initialize 响应"
    result = resp.get('result')
    assert isinstance(result, dict) and result, "initialize.result 应为非空 dict"
    caps = result.get('capabilities')
    assert isinstance(caps, dict) and caps, "capabilities 应为非空 dict"
    assert 'textDocumentSync' in caps, "M4 桩至少声明 textDocumentSync"
    server_info = result.get('serverInfo')
    assert isinstance(server_info, dict)
    assert server_info.get('name') == 'jikuai-lsp'


def test_did_open_publishes_diagnostics(lsp_process):
    """T-M4-L03-02：didOpen 合法 .jk 源码 → 收到 publishDiagnostics，uri 匹配。"""
    resp = _initialize(lsp_process)
    assert resp is not None
    _initialized(lsp_process)

    uri = "file:///tmp/hello.jk"
    text = '\u6253\u5370 "\u4f60\u597d"\u3002\n'  # 打印 "你好"。

    _write_frame(lsp_process.stdin, {
        "jsonrpc": "2.0",
        "method": "textDocument/didOpen",
        "params": {
            "textDocument": {
                "uri": uri,
                "languageId": "jikuai",
                "version": 1,
                "text": text,
            }
        },
    })

    notif = _read_until(
        lsp_process.stdout,
        lambda m: m.get('method') == 'textDocument/publishDiagnostics',
        max_msgs=10,
    )
    assert notif is not None, "未收到 publishDiagnostics 通知"
    params = notif.get('params', {})
    assert params.get('uri') == uri, f"uri 不匹配：{params.get('uri')!r}"
    diagnostics = params.get('diagnostics')
    assert isinstance(diagnostics, list), "diagnostics 应为数组"
    # 合法源码 → 应为空数组
    assert diagnostics == [], f"合法源码不应产生诊断，收到 {diagnostics!r}"


def test_did_open_syntax_error_projects_real_diagnostic(lsp_process):
    """T-M4-L03-可选加分：语法错误源码 → 诊断非空且 code 以 JK-E 开头。

    真实诊断投影：ParseError.info → diagnostics.adapters.from_error_info
    → to_lsp_diagnostic。这是 F1「双消费者实证」从「空数组桩」跃迁到
    「真实诊断」的关键证据。
    """
    resp = _initialize(lsp_process)
    assert resp is not None
    _initialized(lsp_process)

    uri = "file:///tmp/bad.jk"
    # 语法错误：定义 后期望标识符，此处直接换行 → ParseError
    text = "\u5b9a\u4e49\n"  # 定义\n

    _write_frame(lsp_process.stdin, {
        "jsonrpc": "2.0",
        "method": "textDocument/didOpen",
        "params": {
            "textDocument": {
                "uri": uri,
                "languageId": "jikuai",
                "version": 1,
                "text": text,
            }
        },
    })

    notif = _read_until(
        lsp_process.stdout,
        lambda m: m.get('method') == 'textDocument/publishDiagnostics',
        max_msgs=10,
    )
    assert notif is not None, "未收到 publishDiagnostics"
    params = notif.get('params', {})
    assert params.get('uri') == uri
    diagnostics = params.get('diagnostics')
    assert isinstance(diagnostics, list) and diagnostics, \
        f"语法错误应产生诊断，收到 {diagnostics!r}"
    d0 = diagnostics[0]
    assert isinstance(d0.get('code'), str) and d0['code'].startswith('JK-E'), \
        f"诊断码应以 JK-E 开头，收到 {d0.get('code')!r}"
    assert d0.get('source') == 'jikuai'
    rng = d0.get('range')
    assert isinstance(rng, dict) and 'start' in rng and 'end' in rng
    # LSP 坐标应为 0-based
    assert rng['start']['line'] >= 0
    assert rng['start']['character'] >= 0


def test_did_change_publishes_diagnostics(lsp_process):
    """T-M4-L03-03：didChange 后重新推送诊断，uri 与新内容一致。"""
    resp = _initialize(lsp_process)
    assert resp is not None
    _initialized(lsp_process)

    uri = "file:///tmp/change.jk"
    _write_frame(lsp_process.stdin, {
        "jsonrpc": "2.0",
        "method": "textDocument/didOpen",
        "params": {
            "textDocument": {
                "uri": uri, "languageId": "jikuai",
                "version": 1, "text": "\u6253\u5370 1\u3002\n",  # 打印 1。
            }
        },
    })
    # 消费 didOpen 触发的一次推送
    first = _read_until(
        lsp_process.stdout,
        lambda m: m.get('method') == 'textDocument/publishDiagnostics',
        max_msgs=10,
    )
    assert first is not None

    # didChange 用 Full sync
    _write_frame(lsp_process.stdin, {
        "jsonrpc": "2.0",
        "method": "textDocument/didChange",
        "params": {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": "\u5b9a\u4e49\n"}],  # 定义\n → 语法错误
        },
    })
    second = _read_until(
        lsp_process.stdout,
        lambda m: m.get('method') == 'textDocument/publishDiagnostics',
        max_msgs=10,
    )
    assert second is not None
    assert second['params']['uri'] == uri
    # 改成有语法错误的源码，诊断应非空
    assert second['params']['diagnostics'], \
        "didChange 后语法错误内容应触发诊断"


def test_shutdown_and_exit_returncode_zero(lsp_process):
    """T-M4-L03-04：shutdown + exit 能正常退出，returncode 为 0。"""
    resp = _initialize(lsp_process)
    assert resp is not None
    _initialized(lsp_process)

    # shutdown 请求
    _write_frame(lsp_process.stdin, {
        "jsonrpc": "2.0", "id": 99, "method": "shutdown", "params": None,
    })
    shutdown_resp = _read_until(
        lsp_process.stdout,
        lambda m: m.get('id') == 99,
        max_msgs=10,
    )
    assert shutdown_resp is not None
    # shutdown 的 result 允许为 null
    assert 'result' in shutdown_resp

    # exit 通知
    _write_frame(lsp_process.stdin, {
        "jsonrpc": "2.0", "method": "exit",
    })
    try:
        rc = lsp_process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        lsp_process.kill()
        pytest.fail("exit 后进程未在 5 秒内退出")
    assert rc == 0, f"exit 后 returncode 应为 0，实际 {rc}"


# ---------------------------------------------------------------------------
# 物理隔离守护（ADR-15）
# ---------------------------------------------------------------------------

def test_physical_isolation_import_jikuai_alone():
    """import jikuai 时 sys.modules 不应出现 jikuai_lsp 或 pygls。

    通过子进程验证：单独 `import jikuai` 后检查模块表。
    """
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join([_SRC, _LSP, env.get('PYTHONPATH', '')])
    result = subprocess.run(
        [
            sys.executable, '-c',
            "import jikuai, sys; "
            "leaks = [m for m in sys.modules if 'pygls' in m or 'jikuai_lsp' in m]; "
            "print('LEAKS=' + ','.join(leaks))"
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=15.0,
    )
    assert result.returncode == 0, result.stderr
    assert "LEAKS=" in result.stdout
    leaks_line = [ln for ln in result.stdout.splitlines() if ln.startswith("LEAKS=")][0]
    leaks = leaks_line[len("LEAKS="):].strip()
    assert leaks == "", f"物理隔离被打破，泄漏模块：{leaks!r}"
