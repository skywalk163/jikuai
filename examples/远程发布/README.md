# examples/远程发布/ — 远程注册表运维手册（W95 · v0.21.0）

集成方看这份，从零把「起服务端 → 生成密钥 → 授权登记 → 发布 → 装 →
装机验签」跑起来。只用标准库 `http.server` + `jk 包` CLI，不装任何第三方。

想看**自动化**版本（脚本化的两端一起跑），去看
`tests/test_pkg_remote_publish_e2e.py`——14 例，覆盖越权/覆盖/未签名/伪造签名/
协议版本/错误不泄露路径六类反例，CI 里每次都会跑。本目录是**给人看的手册**，
不是回归的一部分。

---

## 场景 · 单机 loopback 全链路

以下步骤在 Windows PowerShell 里可直接跑通（Linux/macOS 同名命令，只换路径分隔）。

### Step 1 · 起服务端

```powershell
mkdir D:\tmp\注册表
python tools/registry-server/server.py `
    --注册表 D:\tmp\注册表 `
    --授权 D:\tmp\授权.json `
    --监听 127.0.0.1 --端口 8765 `
    --审计 D:\tmp\审计.jsonl
```

服务端启动后常驻。观察 `D:\tmp\审计.jsonl` 可看每次请求
（**拒绝也会记，且绝不记 token**）。

### Step 2 · 生成密钥 + 授权

```powershell
$env:JIKUAI_KEY_ROOT = "D:\tmp\密钥"
jk 包 密钥 生成 甲
$公钥 = jk 包 密钥 导出 甲     # 44 字符 base64

$令牌 = "e2e-token-w95-demo"
$令牌哈希 = python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" $令牌
```

写 `D:\tmp\授权.json`（**服务端进程要重启才会读新配置**——ADR-35 §2.3
明确 warm reload 划到下一轮）：

```json
{
  "协议": 1,
  "条目": {
    "<粘 $令牌哈希 输出>": {
      "签名者": "甲",
      "公钥": "<粘 $公钥 输出>",
      "可发布": ["甲包", "甲-*"],
      "每小时次数": 20,
      "单包字节": 16777216
    }
  }
}
```

授权字段的约束（ADR-35 §2.3）：
- `token` **只存 sha256 hex**，服务端不落明文 token
- `可发布` 支持确切名或 `前缀-*`；**不认单独 `*`**
- `公钥` 是 base64，服务端**用这里登记的公钥验签**，不用请求 payload 里带的
- `每小时次数` 是滑动窗速率上限；`单包字节` 是归档大小上限

### Step 3 · 发布

进入任意携带 `包.json` 的目录，例如 `examples/块包/示范块集/`：

```powershell
$env:JIKUAI_REGISTRY = "http://127.0.0.1:8765"
$env:JIKUAI_REGISTRY_TOKEN = $令牌
$env:JIKUAI_REGISTRY_INSECURE = "1"     # 明文 http 需显式开
jk 包 发布 --签名 甲 --确认
```

预期：`发布成功：<包名> <版本> 已推到远程注册表`。
不加 `--确认` 时默认 `--演练`，只算校验和不发请求（远程分支同样支持演练）。

### Step 4 · 装

换一个信任根模拟另一位用户：

```powershell
$env:JIKUAI_TRUST_ROOT = "D:\tmp\信任-乙"
$env:JIKUAI_REGISTRY = "http://127.0.0.1:8765"
$env:JIKUAI_REGISTRY_INSECURE = "1"
Remove-Item Env:JIKUAI_REGISTRY_TOKEN    # 装包不需要 token

cd D:\tmp\宿主项目    # 里面有 包.json 声明依赖：{ "甲包": "*" }
jk 包 装
```

首次装：TOFU pin 公钥到 `D:\tmp\信任-乙\甲.公钥`，
后续任何冒充 `甲` 但公钥不对的包会被 `installer._verify_registry_signature`
三道检查拦下。

## 反例 · 越权

```powershell
# 甲的授权条目里 可发布=['甲包','甲-*']；下面这个包名不在白名单里
cd D:\tmp\丙包       # 一个名为「丙包」的包
$env:JIKUAI_REGISTRY = "http://127.0.0.1:8765"
$env:JIKUAI_REGISTRY_TOKEN = $令牌
$env:JIKUAI_REGISTRY_INSECURE = "1"
jk 包 发布 --签名 甲 --确认
```

预期：客户端报 `远程发布失败：403 · 包名 丙包 不在授权可发布列表`，
`D:\tmp\审计.jsonl` 追加一行 `{"结果":"拒绝","原因":"包名不在白名单",...}`
（**没有 token**）。

## 常见坑

- **token 里带非 latin-1 字符**：`HttpBackend._request` 有 latin-1 pre-check
  （`Authorization: Bearer <token>` 是 HTTP 头，latin-1 编码）。用中文 token
  会当场报错而不是发请求；w92 单元测试踩过这个。
- **`--允许覆盖` 在远程模式无效**：远程分支上来就拒 `--允许覆盖`，要重发只能
  升版本号（ADR-35 §2.4）。
- **明文 http**：`JIKUAI_REGISTRY` 是 `http://...` 默认被拒，要显式
  `$env:JIKUAI_REGISTRY_INSECURE = "1"`。**生产部署请挡在反向代理后面走 https**——
  当前服务端是单进程 + 一把写锁，不做 TLS 终结。
- **`授权.json` 改了服务端不认**：warm reload 划到下一轮，本轮必须重启服务端进程。
- **补 `--审计` 没生成文件**：审计写路径是 `open('a')` 惰性创建；只要发过一次
  请求就会出现，没请求就是空。

## 关联文档

- ADR-35 远程发布协议：`docs/ADR-35-远程发布协议.md`
- 客户端 CLI + 环境变量：`docs/包管理.md` §远程发布
- 服务端源码：`tools/registry-server/{auth,audit,server}.py`
- 自动化端到端回归：`tests/test_pkg_remote_publish_e2e.py`（14 例）
