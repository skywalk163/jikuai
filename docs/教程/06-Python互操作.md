# 06 · Python 互操作

> 极快 ↔ Python 双向调用。

## 极快 → Python：`蟒:` 前缀导入

<!-- run: true -->
<!-- expect: 4.0 -->
```jikuai
导入 蟒:math。
打印 math.sqrt(16)。
```

<!-- run: true -->
<!-- expect: [1, 2, 3] -->
```jikuai
导入 蟒:json。
打印 json.dumps(列 1 2 3)。
```

**Python 函数必须括号调用**（`math.sqrt(16)`）。免括号写法只适用于中文内建动词。

## Python → 极快：`import jikuai`

```python
import jikuai

mod = jikuai.load("脚本.jk")
print(mod.平方(3))        # 9

result = jikuai.run_source('打印 加 3 5。')
# stdout: 8
```

三个入口：`jikuai.load(path)` / `jikuai.run_source(src)` / `jikuai.run_file(path)`。

## ⚠️ 安全边界（必读）

> 引用 `docs/安全边界.md`（ADR-21）：
>
> **极快的 Python 互操作（pybridge）不提供完整沙箱隔离，不适用于执行不受信任的代码。**

pybridge 用**黑名单（拒绝清单）**拦截了 `os.system` / `subprocess.Popen` /
`builtins.eval` / `builtins.exec`，但黑名单是枚举而非语义分析，以下路径可以绕过：

- `importlib.import_module("os").system(...)`
- `getattr(__builtins__, "e" + "val")`
- 任何未列入清单的危险 API

**适用场景**：运行你自己写的代码、来源可信的库、本地开发与脚本自动化。

**禁用场景**：不受信任的第三方代码、用户上传代码、多租户隔离、面向公网的执行服务。

若必须承载不可信输入，请在 pybridge 外部叠加系统级隔离（seccomp / 容器 / 微 VM），
**pybridge 不能作为安全边界**。

下一章：[07 开发工具链](07-开发工具链.md)
