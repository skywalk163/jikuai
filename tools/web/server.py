# -*- coding: utf-8 -*-
"""极快 Web UI 通道 —— 本地开发服务（v0.15.0 W17/W18，v0.16.0 W31 可写化）。

三段式设计语言在 Web 上的落地：`[需求]→[候选]→[方案]→[源码]→[结果]`，与
`jk 块 选/组/跑` 完全同构，只是换了传输层。端点：

    GET    /api/blocks       → `stdlib/blocks/索引.json` 原文
    GET    /api/能力         → {神经可用, 索引版本, 块数}      能力探测（W19 前端用）
    POST   /api/选           → {需求, top?, 神经?}      → `选响应` {需求, 候选[, 降级说明]}
    POST   /api/组           → {方案} 或方案本体        → `组响应` {源码}
    POST   /api/跑           → {方案} 或方案本体        → `跑响应` {源码, 执行结果[, 需求]}
    POST   /api/方案/存      → {方案[, 标题]}           → {id, 标题, 时间戳}     （W31）
    GET    /api/方案/列      →                          → {方案列表:[{id,标题,时间戳}]}
    GET    /api/方案/<id>    →                          → {id, 标题, 时间戳, 方案}
    DELETE /api/方案/<id>    →                          → {id}

`/` 挂 `tools/web/static/` 的单页。

**安全边界（务必先读）**
------------------------
本服务**没有任何鉴权**，`/api/跑` 会在本机进程内执行调用方提交的极快代码
（等价于本地 RCE）。因此：

* 默认只监听 `127.0.0.1`，不要改成 `0.0.0.0`，不要反代到公网；
* 仅供本机单人开发调试使用，不是生产服务，也不是多租户服务；
* 任何需要暴露给他人的场景，请改走 CLI（`jk 块 跑`）或自建带沙箱的通道。

W31 引入了**写端点**，因此又多了三条硬约束（细节见 `README.md` §已做的安全处理）：

* 路径 id 走白名单正则 `ID_PATTERN`（`^[0-9a-f]{8,64}$`），非白名单一律 400；
* 落盘路径经 `abspath` 归一化后再确认前缀仍在 `plans_root()` 内（双重防护）；
* 单档 / 总量 / 条数三道体积闸，防「一次请求写满本地磁盘」。

**实现约束**：只用标准库（`http.server` + `json` + `logging` + `uuid`）。W17 DoD
明写不许新增 pip 依赖。协议字段一律取自 `jikuai.service.schema`（唯一真源，见
`docs/协议-三通道.md`），本通道**不自造字段**——`/api/能力` 的三个字段是
能力探测而非数据协议，不进 schema，只在协议文档里记一笔。
"""

import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    # 与 `tools/ai-bridge/glue.py` 同策略：桥接工具自己把 `src/` 挂上，
    # 免得强制用户先 `pip install -e .` 才能起 Web UI。
    sys.path.insert(0, _SRC)

from jikuai.service import schema                                  # noqa: E402

#: 协议字段名一律从 schema 常量取，本文件不写裸字面量（W20 硬门槛）。
#: 整元组解包而不是硬编码下标：协议真加了字段这里会当场 ValueError。
_F需求, _F共享, _F打印 = schema.PLAN_OPTIONAL
_F步骤 = schema.PLAN_REQUIRED[0]
_F块, _F领域, _F导出名 = schema.STEP_REQUIRED
_F参数, _F说明, _F命名空间 = schema.STEP_OPTIONAL
_F源码, _F执行结果 = schema.RUN_ENVELOPE_REQUIRED
#: `已存方案` 的字段名（W31）。`_F方案` 同时也是 `/api/组`、`/api/跑` 请求体
#: 里那个信封键——存档项与请求信封用的是同一个字段名，取自同一处常量。
_Fid, _F标题, _F时间戳, _F方案 = schema.SAVED_PLAN_REQUIRED
#: PUT /api/方案/<id> 请求体的额外字段（乐观锁标记）。这两个是响应/请求契约字段
#: 不是存档格式的一部分，所以**刻意**不进 `SAVED_PLAN_REQUIRED`——存档字节的
#: 版本 = sha256(存档字节) 前 16 位，是派生值而非落盘字段（见 `_版本标记`）。
_F版本 = '版本'
_F期望版本 = '期望版本'

__all__ = [
    'DEFAULT_HOST', 'DEFAULT_PORT', 'MAX_BODY', 'SAFETY_NOTICE',
    'PLANS_DIR_ENV', 'PLANS_DIR_HOME', 'ID_PATTERN',
    'MAX_PLAN_BYTES', 'MAX_STORE_BYTES', 'MAX_PLAN_COUNT', 'MAX_TITLE_CHARS',
    'plans_root', 'JiKuaiHandler', 'build_server', 'static_root', 'main',
]

_LOG = logging.getLogger('jikuai.web')

#: 默认只绑本机回环。改这个值等于把本地 RCE 开放给同网段，别改。
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 5000

#: 请求体上限 1 MiB。`rfile.read(Content-Length)` 会照着头部给的数字分配内存，
#: 不设上限就等于让任何请求方决定服务端的内存用量。
MAX_BODY = 1024 * 1024

#: 超限请求体的**排空**上限。超限后仍要把已经在路上的 body 读掉丢弃，否则
#: 413 响应发不到客户端手里（见 `JiKuaiHandler._排空`）。排空是分块丢弃，
#: 内存占用是常数；但也不能无限陪读，超过这个数就直接断。
MAX_DRAIN = 8 * 1024 * 1024

#: `top` 的上限。105 个块全返回也没意义，防止有人拿 `top=1e9` 找乐子。
MAX_TOP = 50

#: 方案存档目录的环境变量覆盖（测试用；也方便把存档挪到别处）。
#: 刻意用纯 ASCII 名字：Windows 上带中文的环境变量名在部分 shell 里传不进来。
PLANS_DIR_ENV = 'JIKUAI_WEB_PLANS_DIR'

#: 方案存档目录相对家目录的位置：`~/.jikuai/web-方案/`（与 `~/.jikuai/注册表`
#: 同一个家目录约定，见 `pkg/registry.py`）。
PLANS_DIR_HOME = ('.jikuai', 'web-方案')

#: 单个方案存档的字节上限（序列化后）。方案是一小段 JSON，64 KiB 已经很宽裕；
#: 设上限是为了防「一次请求写满磁盘」。
MAX_PLAN_BYTES = 64 * 1024

#: 存档目录的总字节上限。单个上限挡不住「存一万份合规大小的方案」，
#: 所以总量必须**另有**一道闸。
MAX_STORE_BYTES = 4 * 1024 * 1024

#: 存档条数上限。总字节数之外再加一道：一堆微小文件也会吃满 inode。
MAX_PLAN_COUNT = 200

#: 标题的码点上限。标题只是给人看的索引，超长的一律截断而不是报错
#: （用户在需求框里写长句是常态，为此拒绝保存太粗暴）。
MAX_TITLE_CHARS = 120

#: 方案 id 白名单。**严格锚定** `^...$`，只允许小写 hex，长度 8..64。
#: 这一条正则同时挡掉：`..`、`/`、`\`、盘符、百分号编码残留、绝对路径、
#: 超长名、Windows 保留名（`CON`/`NUL` 都含非 hex 字符）。
#: id 由服务端 `uuid4().hex`（32 位小写 hex）生成，天然落在白名单内。
ID_PATTERN = re.compile(r'^[0-9a-f]{8,64}$')


#: 启动时必打的中文风险提示（README 里同步有一份）。
SAFETY_NOTICE = (
    '⚠ 安全提示：本服务无鉴权，/api/跑 会在本机直接执行你提交的极快代码，'
    '仅供本地开发调试使用。请勿绑定 0.0.0.0、勿反向代理、勿暴露到公网。'
)

#: 粘合器给「参数没填上」留的记号（见 `glue.synthesize`）。`跑` 见到它就拒绝执行。
_占位记号 = '需人工填参'

#: 静态资源的 MIME 表。刻意只认这几种——静态目录里本来就只该有这几种文件，
#: 未知后缀一律 `application/octet-stream`，浏览器不会当脚本执行。
_MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
}

#: 盘符前缀（`C:...`）。Windows 上 `os.path.join(根, 'C:/x')` 会整段替换掉根，
#: 必须在 join 之前就拒掉。
_盘符 = re.compile(r'^[A-Za-z]:')

#: 解释器执行是进程级的（`redirect_stdout` 换的是全局 `sys.stdout`），
#: ThreadingHTTPServer 下必须串行化，否则并发两个 `跑` 会互相偷走对方的输出。
# redirect_stdout/stderr 替换全局 sys.stdout/stderr，故其它 handler 禁止 print；添加中间件时须避免 sys.stdout.write。
_执行锁 = threading.Lock()

#: 粘合器模块缓存。
_GLUE = None


# ---- 仓库内资源 --------------------------------------------------------

def static_root() -> str:
    """单页资源目录 `tools/web/static/` 的绝对路径。"""
    return os.path.join(_HERE, 'static')


def _index_path() -> str:
    """`stdlib/blocks/索引.json` 的绝对路径（走 `blocks.blocks_root()`，别自己拼）。"""
    from jikuai.pkg.blocks import blocks_root
    return os.path.join(blocks_root(), '索引.json')


def _glue():
    """按文件路径加载 `tools/ai-bridge/glue.py`。

    `tools/ai-bridge` 不是包，也不该是。与 `pkg/blocks_cli._glue()` 同一套做法：
    `importlib` 挂一个带命名空间前缀的模块名，既不污染 `sys.path`，也不会和
    别人的 `import glue` 撞车。
    """
    global _GLUE
    if _GLUE is not None:
        return _GLUE
    import importlib.util
    path = os.path.join(_REPO, 'tools', 'ai-bridge', 'glue.py')
    if not os.path.isfile(path):
        raise _请求错误('找不到粘合器 %s；组/跑 需要仓库内的 tools/ai-bridge/' % path)
    spec = importlib.util.spec_from_file_location('_jikuai_ai_bridge_glue', path)
    if spec is None or spec.loader is None:      # 理论上不可达，保底不抛裸异常
        raise _请求错误('粘合器 %s 无法作为模块加载' % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _GLUE = mod
    return mod


# ---- 错误分层 ----------------------------------------------------------

class _请求错误(Exception):
    """调用方的错（坏 JSON / 字段不合法 / 块不存在）→ 4xx + 中文原因。

    与「服务器内部错误」严格分开：前者是 400，后者才是 500。把两者混在一起
    的服务没法用——调用方分不清该改自己的请求还是该报 bug。
    """

    def __init__(self, 原因: str, 状态: int = 400):
        super().__init__(原因)
        self.原因 = 原因
        self.状态 = 状态


# ---- 业务：选 / 组 / 跑 -------------------------------------------------

def _取方案(body: dict) -> dict:
    """从请求体里取出方案。

    两种写法都收（README §端点 有说明）：

        {"方案": {"步骤": [...]}}      -- 信封式，推荐，未来好加平级选项
        {"步骤": [...]}                -- 方案本体，`jk 块 组 -` 的 JSON 直接贴

    判据是「有没有 `方案` 键」。取出后立刻过 `schema.ensure_plan`——协议校验
    在业务之前，不给粘合器喂半成品。
    """
    方案 = body.get('方案') if '方案' in body else body
    if not isinstance(方案, dict):
        raise _请求错误('「方案」必须是 JSON 对象，实际是 %s' % type(方案).__name__)
    try:
        schema.ensure_plan(方案)
    except schema.SchemaError as e:
        raise _请求错误(str(e))
    return _校验块存在(方案)


def _校验块存在(方案: dict) -> dict:
    """协议之外的语义校验：领域在白名单里、块目录真实存在。

    `schema.ensure_plan` 只管字段形状，管不到「这个块是不是真的有」。不在这里
    拦的话，glue 会照着不存在的块生成 `从 blocks.数据.不存在 导入 x`，等到
    执行期才炸——报错位置离病根太远。

    v0.20.0 W73：第三方块（步骤带非空 `命名空间`）要在 `extra_roots()` 里找
    `<根>/<命名空间>/<领域>/<块>/`，不再只查内置 `stdlib/blocks/`。此前硬编码
    内置根让「装得上、检索得到，但一 `组`/`跑` 就报块不存在」。
    """
    from jikuai.pkg.blocks import ALLOWED_DOMAINS, blocks_root, extra_roots
    for i, 步 in enumerate(方案.get(_F步骤) or [], 1):
        领域, 块 = 步.get(_F领域), 步.get(_F块)
        命名空间 = 步.get(_F命名空间) or ''
        if 领域 not in ALLOWED_DOMAINS:
            raise _请求错误('步骤 %d 的领域「%s」不在白名单（允许：%s）'
                          % (i, 领域, '/'.join(sorted(ALLOWED_DOMAINS))))
        if 命名空间:
            候选根 = [os.path.join(根, 命名空间, 领域, 块)
                    for 根 in extra_roots()]
            位置说明 = 'blocks/%s/%s' % (命名空间, 领域)
        else:
            候选根 = [os.path.join(blocks_root(), 领域, 块)]
            位置说明 = 'blocks/%s' % 领域
        if not any(os.path.isdir(p) for p in 候选根):
            raise _请求错误('步骤 %d 的块「%s」不存在：%s 下没有这个目录'
                          % (i, 块, 位置说明))
    return 方案


def 选(body: dict) -> dict:
    """`POST /api/选`：需求文本 → 候选列表。

    `神经: true` 才起 sidecar 子进程拉查询向量；拿不到就降级到启发式，并在
    响应里附 `降级说明`。降级不是失败，所以仍然是 200。
    """
    需求 = body.get(_F需求)
    if not isinstance(需求, str) or not 需求.strip():
        raise _请求错误('「需求」必须是非空字符串')
    需求 = 需求.strip()

    top = body.get('top', 5)
    if isinstance(top, bool) or not isinstance(top, int):
        raise _请求错误('「top」必须是整数')
    if not 1 <= top <= MAX_TOP:
        raise _请求错误('「top」必须在 1..%d 之间' % MAX_TOP)

    用神经 = body.get('神经', False)
    if not isinstance(用神经, bool):
        raise _请求错误('「神经」必须是布尔值')

    from jikuai.ai import retrieval
    查询向量 = None
    降级说明 = None
    if 用神经:
        from jikuai.ai import embed_client
        vec, why = embed_client.fetch_query_vector(
            需求, expected_dim=embed_client.index_dim())
        if vec is None:
            # 文案前缀走常量，与 CLI（blocks_cli）/ REPL（repl_session）同源。
            from jikuai.ai.embed_client import DEGRADE_PREFIX
            降级说明 = DEGRADE_PREFIX + why
        else:
            查询向量 = vec

    try:
        hits = retrieval.retrieve(需求, top=top, query_vector=查询向量)
    except retrieval.RetrievalError as e:
        # 维度不符只可能出在「索引与模型不同步」上。上面已经比对过 dim，
        # 走到这里说明索引在两次读之间被换掉了——算调用方环境问题，400。
        raise _请求错误(str(e))

    return schema.make_select_envelope(
        需求, [schema.candidate_from_hit(h) for h in hits], 降级说明=降级说明)


def 组(body: dict) -> dict:
    """`POST /api/组`：方案 → 极快源码。

    形状是单字段 `{源码: str}`（协议文档里的 `组响应`）。W20 复盘裁决：
    单字段响应不值得为它单立一份 schema 常量，键名从 `RUN_ENVELOPE_REQUIRED`
    借用即可——CLI/Web 两边都靠这一个字面量取值，不重复。
    """
    方案 = _取方案(body)
    glue = _glue()
    try:
        源码 = glue.synthesize(方案, 自动链式=True)
    except ValueError as e:
        raise _请求错误('粘合失败：%s' % e)
    return {_F源码: 源码}


def 能力() -> dict:
    """`GET /api/能力`：给前端点亮「神经」开关的能力探测（v0.15.0 W20）。

    契约（W19 前端按这个写，字段名别改）：

        {"神经可用": bool, "索引版本": str, "块数": int}

    - `神经可用` 判据 = sidecar 可解析出命令 **且** 向量索引能加载。两者都
      复用运行时现成的判据（`embed_client.resolve_command` /
      `retrieval.load_vector_index`），不自己 `os.path.isfile` 拍脑袋——
      自己拍会和真实检索路径的判据漂开：`resolve_command` 还认
      `JIKUAI_AI_EMBED_CMD` 覆盖，`load_vector_index` 还校验魔数与格式版本。
    - `索引版本` / `块数` 取自 `索引.json`（与 `GET /api/blocks` 同源）；
      索引读不到时给 `''` / `0`，而不是报错——能力探测本身不该失败。
    """
    from jikuai.ai import embed_client, retrieval
    神经可用 = (embed_client.resolve_command() is not None
             and retrieval.load_vector_index() is not None)
    版本, 块数 = '', 0
    try:
        with open(_index_path(), 'r', encoding='utf-8') as f:
            索引 = json.load(f)
        版本 = str(索引.get('版本') or '')
        块数 = len(索引.get('块') or [])
    except (OSError, ValueError):
        pass
    return {'神经可用': bool(神经可用), '索引版本': 版本, '块数': 块数}


def 跑(body: dict) -> dict:
    """`POST /api/跑`：方案 → 组 → 执行 → `schema.make_run_envelope`。

    业务失败（源码含占位符、解释器抛异常）走 `错误` 字段而不是 HTTP 5xx——
    「你的程序跑挂了」和「服务坏了」是两件事，混在一个状态码里前端没法处理。
    """
    方案 = _取方案(body)
    glue = _glue()
    try:
        源码 = glue.synthesize(方案, 自动链式=True)
    except ValueError as e:
        raise _请求错误('粘合失败：%s' % e)
    if _占位记号 in 源码:
        执行结果 = schema.make_result(
            错误='方案有步骤的参数未指定（源码里出现「%s」）；'
                '在「步骤」里补 `参数`，或换成类型图能自动链上的组合' % _占位记号)
        return schema.make_run_envelope(源码, 执行结果, 需求=方案.get(_F需求))
    return schema.make_run_envelope(源码, 执行源码(源码), 需求=方案.get(_F需求))


def 执行源码(源码: str) -> dict:
    """把源码落**临时** `.jk` 再交给解释器跑，返回 `schema` 执行结果。

    落临时文件而不直接 `run_source(源码)` 的理由同 `blocks_cli._执行源码`：
    模块解析要靠 `current_file` 定位搜索路径，给个真实路径最省事。

    与 CLI 版的差别：这里**同时**拦 stdout 与 stderr。诊断（`_report_diagnostics`）
    是打到 stderr 的，CLI 让它直接落终端就行，Web 层要是不拦，这些中文诊断会
    跑到服务进程的控制台里、前端一个字都看不到。
    """
    # 与 blocks_cli._执行源码 同源实现；不抽共享是因为 src/ 零第三方依赖约束禁止运行时包依赖 tools/。
    from jikuai.main import run_source

    fd, path = tempfile.mkstemp(prefix='jk_web跑_', suffix='.jk')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(源码)
        缓出, 缓错 = io.StringIO(), io.StringIO()
        错误 = None
        诊断 = None
        结果 = None
        with _执行锁:
            起 = time.perf_counter()
            try:
                with contextlib.redirect_stdout(缓出), \
                        contextlib.redirect_stderr(缓错):
                    结果 = run_source(源码, file=path)
            except Exception as e:                          # noqa: BLE001
                # 只回异常类名 + 消息，不回 traceback：栈里带着服务端的绝对
                # 路径与内部模块结构，对调用方没用，对攻击者有用。
                错误 = '%s：%s' % (type(e).__name__, e)
                # 带位置信息（JiKuaiError.info）时顺手填 `诊断`，前端才能把
                # 出错的行/列高亮出来（W19）；拿不到就保持 None，不伪造。
                # 提取逻辑下沉到 schema（CLI 与 Web 共用一份，不再各写一遍）。
                诊断 = schema.diagnostics_from_error(e)
            耗时 = (time.perf_counter() - 起) * 1000.0
        return schema.make_result(
            stdout=缓出.getvalue(), stderr=缓错.getvalue(),
            返回值='' if 错误 else repr(结果),
            耗时毫秒=耗时, 错误=错误, 诊断=诊断,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def 块索引() -> dict:
    """`GET /api/blocks`：`索引.json` 原文。"""
    path = _index_path()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except OSError as e:
        raise _请求错误('读不到块索引 %s：%s' % (path, e), 状态=503)
    except ValueError as e:
        raise _请求错误('块索引不是合法 JSON：%s' % e, 状态=503)


# ---- 方案存档（W31）---------------------------------------------------

def plans_root() -> str:
    """方案存档目录的绝对路径。

    环境变量 `JIKUAI_WEB_PLANS_DIR` 优先；否则用 `~/.jikuai/web-方案/`。
    环境变量给的是测试与迁移用的逃生舱，运行时并不鼓励改。
    """
    覆盖 = os.environ.get(PLANS_DIR_ENV)
    if 覆盖:
        return os.path.abspath(os.path.expanduser(覆盖))
    return os.path.abspath(
        os.path.join(os.path.expanduser('~'), *PLANS_DIR_HOME))


def _方案文件路径(id: str) -> str:
    """把 id 拼成落盘文件路径，同时做**双重**归一化校验。

    先走白名单正则（`ID_PATTERN` 已经严格锚定 `^[0-9a-f]{8,64}$`），再对
    join 后的路径做 `abspath` 归一化，最后确认前缀仍在 `plans_root()` 之内。
    单靠正则理论上够，但根目录经过 `expanduser` 里的软链接可能不是自己以为
    的那一层——第二步兜住这种意外。
    """
    if not isinstance(id, str) or not ID_PATTERN.match(id):
        raise _请求错误('方案 id 不合法（只允许 8..64 位小写 hex）', 状态=400)
    根 = os.path.abspath(plans_root())
    目标 = os.path.abspath(os.path.join(根, id + '.json'))
    # 归一化后的目标必须是「根目录下」的直接子文件。用带分隔符的前缀比较，
    # 避免 `/foo` 与 `/foobar` 误判成同一前缀。
    根前缀 = 根 + os.sep
    if not (目标.startswith(根前缀) and os.path.dirname(目标) == 根):
        raise _请求错误('方案 id 不合法（归一化后逃出存档目录）', 状态=400)
    return 目标


def _确保根目录() -> str:
    根 = plans_root()
    try:
        os.makedirs(根, exist_ok=True)
    except OSError as e:
        raise _请求错误('存档目录不可用：%s' % e, 状态=500)
    return 根


def _当前时间戳() -> str:
    """UTC ISO 8601 秒级时间戳。写字符串是为了跨语言易读、易排序。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _列存档() -> list:
    """扫存档目录，返回按时间戳倒序（新到旧）的元数据数组。

    坏 JSON、非白名单文件名一律跳过而不是把整个 `列` 打挂——一份坏文件不该
    让整份历史读不到。跳过的会以 warning 打到日志里。
    """
    根 = plans_root()
    if not os.path.isdir(根):
        return []
    条目 = []
    try:
        文件名列表 = os.listdir(根)
    except OSError as e:
        raise _请求错误('存档目录不可读：%s' % e, 状态=500)
    for 名 in 文件名列表:
        if not 名.endswith('.json'):
            continue
        id = 名[:-len('.json')]
        if not ID_PATTERN.match(id):
            continue
        路径 = os.path.join(根, 名)
        try:
            with open(路径, 'r', encoding='utf-8') as f:
                档 = json.load(f)
        except (OSError, ValueError) as e:
            _LOG.warning('跳过坏存档 %s：%s', 名, e)
            continue
        if not isinstance(档, dict):
            continue
        条目.append(schema.make_saved_plan_summary(
            id=str(档.get(_Fid) or id),
            标题=str(档.get(_F标题) or ''),
            时间戳=str(档.get(_F时间戳) or ''),
        ))
    # 时间戳字符串按 ISO 8601 天然可字典序排。倒序 = 新在前。
    条目.sort(key=lambda x: x.get(_F时间戳, ''), reverse=True)
    return 条目


def _目录总字节数(根: str) -> int:
    """存档目录下所有合规 .json 的字节数之和。用于总量上限检查。"""
    总 = 0
    try:
        for 名 in os.listdir(根):
            if not 名.endswith('.json'):
                continue
            id = 名[:-len('.json')]
            if not ID_PATTERN.match(id):
                continue
            try:
                总 += os.path.getsize(os.path.join(根, 名))
            except OSError:
                continue
    except OSError:
        return 0
    return 总


def _有效存档数(根: str) -> int:
    n = 0
    try:
        for 名 in os.listdir(根):
            if not 名.endswith('.json'):
                continue
            if ID_PATTERN.match(名[:-len('.json')]):
                n += 1
    except OSError:
        return 0
    return n


def _提标题(方案: dict) -> str:
    """从方案里挤出一个能给人看的标题。

    优先级：请求体明确给的 `标题` > 方案的 `需求` > 首步骤 `块` 名 > 空串
    （空串意味着 UI 里显示时用「（未命名）」占位）。截断到 `MAX_TITLE_CHARS`
    个码点——用户在需求框里写长句是常态，为长度拒收太粗暴。
    """
    从需求 = 方案.get(_F需求)
    if isinstance(从需求, str) and 从需求.strip():
        return 码点截断(从需求.strip(), MAX_TITLE_CHARS)
    步骤 = 方案.get(_F步骤) or []
    if 步骤 and isinstance(步骤, list):
        首 = 步骤[0]
        if isinstance(首, dict):
            块 = 首.get(_F块)
            if isinstance(块, str) and 块:
                return 码点截断(块, MAX_TITLE_CHARS)
    return ''


def 码点截断(s: str, 上限: int) -> str:
    """按码点（`Array.from` 口径）截断，保持与前端一致。"""
    cps = [c for c in s]
    if len(cps) <= 上限:
        return s
    return ''.join(cps[:上限])


def 方案_存(body: dict) -> dict:
    """`POST /api/方案/存`：把方案存到 `~/.jikuai/web-方案/<id>.json`。

    请求体接受两种写法（与 `/api/组`、`/api/跑` 同款）：
        {"方案": {...}}   -- 信封式，推荐
        {"方案": {...}, "标题": "自定义"}
    直接把方案本体当 body 也收，标题走 `_提标题` 兜底。

    id 由服务端 `uuid4().hex` 生成——不接受调用方指定的 id，避免手写 id
    落进白名单外或覆盖别人已存的方案。
    """
    if not isinstance(body, dict):
        raise _请求错误('请求体必须是对象')
    方案原始 = body.get(_F方案) if _F方案 in body else body
    if not isinstance(方案原始, dict):
        raise _请求错误('「方案」必须是 JSON 对象')
    try:
        schema.ensure_plan(方案原始)
    except schema.SchemaError as e:
        raise _请求错误(str(e))
    方案 = _校验块存在(方案原始)

    自定义标题 = body.get(_F标题) if _F方案 in body else None
    if 自定义标题 is not None and not isinstance(自定义标题, str):
        raise _请求错误('「标题」必须是字符串')
    标题 = 码点截断((自定义标题 or '').strip(), MAX_TITLE_CHARS) or _提标题(方案)

    根 = _确保根目录()
    # 提前拦总量：写完再删的策略会在磁盘满时把用户已有的方案挤丢。
    if _有效存档数(根) >= MAX_PLAN_COUNT:
        raise _请求错误('存档条数已达上限 %d，请先删旧的' % MAX_PLAN_COUNT,
                      状态=413)

    id = uuid.uuid4().hex          # 32 位小写 hex，天然过白名单
    时间戳 = _当前时间戳()
    存档 = schema.make_saved_plan(id=id, 标题=标题, 时间戳=时间戳, 方案=方案)
    数据 = json.dumps(存档, ensure_ascii=False).encode('utf-8')
    if len(数据) > MAX_PLAN_BYTES:
        raise _请求错误('方案序列化后 %d 字节，超过单档上限 %d 字节'
                      % (len(数据), MAX_PLAN_BYTES), 状态=413)
    if _目录总字节数(根) + len(数据) > MAX_STORE_BYTES:
        raise _请求错误('存档总量已达上限 %d 字节，请先删旧的' % MAX_STORE_BYTES,
                      状态=413)

    目标 = _方案文件路径(id)
    _原子写(_确保根目录(), 目标, 数据)
    return schema.make_saved_plan_summary(id=id, 标题=标题, 时间戳=时间戳)


def 方案_列() -> dict:
    """`GET /api/方案/列`：返回 `{方案列表:[{id,标题,时间戳}, ...]}`（新在前）。"""
    return schema.make_saved_plan_list(_列存档())


def _版本标记(数据: bytes) -> str:
    """存档字节的乐观锁标记（ETag 语义），取 sha256 前 16 位 hex。

    **为什么不用 WBS 原文的「`修改时间` 做乐观锁」**：`_当前时间戳()` 是
    **秒级**的（`microsecond=0`），同一秒内的两次更新会拿到相同时间戳，
    时间戳比对会把「别人刚改过」误判成「没人改过」而静默丢失更新——
    这正是乐观锁要防的那件事。内容摘要没有时钟粒度问题。

    标记是**派生值不落盘**：`SAVED_PLAN_REQUIRED` 一个字段都不用动，
    存档格式零 Breaking Change。代价是 `方案_取` 的响应比存档文件多一个
    `版本` 字段（响应契约的加法，不是存储格式的改动）。
    """
    return hashlib.sha256(数据).hexdigest()[:16]


def _读存档(id: str):
    """读存档，返回 `(档dict, 原始字节)`。不存在 → 404，坏格式 → 500。"""
    目标 = _方案文件路径(id)
    try:
        with open(目标, 'rb') as f:
            原始 = f.read()
    except FileNotFoundError:
        raise _请求错误('方案不存在：%s' % id, 状态=404)
    except OSError as e:
        raise _请求错误('存档读取失败：%s' % e, 状态=500)
    try:
        档 = json.loads(原始.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as e:
        raise _请求错误('存档读取失败：%s' % e, 状态=500)
    if not isinstance(档, dict) or _F方案 not in 档:
        raise _请求错误('存档格式错误：缺少「方案」', 状态=500)
    return 档, 原始


def 方案_取(id: str) -> dict:
    """`GET /api/方案/<id>`：取单个存档（含方案本体）+ 派生的 `版本` 乐观锁标记。"""
    档, 原始 = _读存档(id)
    档 = dict(档)
    档[_F版本] = _版本标记(原始)
    return 档


def _原子写(根: str, 目标: str, 数据: bytes) -> None:
    """先落同目录临时文件再 `os.replace` 覆盖，防止半写状态被读到。"""
    tmp_fd, tmp_path = tempfile.mkstemp(prefix='.tmp_', suffix='.json', dir=根)
    try:
        with os.fdopen(tmp_fd, 'wb') as f:
            f.write(数据)
        os.replace(tmp_path, 目标)
    except OSError as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise _请求错误('落盘失败：%s' % e, 状态=500)


def 方案_更新(id: str, body: dict) -> dict:
    """`PUT /api/方案/<id>`：覆盖式原地更新（W46）。

    请求体::

        {"方案": {...}, "期望版本": "<GET 时拿到的版本>", "标题": "可选"}

    **`期望版本` 是必需的**，缺了直接 400 而不是「没给就当无冲突」——
    静默覆盖是这个端点唯一不能犯的错。版本不符回 **409**，响应里带
    `当前版本` 与 `时间戳`，让前端能提示用户「别处改过了，要不要重载」。

    id 白名单 + abspath 双重校验完全走 `_方案文件路径`（W31 安全基线，
    一个字不放松）；PUT **不创建新存档**——id 不存在回 404，避免调用方
    自造 id 往目录里塞文件。
    """
    if not isinstance(body, dict):
        raise _请求错误('请求体必须是对象')
    if _F期望版本 not in body:
        raise _请求错误('缺少「%s」——原地更新必须带 GET 时拿到的版本，'
                      '否则无法判断存档是否已被别处改过' % _F期望版本)
    期望版本 = body.get(_F期望版本)
    if not isinstance(期望版本, str) or not 期望版本:
        raise _请求错误('「%s」必须是非空字符串' % _F期望版本)

    方案原始 = body.get(_F方案)
    if not isinstance(方案原始, dict):
        raise _请求错误('「方案」必须是 JSON 对象')
    try:
        schema.ensure_plan(方案原始)
    except schema.SchemaError as e:
        raise _请求错误(str(e))
    方案 = _校验块存在(方案原始)

    自定义标题 = body.get(_F标题)
    if 自定义标题 is not None and not isinstance(自定义标题, str):
        raise _请求错误('「标题」必须是字符串')

    旧档, 旧原始 = _读存档(id)          # 不存在 → 404
    当前版本 = _版本标记(旧原始)
    if 当前版本 != 期望版本:
        raise _请求错误(
            '方案「%s」已被别处修改（期望版本 %s，当前 %s）——'
            '请重新载入后再保存，或另存为新方案'
            % (id, 期望版本, 当前版本), 状态=409)

    标题 = (码点截断((自定义标题 or '').strip(), MAX_TITLE_CHARS)
            or 旧档.get(_F标题) or _提标题(方案))
    时间戳 = _当前时间戳()
    存档 = schema.make_saved_plan(id=id, 标题=标题, 时间戳=时间戳, 方案=方案)
    数据 = json.dumps(存档, ensure_ascii=False).encode('utf-8')
    if len(数据) > MAX_PLAN_BYTES:
        raise _请求错误('方案序列化后 %d 字节，超过单档上限 %d 字节'
                      % (len(数据), MAX_PLAN_BYTES), 状态=413)
    根 = _确保根目录()
    # 更新不新增条数，总量按「减旧加新」算——照 `方案_存` 的口径会把旧档重复计一次
    if _目录总字节数(根) - len(旧原始) + len(数据) > MAX_STORE_BYTES:
        raise _请求错误('存档总量已达上限 %d 字节，请先删旧的' % MAX_STORE_BYTES,
                      状态=413)

    _原子写(根, _方案文件路径(id), 数据)
    结果 = schema.make_saved_plan_summary(id=id, 标题=标题, 时间戳=时间戳)
    结果 = dict(结果)
    结果[_F版本] = _版本标记(数据)      # 回新版本，前端可继续连续更新
    return 结果


def 方案_删(id: str) -> dict:
    """`DELETE /api/方案/<id>`：删单个存档。幂等——已不在也回 200。"""
    目标 = _方案文件路径(id)
    try:
        os.unlink(目标)
    except FileNotFoundError:
        raise _请求错误('方案不存在：%s' % id, 状态=404)
    except OSError as e:
        raise _请求错误('删除失败：%s' % e, 状态=500)
    return {_Fid: id}



#: POST 路由表。键是 URL path，值是 `(dict) -> dict`。
_POST路由 = {
    '/api/选': 选,
    '/api/组': 组,
    '/api/跑': 跑,
    '/api/方案/存': 方案_存,
}

#: 方案的固定 path 匹配（`/api/方案/列` 是 GET 的**枚举**端点，与 `/api/方案/<id>`
#: 是不同资源；不能混用同一段前缀分派，否则 id=`列` 就会歧义）。
_方案列路径 = '/api/方案/列'
_方案id前缀 = '/api/方案/'

#: `<id>` 占位形态的规范写法——单资源端点在协议文档里写作 `/api/方案/<id>`，
#: 与下面的路由清单口径一致（G16 门禁按此比对）。
_方案id端点 = _方案id前缀 + '<id>'

#: 非 POST 三方法的路由清单（W55 · G16 单一真源）。**既供 404 文案枚举、也供
#: `scripts/check_protocol_doc.py` 比对文档**——两处共用一份，杜绝「文案/校验/
#: 实际分派」三份各写一遍再漂开。GET 的两个固定端点 + 方案枚举 + 方案单资源；
#: PUT / DELETE 只服务方案单资源。POST 见 `_POST路由`（真字典自成清单）。
_GET路由 = ('/api/blocks', '/api/能力', _方案列路径, _方案id端点)
_PUT路由 = (_方案id端点,)
_DELETE路由 = (_方案id端点,)


# ---- HTTP 层 -----------------------------------------------------------

class JiKuaiHandler(BaseHTTPRequestHandler):
    """极快 Web UI 的请求处理器。

    只实现 GET / POST。`BaseHTTPRequestHandler` 对未实现的方法会自动回 501，
    不用自己写。
    """

    server_version = 'JiKuaiWeb/0.16.0'
    protocol_version = 'HTTP/1.1'      # 要 Content-Length 精确才敢开长连接

    # -- 输出辅助 --

    def _发送(self, 状态: int, 数据: bytes, 类型: str,
             额外头=None, 关闭: bool = False) -> None:
        self.send_response(状态)
        self.send_header('Content-Type', 类型)
        self.send_header('Content-Length', str(len(数据)))
        # 单页不需要被嵌进别人的 iframe，也不需要浏览器猜 MIME。
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        if 关闭:
            self.send_header('Connection', 'close')
        for 名, 值 in (额外头 or {}).items():
            self.send_header(名, 值)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(数据)
        if 关闭:
            self.close_connection = True

    def _发JSON(self, 状态: int, 对象, 关闭: bool = False) -> None:
        数据 = json.dumps(对象, ensure_ascii=False).encode('utf-8')
        self._发送(状态, 数据, 'application/json; charset=utf-8', 关闭=关闭)

    def _发错误(self, 状态: int, 原因: str, 关闭: bool = False) -> None:
        """错误也走 JSON —— 前端只需要认一种响应形状。"""
        self._发JSON(状态, {'错误': 原因}, 关闭=关闭)

    # -- 读请求体 --

    def _排空(self, 长度: int) -> None:
        """把超限/不要的请求体分块读掉丢弃。

        为什么不能省：客户端还在往连接里灌 1 MiB body 的时候，我们直接回
        413 + 关连接，TCP 侧会给客户端一个 RST —— 客户端拿到的是
        `ConnectionAbortedError` 而不是我们那句中文 413，错误消息白写。
        分块丢弃的内存占用是常数（64 KiB），`MAX_BODY` 要保护的正是这个。
        超过 `MAX_DRAIN` 就不再陪读，那已经属于滥用。
        """
        余 = min(长度, MAX_DRAIN)
        while 余 > 0:
            try:
                块 = self.rfile.read(min(65536, 余))
            except OSError:
                return
            if not 块:
                return
            余 -= len(块)

    def _读body(self) -> dict:
        """读并解析 JSON 请求体。任何问题抛 `_请求错误`。"""
        raw = self.headers.get('Content-Length')
        if raw is None:
            raise _请求错误('缺少 Content-Length 头')
        try:
            长度 = int(raw)
        except ValueError:
            raise _请求错误('Content-Length 不是整数：%r' % raw)
        if 长度 < 0:
            raise _请求错误('Content-Length 不能为负')
        if 长度 > MAX_BODY:
            self._排空(长度)
            raise _请求错误('请求体 %d 字节，超过上限 %d 字节'
                          % (长度, MAX_BODY), 状态=413)
        数据 = self.rfile.read(长度) if 长度 else b''
        if len(数据) != 长度:
            raise _请求错误('请求体不完整：声明 %d 字节，实收 %d 字节'
                          % (长度, len(数据)))
        try:
            文本 = 数据.decode('utf-8')
        except UnicodeDecodeError as e:
            raise _请求错误('请求体不是合法 UTF-8：%s' % e)
        try:
            body = json.loads(文本) if 文本.strip() else None
        except ValueError as e:
            raise _请求错误('请求体不是合法 JSON：%s' % e)
        if not isinstance(body, dict):
            raise _请求错误('请求体必须是 JSON 对象（得到 %s）'
                          % ('空' if body is None else type(body).__name__))
        return body

    # -- 静态文件 --

    def _解析静态路径(self, 请求路径: str) -> str:
        """把 URL path 映射到 `static/` 下的真实文件，越界抛 `_请求错误`。

        三道闸：
          1. 拒绝盘符前缀与绝对路径（`os.path.join` 遇到它们会整段丢弃前缀）；
          2. `realpath` 归一化（吃掉 `..`，并且**跟随符号链接**）；
          3. `commonpath` 确认归一化后的真实路径仍在 `static/` 真实路径之内。

        第 2 步用 `realpath` 而不是 `normpath` 是关键：`normpath` 只做字符串
        运算，`static/link → /etc` 这种符号链接逃逸它拦不住。
        """
        路径 = urllib.parse.unquote(请求路径)
        if 路径 in ('', '/'):
            路径 = '/index.html'
        相对 = 路径.lstrip('/')
        if not 相对:
            raise _请求错误('路径不合法', 状态=403)
        if os.path.isabs(相对) or _盘符.match(相对):
            raise _请求错误('拒绝绝对路径', 状态=403)
        根 = os.path.realpath(static_root())
        目标 = os.path.realpath(os.path.join(根, 相对))
        try:
            if os.path.commonpath([根, 目标]) != 根:
                raise _请求错误('拒绝越界访问', 状态=403)
        except ValueError:
            # 跨盘（`C:` vs `D:`）时 commonpath 直接抛 ValueError —— 那必然越界。
            raise _请求错误('拒绝越界访问', 状态=403)
        if not os.path.isfile(目标):
            raise _请求错误('找不到 %s' % 请求路径, 状态=404)
        return 目标

    def _发静态(self, 请求路径: str) -> None:
        目标 = self._解析静态路径(请求路径)
        try:
            with open(目标, 'rb') as f:
                数据 = f.read()
        except OSError as e:
            raise _请求错误('读不到静态文件：%s' % e, 状态=500)
        类型 = _MIME.get(os.path.splitext(目标)[1].lower(),
                       'application/octet-stream')
        self._发送(200, 数据, 类型)

    # -- 路由 --

    def do_GET(self) -> None:
        self._分发(self._GET)

    def do_HEAD(self) -> None:
        self._分发(self._GET)

    def do_POST(self) -> None:
        self._分发(self._POST)

    def do_PUT(self) -> None:
        self._分发(self._PUT)

    def do_DELETE(self) -> None:
        self._分发(self._DELETE)

    def _取方案id(self, 路径: str) -> str:
        """从 `/api/方案/<id>` 里抠出 id 段。

        不在这里做白名单校验——校验统一由 `_方案文件路径` 一处负责，避免两处
        规则漂开。这里只负责「切出来」，切出来的可能是 `../../etc/passwd`。
        """
        return 路径[len(_方案id前缀):]

    def _GET(self) -> None:
        原始路径 = urllib.parse.urlsplit(self.path).path
        路径 = urllib.parse.unquote(原始路径)
        if 路径 == '/api/blocks':
            self._发JSON(200, 块索引())
        elif 路径 == '/api/能力':
            self._发JSON(200, 能力())
        elif 路径 == _方案列路径:
            self._发JSON(200, 方案_列())
        elif 路径.startswith(_方案id前缀):
            self._发JSON(200, 方案_取(self._取方案id(路径)))
        elif 路径.startswith('/api/'):
            raise _请求错误('未知端点 %s（GET 只有 %s）'
                          % (路径, '、'.join(_GET路由)), 状态=404)
        else:
            # 静态走**原始**（未 unquote）路径：`_解析静态路径` 内部只 unquote 一次，
            # 提前 unquote 会让 `%252e` 这类二次编码逃逸绕过校验。
            self._发静态(原始路径)

    def _POST(self) -> None:
        路径 = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        处理 = _POST路由.get(路径)
        if 处理 is None:
            raise _请求错误('未知端点 %s（POST 只有 %s）'
                          % (路径, '、'.join(sorted(_POST路由))), 状态=404)
        self._发JSON(200, 处理(self._读body()))

    def _DELETE(self) -> None:
        """DELETE 只服务 `/api/方案/<id>`，其余一律 404。"""
        路径 = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if 路径 == _方案列路径 or not 路径.startswith(_方案id前缀):
            raise _请求错误('未知端点 %s（DELETE 只有 %s<id>）'
                          % (路径, _方案id前缀), 状态=404)
        self._发JSON(200, 方案_删(self._取方案id(路径)))

    def _PUT(self) -> None:
        """PUT 只服务 `/api/方案/<id>`（W46 原地更新）。其余 404。"""
        路径 = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if 路径 == _方案列路径 or not 路径.startswith(_方案id前缀):
            raise _请求错误('未知端点 %s（PUT 只有 %s<id>）'
                          % (路径, _方案id前缀), 状态=404)
        self._发JSON(200, 方案_更新(self._取方案id(路径), self._读body()))

    def _分发(self, 处理) -> None:
        """统一的异常边界：调用方的错 → 4xx，我们的错 → 500。

        刻意**不**吞异常后回 200 空壳：那样前端拿到一份看着正常的空响应，
        排查成本比一个诚实的 500 高得多。
        """
        try:
            处理()
        except _请求错误 as e:
            # 413 之后客户端可能还在往连接里灌 body，长连接会读串；直接断。
            self._发错误(e.状态, e.原因, 关闭=(e.状态 == 413))
        except Exception as e:                                 # noqa: BLE001
            _LOG.exception('处理 %s %s 时内部错误', self.command, self.path)
            self._发错误(500, '服务器内部错误：%s：%s' % (type(e).__name__, e))

    # -- 日志 --

    def log_message(self, fmt: str, *args) -> None:
        """访问日志走 `logging`，别污染 stdout。

        默认实现直接 `sys.stderr.write`，在被当库嵌入（比如测试里）时会把
        噪声灌满 pytest 的捕获缓冲。交给 logging 后，调用方可以自己决定级别。
        """
        _LOG.info('%s - %s', self.address_string(), fmt % args)

    def log_error(self, fmt: str, *args) -> None:
        _LOG.warning('%s - %s', self.address_string(), fmt % args)


def build_server(host: str = DEFAULT_HOST,
                 port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """建一个未启动的服务实例。

    `port=0` 让内核分配空闲端口（测试用；写死 5000 会因为端口被占而 flaky）。
    实际端口在 `server.server_address[1]`。
    """
    return ThreadingHTTPServer((host, port), JiKuaiHandler)


def main(argv=None) -> int:
    """启动 Web UI 本地开发服务。返回退出码（0 正常 / 非 0 异常）。"""
    p = argparse.ArgumentParser(
        description='极快 Web UI 本地开发服务（无鉴权，仅限本机）')
    p.add_argument('--地址', '--host', dest='host', default=DEFAULT_HOST,
                   help='监听地址，默认 %s（不要改成 0.0.0.0）' % DEFAULT_HOST)
    p.add_argument('--端口', '--port', dest='port', type=int,
                   default=DEFAULT_PORT, help='监听端口，默认 %d' % DEFAULT_PORT)
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    srv = build_server(args.host, args.port)
    实际地址, 实际端口 = srv.server_address[0], srv.server_address[1]
    print('极快 Web UI 已启动：http://%s:%d/' % (实际地址, 实际端口), file=sys.stderr)
    print(SAFETY_NOTICE, file=sys.stderr)
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        print('⚠ 你把监听地址设成了 %s —— 这台机器所在网络里的任何人都能'
              '在你的机器上执行任意代码。确定要这样？' % args.host, file=sys.stderr)

    # 预热：在 serve_forever 之前把惰性单例初始化完毕，消除首个请求的
    # 竞态窗口（多线程 handler 同时触发 _glue / _cached_retriever / _LEVELS）。
    # 预热失败不阻断启动——首请求自己再惰性初始化一次只是慢一点，比服务起不来好。
    # `retrieval` 的 import 也放在 try 内：它同样可能失败（缺依赖 / 索引损坏），
    # 而 import 炸在 try 外就变成裸崩，起不了服务。
    try:
        from jikuai.ai import retrieval
        _glue()
        retrieval.retrieve('预热', top=1)
        schema.level_table()
    except Exception as e:                                  # noqa: BLE001
        _LOG.warning('预热失败，将退化为按需初始化：%s', e)

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。', file=sys.stderr)
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
