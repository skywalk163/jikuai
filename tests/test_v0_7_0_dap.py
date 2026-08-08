# -*- coding: utf-8 -*-
'''极快 v0.7.0 · M6-P3 · T-M6-D05 · DAP 协议级测试（AC-M6-05-01..04）。

以 subprocess 启动 `python -m jikuai_dap`，用真实 DAP 帧（Content-Length 头 +
JSON 体）交互，覆盖 ADR-20 定义的 MVP 能力边界。

抗挂死设计：
    - 读取放在独立线程 + Queue，所有等待都有 timeout（默认 5s）
    - 超时/流关闭时抛断言错误并打印已收到的全部消息，绝不无限等待
    - 依赖不可用（dap 包缺失等）→ pytest.skip，不 fail
'''

import json
import os
import queue
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC = os.path.join(ROOT, 'src')
DAP = os.path.join(ROOT, 'dap')

sys.path.insert(0, SRC)
sys.path.insert(0, DAP)

# dap 包不可用时整文件 skip（不许 fail）
jikuai_dap = pytest.importorskip('jikuai_dap', reason='未安装/未找到 jikuai_dap 包')

TIMEOUT = 5.0

# 已知的被调试程序（行号即断点行，务必与断言保持一致）
PROGRAM = (
    '定义王甲=100。\n'          # 1
    '定义王乙=￥50.25。\n'       # 2
    '定义王丙=加 王甲 5。\n'     # 3
    '打印 王丙。\n'              # 4
)


# ─────────────────────── DAP 测试客户端 ───────────────────────

class DapClient:
    '''最小 DAP 客户端：帧编解码 + 带超时的响应/事件等待。'''

    def __init__(self, proc):
        self.proc = proc
        self._seq = 0
        self._q = queue.Queue()
        self._received = []      # 全部收到的消息（超时诊断用）
        self._buffer = []        # 已收到但尚未被消费的消息
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stderr_lines = []
        self._err_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._err_reader.start()

    # ─── 底层帧 ───

    def _read_loop(self):
        stream = self.proc.stdout
        try:
            while True:
                headers = {}
                while True:
                    line = stream.readline()
                    if not line:
                        self._q.put(None)
                        return
                    decoded = line.decode('ascii', errors='replace')
                    if decoded in ('\r\n', '\n', '\r'):
                        break
                    if ':' in decoded:
                        k, v = decoded.split(':', 1)
                        headers[k.strip().lower()] = v.strip()
                length = int(headers.get('content-length', '0') or '0')
                if length <= 0:
                    self._q.put(None)
                    return
                chunks, remaining = [], length
                while remaining > 0:
                    chunk = stream.read(remaining)
                    if not chunk:
                        self._q.put(None)
                        return
                    chunks.append(chunk)
                    remaining -= len(chunk)
                self._q.put(json.loads(b''.join(chunks).decode('utf-8')))
        except Exception:                      # noqa: BLE001
            self._q.put(None)

    def _read_stderr(self):
        try:
            for line in self.proc.stderr:
                self._stderr_lines.append(line.decode('utf-8', errors='replace'))
        except Exception:                      # noqa: BLE001
            pass

    def _dump(self):
        msgs = json.dumps(self._received, ensure_ascii=False, indent=1)
        err = ''.join(self._stderr_lines[-40:])
        return f'\n--- 已收到消息 ---\n{msgs}\n--- 子进程 stderr ---\n{err}'

    def send(self, command, arguments=None):
        self._seq += 1
        msg = {'seq': self._seq, 'type': 'request', 'command': command}
        if arguments is not None:
            msg['arguments'] = arguments
        data = json.dumps(msg, ensure_ascii=False).encode('utf-8')
        self.proc.stdin.write(f'Content-Length: {len(data)}\r\n\r\n'.encode('ascii'))
        self.proc.stdin.write(data)
        self.proc.stdin.flush()
        return self._seq

    # ─── 带超时的等待 ───

    def _wait(self, predicate, what, timeout=TIMEOUT):
        for i, m in enumerate(self._buffer):
            if predicate(m):
                return self._buffer.pop(i)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                m = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            if m is None:
                self._closed = True
                raise AssertionError(f'等待 {what} 时适配器输出流已关闭。{self._dump()}')
            self._received.append(m)
            if predicate(m):
                return m
            self._buffer.append(m)
        raise AssertionError(f'等待 {what} 超时（{timeout}s）。{self._dump()}')

    def response(self, seq, timeout=TIMEOUT):
        return self._wait(
            lambda m: m.get('type') == 'response' and m.get('request_seq') == seq,
            f'seq={seq} 的响应', timeout)

    def event(self, name, timeout=TIMEOUT):
        return self._wait(
            lambda m: m.get('type') == 'event' and m.get('event') == name,
            f'{name} 事件', timeout)

    def request(self, command, arguments=None, timeout=TIMEOUT):
        '''发请求并返回其响应。'''
        return self.response(self.send(command, arguments), timeout=timeout)


def _spawn():
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([SRC, DAP])
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        proc = subprocess.Popen(
            [sys.executable, '-m', 'jikuai_dap'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=ROOT)
    except OSError as e:
        pytest.skip(f'无法启动 DAP 子进程：{e}')
    return proc


def _shutdown(client):
    '''尽力关闭子进程，绝不阻塞测试。'''
    try:
        client.proc.stdin.close()
    except Exception:                          # noqa: BLE001
        pass
    try:
        client.proc.wait(timeout=TIMEOUT)
    except Exception:                          # noqa: BLE001
        client.proc.kill()


@pytest.fixture()
def program_path(tmp_path):
    path = tmp_path / 'known.jk'
    path.write_text(PROGRAM, encoding='utf-8')
    return str(path)


@pytest.fixture()
def client():
    proc = _spawn()
    c = DapClient(proc)
    yield c
    _shutdown(c)


def _init(client, program_path, breakpoints=None):
    '''走完 initialize → launch → setBreakpoints → configurationDone。'''
    resp = client.request('initialize', {
        'clientID': 'pytest', 'adapterID': 'jikuai',
        'linesStartAt1': True, 'columnsStartAt1': True,
    })
    assert resp['success'] is True, resp
    assert resp['body']['supportsConfigurationDoneRequest'] is True
    client.event('initialized')

    resp = client.request('launch', {'program': program_path, 'noDebug': False})
    assert resp['success'] is True, resp

    if breakpoints is not None:
        resp = client.request('setBreakpoints', {
            'source': {'path': program_path},
            'breakpoints': [{'line': n} for n in breakpoints],
        })
        assert resp['success'] is True, resp
        assert [b['line'] for b in resp['body']['breakpoints']] == list(breakpoints)

    resp = client.request('configurationDone')
    assert resp['success'] is True, resp


# ─────────────── AC-M6-05-01 断点命中 ───────────────

def test_AC_M6_05_01_断点处暂停(client, program_path):
    _init(client, program_path, breakpoints=[3])
    stopped = client.event('stopped')
    assert stopped['body']['reason'] == 'breakpoint'
    assert stopped['body']['line'] == 3, stopped
    assert stopped['body']['threadId'] == 1

    # stackTrace 的行号应与 stopped 一致（最小帧）
    resp = client.request('stackTrace', {'threadId': 1})
    assert resp['success'] is True
    frames = resp['body']['stackFrames']
    assert len(frames) == 1
    assert frames[0]['line'] == 3


def test_threads_返回单线程(client, program_path):
    _init(client, program_path, breakpoints=[3])
    client.event('stopped')
    resp = client.request('threads')
    assert resp['success'] is True
    assert resp['body']['threads'] == [{'id': 1, 'name': '主线程'}]


# ─────────────── AC-M6-05-02 单步 next ───────────────

def test_AC_M6_05_02_next前进到下一可暂停位置(client, program_path):
    _init(client, program_path, breakpoints=[3])
    first = client.event('stopped')
    assert first['body']['line'] == 3

    resp = client.request('next', {'threadId': 1})
    assert resp['success'] is True
    second = client.event('stopped')
    assert second['body']['reason'] == 'step'
    assert second['body']['line'] == 4, second


# ─────────────── AC-M6-05-03 变量值与执行点一致 ───────────────

def _variables(client):
    resp = client.request('stackTrace', {'threadId': 1})
    frame_id = resp['body']['stackFrames'][0]['id']
    resp = client.request('scopes', {'frameId': frame_id})
    assert resp['success'] is True
    scopes = resp['body']['scopes']
    assert len(scopes) >= 1
    ref = scopes[0]['variablesReference']
    assert ref != 0
    resp = client.request('variables', {'variablesReference': ref})
    assert resp['success'] is True
    return {v['name']: v['value'] for v in resp['body']['variables']}


def test_AC_M6_05_03_变量值与执行点一致(client, program_path):
    _init(client, program_path, breakpoints=[3])
    client.event('stopped')

    # 第 3 行执行前：王甲/王乙 已赋值，王丙 尚未出现
    at_line3 = _variables(client)
    assert at_line3['王甲'] == '100', at_line3
    assert at_line3['王乙'] == '￥50.25', at_line3
    assert '王丙' not in at_line3, at_line3

    # 单步到第 4 行：王丙 已算出 105
    client.request('next', {'threadId': 1})
    client.event('stopped')
    at_line4 = _variables(client)
    assert at_line4['王甲'] == '100', at_line4
    assert at_line4['王乙'] == '￥50.25', at_line4
    assert at_line4['王丙'] == '105', at_line4


def test_variables携带中文类型名(client, program_path):
    _init(client, program_path, breakpoints=[3])
    client.event('stopped')
    resp = client.request('stackTrace', {'threadId': 1})
    frame_id = resp['body']['stackFrames'][0]['id']
    ref = client.request('scopes', {'frameId': frame_id})['body']['scopes'][0]['variablesReference']
    variables = client.request('variables', {'variablesReference': ref})['body']['variables']
    by_name = {v['name']: v for v in variables}
    assert by_name['王甲']['type'] == '整数'
    assert by_name['王乙']['type'] == '人民币'


# ─────────────── AC-M6-05-04 范围外能力拒绝 ───────────────

def _assert_unsupported(resp):
    assert resp['success'] is False, resp
    msg = resp.get('message', '')
    assert '暂不支持' in msg, msg
    assert 'JK-E8001' in msg, msg


def test_AC_M6_05_04_evaluate返回暂不支持(client, program_path):
    _init(client, program_path, breakpoints=[3])
    client.event('stopped')
    _assert_unsupported(client.request('evaluate', {
        'expression': '王甲', 'frameId': 1, 'context': 'watch',
    }))


def test_AC_M6_05_04_条件断点返回暂不支持(client, program_path):
    resp = client.request('initialize', {'clientID': 'pytest', 'adapterID': 'jikuai'})
    assert resp['success'] is True
    client.event('initialized')
    client.request('launch', {'program': program_path})
    _assert_unsupported(client.request('setBreakpoints', {
        'source': {'path': program_path},
        'breakpoints': [{'line': 3, 'condition': '王甲 大于 10'}],
    }))


def test_AC_M6_05_04_setVariable返回暂不支持(client, program_path):
    _init(client, program_path, breakpoints=[3])
    client.event('stopped')
    _assert_unsupported(client.request('setVariable', {
        'variablesReference': 1000, 'name': '王甲', 'value': '1',
    }))


def test_AC_M6_05_04_未支持命令统一拒绝(client, program_path):
    resp = client.request('initialize', {'clientID': 'pytest', 'adapterID': 'jikuai'})
    assert resp['success'] is True
    client.event('initialized')
    for command, args in (
        ('setFunctionBreakpoints', {'breakpoints': [{'name': '王加一'}]}),
        ('setDataBreakpoints', {'breakpoints': []}),
        ('setExceptionBreakpoints', {'filters': ['all']}),
    ):
        _assert_unsupported(client.request(command, args))


def test_initialize声明的capabilities只含已实现能力(client, program_path):
    resp = client.request('initialize', {'clientID': 'pytest', 'adapterID': 'jikuai'})
    caps = resp['body']
    assert caps['supportsConfigurationDoneRequest'] is True
    for key in ('supportsEvaluateForHovers', 'supportsSetVariable',
                'supportsConditionalBreakpoints', 'supportsHitConditionalBreakpoints',
                'supportsFunctionBreakpoints', 'supportsDataBreakpoints',
                'supportsStepBack'):
        assert caps[key] is False, f'{key} 不应声明为 true'


# ─────────────── continue → terminated → 退出码 0 ───────────────

def test_continue跑完收到terminated且退出码为0(client, program_path):
    _init(client, program_path, breakpoints=[3])
    client.event('stopped')
    resp = client.request('continue', {'threadId': 1})
    assert resp['success'] is True

    # 程序输出经 output 事件回传，不污染协议帧
    output = client.event('output')
    assert output['body']['category'] in ('stdout', 'stderr')

    client.event('terminated')

    resp = client.request('disconnect', {'terminateDebuggee': True})
    assert resp['success'] is True
    client.proc.stdin.close()
    assert client.proc.wait(timeout=TIMEOUT) == 0


def test_打印输出走output事件不污染协议(client, program_path):
    _init(client, program_path, breakpoints=[4])
    client.event('stopped')
    client.request('continue', {'threadId': 1})
    output = client.event('output')
    assert '105' in output['body']['output']


def test_pause请求被接受(client, program_path):
    _init(client, program_path, breakpoints=[3])
    client.event('stopped')
    resp = client.request('pause', {'threadId': 1})
    assert resp['success'] is True


def test_stepIn与stepOut被接受(client, program_path):
    _init(client, program_path, breakpoints=[3])
    client.event('stopped')
    assert client.request('stepIn', {'threadId': 1})['success'] is True
    client.event('stopped')
    assert client.request('stepOut', {'threadId': 1})['success'] is True


# ─────────────── 物理隔离 ───────────────

def test_主包不import_jikuai_dap():
    env = dict(os.environ)
    env['PYTHONPATH'] = SRC          # 故意只给 src，不给 dap
    out = subprocess.run(
        [sys.executable, '-c',
         "import jikuai, sys; print([m for m in sys.modules if 'jikuai_dap' in m])"],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == '[]', out.stdout


def test_主包不依赖dap目录即可正常求值():
    env = dict(os.environ)
    env['PYTHONPATH'] = SRC
    code = (
        'import sys\n'
        'from jikuai.frontend import compile_source\n'
        'from jikuai.evaluator import Evaluator, ExecHook\n'
        'src = "定义王甲=加 1 2。"\n'
        'ev = Evaluator()\n'
        'ev.eval(compile_source(src, file="t").ast, source=src)\n'
        'print(ev.global_env.get("王甲"))\n'
        "print([m for m in sys.modules if 'jikuai_dap' in m])\n"
    )
    out = subprocess.run([sys.executable, '-c', code], capture_output=True,
                         text=True, env=env, cwd=ROOT, timeout=60,
                         encoding='utf-8')
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    assert lines[0] == '3'
    assert lines[1] == '[]'
