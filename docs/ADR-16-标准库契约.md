# ADR-16 · 标准库契约（v0.5.0 · M4）

- 状态：Accepted
- 日期：2026-08-08
- 决策者：架构师（承接交付总监 M4 编排）
- 落地环节：v0.5.0 · M4 · P2 stdlib 支线
- 相关：ADR-14（诊断内核）、基线校正说明-v0.5.0（偏差 C/D）

---

## 1. 背景

`stdlib/` 目录（仓库根）存在三种形态的标准库模块：

- 纯 `.jk` 模块（如 `工具.jk`）
- 纯 `.py` 模块（如 `历法.py`）
- 同名双形态（如 `校验.jk` + `校验.py` 并存）

当前加载语义由 `src/jikuai/module_loader.py` 实现，搜索优先级为：当前文件目录 → `stdlib/` → `JIKUAI_PATH` 环境变量。`_search_paths()` 通过 `os.path.join(here, '..', '..', 'stdlib')` 从 `src/jikuai/` 上溯两级定位 `stdlib/` 目录。

现有导出机制已经可用：`.jk` 源码中的运行期 `导出` 语句由 `evaluator._current_exports` 收集，传递给 `ModuleValue(name, env, exports, ev)` 构造函数。`ModuleValue.get(attr)` 在 `attr` 不在 `exports` 中时抛出 `模块 {name} 未导出：{attr}`。

**问题**：缺乏统一的错误码、缺乏静态契约校验手段、混合模块的加载语义未显式文档化。ADR-16 草案曾提议 `__导出__` 变量机制，但经基线校核发现该机制与已有 `导出` 语句重复（见基线校正偏差 C），予以否决。

---

## 2. 方案候选

### A · 引入 `__导出__` 魔法变量

在模块顶层声明 `定义 __导出__ 为 列 "符号1" "符号2"`，加载器读取该变量作为公共 API 清单。

- 优点：静态可分析（无需执行模块）
- 缺点：与已有 `导出` 语句语义重复；引入「魔法变量」概念违背极快「运行期语义」哲学；已有所有 `.jk` 模块须回改。**拒绝。**

### B · 沿用现有 `导出` 语句 + 补错误码 + 补静态契约校验（采纳）

保留 `导出` 语句作为唯一导出声明机制；为错误路径补稳定错误码；新增静态脚本比对文档声明与实际导出。

- 优点：零破坏、对现有 stdlib 模块无需改动、机制统一
- 缺点：静态校验需执行模块（或解析 `导出` 语句文本）；纯 `.py` 模块无 `导出` 语句需特殊处理

### C · 强制所有模块提供 manifest 文件

每个模块附带 `<模块名>.manifest.json` 声明导出符号。

- 优点：完全静态
- 缺点：维护负担重；与 `.jk` 的 `导出` 语句信息重复；引入新文件格式。**拒绝。**

---

## 3. 决议：方案 B

### 3.1 导出机制统一

**沿用现有 `导出` 语句作为唯一导出声明机制，不引入 `__导出__` 变量。**

- `.jk` 模块通过 `导出 X` 语句声明公共符号
- `evaluator._current_exports` 收集导出名
- `ModuleValue(name, env, exports, ev)` 持有导出集合
- `ModuleValue.get(attr)` 实施访问控制

### 3.2 错误码补充

两条错误路径补入稳定错误码：

- `ModuleValue.get()` 未导出路径 → `JK-E5002`（`模块 {name} 未导出：{attr}`）
- `module_loader.resolve()` 找不到模块 → `JK-E5001`（`找不到模块：{module_name}`）

### 3.3 混合模块语义

当 `stdlib/` 中存在同名 `.jk` 与 `.py` 文件时：

- `.jk` 为**唯一对外门面**，是模块名解析的目标
- `.py` 视为内部实现，**不参与模块名解析**（即 `引入 校验` 只会加载 `校验.jk`）
- `.py` 只能经 `蟒:` 桥接被 `.jk` 内部调用（如 `校验.jk` 中 `蟒:校验` 调用 `校验.py` 的 Python 实现）
- 同名时 `.jk` 优先；若只有 `.py` 无同名 `.jk`，则 `.py` 直接作为模块加载

### 3.4 `stdlib/` 物理位置

**`stdlib/` 固定在仓库根目录，不可移动。**

理由：

- `module_loader._search_paths()` 使用 `os.path.join(here, '..', '..', 'stdlib')` 从 `src/jikuai/` 上溯两级定位
- 移动目录将破坏此解析逻辑
- 引用 Summary 裁决 D-05：「统一为仓库根 `stdlib/`」

分发方案：`pyproject.toml` 以 data files 方式声明 `stdlib/` 目录，安装时复制到 site-packages 对应位置，**不移动源码仓库中的目录结构**。

### 3.5 静态契约校验

新增脚本 `scripts/check_stdlib_contract.py`：

- 扫描每个 `stdlib/*.jk` 中的 `导出` 语句，提取声明的公共符号集合
- 与 `docs/标准库.md` 中声明的公共符号清单比对
- 不一致时输出差异详情并以退出码 1 退出
- 纳入 CI 门禁 G10（stdlib 契约一致性）

### 3.6 `ModuleValue.get()` 的 verb fallback

`ModuleValue.get(attr)` 存在对内建动词的 fallback 路径：当 `attr` 不在 `exports` 中时，会检查 `self._evaluator.verbs` 是否包含该名字。此机制用于支持薄封装模块（模块重导出内建动词的场景）。

**这是有意设计，不是契约漏洞。** 但该 fallback 不触发 `JK-E5002`（因为最终能解析到内建动词），仅当 verb fallback 也失败时才抛出 `JK-E5002`。

---

## 4. 影响面

- **`src/jikuai/module_loader.py`**：`resolve()` 补 `JK-E5001` 错误码；`ModuleValue.get()` 补 `JK-E5002` 错误码
- **`src/jikuai/evaluator.py`**：`_current_exports` 逻辑不变，无改动
- **`stdlib/*.jk`**：无改动（已有 `导出` 语句）
- **`scripts/check_stdlib_contract.py`**：新增
- **`pyproject.toml`**：追加 `stdlib/` 的 data files 声明（代码支线负责，文档支线仅记录）
- **`docs/标准库.md`**：须维护公共符号清单（作为契约校验的比对源）

---

## 5. 验证方式

- **AC-M4-03-01**：`ModuleValue.get()` 未导出路径产出包含 `JK-E5002` 的 `Diagnostic`
- **AC-M4-03-02**：混合模块 `引入 校验` 加载 `校验.jk` 而非 `校验.py`；`校验.jk` 内部可通过 `蟒:校验` 调用 `.py` 实现
- **AC-M4-03-03**：`resolve()` 找不到模块时产出包含 `JK-E5001` 的 `Diagnostic`，消息含模块名
- **G10 门禁**：`scripts/check_stdlib_contract.py` 在 CI 中退出码 0

---

## 6. 已知限制

- 纯 `.py` 模块（如 `历法.py`）无 `导出` 语句，其公共 API 由 Python 的 `__all__` 或模块级函数名决定；`check_stdlib_contract.py` 对 `.py` 模块采用读取 `__all__` 的策略
- `ModuleValue.get()` 的 verb fallback 使得某些名字即使未被 `导出` 也可访问（经 verb 表），这是有意设计但可能令用户困惑；须在 `docs/标准库.md` 中显式说明
- 静态契约校验脚本需要解析 `导出` 语句文本（正则匹配 `导出\s+(\S+)`），不执行模块；若导出名由变量拼接（如 `导出 (拼接 "a" "b")`）则无法静态检出——当前 stdlib 不存在此用法，列为已知限制
