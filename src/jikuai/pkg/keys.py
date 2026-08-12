# -*- coding: utf-8 -*-
"""包签名密钥管理（ADR-33 §2.4）。

密钥根：`$JIKUAI_KEY_ROOT` > `~/.jikuai/密钥/`。

- 私钥：32 字节种子，base64 存 `<密钥根>/<别名>.私钥`
- 公钥：32 字节压缩点，base64 存 `<密钥根>/<别名>.公钥`

Ed25519 的私钥就是 32 字节随机种子（公钥可由种子推出），但存一份公钥
方便「导出给注册表管理员」和「别人给你公钥你存信任库」两个场景——
不用每次都从种子推导。

生成时两个文件同时写；加载时只需对应那一个文件。
"""

import base64
import os
import re
import secrets

from . import _ed25519 as ed

__all__ = [
    'KEY_ROOT_ENV', 'key_root', 'validate_alias', 'generate_keypair',
    'load_private_key', 'load_public_key', 'list_keys',
    'export_public_key_b64',
]

#: 环境变量覆盖密钥根目录
KEY_ROOT_ENV = 'JIKUAI_KEY_ROOT'

_PRIVATE_EXT = '.私钥'
_PUBLIC_EXT = '.公钥'

#: 别名字符白名单。别名会拼进文件名（密钥根下、注册表 `密钥/` 下），
#: 是唯一的路径注入面 —— 禁点、禁路径分隔符、禁空白。
#: 与包名白名单同形，但**不**查标准库重名：别名是人的身份标识，
#: 叫「数学」不该被拒。
_ALIAS_RE = re.compile(r'^[\w\u4e00-\u9fff-]{1,64}$', re.UNICODE)


def validate_alias(alias: str) -> str:
    """校验签名者别名。不合法抛 ValueError。

    `\\w` 在 Unicode 模式下已含中文与下划线，额外列 CJK 区间是为了显式表达
    意图；关键是**不**含点与路径分隔符，`..` / `a/b` / `C:\\x` 全部落空。
    """
    if not isinstance(alias, str):
        raise ValueError('别名必须是字符串，得到 %s' % type(alias).__name__)
    if not _ALIAS_RE.match(alias):
        raise ValueError(
            '别名不合法：%r（只允许中文、字母、数字、下划线、连字符，'
            '1-64 字，且不含点与路径分隔符）' % alias)
    return alias



def key_root() -> str:
    """返回密钥根目录（不保证已存在）。"""
    env = os.environ.get(KEY_ROOT_ENV, '').strip()
    if env:
        return os.path.abspath(env)
    return os.path.join(os.path.expanduser('~'), '.jikuai', '密钥')


def generate_keypair(alias: str) -> str:
    """生成密钥对并写入密钥根，返回公钥的 base64 字符串。

    若别名已存在则抛 FileExistsError —— 不允许静默覆盖，用户必须先手工删除。
    """
    validate_alias(alias)
    root = key_root()
    os.makedirs(root, exist_ok=True)

    sk_path = os.path.join(root, alias + _PRIVATE_EXT)
    pk_path = os.path.join(root, alias + _PUBLIC_EXT)

    if os.path.exists(sk_path) or os.path.exists(pk_path):
        raise FileExistsError(
            '密钥别名「%s」已存在于 %s，不覆盖。'
            '要重新生成请先手工删除旧密钥文件' % (alias, root))

    seed = secrets.token_bytes(ed.SEED_SIZE)
    pk_bytes = ed.public_key_from_seed(seed)

    # 写私钥
    with open(sk_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(base64.b64encode(seed).decode('ascii') + '\n')
    # 尽力设置权限（Windows 语义有限，不因失败中断）
    try:
        os.chmod(sk_path, 0o600)
    except OSError:
        pass

    # 写公钥
    with open(pk_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(base64.b64encode(pk_bytes).decode('ascii') + '\n')

    return base64.b64encode(pk_bytes).decode('ascii')


def load_private_key(alias: str) -> bytes:
    """加载私钥种子（32 字节）。找不到抛 FileNotFoundError。"""
    validate_alias(alias)
    path = os.path.join(key_root(), alias + _PRIVATE_EXT)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            '找不到私钥「%s」：%s 不存在' % (alias, path))
    with open(path, 'r', encoding='utf-8') as f:
        b64 = f.read().strip()
    seed = base64.b64decode(b64)
    if len(seed) != ed.SEED_SIZE:
        raise ValueError(
            '私钥「%s」长度异常（期望 %d 字节，得到 %d）'
            % (alias, ed.SEED_SIZE, len(seed)))
    return seed


def load_public_key(alias: str, root: str = '') -> bytes:
    """加载公钥（32 字节压缩点）。

    `root` 非空时从指定目录读（用于信任库），否则从密钥根读。
    """
    validate_alias(alias)
    base = root if root else key_root()
    path = os.path.join(base, alias + _PUBLIC_EXT)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            '找不到公钥「%s」：%s 不存在' % (alias, path))
    with open(path, 'r', encoding='utf-8') as f:
        b64 = f.read().strip()
    pk = base64.b64decode(b64)
    if len(pk) != ed.PUBLIC_KEY_SIZE:
        raise ValueError(
            '公钥「%s」长度异常（期望 %d 字节，得到 %d）'
            % (alias, ed.PUBLIC_KEY_SIZE, len(pk)))
    return pk


def list_keys() -> list:
    """列出密钥根下所有别名。返回 [(别名, 有私钥, 有公钥)] 列表，按别名排序。"""
    root = key_root()
    if not os.path.isdir(root):
        return []
    aliases = set()
    for name in os.listdir(root):
        if name.endswith(_PRIVATE_EXT):
            aliases.add(name[:-len(_PRIVATE_EXT)])
        elif name.endswith(_PUBLIC_EXT):
            aliases.add(name[:-len(_PUBLIC_EXT)])
    result = []
    for alias in sorted(aliases):
        has_sk = os.path.isfile(os.path.join(root, alias + _PRIVATE_EXT))
        has_pk = os.path.isfile(os.path.join(root, alias + _PUBLIC_EXT))
        result.append((alias, has_sk, has_pk))
    return result


def export_public_key_b64(alias: str) -> str:
    """返回公钥的 base64 字符串（44 字符），可直接粘贴给注册表管理员。"""
    pk = load_public_key(alias)
    return base64.b64encode(pk).decode('ascii')
