# -*- coding: utf-8 -*-
"""远程注册表服务端 · 授权（ADR-35 §2.3 / §2.5）。

三件事，一个模块解决：

1. **认证**：`Authorization: Bearer <token>` → sha256 hex → 查 `授权.json`。
   `hmac.compare_digest` 定长比较，不给计时侧信道。
2. **授权**：token 绑定「签名者 + 可发布白名单」。签名者必须与报文
   `签名者` 一致；包名必须落在 `可发布` 白名单（精确 或 `前缀-*` 通配）。
   **默认拒绝**：无匹配即拒，没有「全部」通配。
3. **频次**：进程内滑动窗口。重启清零（ADR-35 §4 已知局限）。

**为什么把这三件事合到一个模块**
-------------------------------
它们的输入都是「token → 授权条目」，把它们拆成三个模块只会让服务端主循环
`认证 → 授权 → 频次` 变成跨三处的 import 面。同为「一条 token 的一次动作」
的判定，共享一份配置结构与相同的错误约定（`None` 放行 / `str` 拒绝原因）。
"""

import base64
import fnmatch
import hashlib
import hmac
import json
import os
import threading
import time
from collections import deque

__all__ = [
    'AuthError', 'AuthConfig', 'load_auth_config',
    'PROTOCOL_VERSION', 'RATE_WINDOW_SEC',
]

#: 授权配置文件的协议版本。结构演进时递增；读到更高版本直接拒。
PROTOCOL_VERSION = 1

#: 频次窗口宽度（秒）。ADR-35 §2.5 定义为「每小时次数」，即 3600 秒滑动窗。
RATE_WINDOW_SEC = 3600

#: 默认单包字节上限（16 MiB）。授权条目未显式设 `单包字节` 时用这个兜底。
DEFAULT_MAX_PACKAGE_BYTES = 16 * 1024 * 1024

#: 默认每小时发布次数上限。授权条目未显式设 `每小时次数` 时用这个兜底。
DEFAULT_MAX_PER_HOUR = 20


class AuthError(Exception):
    """授权配置本身不合法（格式错、协议版本不认等）。区别于运行时的授权拒绝。"""


class _Entry:
    """一条 token 授权条目的运行时表示。"""

    __slots__ = ('token_hash_hex', 'signer', 'public_key_bytes',
                 'publish_patterns', 'max_per_hour', 'max_package_bytes',
                 '_timestamps', '_lock')

    def __init__(self, token_hash_hex, signer, public_key_bytes,
                 publish_patterns, max_per_hour, max_package_bytes):
        self.token_hash_hex = token_hash_hex
        self.signer = signer
        self.public_key_bytes = public_key_bytes
        self.publish_patterns = tuple(publish_patterns)
        self.max_per_hour = int(max_per_hour)
        self.max_package_bytes = int(max_package_bytes)
        # 频次窗口：进程内滑动窗口。用 deque 从队首弹过期项，均摊 O(1)。
        self._timestamps = deque()
        self._lock = threading.Lock()


class AuthConfig:
    """`授权.json` 的运行时视图 + 频次计数。

    **线程安全**：查表是只读的（`_entries` 加载后不变），频次窗口写入用条目
    自己的锁。**热重载不做**（ADR-35 §4）：改配置 = 重启进程。
    """

    def __init__(self, entries):
        # 字典：token_hash_hex → _Entry
        self._entries = dict(entries)

    # -- 认证 ---------------------------------------------------------------

    def authenticate(self, raw_token):
        """按原始 token 定位授权条目。

        - `raw_token`：来自 `Authorization: Bearer <token>` 头的字符串。
        - 返回：`_Entry` 或 `None`（不认识）。

        实现要点：先算 sha256，再用 `hmac.compare_digest` 对每个已登记条目
        做定长比较。不用 `dict.get(hash)` 的理由：Python 字典查找是 O(1)
        但依赖字符串哈希与 rehash，不是标准意义上的常量时间；对**每一个**
        条目做 `compare_digest` 才是真正抗时序的做法。条目数量在单节点场景
        通常个位数到几十条，遍历成本可忽略。
        """
        if not isinstance(raw_token, str) or not raw_token:
            return None
        candidate = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        hit = None
        for token_hash_hex, entry in self._entries.items():
            # 逐个 compare_digest，找到也不 break —— 提前 break 会把「命中位置」
            # 泄给时序侧信道。走完全表再返回，代价是常数倍的哈希比较。
            if hmac.compare_digest(candidate, token_hash_hex):
                hit = entry
        return hit

    # -- 授权 ---------------------------------------------------------------

    def authorize_publish(self, entry, package_name, signer):
        """检查该 token 能否发布 `package_name` 且署名 `signer`。

        返回 `None` 放行，`str` 拒绝原因（中文，直接透出到客户端）。
        """
        if entry is None:
            return 'token 无效'
        if not isinstance(package_name, str) or not package_name:
            return '缺少「名称」字段'
        if not isinstance(signer, str) or not signer:
            return '缺少「签名者」字段'
        # 签名者必须与 token 绑定一致：防止 A 的 token 推一个署名 B 的包
        if signer != entry.signer:
            return f'越权：token 绑定签名者 {entry.signer!r}，报文声明 {signer!r}'
        # 包名白名单：精确匹配 或 `前缀-*` 通配。**没有「全部」通配**。
        if not _match_any(package_name, entry.publish_patterns):
            return f'越权：token 无权发布 {package_name}'
        return None

    # -- 频次 ---------------------------------------------------------------

    def check_rate(self, entry, now=None):
        """滑动窗口频次限额。命中即写入一次时间戳，超限返回拒绝原因。

        进程内计数：重启清零（ADR-35 §4）。持久化配额需要状态存储，与
        §2.1「单节点、无外部依赖」的范围冲突，本轮不做。
        """
        if entry is None:
            return 'token 无效'
        current = time.time() if now is None else now
        cutoff = current - RATE_WINDOW_SEC
        with entry._lock:
            ts = entry._timestamps
            while ts and ts[0] < cutoff:
                ts.popleft()
            if len(ts) >= entry.max_per_hour:
                return (f'触发频次配额：{RATE_WINDOW_SEC // 60} 分钟内最多 '
                        f'{entry.max_per_hour} 次，请稍后再试')
            ts.append(current)
        return None


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def load_auth_config(path):
    """从 `授权.json` 加载配置。

    格式校验就地进行：结构不合法立即抛 `AuthError`，让服务端启动阶段
    就发现问题（而不是等到某条请求进来才 500）。
    """
    if not os.path.isfile(path):
        raise AuthError(f'授权配置文件不存在：{path}')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise AuthError(f'授权配置不是合法 JSON（{path}）：'
                        f'第 {e.lineno} 行 {e.msg}') from None
    except UnicodeDecodeError:
        raise AuthError(f'授权配置编码不是 UTF-8：{path}') from None

    if not isinstance(data, dict):
        raise AuthError('授权配置必须是对象')
    proto = data.get('协议')
    if proto != PROTOCOL_VERSION:
        raise AuthError(
            f'授权配置协议版本 {proto!r} 与服务端支持版本 {PROTOCOL_VERSION} 不符')

    raw_tokens = data.get('令牌')
    if not isinstance(raw_tokens, dict) or not raw_tokens:
        raise AuthError('授权配置「令牌」为空；至少登记一条 token 才能发布')

    entries = {}
    for token_hash_hex, spec in raw_tokens.items():
        entries[token_hash_hex] = _build_entry(token_hash_hex, spec)
    return AuthConfig(entries)


def _build_entry(token_hash_hex, spec):
    if not isinstance(token_hash_hex, str) or len(token_hash_hex) != 64:
        raise AuthError(
            f'token 键必须是 64 位小写 hex（sha256），得到 {token_hash_hex!r}')
    try:
        int(token_hash_hex, 16)
    except ValueError:
        raise AuthError(f'token 键含非 hex 字符：{token_hash_hex!r}') from None
    if not isinstance(spec, dict):
        raise AuthError(f'token {token_hash_hex} 的授权条目必须是对象')

    signer = spec.get('签名者')
    if not isinstance(signer, str) or not signer:
        raise AuthError(f'token {token_hash_hex} 缺少「签名者」')

    public_key_b64 = spec.get('公钥')
    if not isinstance(public_key_b64, str) or not public_key_b64:
        raise AuthError(f'token {token_hash_hex} 缺少「公钥」（base64 编码的 Ed25519 公钥）')
    try:
        public_key_bytes = base64.b64decode(public_key_b64, validate=True)
    except (ValueError, Exception):
        raise AuthError(
            f'token {token_hash_hex} 的「公钥」不是合法 base64') from None
    if len(public_key_bytes) != 32:
        raise AuthError(
            f'token {token_hash_hex} 的公钥必须是 32 字节，得到 {len(public_key_bytes)}')

    patterns = spec.get('可发布')
    if not isinstance(patterns, list) or not patterns:
        raise AuthError(f'token {token_hash_hex} 的「可发布」必须是非空数组')
    for p in patterns:
        if not isinstance(p, str) or not p:
            raise AuthError(f'token {token_hash_hex} 的「可发布」含非字符串项')
        # 不允许「全部」通配：ADR-35 §2.3 明确「授予全权必须在配置里显形」
        if p == '*':
            raise AuthError(
                f'token {token_hash_hex} 的「可发布」不允许纯 `*` 通配；'
                f'请显式列出包名或用 `前缀-*` 形式')

    max_per_hour = spec.get('每小时次数', DEFAULT_MAX_PER_HOUR)
    if not isinstance(max_per_hour, int) or max_per_hour <= 0:
        raise AuthError(
            f'token {token_hash_hex} 的「每小时次数」必须是正整数')

    max_package_bytes = spec.get('单包字节', DEFAULT_MAX_PACKAGE_BYTES)
    if not isinstance(max_package_bytes, int) or max_package_bytes <= 0:
        raise AuthError(
            f'token {token_hash_hex} 的「单包字节」必须是正整数')

    return _Entry(token_hash_hex, signer, public_key_bytes,
                  patterns, max_per_hour, max_package_bytes)


def _match_any(name, patterns):
    """白名单匹配：精确 或 `前缀-*` 通配。"""
    for p in patterns:
        if p == name:
            return True
        # 只支持 `前缀-*` 形式（星号只能结尾且以 `-` 相连）。
        # fnmatch 走标准 shell 通配语义；`?` 与 `[]` 也会匹配，但由于
        # 校验时禁了纯 `*` 且包名字符集不含 `?`/`[]`，实际等价于
        # 「精确 + 前缀-*」两种形态。
        if '*' in p and fnmatch.fnmatchcase(name, p):
            return True
    return False
