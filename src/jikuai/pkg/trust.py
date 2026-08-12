# -*- coding: utf-8 -*-
"""包签名信任库（ADR-33 §2.5，v0.20.0 W75）。

信任模型是 **TOFU（Trust On First Use）+ 显式白名单**，不是 PKI：

- 首次见到某签名者 → 从注册表 `密钥/<签名者>.公钥` 拉公钥、pin 进本地信任库
- 之后该签名者在注册表里的公钥变了 → 拒装并报「公钥变更」（不静默接受）
- 环境变量 `JIKUAI_TRUSTED_SIGNERS`（`os.pathsep` 分隔）给显式白名单：
  设了就只信名单里的签名者，**即便签名本身有效**

信任库根：`$JIKUAI_TRUST_ROOT` > `~/.jikuai/信任/`。

为什么信任库与密钥根（`~/.jikuai/密钥/`）分开：密钥根装的是**本机自己**的
签名私钥/公钥（我发包用的身份）；信任库装的是**别人**的公钥（我装别人的包
时验签用的）。两者语义不同、泄露后果不同（私钥泄露能冒充我，信任库被改能
骗我装假包），分目录避免误操作互相污染。
"""

import base64
import os

from . import _ed25519 as ed
from . import keys

__all__ = [
    'TrustError', 'TRUST_ROOT_ENV', 'TRUSTED_SIGNERS_ENV',
    'trust_root', 'trusted_signers', 'is_signer_allowed',
    'resolve_and_pin', 'verify_signature',
]

#: 环境变量覆盖信任库根目录
TRUST_ROOT_ENV = 'JIKUAI_TRUST_ROOT'
#: 环境变量：显式白名单（os.pathsep 分隔的签名者别名）
TRUSTED_SIGNERS_ENV = 'JIKUAI_TRUSTED_SIGNERS'

_PUBLIC_EXT = '.公钥'


class TrustError(Exception):
    """信任校验失败：公钥变更、签名无效、签名者不在白名单等。"""


def trust_root() -> str:
    """返回信任库根目录（不保证已存在）。"""
    env = os.environ.get(TRUST_ROOT_ENV, '').strip()
    if env:
        return os.path.abspath(env)
    return os.path.join(os.path.expanduser('~'), '.jikuai', '信任')


def trusted_signers():
    """解析白名单环境变量，返回签名者别名集合；未设置返回 None。

    返回 None 与返回空集合含义不同：
    - None：没配白名单 → 走 TOFU，信任任何签名有效的签名者
    - 空集合（`JIKUAI_TRUSTED_SIGNERS=` 显式设成空）→ 谁都不信，全拒
    """
    raw = os.environ.get(TRUSTED_SIGNERS_ENV)
    if raw is None:
        return None
    return {s.strip() for s in raw.split(os.pathsep) if s.strip()}


def is_signer_allowed(signer: str) -> bool:
    """签名者是否被白名单允许。没配白名单（None）时一律放行（TOFU 兜底）。"""
    allow = trusted_signers()
    if allow is None:
        return True
    return signer in allow


def _pinned_path(signer: str) -> str:
    keys.validate_alias(signer)
    return os.path.join(trust_root(), signer + _PUBLIC_EXT)


def _read_pubkey_file(path: str):
    """读一个 base64 公钥文件，返回 32 字节；不存在返回 None，格式坏抛。"""
    if not os.path.isfile(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return _decode_pubkey(f.read(), path)


def _decode_pubkey(text: str, where: str):
    """base64 文本 → 32 字节公钥。长度不对即抛（不给「差不多」留口子）。"""
    try:
        pk = base64.b64decode(text.strip())
    except (ValueError, TypeError):
        raise TrustError('公钥 %s 不是合法 base64' % where) from None
    if len(pk) != ed.PUBLIC_KEY_SIZE:
        raise TrustError(
            '公钥 %s 长度异常（期望 %d 字节，得到 %d）'
            % (where, ed.PUBLIC_KEY_SIZE, len(pk)))
    return pk


def _registry_pubkey(registry_root: str, signer: str):
    """从注册表（本地或远程 HTTP）取签名者公钥；缺失返回 `(None, 定位串)`。

    v0.20.0 M20：远程注册表下公钥也得走 HTTP（`GET <base>/密钥/<别名>.公钥`），
    否则远程包的 TOFU 首次 pin 无从建立。读端统一走 `RegistryBackend`。
    """
    from . import registry
    from . import backend as _backend
    rel = registry.key_rel(signer)
    try:
        b = _backend.get_backend(registry_root)
        text = b.read_text(rel)
        where = b.describe(rel)
    except _backend.BackendError as e:
        raise TrustError(str(e)) from None
    if text is None:
        return None, where
    return _decode_pubkey(text, where), where


def resolve_and_pin(signer: str, registry_root: str) -> bytes:
    """TOFU：拿到某签名者可信的公钥（32 字节），必要时首次 pin。

    - 信任库已 pin：注册表当前公钥若与之不一致 → 抛 TrustError（公钥变更）；
      一致或注册表没有则返回 pin 的那份（pin 是权威）。
    - 信任库未 pin：从注册表 `密钥/<签名者>.公钥` 拉、pin、返回；注册表也
      没有 → 抛 TrustError（无从建立信任）。

    注册表侧读取走 `RegistryBackend`，本地路径与远程 URL 同一套相对路径，
    路径校验与 registry 模块一致。
    """
    keys.validate_alias(signer)

    reg_pk, reg_where = _registry_pubkey(registry_root, signer)

    pin_path = _pinned_path(signer)
    pinned = _read_pubkey_file(pin_path)

    if pinned is not None:
        if reg_pk is not None and reg_pk != pinned:
            raise TrustError(
                '签名者「%s」的公钥与本地信任库记录不一致——可能是密钥轮换，'
                '也可能是投毒。拒装。确认无误后删除 %s 再重装'
                % (signer, pin_path))
        return pinned

    # 首次见到：pin 注册表里的公钥
    if reg_pk is None:
        raise TrustError(
            '签名者「%s」在注册表 %s 里没有公钥，无法建立信任'
            % (signer, reg_where))
    os.makedirs(os.path.dirname(pin_path), exist_ok=True)
    with open(pin_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(base64.b64encode(reg_pk).decode('ascii') + '\n')
    return reg_pk


def verify_signature(signer: str, signature_b64: str, checksum: str,
                     registry_root: str) -> None:
    """完整验签一个注册表包。失败抛 TrustError，成功静默返回。

    步骤：白名单闸门 → TOFU 取可信公钥 → Ed25519 验签名对校验和字符串。
    签名对象与发布端一致：`checksum.encode('utf-8')`（含 `sha256:` 前缀）。
    """
    if not is_signer_allowed(signer):
        raise TrustError(
            '签名者「%s」不在白名单 %s 内，拒装（即便签名有效）'
            % (signer, TRUSTED_SIGNERS_ENV))
    try:
        sig = base64.b64decode(signature_b64)
    except (ValueError, TypeError):
        raise TrustError('签名者「%s」的签名不是合法 base64' % signer)
    if len(sig) != ed.SIGNATURE_SIZE:
        raise TrustError(
            '签名者「%s」的签名长度异常（期望 %d 字节）'
            % (signer, ed.SIGNATURE_SIZE))

    pk = resolve_and_pin(signer, registry_root)
    if not ed.verify(pk, checksum.encode('utf-8'), sig):
        raise TrustError(
            '签名者「%s」的签名验证失败——包内容或签名被篡改，拒装' % signer)
