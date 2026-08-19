# -*- coding: utf-8 -*-
"""G23 · 规划器契约门禁（v0.27.0 W161 · ADR-41 §4/§8）。

四条断言：

1. **协议字段名只从 schema 常量取**（AST 扫 `planner.py` 的**键位**字面量），且
   上下文包 / 回填信封 / 候选 三处的字段集与 ADR-41 §3 的冻结值严格相等。
2. **五条硬规则长在原处**：`validate_filled` 真的调 `_规则1与2` / `_规则3` /
   `schema.validate_filled_envelope`，`_候选索引` 真的用 `(块, 领域, 导出名)` 三元键。
3. **行为断言：守卫真在守**。合成一份上下文包，跑六个场景（正常回填 / 缺 `参数` /
   幻觉块名 / `参数` 长度不对 / 分歧点两侧都没选 / 多余键）。
4. **录像回放全绿**：子进程跑 `bench_planner.py --只回放 --门禁`。

为什么静态 + 行为两套都要（G19 的教训，v0.22.0 的主教训「守卫绿≠守卫在守」）
--------------------------------------------------------------------------
只有静态断言时，把 `_规则1与2` 的循环体注释掉、函数留着，静态照样绿。只有行为断言
时，`validate_filled` 改成「凡是不认识的键就拒」也能让六个场景全过，而规则 1 的
「长度 = 输入槽数」实际上已经没了。两套一起才卡得住。

第 1 条为什么扫**键位**而不是所有字符串
--------------------------------------
违约的形状是「拿字段名字面量当键读写」：`x['需求']` / `x.get('需求')` /
`{'需求': ...}` / `'参数' in 步`。而 `planner.py` 的 `main()` 里
`add_argument('需求')` 是 argparse 位置参数名，恰好与协议字段同名却**不是**协议
使用——按「所有字符串」扫会假红，按键位扫就不会。拒绝理由要落在真违约上，宁可漏
一种奇葩写法也不制造假红（假红会逼人给门禁加 `# noqa`，那是门禁死亡的开始）。

`豁免赋值` 是刻意的白名单（不是漏网）
------------------------------------
`planner.py` 顶部有三组**非协议**键常量：`_I*`（`索引.json` 落盘格式）、`_Y*`
（`制造/语义层.json` 文件格式）、`_S*`（`共享[]` 的键，schema 没抽成模块常量），
外加 `分歧点表`（规划器自己的口径表，键名与 G22 那张表同源）。它们的字面量里有
`名` / `类型` / `名称` / `表` / `字段` / `块` 这些**与协议字段撞名**的串——撞名是
事实，混成一套才是病。往这个白名单里加名字必须在此写明理由。

豁免同时覆盖「绑定到这些表的循环变量」：`for 处 in 分歧点表:` 之后 `处['名']` 读的
是那张表自己的键。只认「迭代对象就是豁免名」这一层，不做数据流追踪——够覆盖实况，
也不会顺手放过真违约。


第 4 条：录像 / 数据集不在场时显式 skip
--------------------------------------
`tools/ai-bridge/`（bench 与录像）和 `赛题/chatbi/数据集/`（`跑` 要读的 CSV）都
**不进 wheel/sdist**。拿不到就打「跳过第 4 条」并在结论行带跳过标记，**不假装通过**
——同 G22 第 4 条的规矩。第 1-3 条只要 `planner.py` 在场就照跑。

零第三方依赖，只用标准库（`ast` / `json` / `subprocess` / `pathlib` /
`importlib`）。第 4 条走**子进程**而不是 `import`：回放会在进程内执行录像组出来的
极快源码并 `chdir` 到仓库根，隔在子进程里最省事，也让门禁自己的 stdout 保持干净。

用法
----
    python scripts/check_planner_contract.py                   # 查真仓库
    python scripts/check_planner_contract.py --quiet            # 结论走 stderr
    python scripts/check_planner_contract.py --规划器 <path>     # 反例测试用
    python scripts/check_planner_contract.py --录像目录 <dir>    # 反例测试用
    python scripts/check_planner_contract.py --跳过回放          # 只跑 1-3 条

`--quiet` 的约定（与 G16/G22 同款，供 `check_stdlib_contract.py` 串用）：全部输出
走 stderr 且只打一行结论（失败时附明细）。

退出码 0 全过 / 1 有违规 / 2 用法或环境问题（`--规划器` 指的文件不存在等）。
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import pathlib
import subprocess
import sys

# Windows 控制台默认 GBK，本报告全是中文；强制 UTF-8，免得被 subprocess 捕获时炸。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

仓库根 = pathlib.Path(__file__).resolve().parent.parent

默认桥目录 = 仓库根 / "tools" / "ai-bridge"
默认规划器 = 默认桥目录 / "planner.py"
默认bench = 默认桥目录 / "bench_planner.py"
默认录像目录 = 默认桥目录 / "规划录像"
默认数据集 = 仓库根 / "赛题" / "chatbi" / "数据集"

# ---------------------------------------------------------------------------
# 断言 1 的冻结表（ADR-41 §3 逐字）
# ---------------------------------------------------------------------------
#: 改协议要**先改 ADR-41 再改这里**，顺序反了等于让代码悄悄取代文档当真源。
冻结字段集 = {
    "CONTEXT_ENVELOPE_REQUIRED": ("需求", "语义命中", "候选", "回填契约", "拒答建议"),
    "CONTEXT_ENVELOPE_OPTIONAL": ("分歧告警",),
    "CONTEXT_CANDIDATE_REQUIRED": ("名称", "领域", "层级", "导出名", "描述", "分数",
                                   "路径", "输入槽", "输出类型"),
    "SLOT_REQUIRED": ("名", "类型"),
    "SEMANTIC_HIT_REQUIRED": ("业务词", "表", "字段", "口径说明"),
    "DIVERGENCE_WARNING_REQUIRED": ("分歧点", "两侧块名", "实测差值", "须显式选一条"),
    "FILL_CONTRACT_REQUIRED": ("目标", "必填", "禁止"),
    "REJECT_ADVICE_REQUIRED": ("覆盖", "理由"),
    "FILLED_ENVELOPE_REQUIRED": ("需求", "方案", "模型"),
    "FILLED_ENVELOPE_OPTIONAL": ("溯源",),
}

#: 协议字段全集从这些常量取（不写死一份清单：schema 加字段时这里自动跟上）。
协议常量名 = tuple(冻结字段集) + (
    "STEP_REQUIRED", "STEP_OPTIONAL", "PLAN_REQUIRED", "PLAN_OPTIONAL",
)

#: 键位字面量的豁免：顶层赋值目标名以此开头，或恰好等于 `豁免赋值`，整棵子树不扫。
#: 理由见模块 docstring。
豁免前缀 = ("_I", "_Y", "_S")
豁免赋值 = ("分歧点表",)

# ---------------------------------------------------------------------------
# 断言 2 的「必须长在原处」清单
# ---------------------------------------------------------------------------
必备函数 = ("build_context", "validate_filled", "ensure_filled",
            "_候选索引", "_规则1与2", "_规则3", "_规则5")

#: `validate_filled` 体内必须出现的被调名（`_规则5` 只在 `严格` 分支，同样要在）。
校验器必调 = ("validate_filled_envelope", "_规则1与2", "_规则3", "_规则5")


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------

def _schema():
    """从仓库源码树导入 `schema`。**读不到就抛**（同 G16/G17：新门禁不学 except→跳过）。"""
    src = str(仓库根 / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from jikuai.service import schema
    return schema


def 加载规划器(路径: pathlib.Path):
    """按路径加载 `planner.py`（它不进 wheel，见 ADR-41 §7）。

    它自己会把 `src/` 与自身目录塞进 `sys.path` **头部**再 `import glue`，所以副本
    必须放在一个**完整的桥目录**里（G23 反例测试用 `shutil.copytree`）。

    加载后把 `sys.path` 新增的条目**挪到末尾**：`tools/ai-bridge/` 里有个
    `select.py`，留在头部会遮蔽标准库 `select`（Linux 上 `subprocess` 等模块要它），
    而本门禁是被 `check_stdlib_contract.py` 串在中间跑的，不能给后面的门禁留坑。
    挪而不删——planner 里还有惰性 import 要用得到它（同 `bench_planner.py` 的做法）。
    """
    spec = importlib.util.spec_from_file_location("_g23_planner", str(路径))
    if spec is None or spec.loader is None:
        raise ValueError("%s 无法作为模块加载" % 路径)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    旧路径 = list(sys.path)
    try:
        spec.loader.exec_module(mod)
    finally:
        新增 = [p for p in sys.path if p not in 旧路径]
        sys.path[:] = 旧路径 + 新增
    return mod


# ---------------------------------------------------------------------------
# 断言 1：协议字段名只从 schema 常量取
# ---------------------------------------------------------------------------

def _协议字段全集(schema) -> tuple:
    """返回 (字段名集合, 缺失的常量名列表)。"""
    全 = set()
    缺 = []
    for 名 in 协议常量名:
        值 = getattr(schema, 名, None)
        if not isinstance(值, tuple):
            缺.append(名)
            continue
        全 |= {x for x in 值 if isinstance(x, str)}
    return 全, 缺


def _键位字面量(树: ast.AST, 豁免变量: frozenset = frozenset()):
    """产出 (字段名, 行号, 形状) —— 只看**键位**：下标 / `.get()` 首参 / 字典键 /
    `in` 左侧。理由见模块 docstring。

    `豁免变量` 是「绑定到非协议 dict 的循环变量名」（如 `for 处 in 分歧点表` 里的
    `处`）：对它们的下标 / `.get()` 读的是那张表自己的键（`处['名']` 的 `名` 恰好与
    协议 `SLOT_REQUIRED` 撞名），不是协议通道，跳过。撞名是事实，混成一套才是病。
    """
    取键方法 = ("get", "pop", "setdefault")

    def _基名(节点):
        return 节点.id if isinstance(节点, ast.Name) else None

    for 节点 in ast.walk(树):
        if isinstance(节点, ast.Subscript):
            if _基名(节点.value) in 豁免变量:
                continue
            s = 节点.slice
            if isinstance(s, ast.Constant) and isinstance(s.value, str):
                yield s.value, s.lineno, "下标"
        elif isinstance(节点, ast.Dict):
            for k in 节点.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    yield k.value, k.lineno, "字典键"
        elif isinstance(节点, ast.Call):
            f = 节点.func
            if (isinstance(f, ast.Attribute) and f.attr in 取键方法
                    and 节点.args and _基名(f.value) not in 豁免变量):
                a = 节点.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    yield a.value, a.lineno, ".%s()" % f.attr
        elif isinstance(节点, ast.Compare):
            if any(isinstance(op, (ast.In, ast.NotIn)) for op in 节点.ops):
                a = 节点.left
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    yield a.value, a.lineno, "in 左侧"


def _豁免循环变量(树: ast.AST) -> frozenset:
    """收集绑定到豁免表的循环变量名：`for X in <豁免名>` / 推导式的 `for X in <豁免名>`。

    只认「迭代对象是**豁免名字面变量**」这一种（`for 处 in 分歧点表`），不做数据流
    追踪——够覆盖 planner.py 的实况（`_分歧告警` 那一处），也不会把无关变量放进来。
    """
    名字 = set()

    def 迭代名(节点) -> bool:
        return (isinstance(节点, ast.Name)
                and (节点.id in 豁免赋值 or 节点.id.startswith(豁免前缀)))

    for 节点 in ast.walk(树):
        if isinstance(节点, ast.For) and 迭代名(节点.iter):
            if isinstance(节点.target, ast.Name):
                名字.add(节点.target.id)
        elif isinstance(节点, ast.comprehension) and 迭代名(节点.iter):
            if isinstance(节点.target, ast.Name):
                名字.add(节点.target.id)
    return frozenset(名字)


def 校验字段来源(规划器路径: pathlib.Path, schema) -> tuple:
    """返回 (问题列表, 通过说明)。"""
    问题 = []
    字段全集, 缺常量 = _协议字段全集(schema)
    for 名 in 缺常量:
        问题.append("schema 里没有元组常量 `%s`——协议常量被删/改名，"
                    "本门禁的字段全集就不完整了，拒绝在这种状态下判通过" % 名)

    # 1a 冻结字段集严格相等
    for 名, 冻 in 冻结字段集.items():
        实 = getattr(schema, 名, None)
        if not isinstance(实, tuple):
            continue                      # 上面已经报过
        if tuple(实) != 冻:
            问题.append("schema.%s = %s，与 ADR-41 §3 冻结值 %s 不等——"
                        "改协议要先改 ADR-41 再改本门禁的 `冻结字段集`"
                        % (名, list(实), list(冻)))

    # 1b 键位字面量
    源 = 规划器路径.read_text(encoding="utf-8")
    树 = ast.parse(源, filename=str(规划器路径))
    扫描体 = []
    豁免数 = 0
    for 顶 in 树.body:
        if isinstance(顶, (ast.Assign, ast.AnnAssign)):
            目标 = ([顶.target] if isinstance(顶, ast.AnnAssign) else 顶.targets)
            名字 = [t.id for t in 目标 if isinstance(t, ast.Name)]
            if any(n in 豁免赋值 or n.startswith(豁免前缀) for n in 名字):
                豁免数 += 1
                continue
        扫描体.append(顶)
    命中 = [(字, 行, 形) for 顶 in 扫描体
            for 字, 行, 形 in _键位字面量(顶, _豁免循环变量(树)) if 字 in 字段全集]
    for 字, 行, 形 in 命中:
        问题.append("%s:%d 把协议字段名 %r 当键用（%s）。字段名只能从 "
                    "schema 常量取——W20 硬门槛，理由是协议改名时字面量不会跟着改，"
                    "通道会静默错位" % (规划器路径.name, 行, 字, 形))

    if 问题:
        return (问题, None)
    return ([], "协议字段全集 %d 个；%s 的键位字面量零违约（豁免 %d 处非协议键常量：%s）"
                % (len(字段全集), 规划器路径.name, 豁免数,
                   "/".join(豁免前缀) + "* 与 " + "、".join(豁免赋值)))


# ---------------------------------------------------------------------------
# 断言 2：五条硬规则长在原处
# ---------------------------------------------------------------------------

def 校验规则在位(规划器路径: pathlib.Path) -> tuple:
    问题 = []
    源 = 规划器路径.read_text(encoding="utf-8")
    树 = ast.parse(源, filename=str(规划器路径))
    函数 = {n.name: n for n in 树.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    for 名 in 必备函数:
        if 名 not in 函数:
            问题.append("`%s` 不在 %s 的顶层函数里——ADR-41 §4 的五条硬规则"
                        "少了实现处" % (名, 规划器路径.name))

    校验器 = 函数.get("validate_filled")
    if 校验器 is not None:
        被调 = set()
        for 节点 in ast.walk(校验器):
            if isinstance(节点, ast.Call):
                f = 节点.func
                if isinstance(f, ast.Name):
                    被调.add(f.id)
                elif isinstance(f, ast.Attribute):
                    被调.add(f.attr)
        for 名 in 校验器必调:
            if 名 not in 被调:
                问题.append("`validate_filled` 里没有调用 `%s`——规则被摘掉而函数"
                            "还在，静态与行为两套断言里只有这一套抓得到" % 名)

    索引 = 函数.get("_候选索引")
    if 索引 is not None:
        三元 = False
        for 节点 in ast.walk(索引):
            if isinstance(节点, ast.Tuple) and len(节点.elts) == 3:
                取 = [e for e in 节点.elts
                      if isinstance(e, ast.Call)
                      and isinstance(e.func, ast.Attribute)
                      and e.func.attr == "get"]
                if len(取) == 3:
                    三元 = True
        if not 三元:
            问题.append("`_候选索引` 的键不再是三个 `.get()` 组成的三元组——"
                        "白名单退化成按块名单键就会放行「块名对、领域/导出名张冠李戴」"
                        "的回填（ADR-41 §4 规则 2）")

    if 问题:
        return (问题, None)
    return ([], "%d 个必备函数在位；`validate_filled` 真调 %s；"
                "`_候选索引` 仍是三元键"
                % (len(必备函数), "、".join(校验器必调)))


# ---------------------------------------------------------------------------
# 断言 3：行为断言 —— 守卫真在守
# ---------------------------------------------------------------------------

def _合成上下文包(schema):
    """造一份**不依赖块库与数据集**的合法上下文包（三个候选 + 一处分歧告警）。

    刻意不走 `build_context`：那条路要索引 + 语义层 + embeddings，任一缺席都会让
    本条断言变成「环境检查」。这里量的是校验器，不是环境。

    `丙块` 存在的唯一理由：`schema.validate_plan` 要求 `步骤` **非空**，所以「命中
    分歧点却两侧都没选」这个场景需要一个与分歧点无关、且在白名单里的零参数块——
    否则那条拒因会被形状错误或白名单错误盖掉，测的就不是规则 3 了。
    """
    甲 = schema.make_context_candidate(
        名称="甲块", 领域="制造", 层级=0, 导出名="甲",
        描述="G23 行为断言用的假块（一个输入槽）", 分数=1.0,
        输入槽=[schema.make_slot(名="路径", 类型="字符串")],
        输出类型="表", 路径="[启发式]")
    乙 = schema.make_context_candidate(
        名称="乙块", 领域="制造", 层级=0, 导出名="乙",
        描述="G23 行为断言用的假块（口径分歧点另一侧）", 分数=0.9,
        输入槽=[schema.make_slot(名="表", 类型="表")],
        输出类型="数字", 路径="[启发式]")
    丙 = schema.make_context_candidate(
        名称="丙块", 领域="制造", 层级=0, 导出名="丙",
        描述="G23 行为断言用的假块（零参数、与分歧点无关）", 分数=0.8,
        输入槽=[], 输出类型="数字", 路径="[启发式]")
    return schema.make_context_envelope(
        需求="G23 行为断言",
        语义命中=[schema.make_semantic_hit(
            业务词="产量", 表="production", 字段="actual_quantity",
            口径说明="G23 假条目")],
        候选=[甲, 乙, 丙],
        回填契约=schema.make_fill_contract(["假必填"], ["假禁止"]),
        拒答建议=schema.make_reject_advice(True, "G23 假理由"),
        分歧告警=[schema.make_divergence_warning(
            分歧点="G23 假分歧点", 两侧块名=["甲块", "乙块"],
            实测差值="甲 vs 乙")])


def _步(块, 领域, 导出名, 参数=None):
    步 = {"块": 块, "领域": 领域, "导出名": 导出名}
    if 参数 is not None:
        步["参数"] = 参数
    return 步


def 校验行为(pl, schema) -> tuple:
    """六个场景。返回 (问题列表, 通过说明)。

    注意场景表里的 `块`/`领域`/`导出名`/`参数` 是**测试夹具的数据**，不是通道代码，
    所以这里写字面量键不违 W20（本门禁自己不在协议通道上）。
    """
    包 = _合成上下文包(schema)
    # 分歧告警在场 → 规则 3 要求两侧恰好选一条，故基线方案只用 甲块。
    基线 = {"步骤": [_步("甲块", "制造", "甲", ["赵路径"])]}

    def 回填(方案):
        return schema.make_filled_envelope("G23 行为断言", 方案, "G23")

    场景 = [
        ("正常回填", 回填(基线), True, ()),
        ("缺 `参数`",
         回填({"步骤": [_步("甲块", "制造", "甲")]}), False, ("参数", "输入槽")),
        ("幻觉块名",
         回填({"步骤": [_步("丁块", "制造", "丁", ["赵路径"])]}), False, ("白名单",)),
        ("`参数` 长度不对",
         回填({"步骤": [_步("甲块", "制造", "甲", ["赵路径", "多一个"])]}),
         False, ("给了 2 个",)),
        ("分歧点两侧都没选",
         回填({"步骤": [_步("丙块", "制造", "丙", [])]}),
         False, ("必须显式挑一条",)),
        ("两侧同时出现",
         回填({"步骤": [_步("甲块", "制造", "甲", ["赵路径"]),
                       _步("乙块", "制造", "乙", ["赵表"])]}),
         False, ("同时出现",)),
    ]

    问题 = []
    for 名, 信封, 应通过, 关键词 in 场景:
        try:
            理由 = pl.validate_filled(信封, 包)
        except Exception as e:                       # noqa: BLE001 - 见下
            # 校验器**不许**被输入打挂：拒答理由是产品，异常是 bug（W157 review C001）。
            问题.append("场景「%s」把 validate_filled 打挂了：%s: %s"
                        % (名, type(e).__name__, e))
            continue
        if 应通过 and 理由:
            问题.append("场景「%s」本该通过却被拒：%s" % (名, 理由))
        elif not 应通过 and not 理由:
            问题.append("场景「%s」本该被拒却通过了——ADR-41 §4 的规则在这条路上"
                        "已经不生效（守卫绿不等于守卫在守）" % 名)
        elif not 应通过:
            合 = "\n".join(理由)
            缺 = [k for k in 关键词 if k not in 合]
            if 缺:
                问题.append("场景「%s」拒对了但理由不可操作：缺 %s。实际：%s"
                            % (名, 缺, 理由))

    # 多余键走 schema 层（规则 4），单独一条：它证明形状校验**先跑**且真拦。
    脏 = 回填(基线)
    脏["多余键"] = 1
    if not pl.validate_filled(脏, 包):
        问题.append("回填信封多一个键竟然通过——规则 4 的 "
                    "`schema.validate_filled_envelope` 没在 `validate_filled` "
                    "里生效（键集是白名单，多一个就该拒）")

    if 问题:
        return (问题, None)
    return ([], "%d 个行为场景全部如期（含 5 类拒答，每类的理由都带可操作信息）"
                % (len(场景) + 1))


# ---------------------------------------------------------------------------
# 断言 4：录像回放全绿
# ---------------------------------------------------------------------------

def 校验回放(bench路径: pathlib.Path, 录像目录: pathlib.Path,
           数据集目录: pathlib.Path) -> tuple:
    """返回 (问题列表, 通过说明, 跳过说明)。缺件时**显式 skip**，不假装通过。"""
    if not bench路径.is_file():
        return ([], None,
                "因 %s 不在场跳过第 4 条（录像回放）。tools/ 不进 wheel/sdist，"
                "拿不到 bench 就**不能**假装回放过了" % bench路径.name)
    if not (录像目录 / "清单.json").is_file():
        return ([], None,
                "因 %s/清单.json 不在场跳过第 4 条（录像回放）" % 录像目录)
    if not 数据集目录.is_dir():
        return ([], None,
                "因数据集缺失跳过第 4 条（录像回放要 `跑` 那 15 份方案，"
                "会读 %s/*.csv）" % 数据集目录)

    命令 = [sys.executable, str(bench路径), "--只回放", "--门禁"]
    if 录像目录.resolve() != 默认录像目录.resolve():
        命令 += ["--录像目录", str(录像目录)]
    环境 = dict(os.environ, PYTHONIOENCODING="utf-8")
    完成 = subprocess.run(命令, cwd=str(仓库根), env=环境,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    输出 = 完成.stdout.decode("utf-8", "replace")
    if 完成.returncode != 0:
        末 = [行 for 行 in 输出.splitlines() if 行.strip()][-6:]
        return (["`bench_planner.py --只回放 --门禁` 退 %d（回放判定与 "
                 "规划录像/清单.json 登记不一致，或回放自身报错）。尾部输出：\n      "
                 % 完成.returncode + "\n      ".join(末)], None, None)
    一致行 = [行.strip() for 行 in 输出.splitlines() if "判定一致率" in 行]
    return ([], "录像回放退 0（%s）"
                % (一致行[0] if 一致行 else "bench_planner --只回放 --门禁"), None)


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def 校验(规划器路径: pathlib.Path, bench路径: pathlib.Path,
        录像目录: pathlib.Path, 数据集目录: pathlib.Path,
        跳过回放: bool = False) -> tuple:
    """跑四条断言，返回 (问题, 通过, 跳过)。"""
    问题, 通过, 跳过 = [], [], []
    schema = _schema()

    条1问题, 条1过 = 校验字段来源(规划器路径, schema)
    问题 += ["[断言1 字段来源] " + c for c in 条1问题]
    if 条1过:
        通过.append("[断言1 字段来源] " + 条1过)

    条2问题, 条2过 = 校验规则在位(规划器路径)
    问题 += ["[断言2 规则在位] " + c for c in 条2问题]
    if 条2过:
        通过.append("[断言2 规则在位] " + 条2过)

    pl = 加载规划器(规划器路径)
    条3问题, 条3过 = 校验行为(pl, schema)
    问题 += ["[断言3 守卫在守] " + c for c in 条3问题]
    if 条3过:
        通过.append("[断言3 守卫在守] " + 条3过)

    if 跳过回放:
        跳过.append("[断言4 录像回放] 按 `--跳过回放` 跳过（只在本机快速迭代时用；"
                    "CI 与 check_stdlib_contract.py 都不加这个开关）")
    else:
        条4问题, 条4过, 条4跳 = 校验回放(bench路径, 录像目录, 数据集目录)
        问题 += ["[断言4 录像回放] " + c for c in 条4问题]
        if 条4过:
            通过.append("[断言4 录像回放] " + 条4过)
        if 条4跳:
            跳过.append("[断言4 录像回放] " + 条4跳)

    return 问题, 通过, 跳过


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="check_planner_contract",
        description="G23 · 规划器契约门禁（ADR-41 §4/§8，四条断言）")
    p.add_argument("--规划器", default=None,
                   help="planner.py 路径，默认 tools/ai-bridge/planner.py")
    p.add_argument("--bench", default=None,
                   help="bench_planner.py 路径，默认 tools/ai-bridge/bench_planner.py")
    p.add_argument("--录像目录", default=None,
                   help="默认 tools/ai-bridge/规划录像；不在场则显式跳过第 4 条")
    p.add_argument("--数据集", default=None,
                   help="默认 赛题/chatbi/数据集；不在场则显式跳过第 4 条")
    p.add_argument("--跳过回放", action="store_true",
                   help="只跑 1-3 条静态与行为断言（第 4 条要 8 秒上下）")
    p.add_argument("--quiet", action="store_true",
                   help="只打一行结论且全部输出走 stderr（供 check_stdlib_contract 串用）")
    args = p.parse_args(argv)

    规划器路径 = pathlib.Path(getattr(args, "规划器") or 默认规划器)
    if getattr(args, "bench"):
        bench路径 = pathlib.Path(getattr(args, "bench"))
    else:
        # 缺省跟着 `--规划器` 走同一个桥目录：反例测试复制整棵 tools/ai-bridge，
        # 那份副本里的 bench 才和被篡改的 planner 配套。
        兄弟 = 规划器路径.parent / 默认bench.name
        bench路径 = 兄弟 if 兄弟.is_file() else 默认bench
    录像目录 = pathlib.Path(getattr(args, "录像目录") or 默认录像目录)
    数据集目录 = pathlib.Path(getattr(args, "数据集") or 默认数据集)

    if not 规划器路径.is_file():
        print("  [错误] 规划器文件不存在：%s" % 规划器路径, file=sys.stderr)
        return 2

    出 = sys.stderr if args.quiet else sys.stdout
    if not args.quiet:
        print("G23 · 规划器契约门禁（ADR-41 §4/§8 · 规划器 %s）" % 规划器路径)

    try:
        问题, 通过, 跳过 = 校验(规划器路径, bench路径, 录像目录, 数据集目录,
                              getattr(args, "跳过回放"))
    except (ValueError, OSError, KeyError, TypeError, SyntaxError,
            ImportError, AttributeError) as e:
        # 同 G16/G17/G19/G22：**不**学 G13+ 的 except→跳过。解析不了就是它自己坏了。
        print("错误：G23 规划器契约门禁自身失败：%s: %s"
              % (type(e).__name__, e), file=sys.stderr)
        return 1

    if not args.quiet:
        for c in 通过:
            print("  [通过] %s" % c)

    # 跳过说明**任何模式下都打**：静默跳过与通过无法区分，正是本门禁要防的病。
    for c in 跳过:
        print("  [跳过] %s" % c, file=出)

    if 问题:
        print("错误：G23 规划器契约门禁失败（%d 条）" % len(问题), file=sys.stderr)
        for c in 问题:
            print("  [失败] %s" % c, file=sys.stderr)
        print("  修复：正本是 docs/ADR-41-规划器与NL层.md §4（五条硬规则）与 §8"
              "（录像回放）。回放不一致时先跑 "
              "`python tools/ai-bridge/bench_planner.py --只回放 --拒因` 看逐条拒因，"
              "再决定是改代码还是改 规划录像/清单.json 的 `期望判定`——"
              "**改登记等于改期望，要有理由**。", file=sys.stderr)
        return 1

    print("G23 规划器契约：通过（断言 1 字段来源 · 断言 2 规则在位 · "
          "断言 3 守卫在守 · 断言 4 %s）"
          % ("跳过" if 跳过 else "录像回放全绿"), file=出)
    return 0


if __name__ == "__main__":
    sys.exit(main())
