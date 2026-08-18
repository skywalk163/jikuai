# -*- coding: utf-8 -*-
"""W130 · 赛题数据集 schema 冻结校验（v0.26.0 · ADR-40 §4）。

**唯一 schema 真源是 ADR-40 §4**，本脚本只是把那一节机读化：下面的 `冻结表`
逐字对应 ADR-40 §4.1/§4.2 的列清单、行数、主键，`允许空值列` 对应 §4.3，
`单文件上限字节` 对应 §4.4。**改数据集必须先改 ADR-40 §4，再改这里的常量**，
顺序反了就等于让代码悄悄取代文档当真源。

为什么要这个脚本
----------------
引擎层（ADR-40 §3.1）的表表示是 `列表<字典<字符串,任意>>`，`值类型: 任意` 会让
`glue.py` 的 `type_feeds` 双向放行 —— **类型图在引擎层不起作用**。也就是说
「数据集被换掉 / 被误改」这类事故没有任何语言层机制会拦，只能靠本脚本拦。

尤其是空值分布那一条：ADR-40 §4.3 冻结的结论是「只有 `fact_orders` 的
`actual_delivery_date` 与 `delay_days` 有空值」。多出一个有空值的列**必须红**，
因为口径块会按「这两列可能为空、其余不可能为空」写实现，多一列空值意味着上层
所有指标的分母都可能已经错了，而且运行期不会报错。

为什么不并进 `check_stdlib_contract.py`
--------------------------------------
ADR-40 §6 规划的制造域门禁还有另外三条断言（预置异常恰好 5 条、口径关键词、
三处分歧点各两块）没实现，要到 W144 才整体挂进主流程。提前挂进去会让门禁编号
名不副实 —— 本轮**刻意保持独立可跑**。

（编号冲突已收口：`scripts/check_dist_metadata.py` 自 v0.25.0 W127 起占用 G21，
制造域口径契约因此排到 **G22**，见 ADR-40 §6 的编号说明。本脚本仍不自称门禁
编号 —— 它到 W144 是并入 G22 的一部分，不是独立门禁。）

零第三方依赖
------------
只用标准库 `csv`。ADR-40 §4.4 已实测最大单文件 203KB，远小于内建 `读取` 的
10 MiB 上限，所以不需要 pandas —— 本脚本顺手把「< 10 MiB」这条前提也断言掉，
免得它从「实测结论」退化成「当时看了一眼」。

用法
----
    python scripts/check_chatbi_schema.py                  # 查仓库里的真实数据集
    python scripts/check_chatbi_schema.py <数据集目录>      # 查指定副本（反例测试用）
    python scripts/check_chatbi_schema.py --quiet           # 只在失败时输出

退出码 0 全过 / 1 有违规 / 2 用法或环境问题（数据集目录不存在等）。
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import sys

# Windows 控制台默认 GBK，报告全是中文；强制 UTF-8 免得被 subprocess 捕获时炸。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

仓库根 = pathlib.Path(__file__).resolve().parent.parent

#: 数据集默认落点（ADR-40 §4）。
默认数据集目录 = 仓库根 / "赛题" / "chatbi" / "数据集"

#: 关系表文件名。1:N / 1:1 两类是真外键，`业务关联` 那三行不是（复合键 + 时间
#: 窗口对齐，不是引用完整性约束），做外键检查会假红。
关系表文件名 = "schema_relationships.csv"
非外键关系类型 = ("业务关联",)
外键关系类型 = ("1:N", "1:1")

#: 内建 `读取` 的上限（docs/语法参考.md §JK-E4003）。ADR-40 §4.4「不需要 pandas」
#: 这个结论就靠这条前提，所以它要被断言，不能只当注释。
单文件上限字节 = 10 * 1024 * 1024

#: **冻结值集中在这一张表里**（对应 ADR-40 §4.1/§4.2/§4.3）。换数据集只改这一处。
#: - 列：逐列逐序，**列序也是契约** —— 引擎层的投影与元组形状会依赖它。
#: - 允许空值列：不在这里的列出现空值即违规。
冻结表 = {
    "dim_model": {
        "主键": ("model_id",),
        "列": ("model_id", "model_name", "product_series", "vehicle_type",
               "standard_cycle_minutes", "standard_energy_kwh", "launch_year"),
        "行数": 8,
        "允许空值列": (),
    },
    "dim_workshop_line": {
        "主键": ("line_id",),
        "列": ("line_id", "line_name", "workshop", "shift_type",
               "designed_daily_capacity", "main_model_series"),
        "行数": 8,
        "允许空值列": (),
    },
    "dim_customer": {
        "主键": ("customer_id",),
        "列": ("customer_id", "customer_name", "customer_type", "region",
               "priority_level"),
        "行数": 60,
        "允许空值列": (),
    },
    "fact_orders": {
        "主键": ("order_id",),
        "列": ("order_id", "customer_id", "model_id", "order_date",
               "planned_delivery_date", "actual_delivery_date", "order_quantity",
               "delivered_quantity", "order_status", "delay_days"),
        "行数": 1850,
        # 未交付时为空。ADR-40 §4.3：空串载为极快 `空`，**不填 0** ——
        # 把「未交付」当 0 天延期会直接污染延期统计。
        "允许空值列": ("actual_delivery_date", "delay_days"),
    },
    "fact_production_plan": {
        "主键": ("plan_id",),
        "列": ("plan_id", "production_date", "line_id", "model_id", "shift",
               "planned_quantity"),
        "行数": 2896,
        "允许空值列": (),
    },
    "fact_production_actual": {
        "主键": ("actual_id",),
        "列": ("actual_id", "plan_id", "production_date", "line_id", "model_id",
               "shift", "actual_quantity", "working_hours", "downtime_minutes",
               "achievement_rate"),
        "行数": 2896,
        "允许空值列": (),
    },
    "fact_quality_defects": {
        "主键": ("defect_id",),
        "列": ("defect_id", "defect_date", "line_id", "model_id", "process",
               "defect_type", "defect_count", "severity", "rework_status"),
        "行数": 2992,
        "允许空值列": (),
    },
    "fact_energy_usage": {
        "主键": ("energy_id",),
        "列": ("energy_id", "usage_date", "workshop", "line_id", "model_id",
               "shift", "electricity_kwh", "water_ton", "gas_m3",
               "energy_per_vehicle"),
        "行数": 2896,
        "允许空值列": (),
    },
}

#: ADR-40 §4.2 末尾那条断言：两表 `plan_id` 集合必须相等，1:1 才成立。
#: 只查行数相等是不够的 —— 行数相同而键错位照样能「看起来对」。
一对一约束 = ("fact_production_plan", "fact_production_actual", "plan_id")


def 是空值(值) -> bool:
    """空串与纯空白都算空值。缺字段（None）也算。"""
    return 值 is None or str(值).strip() == ""


def 读表(路径: pathlib.Path):
    """读一张 CSV，返回 (表头, 行字典列表, 畸形行说明列表)。

    一律 `utf-8-sig`：赛事方的文件带不带 BOM 都要能吃（W133 的 CSV 载入块同此）。
    """
    畸形: list[str] = []
    with 路径.open("r", encoding="utf-8-sig", newline="") as f:
        读者 = csv.reader(f)
        try:
            表头 = next(读者)
        except StopIteration:
            return [], [], ["文件是空的，连表头都没有"]
        行: list[dict] = []
        for 序号, 原行 in enumerate(读者, start=2):
            if not 原行:                       # 尾部空行，csv 常见，忽略
                continue
            if len(原行) != len(表头):
                畸形.append("第 %d 行有 %d 个字段，表头有 %d 个"
                            % (序号, len(原行), len(表头)))
                continue
            行.append(dict(zip(表头, 原行)))
    return 表头, 行, 畸形


def 校验一张表(表名: str, 目录: pathlib.Path):
    """返回 (问题列表, 行字典列表)。行列表为 None 表示这张表没读成。"""
    问题: list[str] = []
    冻结 = 冻结表[表名]
    路径 = 目录 / (表名 + ".csv")

    # 1. 文件存在
    if not 路径.is_file():
        return ["%s：文件不存在（期望 %s）" % (表名, 路径)], None

    # 4. 单文件大小 —— 放在读之前，超限就没必要读了
    大小 = 路径.stat().st_size
    if 大小 >= 单文件上限字节:
        问题.append("%s：文件 %d 字节，达到或超过内建 `读取` 的 %d 字节上限"
                    "（ADR-40 §4.4「不需要 pandas」这个结论就以此为前提）"
                    % (表名, 大小, 单文件上限字节))

    表头, 行, 畸形 = 读表(路径)
    for 条 in 畸形:
        问题.append("%s：%s" % (表名, 条))

    # 1. 列名逐列逐序一致
    期望列 = list(冻结["列"])
    if 表头 != 期望列:
        缺 = [c for c in 期望列 if c not in 表头]
        多 = [c for c in 表头 if c not in 期望列]
        细节 = []
        if 缺:
            细节.append("缺列 %s" % 缺)
        if 多:
            细节.append("多列 %s" % 多)
        if not 细节:
            细节.append("列名一致但**列序不同**（列序也是契约，"
                        "引擎层的投影与元组形状依赖它）")
        问题.append("%s：表头与 ADR-40 §4 冻结的清单不一致 —— %s\n"
                    "      实际：%s\n"
                    "      冻结：%s"
                    % (表名, "；".join(细节), 表头, 期望列))
        # 表头都对不上，后面按列名做的检查全无意义，直接收摊
        return 问题, None

    # 2. 行数
    if len(行) != 冻结["行数"]:
        问题.append("%s：%d 行，冻结值是 %d 行"
                    "（数据集被换掉或被截断的信号，改数据集要先改 ADR-40 §4）"
                    % (表名, len(行), 冻结["行数"]))

    # 3. 空值分布
    允许 = set(冻结["允许空值列"])
    空值计数 = {}
    for 行字典 in 行:
        for 列, 值 in 行字典.items():
            if 是空值(值):
                空值计数[列] = 空值计数.get(列, 0) + 1
    意外空值 = sorted(c for c in 空值计数 if c not in 允许)
    if 意外空值:
        问题.append("%s：出现 ADR-40 §4.3 未冻结的空值列 %s（计数 %s）—— "
                    "这是数据集被换掉或被误改的信号；口径块按"
                    "「只有 fact_orders 那两列可能为空」写实现，"
                    "多一列空值意味着上层指标的分母可能已经错了，且运行期不报错"
                    % (表名, 意外空值, {c: 空值计数[c] for c in 意外空值}))

    # 5. 主键唯一
    主键 = 冻结["主键"]
    见过 = set()
    重复 = []
    for 行字典 in 行:
        键 = tuple(行字典[c] for c in 主键)
        if 键 in 见过:
            重复.append(键)
        else:
            见过.add(键)
    if 重复:
        样本 = 重复[:3]
        问题.append("%s：主键 %s 有 %d 行重复，样本 %s"
                    % (表名, "+".join(主键), len(重复), 样本))

    return 问题, 行


def 读关系表(目录: pathlib.Path):
    """返回 (外键关系列表, 问题列表)。每条关系是 (父表, 父列, 子表, 子列, 类型)。"""
    路径 = 目录 / 关系表文件名
    if not 路径.is_file():
        return [], ["%s：文件不存在，无法做外键检查（期望 %s）"
                    % (关系表文件名, 路径)]
    问题: list[str] = []
    关系: list[tuple] = []
    with 路径.open("r", encoding="utf-8-sig", newline="") as f:
        for 行 in csv.DictReader(f):
            类型 = (行.get("relationship_type") or "").strip()
            if 类型 in 非外键关系类型:
                # 复合键 + 时间窗口对齐，不是引用完整性约束，跳过是刻意的
                continue
            if 类型 not in 外键关系类型:
                问题.append("%s：不认识的 relationship_type %r"
                            "（认得的是 %s，跳过的是 %s）"
                            % (关系表文件名, 类型,
                               list(外键关系类型), list(非外键关系类型)))
                continue
            父表 = (行.get("source_table") or "").strip()
            父列 = (行.get("source_key") or "").strip()
            子表 = (行.get("target_table") or "").strip()
            子列 = (行.get("target_key") or "").strip()
            if "+" in 父列 or "+" in 子列:
                问题.append("%s：%s 关系上出现复合键 %s→%s，本脚本只做单列外键"
                            % (关系表文件名, 类型, 父列, 子列))
                continue
            关系.append((父表, 父列, 子表, 子列, 类型))
    return 关系, 问题


def 校验外键(关系: list, 各表行: dict):
    """按关系表做孤儿行检查。孤儿数不为 0 就如实报出并红 —— 不静默兜掉。"""
    问题: list[str] = []
    for 父表, 父列, 子表, 子列, 类型 in 关系:
        for 表名, 列名 in ((父表, 父列), (子表, 子列)):
            if 表名 not in 冻结表:
                问题.append("%s：关系引用了不在 ADR-40 §4 冻结清单里的表 %r"
                            % (关系表文件名, 表名))
                break
            if 各表行.get(表名) is None:
                break                      # 那张表自己已经报过错了，不重复刷屏
            if 列名 not in 冻结表[表名]["列"]:
                问题.append("%s：关系引用了 %s 里不存在的列 %r"
                            % (关系表文件名, 表名, 列名))
                break
        else:
            父值集 = {行[父列] for 行 in 各表行[父表]}
            孤儿 = [行[子列] for 行 in 各表行[子表]
                    if not 是空值(行[子列]) and 行[子列] not in 父值集]
            if 孤儿:
                问题.append("%s.%s → %s.%s（%s）：%d 行孤儿，样本 %s —— "
                            "按 W139 的 DoD，实测不为 0 要回写进 ADR-40 §4，"
                            "不许静默兜掉"
                            % (父表, 父列, 子表, 子列, 类型,
                               len(孤儿), sorted(set(孤儿))[:3]))
    return 问题


def 校验一对一(各表行: dict):
    """ADR-40 §4.2：plan ↔ actual 的 plan_id 集合必须相等。"""
    左表, 右表, 键 = 一对一约束
    if 各表行.get(左表) is None or 各表行.get(右表) is None:
        return []                          # 前面已经报过了
    左 = {行[键] for 行 in 各表行[左表]}
    右 = {行[键] for 行 in 各表行[右表]}
    if 左 == 右:
        return []
    只在左 = sorted(左 - 右)
    只在右 = sorted(右 - 左)
    细节 = []
    if 只在左:
        细节.append("只在 %s 里的 %d 个（样本 %s）"
                    % (左表, len(只在左), 只在左[:3]))
    if 只在右:
        细节.append("只在 %s 里的 %d 个（样本 %s）"
                    % (右表, len(只在右), 只在右[:3]))
    return ["%s ↔ %s 的 %s 集合不相等，1:1 不成立 —— %s"
            % (左表, 右表, 键, "；".join(细节))]


def 校验数据集(数据集目录: pathlib.Path, 记录=None):
    """跑全部断言，返回问题说明列表（空 = 全过）。

    `记录` 是可选的单参回调，用来打印通过项的进度。
    """
    def 报(文本):
        if 记录 is not None:
            记录(文本)

    问题: list[str] = []
    各表行: dict = {}
    for 表名 in 冻结表:
        表问题, 行 = 校验一张表(表名, 数据集目录)
        各表行[表名] = 行
        问题.extend(表问题)
        if not 表问题:
            报("  [通过] %-24s %d 行 / %d 列，主键 %s 唯一，空值列 %s"
               % (表名, len(行), len(冻结表[表名]["列"]),
                  "+".join(冻结表[表名]["主键"]),
                  list(冻结表[表名]["允许空值列"]) or "无"))

    关系, 关系问题 = 读关系表(数据集目录)
    问题.extend(关系问题)
    外键问题 = 校验外键(关系, 各表行)
    问题.extend(外键问题)
    if 关系 and not 关系问题 and not 外键问题:
        报("  [通过] 外键 %d 条（%s 那 %d 条按 ADR-40 跳过）零孤儿行"
           % (len(关系), "/".join(非外键关系类型), 3))

    一对一问题 = 校验一对一(各表行)
    问题.extend(一对一问题)
    if not 一对一问题:
        报("  [通过] %s ↔ %s 的 %s 集合相等，1:1 成立" % 一对一约束)

    return 问题


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="W130 · 赛题数据集 schema 冻结校验（ADR-40 §4 是唯一真源）")
    p.add_argument("数据集目录", nargs="?", default=None,
                   help="默认 赛题/chatbi/数据集/")
    p.add_argument("--quiet", action="store_true", help="只在失败时输出")
    args = p.parse_args(argv)

    目录 = (pathlib.Path(args.数据集目录) if args.数据集目录
            else 默认数据集目录)
    if not 目录.is_dir():
        print("  [错误] 数据集目录不存在：%s" % 目录)
        return 2

    if not args.quiet:
        print("W130 · 赛题数据集 schema 冻结校验（%s）" % 目录)

    问题 = 校验数据集(目录, 记录=None if args.quiet else print)

    if 问题:
        print("W130 schema 冻结校验：失败（%d 条）" % len(问题))
        for 条 in 问题:
            print("  [失败] %s" % 条)
        print("  提示：ADR-40 §4 是唯一 schema 真源。数据集真的换了，"
              "先改 ADR-40 §4，再同步 scripts/check_chatbi_schema.py 的 `冻结表`。")
        return 1

    print("W130 schema 冻结校验：通过（%d 张表，共 %d 行）"
          % (len(冻结表), sum(t["行数"] for t in 冻结表.values())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
