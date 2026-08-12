# ADR-34：远程 HTTP 注册表 + token 鉴权

- 状态：**已实施**（v0.20.0 W77-W80）
- 日期：2026-08-12
- 阶段：v0.20.0 · M20（W77-W80）
- 关联：`docs/ADR-33-包签名.md`、`docs/ADR-27-第三方块注册表.md`、`docs/包管理.md`、`src/jikuai/pkg/registry.py`、`src/jikuai/pkg/sources.py`、`src/jikuai/pkg/manifest.py`、`docs/路线图-v0.19-v0.20.md`
- 前置事实：M19 包签名已实施（Ed25519 纯标准库 + TOFU 信任库 + 装包端三道检查）；注册表/发布/安装本地闭环；HTTP 分发是签名的下游，签名已就绪

---

## 1. 背景与问题

M19 把「可信」立起来了：装包端验校验和 + 验签 + TOFU pin。但注册表至今
**只能跨不了机**——`sources._fetch_registry` 调 `registry.lookup()` 读的是
`JIKUAI_REGISTRY` / `~/.jikuai/注册表` 下的**本地文件系统索引**，`registry.py`
每个函数都直接 `os.path.join(root, ...)` + `open()`。

要跨机分发，注册表根必须能是 `https://...`。这带来三个必须一起解的问题：

1. **读端抽象**：`load_index` / `lookup` / `lookup_signature` 全绑死本地路径，
   HTTP 下 `os.path.join` 直接失效。
2. **快照传输**：本地 `lookup` 返回一个**目录路径**；HTTP 下服务端得把快照打成
   一个可下载的归档，客户端下完解压到临时目录再走既有安装流程。
3. **鉴权**：私有注册表要能拦未授权的读/写。

M19 的签名在这里发挥前置作用：HTTP 传输**不需要**信任传输通道本身——包内容
由签名背书，中间人改一个字节就验签失败。TLS 只需兜「首次拉公钥」（TOFU 的
薄弱点，ADR-33 §5 已记）。

---

## 2. 决策

### 2.1 范围切分：本轮只做**装包端**远程读，发布端延后

M20 只实现 **HTTP 读**（`装` / `搜` / `列表` 能指向远程注册表），**发布仍是
本地操作**（`jk 包 发布` 只写本地注册表根，管理员另行同步到远端静态托管）。

**为什么这样切**：

- 远程**读**是零信任的——服务端只是静态文件托管（`GET /索引.json` 等），
  加上签名验证，安全边界清晰、可测。
- 远程**写**（`POST /publish` + token 鉴权 + 服务端并发/覆盖/配额策略）是另一
  个量级的工程：要有真正的服务端进程、鉴权中间件、审计。塞进 M20 会让这个
  里程碑失焦。
- **静态托管即可跑通读端**：把本地 `发布` 出来的注册表根目录整个丢到任何 HTTP
  静态服务器（nginx / GitHub Pages / OSS 桶）就是一个可用的远程注册表。这让
  M20 的产出**当天可用**，不必等服务端。

**但 ADR-34 预留发布端接口**（§2.6），避免 M21+ 做远程发布时推翻本轮抽象。

### 2.2 后端抽象：`RegistryBackend` 协议

在 `registry.py` 现有 `_read_json` / `_write_json` 之下引入后端抽象：

```
RegistryBackend（协议）
  read_text(相对路径) -> str          # 读一个注册表内文件（索引/分片/公钥）
  read_bytes(相对路径) -> bytes       # 读二进制（快照归档）
  exists(相对路径) -> bool
  # 写操作只在 LocalBackend 实现；HttpBackend 抛 UnsupportedOperation
  write_text(相对路径, 文本)
  ...
```

- `LocalBackend`：包住现有 `os.path.join(root, ...)` + `open()` 逻辑，
  行为与今天逐字节一致（回归必须全绿）。
- `HttpBackend`：`urllib.request` 实现 `read_*`，`GET <base_url>/<相对路径>`；
  写操作抛 `UnsupportedOperation`（本轮发布端不走 HTTP）。

**关键约束**：路径拼接逻辑**收敛进 backend**。今天散落在 `_index_path` /
`_category_path` / `_package_path` / `registry_key_path` 的 `os.path.join`
改为「产出相对路径 + 交给 backend 解析」。`_ensure_within` 的路径逃逸防护
在 `LocalBackend` 内保留；`HttpBackend` 用 URL 拼接不涉及本地逃逸，但要防
`..` 段注入远程路径。

### 2.3 网络实现：只用 `urllib.request`（零依赖底线）

`src/jikuai/` 运行时零第三方 pip 依赖是 v0.16.0 起的硬约束（ADR-33 §2.1 同）。
HTTP 客户端**只能用标准库 `urllib.request`**，不引 `requests` / `httpx`。
`examples/scenarios/推理演示/智言.py` 已证明这是项目可接受的模式。

- 超时：所有请求带显式 `timeout`（默认 30s，`JIKUAI_REGISTRY_TIMEOUT` 覆盖），
  不允许无限等待。
- 仅 `https://`（明文 `http://` 只在 `JIKUAI_REGISTRY_INSECURE=1` 时放行，
  给内网/测试用；默认拒，日志 stderr 告警）。
- 错误映射：404 → `RegistryError(包/版本不存在)`；401/403 → `RegistryError(鉴权失败)`；
  超时/连接失败 → `RegistryError(网络不可达)`。绝不把 `urllib` 的
  `HTTPError`/`URLError` 泄漏到 CLI。

### 2.4 快照传输：**tar.gz**

服务端每个 `包/<名称>/<版本>/` 目录对应一个 `包/<名称>/<版本>.tar.gz`。
客户端 `GET` 归档 → 流式下载到临时文件 → `tarfile` 解压到
`tempfile.mkdtemp()` → 返回 `FetchedSource(..., ephemeral=True)`，安装完由
installer 清理（与 `_fetch_git` 的临时目录生命周期一致）。

**为什么 tar.gz 而非 zip**：

- `tarfile` / `gzip` 都是标准库，零依赖。
- 与 `_fetch_git` clone 出目录的语义对齐——两条远程路径产出同构的临时目录，
  下游安装逻辑不必分叉。
- **Windows tar 路径 bug 的教训**（历史记录）用**解压时校验**兜：拒绝任何
  成员路径含 `..`、绝对路径、或解压后逃出目标目录的归档（`tarfile` 的
  `data_filter`，Python 3.12+ 有内置；3.10/3.11 手写等价校验，因为
  `requires-python >= 3.10`）。

**校验时机**：解压后**立刻**重算 `compute_checksum`，与索引 `校验和` 比对，
再验签——与本地路径**完全复用 M19 的 `installer._verify_registry_signature`**，
不为 HTTP 单开一套验证。归档本身不签名，签名对象仍是校验和字符串（ADR-33 §2.2）。

### 2.5 多注册表：全局默认 + per-dependency override（两层）

**两层都做**：

- **全局默认**：`JIKUAI_REGISTRY` 既可以是本地路径也可以是 `https://...`。
  未指定 override 的依赖走这个。
- **per-dependency override**：`包.json` 依赖新增规格形态
  ```json
  { "注册表": "https://reg.example.com", "版本": "^1.2.0" }
  ```
  该依赖强制走指定注册表，忽略全局默认。

**为什么两层都要**：单靠全局默认，混用公共 + 私有注册表时无法表达「这个包从
私有源、那个包从公共源」；单靠 per-dependency，每个依赖都要写全 URL 太啰嗦。
两层是 npm/cargo 等成熟生态的既定实践。

**`Dependency` 类改动**（`manifest.py`）：

- `__slots__` 加 `registry_url`（默认 `None`）。
- `kind` 判定：`路径` > `仓库` > `注册表`（不变）；`registry_url` 只是
  `注册表` kind 的一个可选修饰，不新增 kind——远程与本地注册表是同一种依赖，
  只是解析源不同。
- `from_spec`：dict 分支加 `'注册表' in spec` 判断（在 `'路径'`/`'仓库'` 之后、
  报错之前）。
- `to_spec`：`registry_url` 非空时输出 `{注册表: url, 版本: constraint}` dict，
  否则维持裸字符串——**round-trip 必须无损**（既有纯字符串依赖不能被改写成
  dict，否则 diff 噪声 + 破坏既有 `包.json`）。

### 2.6 预留（本轮不实现）：发布端 HTTP 接口

为避免 M21+ 做远程发布时推翻本轮抽象，`RegistryBackend` 协议**预留但不实现**
写操作签名：

- `write_text` / `write_bytes` / `remove`：`LocalBackend` 实现，`HttpBackend`
  抛 `UnsupportedOperation('远程发布见 M21')`。
- token 凭证的读取位置**本轮就定下**（§2.7），发布端复用同一套。

这样 M21 做 `POST /publish` 时只需在 `HttpBackend` 填实现 + 加服务端，不动
上层 `publish()` 的调用面。

### 2.7 鉴权：Bearer token，凭证与代码/项目分离

读端鉴权（私有注册表）用 HTTP `Authorization: Bearer <token>` 头。token 来源
优先级：

1. 环境变量 `JIKUAI_REGISTRY_TOKEN`（CI 场景）
2. `~/.jikuai/凭证.json`：`{ "<注册表 URL 前缀>": "<token>" }`（本机多注册表）

**凭证绝不进 `包.json` / `包.锁` / 项目目录**——那些会进版本库。凭证只在用户
主目录或环境变量，与 ADR-33 的私钥同一隔离原则（`~/.jikuai/密钥` /
`~/.jikuai/信任`，现在多一个 `~/.jikuai/凭证.json`）。凭证文件权限异常
（组/他人可读）时 stderr 告警。

公共注册表无 token 也能读（匿名 GET）；401/403 时提示用户配 token。

---

## 3. CLI 表面

- `jk 包 源`（新子命令族，G17 逼同步文档）：
  - `jk 包 源 列表`：显示当前生效的注册表（全局默认 + 各 override）
  - `jk 包 源 加 <名> <url>` / `jk 包 源 删 <名>`：管理 `~/.jikuai/源.json`
    的命名注册表（可选糖，让 `包.json` 写短名而非全 URL）——**本轮可延后到
    W80 视时间**，核心是环境变量 + per-dependency URL 先跑通。
- `JIKUAI_REGISTRY=https://...` 直接让所有默认依赖走远程，无需新命令。

---

## 4. 影响面

**改**：
- `registry.py`：引 `RegistryBackend` + `Local`/`Http` 两实现；`load_index` /
  `lookup` / `lookup_signature` / `list_packages` / `search` / `registry_key_path`
  改走 backend。`registry_root` 语义扩展为「解析注册表定位符（路径或 URL）」。
- `sources.py`：`_fetch_registry` 增 HTTP 分支——远端下 tar.gz、解压临时目录、
  `ephemeral=True`；per-dependency `registry_url` 决定用哪个 backend。
- `manifest.py`：`Dependency` 加 `registry_url` + `from_spec`/`to_spec`/`kind` 同步。
- `installer.py`：**不动验证逻辑**（复用 M19 的三道检查）；仅确认临时目录清理
  路径对 HTTP 快照同样生效。
- `docs/包管理.md`：新增远程注册表小节 + `JIKUAI_REGISTRY_TOKEN` /
  `_TIMEOUT` / `_INSECURE` 环境变量（G18 逼同步）。

**不动**：
- 签名/验签逻辑（ADR-33，复用）
- `包.锁` 格式
- 本地注册表磁盘布局（远程就是它的静态镜像）
- 安装落点 `极快_包/<包名>/`

---

## 5. 已知局限

- **服务端不在本轮**：远程注册表 = 静态文件托管。索引一致性（发布时的原子
  更新、并发写）由托管方式兜，不由极快保证。远程**写**留 M21。
- **无增量/缓存协商**：每次 `装` 重新 `GET 索引.json` 全量。ETag / If-None-Match
  条件请求留作后续优化（`重开条件`）。大注册表索引会偏慢。
- **tar.gz 无断点续传**：大包下载中断要重来。M20 目标是「能跨机装」，不是
  「弱网优化」。
- **TOFU 首次信任仍靠 TLS**：HTTP 模式下首次拉公钥若 TLS 被劫持，pin 的是假
  身份（ADR-33 §5 已记）。本轮用 TLS 兜，不做证书 pinning。
- **凭证明文存盘**：`~/.jikuai/凭证.json` 是明文（同 npm `.npmrc` / pip
  `.netrc` 的既定妥协）。系统级密钥环集成留作后续。

---

## 6. 备选方案（已拒）

- **直接让 `registry_root` 返回 URL、各处 `if root.startswith('http')` 分叉**：
  逻辑散落到每个函数，无法测试、易漏。**拒**——用 backend 抽象收敛。
- **快照用 zip**：Windows 原生友好，但与 `_fetch_git` 的目录语义不对齐，下游
  要分叉；且 `zipfile` 的路径逃逸防护同样要手写，省不了。**拒**——tar.gz 与
  git 路径同构。
- **本轮就做远程发布（POST）**：范围爆炸（服务端 + 鉴权 + 审计），拖垮 M20。
  **拒**——预留接口，M21 做。
- **引 `requests`**：破零依赖底线。**拒**——`urllib.request` 够用。
- **per-dependency 只支持命名源（不支持内联 URL）**：强制所有远程依赖先在
  `源.json` 注册。更规整但多一步配置，且 `包.json` 无法自包含（拿到别人的
  `包.json` 还得先配源）。**拒**——内联 URL 优先，命名源作为可选糖。

---

## 7. 重开条件

以下任一成立时重评审对应决策：

- **单个注册表索引超过约 5MB** 或 `装` 因全量拉索引明显变慢 → 重议 §5，上
  ETag 条件请求 / 分片索引增量拉取。
- **出现远程发布诉求**（多人往同一远程注册表推包）→ 触发 M21 的服务端 +
  `POST /publish` + token 鉴权写路径，兑现 §2.6 的预留接口。
- **企业要求凭证不落明文盘** → 重议 §2.7，集成 OS 密钥环（keyring）。
- **需要签名注册表索引本体**（防服务端/中间人篡改路由与依赖字段）→ 与 M21 的
  token 鉴权合并设计（ADR-33 §5 末条已记此坑）。
- **弱网/大包成为常见场景** → 重议 §5，上断点续传 / 分块下载。
