# -*- coding: utf-8 -*-
"""Ed25519 签名/验签 —— **纯标准库实现**（RFC 8032）。

## 为什么自己实现

`src/jikuai/` 下**运行时零第三方 pip 依赖**是项目硬约束（v0.16.0 起）。
包签名（ADR-33）要求非对称签名，而 Python 标准库**没有**任何非对称原语
（`hashlib` 只有哈希，`hmac` 是对称的）。三条出路：

1. 引 `cryptography` / `PyNaCl` —— 破零依赖约束，**否**
2. 把签名下沉到 `tools/`（允许第三方依赖）—— 但**验签必须在 `jk 包 装` 的
   运行时路径上**，那是 `src/jikuai/pkg/installer.py`，绕不开，**否**
3. 按 RFC 8032 自己实现（只依赖 `hashlib.sha512` + 整数运算）—— **选这条**

## 为什么可以接受

- **算法是标准的、实现是可验证的**：RFC 8032 §7.1 给了官方 test vectors，
  `tests/test_ed25519_vectors.py` 全跑。密码学风险在「自创算法」，不在
  「照 RFC 实现标准算法」。
- **性能够用**：签名/验签是**离线一次性**操作（发一次包、装一次包），
  用扩展齐次坐标（extended homogeneous coordinates）后单次约十毫秒级，
  用户无感。不是 TLS 握手那种每秒千次的场景。

## 已知局限（ADR-33 §5 记录，别当成没写）

- **非常量时间**：Python 大整数运算没有常量时间保证，理论上存在时序侧信道。
  威胁模型：私钥只在**本机开发者手里**签自己的包，攻击者要测时序得先能在
  同机跑代码——那时私钥文件本身已经暴露了。**验签路径读的全是公开数据，
  时序无秘密可泄。**
- **无侧信道加固**：不做 blinding / 不清零中间变量。同上，威胁模型不要求。
- 要真正的加固实现请在 `tools/` 侧用 `cryptography`，或等 v0.21.0 评估
  「可选加速后端」（有库就用、没库落回本实现）。

## 命名说明

本文件**刻意沿用 RFC 8032 参考实现的英文标识符**（`point_add` / `sha512_modq`
/ `point_compress`…），不改成中文名。理由：这是逐行对照 RFC 可审计的代码，
换名字会让「和 RFC 一致吗」这个问题从「对照读」变成「先做名词映射再对照读」。
项目其它地方的中文命名约定在这里让位于**可审计性**。
"""

import hashlib

__all__ = ['SEED_SIZE', 'PUBLIC_KEY_SIZE', 'SIGNATURE_SIZE',
           'public_key_from_seed', 'sign', 'verify']

#: 私钥种子字节数（RFC 8032 的 Ed25519 私钥就是 32 字节随机种子）
SEED_SIZE = 32
#: 公钥字节数（压缩点）
PUBLIC_KEY_SIZE = 32
#: 签名字节数（R‖S）
SIGNATURE_SIZE = 64

# --- 曲线参数（RFC 8032 §5.1）-------------------------------------------------

p = 2 ** 255 - 19
_q = 2 ** 252 + 27742317777372353535851937790883648493   # 群阶 L

_d = -121665 * pow(121666, p - 2, p) % p
_modp_sqrt_m1 = pow(2, (p - 1) // 4, p)


def _modp_inv(x):
    return pow(x, p - 2, p)


def _recover_x(y, sign):
    """由 y 与符号位恢复 x；点不在曲线上返回 None。"""
    if y >= p:
        return None
    x2 = (y * y - 1) * _modp_inv(_d * y * y + 1) % p
    if x2 == 0:
        if sign:
            return None
        return 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p != 0:
        x = x * _modp_sqrt_m1 % p
    if (x * x - x2) % p != 0:
        return None
    if (x & 1) != sign:
        x = p - x
    return x


_g_y = 4 * _modp_inv(5) % p
_g_x = _recover_x(_g_y, 0)
#: 基点 G，扩展齐次坐标 (X, Y, Z, T)，T = X*Y/Z
G = (_g_x, _g_y, 1, _g_x * _g_y % p)


# --- 点运算（扩展齐次坐标，避免每次加法做模逆）--------------------------------

def _point_add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C = 2 * P[3] * Q[3] * _d % p
    D = 2 * P[2] * Q[2] % p
    E, F, Gg, H = B - A, D - C, D + C, B + A
    return (E * F % p, Gg * H % p, F * Gg % p, E * H % p)


def _point_mul(s, P):
    """标量乘。逐位 double-and-add；s 是 254 位左右，约 256 次倍点。"""
    Q = (0, 1, 1, 0)          # 单位元（中性点）
    while s > 0:
        if s & 1:
            Q = _point_add(Q, P)
        P = _point_add(P, P)
        s >>= 1
    return Q


def _point_equal(P, Q):
    # 齐次坐标比较：交叉相乘消掉 Z
    if (P[0] * Q[2] - Q[0] * P[2]) % p != 0:
        return False
    if (P[1] * Q[2] - Q[1] * P[2]) % p != 0:
        return False
    return True


# --- 编解码 -------------------------------------------------------------------

def _point_compress(P):
    zinv = _modp_inv(P[2])
    x = P[0] * zinv % p
    y = P[1] * zinv % p
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _point_decompress(s):
    """解压公钥/R 点。长度不对抛 ValueError；不在曲线上返回 None。"""
    if len(s) != 32:
        raise ValueError('压缩点必须是 32 字节（得到 %d）' % len(s))
    y = int.from_bytes(s, 'little')
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % p)


def _sha512(data):
    return hashlib.sha512(data).digest()


def _sha512_modq(data):
    return int.from_bytes(_sha512(data), 'little') % _q


def _secret_expand(seed):
    if len(seed) != SEED_SIZE:
        raise ValueError('私钥种子必须是 %d 字节（得到 %d）'
                         % (SEED_SIZE, len(seed)))
    h = _sha512(seed)
    a = int.from_bytes(h[:32], 'little')
    # clamping（RFC 8032 §5.1.5）：清低三位、清最高位、置次高位
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return (a, h[32:])


# --- 公开接口 -----------------------------------------------------------------

def public_key_from_seed(seed):
    """32 字节私钥种子 → 32 字节公钥。"""
    a, _ = _secret_expand(seed)
    return _point_compress(_point_mul(a, G))


def sign(seed, msg):
    """用私钥种子签消息，返回 64 字节签名（R‖S）。"""
    a, prefix = _secret_expand(seed)
    A = _point_compress(_point_mul(a, G))
    r = _sha512_modq(prefix + msg)
    R = _point_mul(r, G)
    Rs = _point_compress(R)
    h = _sha512_modq(Rs + A + msg)
    s = (r + h * a) % _q
    return Rs + int.to_bytes(s, 32, 'little')


def verify(public_key, msg, signature):
    """验签。**任何形态不合法都返回 False，不抛异常**——调用方只需看真假。

    刻意不抛：验签的输入全部来自不可信的注册表/网络，长度不对、点不在曲线上、
    S 超出群阶都是「这个签名不可信」的同义词，不该让调用方分别 catch 三种
    异常再各自判断。真正该抛的是「你把公钥和签名传反了」这类程序 bug，
    但那也表现为长度不对 → False，上层会以「不可信包拒装」收敛。
    """
    if len(public_key) != PUBLIC_KEY_SIZE or len(signature) != SIGNATURE_SIZE:
        return False
    try:
        A = _point_decompress(public_key)
        if A is None:
            return False
        Rs = signature[:32]
        R = _point_decompress(Rs)
        if R is None:
            return False
        s = int.from_bytes(signature[32:], 'little')
        if s >= _q:
            return False
        h = _sha512_modq(Rs + public_key + msg)
        sB = _point_mul(s, G)
        hA = _point_mul(h, A)
        return _point_equal(sB, _point_add(R, hA))
    except (ValueError, TypeError):
        return False
