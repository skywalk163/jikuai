# -*- coding: utf-8 -*-
"""远程注册表服务端（ADR-35 / v0.21.0 M23 · W90-W91）。

一个进程做两件事：

* `GET  <路径>`   → 把注册表根目录当静态镜像原样吐出。与 v0.20.0 W78 起
  `HttpBackend` 期望的形态**逐字节一致**：`索引.json` / `分类/*.json` /
  `包/<名>/<版本>.tar.gz` / `密钥/<签名者>.公钥`。运维只跑这一个进程就
  同时具备读端与写端。
* `POST /publish` → 受控写入口。走完整 8 步流水：**认证 → 授权 → 频次
  → 报文体量 → 验签 → 校验和复核 → 覆盖检查 → 原子落盘**。

## 为什么零第三方依赖

延续 `tools/web/server.py` 的选型：`http.server.ThreadingHTTPServer` 支撑
单节点静态托管 + 一个 POST 端点已经绰绰有余，引 FastAPI/Flask 换来的只有
「更好看的路由 DSL」，代价是一个必须 `pip install` 的运维面。ADR-35 §5
拒方案一栏已定案。

## 为什么不复用 `registry.publish(..., signer=...)`

服务端**没有**私钥（ADR-35 §2.3 rule 3：公钥由管理员登记在服务端，
报文里不带公钥；签名由客户端已完成）。走 `publish(signer=...)` 会调
`_keys.load_private_key`，本机私钥库里必然找不到。因此这里的落盘策略是：

  1. 先 `registry.publish()` **不带 signer** —— 完成快照 + 归档 + 索引/分片；
  2. 再把客户端提交的签名 + 授权登记的公钥**注入**分片与 `密钥/` 目录。

两步都在同一把写锁下串行执行，外部观察不到中间态。

## 错误响应约束（务必守住）

- **绝不回显服务端文件系统路径**：错误里带路径 = 给攻击者的地形图。上游异常
  可能是本地磁盘 IO 报错，往上抛前必须转成一句中文原因或干脆 500。
- **协议版本不认** → 400 明说服务端版本与客户端版本（§2.7），别猜字段。
- **`名称@版本` 已存在** → 409。**没有** `--允许覆盖` 开关（§2.4）。

## 生产化约束（ADR-36 / v0.22.0 W102）

- **TLS 自身永不做**。`--要求TLS转发` 让 `POST /publish` 必须带
  `X-Forwarded-Proto: https`（缺则 403）；非回环绑定且未开该开关时启动横幅
  醒目告警但**不拒绝启动**（内网可信段直连是合理场景）。
- **单写者**。启动期抢 `<注册表根>/.发布锁`（`O_CREAT|O_EXCL`），已被占用即
  拒绝启动（退出码 3）；陈旧锁靠 `--强制解锁` 手工清。裁决理由与「为什么不做
  跨进程文件锁」见 ADR-36 §2.2 与 `抢发布锁` 的 docstring。
- **授权配置热重载**。`授权.json` 改动无需重启，见 `auth.AuthProvider`。
"""

import argparse
import base64
import gzip
import hashlib
import io
import json
import logging
import os
import re
import shutil
import socketserver
import sys
import tarfile
import tempfile
import threading
import urllib.parse
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    # 与 `tools/web/server.py` / `tools/ai-bridge/glue.py` 同策略：
    # 工具自己把 `src/` 挂上，免得强制用户先 `pip install -e .`。
    sys.path.insert(0, _SRC)

# 相对/绝对两种导入：既支持 `python tools/registry-server/server.py`
# 直接跑，也支持从测试里 `import tools.registry_server.server`。
try:                                                    # pragma: no cover
    from . import auth as _auth_mod
    from . import audit as _audit_mod
except ImportError:                                     # pragma: no cover
    sys.path.insert(0, _HERE)
    import auth as _auth_mod                            # type: ignore
    import audit as _audit_mod                          # type: ignore

from jikuai.pkg import _ed25519 as _ed                          # noqa: E402
from jikuai.pkg import registry as _registry                    # noqa: E402
from jikuai.pkg import sources as _sources                      # noqa: E402
from jikuai.pkg.manifest import (                               # noqa: E402
    MANIFEST_NAME, ManifestError, load_manifest,
)

__all__ = [
    'PROTOCOL_VERSION', 'MAX_BODY_ENV', 'DEFAULT_MAX_BODY',
    'DEFAULT_HOST', 'DEFAULT_PORT', 'PUBLISH_PATH',
    'LOCK_NAME', '锁冲突', '锁路径', '抢发布锁', '释放发布锁', '危险绑定横幅',
    'build_server', 'main',
]

_LOG = logging.getLogger('jikuai.registry-server')

#: 报文顶层的协议版本；与 ADR-35 §2.2 一致。
PROTOCOL_VERSION = 1

#: 单请求体上限的环境变量名（ADR-35 §2.5）。
MAX_BODY_ENV = 'JIKUAI_REGISTRY_SERVER_MAX_BODY'
#: 单请求体上限默认值（32 MiB）—— base64 后的报文，先于解析生效。
DEFAULT_MAX_BODY = 32 * 1024 * 1024

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8765

#: 唯一写端点。ADR 明文 ASCII 路径，理由见 §2.2。
PUBLISH_PATH = '/publish'

#: 报文里必须出现的字段（缺一即 400）。
_REQUIRED_FIELDS = ('协议', '名称', '版本', '校验和', '签名者', '签名', '归档')

#: 归档相对路径的白名单正则：`包/<名>/<版本>.tar.gz`。用于 GET 时的额外
#: 白名单校验（静态托管本身已经防越界，这里加一层可读的形状检查）。
_ARCHIVE_RE = re.compile(r'^包/[^/\\]+/[^/\\]+\.tar\.gz$')


class _响应(Exception):
    """带 HTTP 状态码的可控响应异常。上层 `_分发` 统一转 JSON 输出。

    `原因` 面向客户端，一定是中文短句、**不含**服务端路径。
    """

    def __init__(self, 状态, 原因):
        super().__init__(原因)
        self.状态 = 状态
        self.原因 = 原因


# ---------------------------------------------------------------------------
# 请求处理器工厂
# ---------------------------------------------------------------------------

def _make_handler(注册表根, 授权源, 审计路径, 写锁, max_body, 要求TLS=False):
    """闭包出一个绑定运行时状态的 `BaseHTTPRequestHandler` 子类。

    不用类属性/全局：便于测试起多个实例互不干扰。

    `授权源` 是 `AuthProvider`（不是 `AuthConfig`）——每次处理写请求时向它
    要一份当前配置，从而支持 `授权.json` 的热重载（ADR-36 §2.3）。

    `要求TLS` 为真时，`POST /publish` 必须携带 `X-Forwarded-Proto: https`
    （ADR-36 §2.1），否则 403。这条断言的是「我确认自己只被反代访问」——
    在默认回环绑定下该头由反代设置、不可被外部伪造；它**不是**对真实 TLS
    的密码学鉴别，文档必须这么写，不给错误的安全感。
    """
    注册表根_abs = os.path.abspath(注册表根)


    class 处理器(BaseHTTPRequestHandler):
        server_version = 'JiKuaiRegistry/0.21'

        # `BaseHTTPRequestHandler` 的默认日志写到 stderr，与项目 logging
        # 配置抢流。全部走 `_LOG`，让调用侧按需过滤。
        def log_message(self, fmt, *args):
            _LOG.info('%s - %s', self.address_string(), fmt % args)

        def log_error(self, fmt, *args):
            _LOG.warning('%s - %s', self.address_string(), fmt % args)

        # ---- GET：静态托管注册表根 -------------------------------------

        def do_GET(self):
            try:
                self._静态GET()
            except _响应 as e:
                self._发错误(e.状态, e.原因)
            except Exception as e:                       # noqa: BLE001
                _LOG.exception('GET %s 内部错误', self.path)
                self._发错误(500, '服务器内部错误')      # 不回显细节

        def _静态GET(self):
            # 只处理相对路径；`?..` 一律无视 —— 查询串对静态资源没有语义。
            原始路径 = urllib.parse.urlsplit(self.path).path
            相对 = urllib.parse.unquote(原始路径).lstrip('/')
            if not 相对:
                raise _响应(404, '未找到')
            # 路径穿越白名单：不允许任何 `..` 段，也不允许绝对路径。
            段列表 = 相对.replace('\\', '/').split('/')
            if any(段 in ('', '..') for 段 in 段列表):
                raise _响应(400, '路径不合法')
            if os.path.isabs(相对):
                raise _响应(400, '路径不合法')
            目标 = os.path.join(注册表根_abs, *段列表)
            目标_abs = os.path.abspath(目标)
            # 双重防护：normalize 后仍需前缀在注册表根内。
            共同 = os.path.commonpath([注册表根_abs, 目标_abs])
            if 共同 != 注册表根_abs:
                raise _响应(400, '路径越界')
            if not os.path.isfile(目标_abs):
                raise _响应(404, '未找到')
            try:
                with open(目标_abs, 'rb') as f:
                    数据 = f.read()
            except OSError:
                raise _响应(500, '读文件失败')
            self.send_response(200)
            self.send_header('Content-Type', _猜类型(相对))
            self.send_header('Content-Length', str(len(数据)))
            self.end_headers()
            self.wfile.write(数据)

        # ---- POST /publish --------------------------------------------

        def do_POST(self):
            try:
                路径 = urllib.parse.urlsplit(self.path).path
                if 路径 != PUBLISH_PATH:
                    raise _响应(404, f'未知端点（POST 只有 {PUBLISH_PATH}）')
                结果 = self._发布()
                self._发JSON(200, 结果)
            except _响应 as e:
                self._记审计(拒绝原因=e.原因)
                self._发错误(e.状态, e.原因)
            except Exception as e:                       # noqa: BLE001
                _LOG.exception('POST %s 内部错误', self.path)
                self._记审计(拒绝原因=f'内部错误:{type(e).__name__}')
                self._发错误(500, '服务器内部错误')

        # -- 发布主流水（8 步 + 落盘 + 审计）-----------------------------

        def _发布(self):
            # 0. TLS 转发闸门（ADR-36 §2.1）。放在读 body **之前**：误配置的
            #    部署不该先把一个 32 MiB 的报文明文灌进来再拒。
            if 要求TLS:
                proto = (self.headers.get('X-Forwarded-Proto') or '').strip()
                if proto.lower() != 'https':
                    raise _响应(
                        403,
                        '本服务端以 --要求TLS转发 启动，POST 必须带 '
                        '`X-Forwarded-Proto: https`。请检查反向代理是否设置了'
                        '该头，或确认你并非在绕过反代直连')

            # 1. 读 body，卡上限。`Content-Length` 缺失直接拒（不接 chunked）。
            长度头 = self.headers.get('Content-Length')
            if 长度头 is None:
                raise _响应(411, '缺少 Content-Length')
            try:
                长度 = int(长度头)
            except ValueError:
                raise _响应(400, 'Content-Length 非法')
            if 长度 < 0:
                raise _响应(400, 'Content-Length 非法')
            if 长度 > max_body:
                # 413 之后客户端可能还在灌 body，长连接会读串；上层选择关闭。
                raise _响应(
                    413, f'请求体 {长度} 字节超过上限 {max_body} 字节')
            try:
                raw = self.rfile.read(长度) if 长度 else b''
            except OSError:
                raise _响应(400, '读请求体失败')

            # 2. JSON 解析 + 协议版本
            try:
                payload = json.loads(raw.decode('utf-8'))
            except UnicodeDecodeError:
                raise _响应(400, '请求体不是 UTF-8')
            except json.JSONDecodeError as e:
                raise _响应(400, f'请求体不是合法 JSON：第 {e.lineno} 行 {e.msg}')
            if not isinstance(payload, dict):
                raise _响应(400, '请求体必须是对象')
            for f in _REQUIRED_FIELDS:
                if f not in payload:
                    raise _响应(400, f'请求体缺少字段「{f}」')
            proto = payload.get('协议')
            if proto != PROTOCOL_VERSION:
                raise _响应(
                    400,
                    f'服务端支持协议 {PROTOCOL_VERSION}，客户端发来 {proto!r}，'
                    f'请升级其中一端')

            名称 = payload['名称']
            版本 = payload['版本']
            校验和 = payload['校验和']
            签名者 = payload['签名者']
            签名_b64 = payload['签名']
            归档_b64 = payload['归档']
            # 报文里也带了 `条目` 分片明细，但服务端不信客户端提交的详情——
            # 由 `registry.publish` 从清单重新计算落盘（防条目被篡）。
            # 为可追踪保留在暂存字典里，下面只在需要时读。
            self._本次_名称 = 名称 if isinstance(名称, str) else '?'
            self._本次_版本 = 版本 if isinstance(版本, str) else '?'
            self._本次_签名者 = 签名者 if isinstance(签名者, str) else '?'

            if not isinstance(校验和, str) or not 校验和.startswith('sha256:'):
                raise _响应(400, '「校验和」必须是 `sha256:<hex>` 形式')
            if not isinstance(签名_b64, str) or not 签名_b64:
                raise _响应(403, '未提供签名；远程发布强制签名')
            if not isinstance(归档_b64, str) or not 归档_b64:
                raise _响应(400, '「归档」字段为空')

            # 3. 认证（Authorization: Bearer <token>）
            #    先向 AuthProvider 要一份当前配置：`授权.json` 若有改动，
            #    这一步就地热重载（ADR-36 §2.3），撤销 token 立刻生效。
            授权配置 = 授权源.current()
            auth头 = self.headers.get('Authorization') or ''

            if not auth头.startswith('Bearer '):
                raise _响应(401, '缺少 `Authorization: Bearer <token>` 头')
            raw_token = auth头[len('Bearer '):].strip()
            entry = 授权配置.authenticate(raw_token)
            if entry is None:
                raise _响应(401, 'token 不认识；请检查 `JIKUAI_REGISTRY_TOKEN` '
                                 '或 `~/.jikuai/凭证.json`')

            # 4. 授权：签名者一致 + 包名白名单
            拒 = 授权配置.authorize_publish(entry, 名称, 签名者)
            if 拒 is not None:
                raise _响应(403, 拒)

            # 5. 频次限额
            拒 = 授权配置.check_rate(entry)
            if 拒 is not None:
                raise _响应(429, 拒)

            # 6. 归档字节上限（本 token 的 `单包字节`）
            try:
                归档字节 = base64.b64decode(归档_b64, validate=True)
            except (ValueError, Exception):
                raise _响应(400, '「归档」不是合法 base64')
            if len(归档字节) > entry.max_package_bytes:
                raise _响应(
                    413,
                    f'归档 {len(归档字节)} 字节超过本 token 单包上限 '
                    f'{entry.max_package_bytes} 字节')
            self._本次_字节 = len(归档字节)

            # 7. 验签：Ed25519 对**校验和字符串**（与 registry.publish 一致）。
            #    公钥来自 `授权.json` 里管理员登记的那把——绝不用报文自带。
            try:
                签名字节 = base64.b64decode(签名_b64, validate=True)
            except (ValueError, Exception):
                raise _响应(403, '签名不是合法 base64')
            if not _ed.verify(entry.public_key_bytes,
                              校验和.encode('utf-8'), 签名字节):
                raise _响应(403, '签名验证失败；请核对签名者与登记公钥是否一致')

            # 8. 校验和复核：解压 → compute_checksum → 比对
            临时根 = tempfile.mkdtemp(prefix='jikuai-publish-')
            try:
                try:
                    _sources._safe_extract_targz(归档字节, 临时根)
                except _sources.SourceError as e:
                    # 安全解压失败：路径穿越、tar bomb、链接成员等。
                    # 原因是「归档不安全」，透出简短中文原因，**不带路径**。
                    raise _响应(400, f'归档不安全：{e}')
                except (tarfile.TarError, gzip.BadGzipFile, zlib.error,
                        EOFError) as e:
                    # 根本不是合法 tar.gz（垃圾字节、截断的 gzip 流、坏 tar 头）。
                    # 这是**客户端**输入的问题，必须是 4xx：报 500 会让运维在
                    # 告警里追一个不存在的服务端故障，还会淹掉真实的 500。
                    # 只报异常类名不报原文：上游文本可能带上临时目录路径。
                    raise _响应(
                        400, f'归档不安全：不是合法的 tar.gz（{type(e).__name__}）')

                # 归档以 `<版本>/...` 为根（见 registry._archive_snapshot）。
                子目录 = [e for e in os.listdir(临时根)
                          if os.path.isdir(os.path.join(临时根, e))]
                if len(子目录) == 1:
                    包根 = os.path.join(临时根, 子目录[0])
                else:
                    包根 = 临时根
                if not os.path.isfile(os.path.join(包根, MANIFEST_NAME)):
                    raise _响应(400, f'归档里找不到 {MANIFEST_NAME}')

                实算 = _sources.compute_checksum(包根)[0]
                期望 = 校验和[len('sha256:'):]
                if 实算 != 期望:
                    raise _响应(
                        400, f'校验和不匹配：报文声明 {期望[:12]}…，'
                             f'归档实算 {实算[:12]}…')

                # 加载清单（用于后续 registry.publish 复用元数据）
                try:
                    manifest = load_manifest(包根)
                except ManifestError as e:
                    raise _响应(400, f'清单不合法：{e}')
                if manifest.name != 名称:
                    raise _响应(
                        400, f'清单「名称」{manifest.name!r} 与报文 {名称!r} 不一致')
                if manifest.version != 版本:
                    raise _响应(
                        400, f'清单「版本」{manifest.version!r} 与报文 {版本!r} 不一致')

                # 9-10 与 11 落盘：单进程写锁下串行
                with 写锁:
                    self._覆盖检查与落盘(manifest, entry, 校验和, 签名_b64)

            finally:
                shutil.rmtree(临时根, ignore_errors=True)

            # 11. 审计（成功也记）
            self._记审计(结果='已发布')
            # 12. 响应
            return {'结果': '已发布', '名称': 名称, '版本': 版本, '校验和': 校验和}

        def _覆盖检查与落盘(self, manifest, entry, 校验和, 签名_b64):
            # 9. 覆盖检查：lookup_entry 会抛 RegistryError 表示「不存在」；
            #    存在则版本列表里能查到本次版本 → 409。
            名称 = manifest.name
            版本 = manifest.version
            try:
                _索引 = _registry.load_index(注册表根_abs)
                旧条目 = (_索引.get('索引') or {}).get(名称) or {}
                已有版本 = 旧条目.get('版本') or []
            except _registry.RegistryError:
                已有版本 = []
            if 版本 in 已有版本:
                raise _响应(
                    409,
                    f'{名称}@{版本} 已存在；远程注册表不提供覆盖开关，'
                    f'请提升版本号（语义化版本本就该这么用）')

            # 10. 落盘：先做无签名 publish，再注入签名 + 公钥。
            #    服务端没有私钥，走 signer=None 的路径。
            try:
                报告 = _registry.publish(
                    manifest, root=注册表根_abs,
                    dry_run=False, allow_overwrite=False)
            except _registry.RegistryError as e:
                # 上游报错原文里可能包含 staging 目录路径，转成中性描述。
                raise _响应(500, f'落盘失败：{type(e).__name__}')

            # 二次核对：publish 重算的校验和必须与报文一致
            #    （compute_checksum 只哈 .jk/.py，与验签走的是同一把秤）。
            if 报告.checksum != 校验和:
                raise _响应(
                    500, '落盘后校验和与报文不一致（内部异常）')

            # 注入签名字段：读分片、加字段、原子回写。
            try:
                分片_rel = _registry.CATEGORY_DIR + '/' + 报告.category + '.json'
                后端 = _registry.open_backend(注册表根_abs)
                分片 = 后端.read_json(分片_rel) or {}
                该包 = 分片.get(名称) or {}
                本版 = 该包.get(版本) or {}
                本版['签名者'] = entry.signer
                本版['签名'] = 签名_b64
                该包[版本] = 本版
                分片[名称] = 该包
                后端.write_text(
                    分片_rel,
                    json.dumps(分片, ensure_ascii=False, indent=2) + '\n')

                # 写公钥：`密钥/<签名者>.公钥` = 每行一把 base64 公钥。
                # 与 registry/trust 的多公钥格式对齐（ADR-36 §2.4）。
                # 公钥来自 `授权.json` 登记的那把（管理员显式授权），不是报文。
                # 已有该公钥 → 什么都不做；未含该公钥 → **追加**（支持轮换：
                # 管理员在 授权.json 换 `公钥` 字段后，新钥随下一次发布并入）。
                # 绝不 409 拒——注册表侧只是把管理员已授权的事实分发出去。
                pk_rel = _registry.key_rel(entry.signer)
                本钥 = base64.b64encode(entry.public_key_bytes).decode('ascii')
                旧pk = 后端.read_text(pk_rel)
                已有钥 = []
                if 旧pk is not None:
                    已有钥 = [行.strip() for 行 in 旧pk.splitlines()
                             if 行.strip() and not 行.strip().startswith('#')]
                if 本钥 not in 已有钥:
                    已有钥.append(本钥)
                    后端.write_text(pk_rel,
                                    ''.join(k + '\n' for k in 已有钥))

            except _响应:
                raise
            except Exception:                           # noqa: BLE001
                _LOG.exception('注入签名字段失败')
                raise _响应(500, '签名字段落盘失败')

        # -- 审计 --------------------------------------------------------

        def _记审计(self, 结果=None, 拒绝原因=None):
            if not 审计路径:
                return
            条目 = {
                '时间': datetime.now(timezone.utc).strftime(
                    '%Y-%m-%dT%H:%M:%SZ'),
                '远端': self.address_string(),
                '签名者': getattr(self, '_本次_签名者', ''),
                '名称': getattr(self, '_本次_名称', ''),
                '版本': getattr(self, '_本次_版本', ''),
                '字节': getattr(self, '_本次_字节', 0),
            }
            if 结果:
                条目['结果'] = 结果
            if 拒绝原因:
                条目['结果'] = '拒绝'
                条目['原因'] = 拒绝原因
            try:
                _audit_mod.append_entry(审计路径, 条目)
            except OSError:
                _LOG.exception('审计日志写失败')

        # -- 响应封装 ----------------------------------------------------

        def _发JSON(self, 状态, 体):
            数据 = json.dumps(体, ensure_ascii=False).encode('utf-8')
            self.send_response(状态)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(数据)))
            self.end_headers()
            self.wfile.write(数据)

        def _发错误(self, 状态, 原因):
            数据 = json.dumps({'错误': 原因}, ensure_ascii=False).encode('utf-8')
            self.send_response(状态)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(数据)))
            # 413/401 后客户端行为不定，主动关闭连接更稳。
            if 状态 in (413, 401):
                self.send_header('Connection', 'close')
            self.end_headers()
            try:
                self.wfile.write(数据)
            except OSError:
                pass

    return 处理器


def _猜类型(rel):
    """极简 MIME：注册表里只有 .json / .tar.gz / .公钥。"""
    低 = rel.lower()
    if 低.endswith('.json'):
        return 'application/json; charset=utf-8'
    if 低.endswith('.tar.gz') or 低.endswith('.tgz'):
        return 'application/gzip'
    return 'application/octet-stream'


# ---------------------------------------------------------------------------
# 服务构造 & CLI
# ---------------------------------------------------------------------------

class _不反查HTTPServer(ThreadingHTTPServer):
    """`ThreadingHTTPServer`，但绑定时不做反向 DNS。

    `http.server.HTTPServer.server_bind` 在 `bind()` 之后会执行
    `self.server_name = socket.getfqdn(host)`。`getfqdn` 是一次**反向 DNS 查询**，
    而 `TCPServer.__init__` 的顺序是 `server_bind()` → `server_activate()`
    （`listen()`）—— 也就是说 FQDN 反查没返回之前，端口是「已 bind 但未 listen」，
    对外表现为连接被拒。

    在 GitHub macOS runner 上 `socket.getfqdn()` 实测会阻塞一分钟以上
    （actions/runner-images#12162），于是「构造服务实例」这一步就要耗一分钟，
    起服务端的测试在就绪超时内根本等不到端口。Linux/Windows 上该反查是毫秒级，
    所以这个坑只在 macOS 亮红——v0.22.0 的 macOS job 首次真跑时逮到。

    `server_name` 本服务从头到尾不使用（没有 CGI，也不据此拼 URL），直接取
    `host` 字面量即可。这不是测试专用绕行：任何主机上让「服务启动」依赖一次
    反向 DNS 都是错的。
    """

    def server_bind(self):
        # 跳过 HTTPServer.server_bind（它含 getfqdn），直接走 TCPServer 的
        # bind 逻辑，再把 server_name/server_port 按字面量补齐。
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def build_server(注册表, 授权, host=DEFAULT_HOST, port=DEFAULT_PORT,
                 审计路径=None, max_body=None, 要求TLS=False):
    """建一个未启动的服务实例。测试与 CLI 都走这个入口。

    - `注册表`：本地注册表根目录（远程 URL 不支持—— ADR-34 §2.1 明文本地）。
    - `授权`：`AuthConfig` / `AuthProvider` 实例或 `授权.json` 路径。
    - `审计路径`：可选。省略即不写审计日志。
    - `max_body`：请求体上限，`None` 时读环境变量或用默认 32 MiB。
    - `要求TLS`：真则 `POST /publish` 必须带 `X-Forwarded-Proto: https`
      （ADR-36 §2.1）。

    **本函数不抢 `.发布锁`**：锁是「一个进程独占一个注册表根」的约束，属于
    进程生命周期，由 `main` 负责（见 `抢发布锁`）。测试要在同一个进程里起好
    几个 server 实例，若在这里抢锁就得每个测试都造独立注册表根——那是把
    部署约束泄漏到了构造函数里。
    """
    根 = os.path.abspath(注册表)
    if not os.path.isdir(根):
        os.makedirs(根, exist_ok=True)

    if isinstance(授权, _auth_mod.AuthConfig):
        # 测试直接塞配置：没有文件可 stat，热重载自然是 no-op。
        授权源 = _auth_mod.AuthProvider.from_config(授权)
    elif isinstance(授权, _auth_mod.AuthProvider):
        授权源 = 授权
    else:
        授权源 = _auth_mod.AuthProvider.from_path(授权)

    if max_body is None:
        max_body = _读上限(MAX_BODY_ENV, DEFAULT_MAX_BODY)

    写锁 = threading.Lock()
    处理器 = _make_handler(根, 授权源, 审计路径, 写锁, max_body, 要求TLS)
    srv = _不反查HTTPServer((host, port), 处理器)
    # 挂到实例上，供 CLI 打横幅与测试断言重载次数用。
    srv.授权源 = 授权源
    srv.要求TLS = bool(要求TLS)
    return srv


# ---------------------------------------------------------------------------
# 单写者锁文件（ADR-36 §2.2）
# ---------------------------------------------------------------------------

#: 锁文件名。放在注册表根下而不是系统临时目录：约束的对象是「这个注册表根」，
#: 锁就该和它同生共死。跨机器共享同一个网络盘时也能挡住（尽最大努力——
#: NFS 上 O_EXCL 的原子性不保证，ADR-36 §4 已记为局限）。
LOCK_NAME = '.发布锁'


class 锁冲突(Exception):
    """`.发布锁` 已被占用。`原因` 是可直接打给运维的中文说明。"""


def 锁路径(注册表根):
    return os.path.join(os.path.abspath(注册表根), LOCK_NAME)


def 抢发布锁(注册表根, 强制解锁=False):
    """原子抢占 `<注册表根>/.发布锁`，返回锁文件路径。

    ADR-36 §2.2：服务端是**单写者**。索引是全局单点，写路径本就必须串行，
    多 worker 只能提升读吞吐——而读端是静态文件，本该由反代/CDN 直接吐。
    与其做一套跨平台文件锁（POSIX `flock` / Windows `msvcrt.locking` 两份
    实现 + 陈旧锁回收 + 锁升级），不如把「只能起一个」变成启动期的硬失败。

    `强制解锁=True` 时先删掉既有锁再抢。**刻意不做自动判活**：
    `os.kill(pid, 0)` 在 Windows 上不可用，pid 又会被系统复用，跨平台的
    自动判活必然要么误杀活进程要么误放陈旧锁。让人看一眼锁里记的 pid、
    自己敲一个 `--强制解锁`，比一个有时猜错的启发式可靠。
    """
    根 = os.path.abspath(注册表根)
    os.makedirs(根, exist_ok=True)
    path = 锁路径(根)
    if 强制解锁:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            raise 锁冲突('删除既有锁文件失败：%s' % e) from None
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise 锁冲突(_陈旧锁说明(path)) from None
    except OSError as e:
        raise 锁冲突('创建锁文件失败：%s' % e) from None
    try:
        内容 = json.dumps({
            'pid': os.getpid(),
            '启动时刻': datetime.now(timezone.utc).strftime(
                '%Y-%m-%dT%H:%M:%SZ'),
            '注册表根': 根,
        }, ensure_ascii=False, indent=2) + '\n'
        os.write(fd, 内容.encode('utf-8'))
    finally:
        os.close(fd)
    return path


def 释放发布锁(path):
    """删除锁文件。已经不在了也算成功（幂等，便于放进 `finally`）。"""
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        _LOG.exception('删除锁文件失败，下次启动需要 --强制解锁')
        return False


def _陈旧锁说明(path):
    """读锁文件里的 pid/时刻拼一句可操作的中文说明。读不出也要能说话。"""
    信息 = ''
    try:
        with open(path, 'r', encoding='utf-8') as f:
            记 = json.load(f)
        信息 = '（持锁 pid=%s，启动于 %s）' % (
            记.get('pid', '?'), 记.get('启动时刻', '?'))
    except (OSError, ValueError, json.JSONDecodeError):
        信息 = '（锁文件内容读不出，可能是上次崩在写锁的半路）'
    return (
        '该注册表根已有一个服务端持锁 %s。本服务端是单写者（ADR-36 §2.2），'
        '不支持多进程同时写同一个注册表根。\n'
        '  · 若确实还有进程在跑：先停掉它。\n'
        '  · 若那是崩溃留下的陈旧锁：确认该 pid 已不存在后，'
        '加 --强制解锁 重启。\n'
        '  （刻意不自动判活：pid 会复用，自动判断必然有猜错的时候。）'
        % 信息)


def _是回环(host):
    """判断监听地址是否只对本机可见。空串/`0.0.0.0`/`::` 都是**全网**。"""
    h = (host or '').strip().strip('[]').lower()
    if h in ('127.0.0.1', '::1', 'localhost'):
        return True
    # 127.0.0.0/8 整段都是回环
    return h.startswith('127.')


def 危险绑定横幅(host, 要求TLS):
    """非回环绑定且未要求 TLS 转发时，返回多行告警文本；否则返回 ''。

    **只告警不拒绝启动**（ADR-36 §2.1）：内网可信段直连是合理场景，硬拒会
    逼用户改代码，那比告警更糟。
    """
    if _是回环(host) or 要求TLS:
        return ''
    return (
        '\n'
        '!!! ====================================================== !!!\n'
        '!!!  警告：监听地址 %s 不是回环地址，且未开 --要求TLS转发    \n'
        '!!!  · token 会以明文经网络传输，任何能路由到本机的人都能\n'
        '!!!    试探 POST %s\n'
        '!!!  · 正确形态：反代终结 TLS → 转发到 127.0.0.1:<端口>，\n'
        '!!!    反代设置 X-Forwarded-Proto，服务端加 --要求TLS转发\n'
        '!!!  · 部署样例见 docs/远程注册表部署.md\n'
        '!!!  （若这是内网可信段直连，可无视本告警——不拒绝启动。）\n'
        '!!! ====================================================== !!!\n'
        % (host, PUBLISH_PATH))



def _读上限(名, 默认):
    v = os.environ.get(名)
    if not v:
        return 默认
    try:
        n = int(v)
    except ValueError:
        return 默认
    return n if n > 0 else 默认


def main(argv=None):
    """CLI 入口。返回退出码（0 正常 / 2 授权配置错 / 3 锁冲突）。"""
    p = argparse.ArgumentParser(
        description='极快远程注册表服务端（ADR-35 / ADR-36）')
    p.add_argument('--注册表', dest='registry', required=True,
                   help='本地注册表根目录（写入目标）')
    p.add_argument('--授权', dest='auth', required=True,
                   help='授权配置文件路径（授权.json）；改动无需重启即生效')
    p.add_argument('--监听', '--host', dest='host', default=DEFAULT_HOST,
                   help='监听地址，默认 %s' % DEFAULT_HOST)
    p.add_argument('--端口', '--port', dest='port', type=int,
                   default=DEFAULT_PORT, help='监听端口，默认 %d' % DEFAULT_PORT)
    p.add_argument('--审计', dest='audit', default=None,
                   help='审计日志路径；省略即不写审计')
    p.add_argument('--要求TLS转发', dest='require_tls', action='store_true',
                   help='POST %s 必须带 `X-Forwarded-Proto: https`，否则 403。'
                        '语义是「我确认自己只被反代访问」，不是鉴别真实 TLS'
                        % PUBLISH_PATH)
    p.add_argument('--强制解锁', dest='force_unlock', action='store_true',
                   help='启动前删掉既有 `%s`（崩溃留下陈旧锁时用）。'
                        '刻意不自动判活：pid 会复用' % LOCK_NAME)
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    # 抢锁在建服务**之前**：锁冲突就不该占端口，也不该读授权配置。
    try:
        锁 = 抢发布锁(args.registry, 强制解锁=args.force_unlock)
    except 锁冲突 as e:
        print('无法启动：%s' % e, file=sys.stderr)
        return 3

    try:
        try:
            srv = build_server(
                注册表=args.registry, 授权=args.auth,
                host=args.host, port=args.port, 审计路径=args.audit,
                要求TLS=args.require_tls)
        except _auth_mod.AuthError as e:
            print('授权配置错误：%s' % e, file=sys.stderr)
            return 2

        实际地址, 实际端口 = srv.server_address[0], srv.server_address[1]
        print('极快远程注册表服务端已启动：http://%s:%d/' % (实际地址, 实际端口),
              file=sys.stderr)
        print('提示：写端点是 POST %s，读端点走静态托管；'
              '本进程不做 TLS，请前置 nginx/Caddy。' % PUBLISH_PATH,
              file=sys.stderr)
        print('单写者锁：%s（正常退出会自动删除）' % LOCK_NAME, file=sys.stderr)
        if args.require_tls:
            print('已开 --要求TLS转发：缺 `X-Forwarded-Proto: https` 的 POST 一律 403。',
                  file=sys.stderr)
        横幅 = 危险绑定横幅(args.host, args.require_tls)
        if 横幅:
            print(横幅, file=sys.stderr)
            _LOG.warning('非回环绑定且未要求 TLS 转发：%s', args.host)

        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print('\n已停止。', file=sys.stderr)
        finally:
            srv.server_close()
    finally:
        # 无论怎么退（含 KeyboardInterrupt / SIGTERM 触发的 SystemExit）都要
        # 把锁删掉，否则下次启动得靠人敲 --强制解锁。
        释放发布锁(锁)
    return 0



if __name__ == '__main__':                              # pragma: no cover
    raise SystemExit(main())
