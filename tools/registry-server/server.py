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
"""

import argparse
import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sys
import tarfile
import tempfile
import threading
import urllib.parse
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
    'DEFAULT_HOST', 'DEFAULT_PORT',
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

def _make_handler(注册表根, 授权源, 审计路径, 写锁, max_body):
    """闭包出一个绑定运行时状态的 `BaseHTTPRequestHandler` 子类。

    不用类属性/全局：便于测试起多个实例互不干扰。

    `授权源` 是 `AuthProvider`（不是 `AuthConfig`）——每次处理写请求时向它
    要一份当前配置，从而支持 `授权.json` 的热重载（ADR-36 §2.3）。
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

def build_server(注册表, 授权, host=DEFAULT_HOST, port=DEFAULT_PORT,
                 审计路径=None, max_body=None):
    """建一个未启动的服务实例。测试与 CLI 都走这个入口。

    - `注册表`：本地注册表根目录（远程 URL 不支持—— ADR-34 §2.1 明文本地）。
    - `授权`：`AuthConfig` 实例或 `授权.json` 路径。
    - `审计路径`：可选。省略即不写审计日志。
    - `max_body`：请求体上限，`None` 时读环境变量或用默认 32 MiB。
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
    处理器 = _make_handler(根, 授权源, 审计路径, 写锁, max_body)
    srv = ThreadingHTTPServer((host, port), 处理器)
    # 挂到实例上，供 CLI 打横幅与测试断言重载次数用。
    srv.授权源 = 授权源
    return srv



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
    """CLI 入口。返回退出码（0 正常 / 非 0 异常）。"""
    p = argparse.ArgumentParser(
        description='极快远程注册表服务端（ADR-35）')
    p.add_argument('--注册表', dest='registry', required=True,
                   help='本地注册表根目录（写入目标）')
    p.add_argument('--授权', dest='auth', required=True,
                   help='授权配置文件路径（授权.json）')
    p.add_argument('--监听', '--host', dest='host', default=DEFAULT_HOST,
                   help='监听地址，默认 %s' % DEFAULT_HOST)
    p.add_argument('--端口', '--port', dest='port', type=int,
                   default=DEFAULT_PORT, help='监听端口，默认 %d' % DEFAULT_PORT)
    p.add_argument('--审计', dest='audit', default=None,
                   help='审计日志路径；省略即不写审计')
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    try:
        srv = build_server(
            注册表=args.registry, 授权=args.auth,
            host=args.host, port=args.port, 审计路径=args.audit)
    except _auth_mod.AuthError as e:
        print('授权配置错误：%s' % e, file=sys.stderr)
        return 2

    实际地址, 实际端口 = srv.server_address[0], srv.server_address[1]
    print('极快远程注册表服务端已启动：http://%s:%d/' % (实际地址, 实际端口),
          file=sys.stderr)
    print('提示：写端点是 POST %s，读端点走静态托管；无 TLS，请前置 nginx/Caddy。'
          % PUBLISH_PATH, file=sys.stderr)

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。', file=sys.stderr)
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':                              # pragma: no cover
    raise SystemExit(main())
