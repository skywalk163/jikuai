# -*- coding: utf-8 -*-
"""神经查询向量的 sidecar 调用胶水 —— ADR-25 §3.1 调用方责任链（v0.14.0 W11）。

`retrieval.retrieve(query, query_vector=...)` 把「查询文本怎么变成向量」这件事
甩给了调用方（运行时做推理就破了零依赖红线）。W11 之前这个责任落在用户手上：
得自己跑模型、把向量存成 JSON、再 `--向量 文件` 传进来。本模块把这一步自动化：
subprocess 拉一次 `tools/ai-bridge/embed_query.py`（或 `JIKUAI_AI_EMBED_CMD`
指定的任意命令），从它的 stdout 收一行 JSON 数组。

**硬约束（ADR-25 §2）**：本模块只 import 标准库。torch / sentence-transformers /
numpy 全部隔离在子进程另一侧，主发布包的依赖面积零变化。

**失败即降级，不是失败。** 所有失败模式（命令不存在、非零退出、stdout 不是
合法 JSON 数组、维度与索引不符、超时）都收敛成 `(None, 原因)` 返回值，由调用方
打一行 stderr 提示后走启发式路径。神经路径是「能用则用」的加分项，不是前置条件。
"""

import json
import os
import shlex
import subprocess
import sys
from typing import List, Optional, Sequence, Tuple

__all__ = [
    'ENV_CMD', 'ENV_NEURAL', 'DEFAULT_TIMEOUT', 'DEGRADE_PREFIX',
    'sidecar_script_path', 'resolve_command', 'neural_enabled_by_env',
    'fetch_query_vector', 'index_dim',
]

#: 指向 sidecar 命令行的环境变量。缺省时用
#: `sys.executable tools/ai-bridge/embed_query.py`。
ENV_CMD = 'JIKUAI_AI_EMBED_CMD'

#: REPL 侧的神经路径开关。**刻意不默认开** —— 冷启动一次模型约 10s，
#: 用户第一次跑 REPL 打个 `需求 求平均` 不该干等。
ENV_NEURAL = 'JIKUAI_AI_NEURAL'

#: subprocess 超时（秒）。模型冷启动实测 ~10s，留足余量；卡死也不会挂住调用方。
DEFAULT_TIMEOUT = 120.0

#: `ENV_NEURAL` 认作「开」的取值。
_TRUTHY = frozenset({'1', 'true', 'yes', 'on', '开', 'neural', '神经'})

#: 降级文案前缀。CLI / Web / REPL 三处都拼这句 + `fetch_query_vector` 的原因串，
#: 措辞必须一字不差（协议 `降级说明` 字段与测试都按这句断言），故收在这里。
DEGRADE_PREFIX = '神经检索不可用，降级到启发式：'


def sidecar_script_path() -> str:
    """内置 sidecar `tools/ai-bridge/embed_query.py` 的绝对路径。

    路径推法与 `retrieval.vector_index_path()` 一致（都从本文件回溯三级到仓库
    根）。pip 安装场景下 `tools/` 不随包发布，此文件不存在——那正是「降级到
    启发式」该覆盖的情况，不是错误。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(here, '..', '..', '..'))
    return os.path.join(repo_root, 'tools', 'ai-bridge', 'embed_query.py')


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        return token[1:-1]
    return token


def _split_command(raw: str) -> List[str]:
    """把环境变量里的命令行切成 argv。

    Windows 上刻意走 `posix=False` 再手工剥引号：`posix=True` 会把路径里的
    反斜杠当转义符吃掉（`C:\\Py\\python.exe` → `C:Pypython.exe`），而
    `posix=False` 又会把引号留在 token 里（`-c "import sys"` 的引号会被
    `list2cmdline` 二次转义，子进程收到的是带引号的字面量）。两步组合才对。
    """
    if os.name == 'nt':
        return [_strip_quotes(p) for p in shlex.split(raw, posix=False)]
    return shlex.split(raw)


def resolve_command() -> Optional[List[str]]:
    """决定实际执行的 sidecar argv。拿不到可用命令返回 None。

    优先读 `JIKUAI_AI_EMBED_CMD`（允许带参数，如
    `python -X utf8 my_embed.py --model xxx`）；未设置时回落到内置 sidecar，
    脚本不存在则返回 None。
    """
    raw = os.environ.get(ENV_CMD, '').strip()
    if raw:
        parts = _split_command(raw)
        return parts or None
    script = sidecar_script_path()
    if not os.path.isfile(script):
        return None
    return [sys.executable, script]


def neural_enabled_by_env() -> bool:
    """`JIKUAI_AI_NEURAL` 是否开着（REPL 用）。"""
    return os.environ.get(ENV_NEURAL, '').strip().lower() in _TRUTHY


def index_dim() -> Optional[int]:
    """当前向量索引的维度；无索引返回 None。

    拿来给 `fetch_query_vector` 做维度校验：模型换了但索引没重生成时，
    `retrieval._retrieve_neural` 会抛 `RetrievalError`；提前在这里比对能把它
    变成一次「降级 + 提示」，比让用户吃一个非零退出码友好。
    """
    from . import retrieval
    vi = retrieval.load_vector_index()
    return vi.dim if vi is not None else None


def fetch_query_vector(query: str,
                       expected_dim: Optional[int] = None,
                       timeout: float = DEFAULT_TIMEOUT
                       ) -> Tuple[Optional[Sequence[float]], Optional[str]]:
    """subprocess 拉一次查询向量。**任何情况都不抛异常。**

    Args:
        query: 自然语言需求（单行；内部会 strip 并补换行喂给 sidecar stdin）。
        expected_dim: 期望维度（通常是 `index_dim()`）。给了就校验。
        timeout: 子进程超时秒数。

    Returns:
        `(向量, None)` 成功；`(None, 原因)` 失败。`原因` 是一句可以直接打到
        stderr 的中文说明。
    """
    text = (query or '').strip()
    if not text:
        return None, '查询为空'

    cmd = resolve_command()
    if not cmd:
        return None, ('找不到 sidecar（%s 未设置，且内置 %s 不存在）'
                      % (ENV_CMD, os.path.basename(sidecar_script_path())))

    try:
        proc = subprocess.run(
            cmd,
            input=text + '\n',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, 'sidecar 超时（>%.0fs）：%s' % (timeout, cmd[0])
    except FileNotFoundError:
        return None, '找不到 sidecar 命令：%s' % cmd[0]
    except OSError as e:
        return None, '启动 sidecar 失败：%s' % e

    if proc.returncode != 0:
        detail = [ln for ln in (proc.stderr or '').strip().splitlines() if ln.strip()]
        tail = detail[-1] if detail else '无 stderr 输出'
        return None, 'sidecar 退出码 %d（%s）' % (proc.returncode, tail)

    payload = (proc.stdout or '').strip()
    if not payload:
        return None, 'sidecar 退出码 0 但 stdout 为空'

    # 契约是「一行一个 JSON 数组」。模型库常往 stdout 打进度条/告警，取最后
    # 一行是最稳的读法——真正的向量总是最后被打出来的。
    try:
        vec = json.loads(payload.splitlines()[-1])
    except ValueError as e:
        return None, 'sidecar 输出不是合法 JSON：%s' % e

    if not isinstance(vec, list) or not vec:
        return None, 'sidecar 输出不是非空 JSON 数组'
    try:
        vec = [float(x) for x in vec]
    except (TypeError, ValueError):
        return None, 'sidecar 输出的数组含非数值元素'

    if expected_dim is not None and len(vec) != expected_dim:
        return None, ('sidecar 向量维度 %d 与索引维度 %d 不符（模型不一致？）'
                      % (len(vec), expected_dim))
    return vec, None
