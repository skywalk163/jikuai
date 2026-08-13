# -*- coding: utf-8 -*-
"""包签名信任库（ADR-33 §2.5，v0.20.0 W75；多公钥轮换见 ADR-36 §2.4）。

信任模型是 **TOFU（Trust On First Use）+ 显式白名单**，不是 PKI：

- 首次见到某签名者 → 从注册表 `密钥/<签名者>.公钥` 拉公钥、pin 进本地信任库
- 之后**不再自动接受**注册表侧的任何新公钥。已 pin 的别名只认 pin 列表里的
  公钥；注册表侧出现新公钥时拒装并指向显式动作 `jk 包 密钥 信任 <别名> <公钥>`
- 环境变量 `JIKUAI_TRUSTED_SIGNERS`（`os.pathsep` 分隔）给显式白名单：
  设了就只信名单里的签名者，**即便签名本身有效**

**一别名多公钥（v0.22.0 W101 / ADR-36 §2.4）**
--------------------------------------------
pin 文件是「每行一把 base64 公钥」：第一行是当前主公钥，后续行是仍受信的
历史公钥。签名用其中**任意一把**验过即通过——于是签名者轮换密钥时，
用旧公钥签的老包照样能装，不会集体失效（这是 v0.21.0 及之前的真实痛点：
换一次密钥，该签名者的所有历史包一起装不上）。

既有的单行 pin 文件本身就是合法的单行多行文件，**零迁移**。这也是选择
多行纯文本而不是 JSON 数组的全部理由。

**新增公钥只能本地显式授权**：绝不能让「注册表侧出现了一把新公钥」自动并入
pin，否则攻破注册表的人追加一把自己的公钥就能签发受信包，TOFU 归零。
自动路径只保留首次 pin。

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
    'pinned_keys', 'trust_key', 'untrust_key',
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


def _read_pinned_keys(path: str):
    """读 pin 文件，返回公钥列表（32 字节 × n）。不存在返回 `[]`，格式坏抛。

    每行一把 base64 公钥；空行与 `#` 开头的注释行跳过（管理员手工编辑时
    想标注「这把是 2026-08 轮换前的旧钥」，不该被格式挡住）。
    顺序保留：第一行是当前主公钥。
    """
    if not os.path.isfile(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        文本 = f.read()
    结果 = []
    for 行 in 文本.splitlines():
        行 = 行.strip()
        if not 行 or 行.startswith('#'):
            continue
        pk = _decode_pubkey(行, path)
        if pk not in 结果:                  # 去重，避免手工编辑重复追加
            结果.append(pk)
    return 结果


def _write_pinned_keys(path: str, 公钥列表) -> None:
    """原子写回 pin 文件（每行一把 base64 公钥）。空列表则删除文件。

    删除而不是留一个空文件：空文件在 `_read_pinned_keys` 眼里等于「没 pin
    过」，会让下一次装包走 TOFU 首次 pin 分支——那正是 `密钥 撤信` 撤到一把
    不剩时该有的语义（回到未建立信任的状态），但留个空文件会让 `ls` 看起来
    像还信着，误导人。
    """
    if not 公钥列表:
        if os.path.isfile(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    文本 = ''.join(base64.b64encode(pk).decode('ascii') + '\n'
                   for pk in 公钥列表)
    临时 = path + '.tmp'
    with open(临时, 'w', encoding='utf-8', newline='\n') as f:
        f.write(文本)
    os.replace(临时, path)



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


def _registry_pubkeys(registry_root: str, signer: str):
    """从注册表（本地或远程 HTTP）取签名者的公钥列表；缺失返回 `([], 定位串)`。

    v0.20.0 M20：远程注册表下公钥也得走 HTTP（`GET <base>/密钥/<别名>.公钥`），
    否则远程包的 TOFU 首次 pin 无从建立。读端统一走 `RegistryBackend`。

    注册表侧的公钥文件与本地 pin 文件同为「每行一把 base64」（ADR-36 §2.4）。
    v0.21.0 及之前写的都是单行文件，天然是合法的单元素列表。
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
        return [], where
    结果 = []
    for 行 in text.splitlines():
        行 = 行.strip()
        if not 行 or 行.startswith('#'):
            continue
        pk = _decode_pubkey(行, where)
        if pk not in 结果:
            结果.append(pk)
    return 结果, where



def pinned_keys(signer: str):
    """返回该签名者在本地信任库里受信的公钥列表（可能为空）。"""
    keys.validate_alias(signer)
    return _read_pinned_keys(_pinned_path(signer))


def trust_key(signer: str, public_key_b64: str) -> bool:
    """把一把公钥**追加**到某签名者的受信列表（ADR-36 §2.4 的显式轮换动作）。

    返回 True 表示新增，False 表示本来就在列表里（幂等）。

    追加而非替换：旧公钥留着，用旧钥签的老包照样能装。这正是「轮换不炸老包」
    的实现方式。要清掉一把（密钥泄露）用 `untrust_key`。
    """
    keys.validate_alias(signer)
    pk = _decode_pubkey(public_key_b64, '（命令行参数）')
    path = _pinned_path(signer)
    已有 = _read_pinned_keys(path)
    if pk in 已有:
        return False
    # 追加到**末尾**：第一行保持为原主公钥。新钥要成为主钥得先撤旧钥，
    # 这样「主公钥」的变更永远是一次显式的两步动作，不会被一条追加悄悄改掉。
    已有.append(pk)
    _write_pinned_keys(path, 已有)
    return True


def untrust_key(signer: str, public_key_b64: str) -> bool:
    """从受信列表里移除一把公钥（密钥泄露时用）。

    返回 True 表示确实删掉了，False 表示列表里没有这把。
    删空则整个 pin 文件被移除，回到「未建立信任」状态。
    """
    keys.validate_alias(signer)
    pk = _decode_pubkey(public_key_b64, '（命令行参数）')
    path = _pinned_path(signer)
    已有 = _read_pinned_keys(path)
    if pk not in 已有:
        return False
    已有.remove(pk)
    _write_pinned_keys(path, 已有)
    return True


def resolve_trusted_keys(signer: str, registry_root: str):
    """拿到该签名者当前受信的全部公钥（列表，至少一把），必要时首次 pin。

    - 信任库已有记录 → **原样返回，不看注册表**。注册表侧新增/替换公钥
      一概不自动接受（ADR-36 §2.4：自动接受等于把 TOFU 关掉）。
    - 信任库没有记录 → TOFU 首次使用：从注册表 `密钥/<签名者>.公钥` 拉、
      pin、返回。注册表也没有 → 抛 `TrustError`（无从建立信任）。
    """
    keys.validate_alias(signer)

    已信 = _read_pinned_keys(_pinned_path(signer))
    if 已信:
        return 已信


    reg_pks, reg_where = _registry_pubkeys(registry_root, signer)
    if not reg_pks:
        raise TrustError(
            '签名者「%s」在注册表 %s 里没有公钥，无法建立信任'
            % (signer, reg_where))
    _write_pinned_keys(_pinned_path(signer), reg_pks)
    return reg_pks


def resolve_and_pin(signer: str, registry_root: str) -> bytes:
    """兼容入口：返回**主**公钥（受信列表的第一把）。

    保留是因为「某签名者的当前公钥」在展示与排障场景仍是个有意义的问题。
    验签**不要**走这里——用 `verify_signature`，它会试全部受信公钥。
    """
    return resolve_trusted_keys(signer, registry_root)[0]


def verify_signature(signer: str, signature_b64: str, checksum: str,
                     registry_root: str) -> None:
    """完整验签一个注册表包。失败抛 TrustError，成功静默返回。

    步骤：白名单闸门 → 取受信公钥列表（必要时 TOFU 首次 pin）→ 用**任意一把**
    验 Ed25519 签名对校验和字符串，有一把过就算过。
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

    受信 = resolve_trusted_keys(signer, registry_root)
    消息 = checksum.encode('utf-8')
    for pk in 受信:
        if ed.verify(pk, 消息, sig):
            return
    raise TrustError(_验签失败说明(signer, registry_root, 受信))


def _验签失败说明(signer: str, registry_root: str, 受信) -> str:
    """区分「疑似轮换」与「疑似篡改」，给出对应的处置指引。

    这两种情况的处置完全相反——轮换要追加公钥，篡改要报警——所以错误信息
    必须分开。判据：注册表侧是否存在一把**不在**受信列表里的公钥。
    """
    try:
        reg_pks, _ = _registry_pubkeys(registry_root, signer)
    except TrustError:
        reg_pks = []
    新钥 = [pk for pk in reg_pks if pk not in 受信]
    if 新钥:
        return (
            '签名者「%s」的签名验不过本地已信任的 %d 把公钥，但注册表里出现了'
            '一把新公钥——可能是密钥轮换，也可能是投毒。**不自动接受**。'
            '确认这把公钥确实属于该签名者后，执行：\n'
            '  jk 包 密钥 信任 %s %s\n'
            '旧公钥会保留，用旧钥签的老包不受影响。'
            % (signer, len(受信), signer,
               base64.b64encode(新钥[0]).decode('ascii')))
    return ('签名者「%s」的签名验证失败——包内容或签名被篡改，拒装' % signer)

