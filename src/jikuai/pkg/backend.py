# -*- coding: utf-8 -*-
"""极快包管理 - 注册表后端抽象（v0.20.0 M20 / ADR-34 §2.2）。

为什么要这一层
--------------
`registry.py` 从 v0.11.0 起每个函数都直接 `os.path.join(root, ...)` + `open()`。
要让注册表根能是 `https://...`，最省事的写法是在每个函数里
`if root.startswith('http')` 分叉——ADR-34 §6 明确**拒**了这条路：逻辑会散
落到十来个函数里，测不动也必漏。

改为：注册表内的每个文件都用**相对路径**（`'索引.json'`、
`'分类/通用.json'`、`'密钥/甲.公钥'`）表达，由后端负责把相对路径解析成
本地绝对路径或远程 URL。上层只管「读这个相对路径」。

两个实现
--------
- `LocalBackend`：包住原来的 `os.path.join` + `open`，行为与 v0.19.0 逐字节
  一致（回归必须全绿）。路径逃逸防护 `_ensure_within` 收敛在这里。
- `HttpBackend`：`urllib.request` 实现只读；写操作抛 `UnsupportedOperation`
  —— 本轮发布端不走 HTTP（ADR-34 §2.1），但协议签名先预留（§2.6），
  M21 做远程发布时只填实现，不动上层调用面。

零依赖底线
----------
HTTP 客户端**只用标准库 `urllib.request`**，不引 `requests` / `httpx`
（v0.16.0 起的硬约束）。所有请求带显式超时，绝不无限等待。
"""

import base64
import json
import os
import posixpath
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

__all__ = [
    'BackendError', 'UnsupportedOperation',
    'TOKEN_ENV', 'TIMEOUT_ENV', 'INSECURE_ENV', 'CREDENTIALS_NAME',
    'DEFAULT_TIMEOUT',
    'RegistryBackend', 'LocalBackend', 'HttpBackend',
    'is_remote', 'get_backend', 'resolve_token',
]

#: 私有注册表的 Bearer token（CI 场景优先用环境变量）。
TOKEN_ENV = 'JIKUAI_REGISTRY_TOKEN'
#: 单次 HTTP 请求超时秒数，覆盖 `DEFAULT_TIMEOUT`。
TIMEOUT_ENV = 'JIKUAI_REGISTRY_TIMEOUT'
#: 置 `1` 才放行明文 `http://` 注册表（内网/测试用），默认拒。
INSECURE_ENV = 'JIKUAI_REGISTRY_INSECURE'
#: 本机凭证文件名，落在 `~/.jikuai/` 下。`{URL 前缀: token}`。
CREDENTIALS_NAME = '凭证.json'
#: 默认请求超时（秒）。
DEFAULT_TIMEOUT = 30.0
#: 单次 HTTP 响应体大小上限（v0.21.0 W86 安全审计）。防止恶意注册表推送超大
#: 响应让客户端 OOM。与 sources._MAX_TOTAL_BYTES 对齐（归档解压后上限 256 MiB，
#: 压缩前一般远小于此），这里卡 512 MiB 给足余量——若归档本身大于此上限，
#: 说明里面的内容解压后必然超出 sources 的体量限制，不值得继续下载。
#: 索引/公钥/分类 JSON 文件远小于此，不需要单独设一条更小的限。
_MAX_RESPONSE_BYTES = 512 * 1024 * 1024
_RESPONSE_BYTES_ENV = 'JIKUAI_REGISTRY_MAX_RESPONSE'


class BackendError(Exception):
    """后端读写失败：文件缺失、网络不可达、鉴权失败、响应损坏等。

    `registry.py` 会把它转成 `RegistryError` 再抛给 CLI —— `urllib` 的
    `HTTPError`/`URLError` 绝不泄漏到用户面前（ADR-34 §2.3）。
    """


class UnsupportedOperation(BackendError):
    """该后端不支持这个操作（如远程注册表的写入）。"""


# ---- 相对路径规范化 ---------------------------------------------------

def _normalize_rel(rel: str) -> str:
    """把注册表内相对路径规范成 `a/b/c` 形式，顺手拦掉逃逸段。

    统一用 POSIX 分隔符：注册表布局在磁盘上和 URL 上是同一套相对路径，
    分隔符必须只有一种，否则 `分类\\通用.json` 和 `分类/通用.json` 会在
    远程侧变成两个不同的 URL。
    """
    if not isinstance(rel, str) or not rel:
        raise BackendError('注册表内相对路径不能为空')
    unified = rel.replace('\\', '/')
    if unified.startswith('/'):
        raise BackendError(f'注册表内相对路径不能以 / 开头：{rel!r}')
    parts = [p for p in unified.split('/') if p not in ('', '.')]
    if any(p == '..' for p in parts):
        raise BackendError(f'注册表内相对路径不允许 .. 段：{rel!r}')
    if not parts:
        raise BackendError(f'注册表内相对路径为空：{rel!r}')
    return '/'.join(parts)


# ---- 协议 -------------------------------------------------------------

class RegistryBackend:
    """注册表存储后端。子类实现读；写只有本地实现。

    `locator` 是注册表定位符：本地是绝对路径，远程是 `https://...`。
    所有 `rel` 参数都是注册表内**相对路径**（POSIX 分隔符）。
    """

    __slots__ = ('locator',)

    #: 远程后端为真。上层据此决定快照走目录还是走 tar.gz 下载。
    remote = False

    def __init__(self, locator: str):
        self.locator = locator

    # -- 读（两个后端都实现） --
    def read_text(self, rel: str) -> Optional[str]:
        """读文本；不存在返回 `None`（不抛）。其他失败抛 `BackendError`。"""
        raise NotImplementedError

    def read_bytes(self, rel: str) -> Optional[bytes]:
        """读二进制；不存在返回 `None`。"""
        raise NotImplementedError

    def exists(self, rel: str) -> bool:
        raise NotImplementedError

    def read_json(self, rel: str) -> Optional[dict]:
        """读 JSON；不存在返回 `None`，不是合法 JSON 抛 `BackendError`。"""
        text = self.read_text(rel)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise BackendError(
                f'{self.describe(rel)} 不是合法 JSON：第 {e.lineno} 行 {e.msg}'
            ) from None

    # -- 写（ADR-34 §2.6 预留：远程实现留 M21） --
    def write_text(self, rel: str, text: str) -> None:
        raise UnsupportedOperation(f'{type(self).__name__} 不支持写入')

    def write_bytes(self, rel: str, data: bytes) -> None:
        raise UnsupportedOperation(f'{type(self).__name__} 不支持写入')

    def remove(self, rel: str) -> None:
        raise UnsupportedOperation(f'{type(self).__name__} 不支持删除')

    # -- 诊断 --
    def describe(self, rel: str = '') -> str:
        """给错误信息用的人类可读定位串。"""
        return self.locator if not rel else f'{self.locator}/{rel}'


class LocalBackend(RegistryBackend):
    """本地文件系统注册表。行为与 v0.19.0 的 `registry.py` 一致。"""

    __slots__ = ()
    remote = False

    def __init__(self, locator: str):
        super().__init__(os.path.abspath(locator))

    def path_of(self, rel: str) -> str:
        """相对路径 → 本地绝对路径。含逃逸防护（原 `_ensure_within`）。

        `_normalize_rel` 已拦 `..`，这里再核一遍最终路径确实落在根下——
        符号链接、Windows 短名（`FOO~1`）等能绕过纯字符串检查的情况只有
        `abspath` 之后比较才拦得住。双重保险，与 v0.19.0 同策略。
        """
        parts = _normalize_rel(rel).split('/')
        target = os.path.abspath(os.path.join(self.locator, *parts))
        try:
            common = os.path.commonpath([self.locator, target])
        except ValueError:
            raise BackendError(
                f'路径 {rel!r} 与注册表根 {self.locator!r} 不在同一根下') from None
        if common != self.locator:
            raise BackendError(f'路径 {rel!r} 越出注册表根 {self.locator!r}')
        return target

    def read_text(self, rel: str) -> Optional[str]:
        path = self.path_of(rel)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            raise BackendError(f'{path} 编码不是 UTF-8') from None

    def read_bytes(self, rel: str) -> Optional[bytes]:
        path = self.path_of(rel)
        if not os.path.isfile(path):
            return None
        with open(path, 'rb') as f:
            return f.read()

    def exists(self, rel: str) -> bool:
        return os.path.exists(self.path_of(rel))

    def write_text(self, rel: str, text: str) -> None:
        """原子写：先落 `.tmp` 再 `os.replace`。

        非原子写会在写一半崩溃时留下半个索引 JSON，之后所有 `装` 都读不了
        注册表——索引是全局单点，必须整体可见或整体不可见。
        """
        path = self.path_of(rel)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        os.replace(tmp, path)

    def write_bytes(self, rel: str, data: bytes) -> None:
        path = self.path_of(rel)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)

    def remove(self, rel: str) -> None:
        path = self.path_of(rel)
        if os.path.isfile(path):
            os.remove(path)

    def describe(self, rel: str = '') -> str:
        return self.locator if not rel else self.path_of(rel)


class HttpBackend(RegistryBackend):
    """远程 HTTP 注册表（只读）。服务端可以只是静态文件托管。

    远程注册表就是本地注册表目录的静态镜像：`GET <base>/索引.json`、
    `GET <base>/分类/通用.json`、`GET <base>/包/<名>/<版本>.tar.gz`。
    不需要专门的服务端进程（ADR-34 §2.1）。
    """

    __slots__ = ('_base', '_token', '_timeout', '_max_response')
    remote = True

    def __init__(self, locator: str, token: Optional[str] = None,
                 timeout: Optional[float] = None):
        scheme = urllib.parse.urlsplit(locator).scheme.lower()
        if scheme not in ('http', 'https'):
            raise BackendError(f'远程注册表定位符必须是 http(s)://：{locator!r}')
        if scheme == 'http' and os.environ.get(INSECURE_ENV) != '1':
            raise BackendError(
                f'拒绝明文 http:// 注册表：{locator}。'
                f'包内容有签名背书，但索引与公钥首次拉取仍需 TLS 兜底；'
                f'内网/测试确需明文请设 {INSECURE_ENV}=1')
        if scheme == 'http':
            print(f'注意：正在使用明文 http:// 注册表 {locator}，'
                  f'索引与公钥可被中间人篡改（{INSECURE_ENV}=1 已放行）',
                  file=sys.stderr)
        super().__init__(locator.rstrip('/'))
        self._base = self.locator
        self._token = token if token is not None else resolve_token(self.locator)
        self._timeout = timeout if timeout is not None else _resolve_timeout()
        self._max_response = _resolve_max_response()

    def url_of(self, rel: str) -> str:
        """相对路径 → 完整 URL。每段做 percent 编码（中文路径必须）。"""
        parts = _normalize_rel(rel).split('/')
        quoted = '/'.join(urllib.parse.quote(p, safe='') for p in parts)
        return f'{self._base}/{quoted}'

    def _request(self, rel: str) -> Optional[bytes]:
        url = self.url_of(rel)
        req = urllib.request.Request(url, method='GET')
        req.add_header('User-Agent', 'jikuai-pkg')
        if self._token:
            # HTTP 头只能是 latin-1；token 含非 latin-1 字符会在 urllib 内部
            # 抛 UnicodeEncodeError，这里提前拦成可读的 BackendError。
            try:
                self._token.encode('latin-1')
            except UnicodeEncodeError:
                raise BackendError(
                    '注册表 token 含非 latin-1 字符，无法放进 HTTP 头；'
                    'token 通常是 ASCII 串，请检查配置') from None
            req.add_header('Authorization', f'Bearer {self._token}')
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                # W86：分块读取 + 上限检查，防止恶意服务端用超大响应耗尽内存。
                # 不信任 Content-Length（可伪造），按实际读入字节累计。
                chunks = []
                total = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self._max_response:
                        raise BackendError(
                            f'注册表响应体超过上限 {self._max_response} 字节，'
                            f'已中断下载：{url}（确需放宽设 {_RESPONSE_BYTES_ENV}）')
                    chunks.append(chunk)
                return b''.join(chunks)
        except urllib.error.HTTPError as e:
            # 404 → 「不存在」而非错误：上层 `read_text` 返回 None 的语义与
            # 本地 `os.path.isfile` 为假一致，索引缺失等分支不必分叉。
            if e.code == 404:
                return None
            if e.code in (401, 403):
                hint = ('已带 token 但仍被拒，检查 token 是否过期或权限不足'
                        if self._token else
                        f'未提供凭证；设环境变量 {TOKEN_ENV}，'
                        f'或在 ~/.jikuai/{CREDENTIALS_NAME} 里配置该注册表的 token')
                raise BackendError(f'注册表鉴权失败（HTTP {e.code}）：{url}。{hint}') from None
            raise BackendError(f'注册表返回 HTTP {e.code}：{url}') from None
        except urllib.error.URLError as e:
            raise BackendError(f'注册表不可达：{url}（{e.reason}）') from None
        except OSError as e:
            # socket.timeout 在 3.10 是 OSError 子类，统一收口
            raise BackendError(f'注册表读取失败：{url}（{e}）') from None

    def read_text(self, rel: str) -> Optional[str]:
        raw = self._request(rel)
        if raw is None:
            return None
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise BackendError(f'{self.url_of(rel)} 编码不是 UTF-8') from None

    def read_bytes(self, rel: str) -> Optional[bytes]:
        return self._request(rel)

    def exists(self, rel: str) -> bool:
        # 静态托管未必支持 HEAD，直接 GET 判空最稳（索引/公钥都是小文件）。
        return self._request(rel) is not None

    def write_text(self, rel: str, text: str) -> None:
        raise UnsupportedOperation('远程注册表暂不支持发布，见 ADR-34 §2.6（M21）')

    def write_bytes(self, rel: str, data: bytes) -> None:
        raise UnsupportedOperation('远程注册表暂不支持发布，见 ADR-34 §2.6（M21）')

    def remove(self, rel: str) -> None:
        raise UnsupportedOperation('远程注册表暂不支持删除，见 ADR-34 §2.6（M21）')

    def describe(self, rel: str = '') -> str:
        return self._base if not rel else self.url_of(rel)


# ---- 定位符与凭证 -----------------------------------------------------

def is_remote(locator: str) -> bool:
    """定位符是否远程（`http://` / `https://`）。"""
    if not locator:
        return False
    return urllib.parse.urlsplit(locator).scheme.lower() in ('http', 'https')


def get_backend(locator: str) -> RegistryBackend:
    """按定位符选后端。远程给 `HttpBackend`，其余给 `LocalBackend`。"""
    return HttpBackend(locator) if is_remote(locator) else LocalBackend(locator)


def _resolve_timeout() -> float:
    raw = os.environ.get(TIMEOUT_ENV)
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        raise BackendError(f'{TIMEOUT_ENV} 不是数字：{raw!r}') from None
    if value <= 0:
        raise BackendError(f'{TIMEOUT_ENV} 必须为正数：{raw!r}')
    return value


def _resolve_max_response() -> int:
    """响应体上限（字节）。非正/非数字回落默认——安全网配错应退回更安全的默认。"""
    raw = os.environ.get(_RESPONSE_BYTES_ENV)
    if not raw:
        return _MAX_RESPONSE_BYTES
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _MAX_RESPONSE_BYTES
    return value if value > 0 else _MAX_RESPONSE_BYTES


def _credentials_path() -> str:
    return os.path.join(os.path.expanduser('~'), '.jikuai', CREDENTIALS_NAME)


def _load_credentials() -> Dict[str, str]:
    """读 `~/.jikuai/凭证.json`。缺失或损坏都返回空表（不阻断匿名读）。"""
    path = _credentials_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        print(f'注意：{path} 无法解析，已忽略；私有注册表将按匿名访问',
              file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def resolve_token(locator: str) -> str:
    """取该注册表的 Bearer token。环境变量优先于凭证文件（ADR-34 §2.7）。

    凭证文件按 **URL 前缀最长匹配**：一台机器可能同时配公共源和多个私有源，
    `{"https://reg.a.com": "...", "https://reg.a.com/内部": "..."}` 要能让
    更具体的前缀胜出。
    """
    env = os.environ.get(TOKEN_ENV)
    if env:
        return env
    base = locator.rstrip('/')
    best_prefix = ''
    best_token = ''
    for prefix, token in _load_credentials().items():
        norm = prefix.rstrip('/')
        if (base == norm or base.startswith(norm + '/')) and len(norm) > len(best_prefix):
            best_prefix, best_token = norm, token
    return best_token


def join_rel(*parts: str) -> str:
    """拼注册表内相对路径。统一走 POSIX 分隔符。"""
    return posixpath.join(*[p.replace('\\', '/').strip('/') for p in parts if p])


def encode_public_key(raw: bytes) -> str:
    """公钥字节 → 注册表里存的 base64 行（含末尾换行）。"""
    return base64.b64encode(raw).decode('ascii') + '\n'
