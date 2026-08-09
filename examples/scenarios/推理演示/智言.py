# -*- coding: utf-8 -*-
"""推理演示 · Python 侧 AI 桥接 · sidecar 模块

- 与同目录 `.env` 一起工作：REASONIX_API_KEY / REASONIX_API_BASE_URL /
  REASONIX_MODEL / REASONIX_MAX_TOKENS / REASONIX_TEMPERATURE
- 支持所有 OpenAI 兼容 Chat Completions 接口（OpenAI / DeepSeek / 通义 …）
- 只用 Python 标准库（urllib），不引入 requests 依赖
- 极快侧通过 ADR-23 的「同目录 sidecar」加载：`导入 蟒:智言。`
- 极快端通过 ASCII 函数名调用，避免中文标识符中夹带内建动词字（读取/取值 等）
  被词法器切断

安全边界：
- API_KEY 只在进程内内存中留存，不打印，不落盘
- 网络调用失败时返回空串，让极快侧回退到模拟内容——demo 永远能跑
- REASONIX_OFFLINE=1 环境变量强制离线（用于场景快照测试的确定性）
"""

import json
import os
import sys
import urllib.error
import urllib.request


def _read_env_file(path):
    """极简 .env 解析：KEY=VALUE，忽略空行与 `#` 注释，去掉外层引号。"""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            out[k] = v
    return out


# 模块加载时一次性读入。sidecar 与调用它的 .jk 位于同一目录。
_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = _read_env_file(os.path.join(_HERE, '.env'))


def _cfg():
    """把 .env + 默认值合成一份配置字典。"""
    return {
        'api_key': _ENV.get('REASONIX_API_KEY', '').strip(),
        'base_url': _ENV.get('REASONIX_API_BASE_URL',
                             'https://api.openai.com/v1').rstrip('/'),
        'model': _ENV.get('REASONIX_MODEL', 'gpt-4o-mini').strip(),
        'max_tokens': int(_ENV.get('REASONIX_MAX_TOKENS', '2000') or '2000'),
        'temperature': float(_ENV.get('REASONIX_TEMPERATURE', '0.7') or '0.7'),
    }


def available():
    """AI 是否可用：非离线模式 + 有 API_KEY。极快侧靠这个决定分支。"""
    if os.environ.get('REASONIX_OFFLINE') == '1':
        return False
    return bool(_cfg()['api_key'])


def model_name():
    """返回当前模型名，供极快侧打印用。"""
    return _cfg()['model']


def _endpoint(base_url):
    """OpenAI 兼容接口的 chat completions 端点补齐。

    - 已含 `/chat/completions` → 直接用
    - 已含 `/v1` → 拼 `/chat/completions`
    - 都没有 → 补 `/v1/chat/completions`
    """
    b = base_url.rstrip('/')
    if b.endswith('/chat/completions'):
        return b
    if '/v1' in b or b.endswith('/v1'):
        return b + '/chat/completions'
    return b + '/v1/chat/completions'


def chat(system, user):
    """向 OpenAI 兼容接口发一次 Chat Completions。

    成功返回助手回复字符串；网络/鉴权/解析任何一环失败都吞掉并返回空串，
    让极快侧走 `如果 结果 不等于 ""` 的回退分支——保证 demo 永远能跑完。
    """
    cfg = _cfg()
    if not cfg['api_key']:
        return ''
    url = _endpoint(cfg['base_url'])
    body = json.dumps({
        'model': cfg['model'],
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        'max_tokens': cfg['max_tokens'],
        'temperature': cfg['temperature'],
    }).encode('utf-8')
    req = urllib.request.Request(
        url, data=body, method='POST',
        headers={
            'Authorization': 'Bearer ' + cfg['api_key'],
            'Content-Type': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return (data.get('choices', [{}])[0]
                    .get('message', {})
                    .get('content', '') or '').strip()
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, ValueError, KeyError, IndexError):
        return ''


def user_questions():
    """采集用户自定义问题清单。空列表表示"用 main.jk 里的默认问题"。

    来源（按优先级）：
    1. 环境变量 REASONIX_QUESTIONS：多个问题用 `|` 或换行分隔
    2. 命令行位置参数：`python -m jikuai main.jk 我的问题1 我的问题2`
       —— jikuai CLI 只消费 argv[1]（脚本路径），其余透传给 sys.argv 供本函数读取

    去掉空白与空条目，不做任何长度/内容校验（大模型侧自己会拒答）。
    """
    raw = os.environ.get('REASONIX_QUESTIONS', '').strip()
    if raw:
        parts = raw.replace('\n', '|').split('|')
        return [p.strip() for p in parts if p.strip()]

    # argv 兜底：跳到 .jk 文件之后的所有位置参数
    argv = sys.argv[:]
    seen_jk = False
    tail = []
    for a in argv[1:]:
        if seen_jk:
            tail.append(a)
        elif a.endswith('.jk'):
            seen_jk = True
    return [t for t in (s.strip() for s in tail) if t]
