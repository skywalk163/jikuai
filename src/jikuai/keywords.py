# -*- coding: utf-8 -*-
"""极快语言 - 关键字、动词元数、百家姓、中文数字定义。

设计三支柱（借鉴并融合了段言/翰语/知行等语言的精华）：
  1. 双字关键词：核心关键字统一为两个汉字，消除歧义。
  2. 元数驱动：动词声明参数个数，实现免括号、免空格调用。
  3. 百家姓标识符：变量名以百家姓开头，天然区分标识符与关键字。
"""

# ---------------------------------------------------------------------------
# 关键字（多为双字，兼容少量单字友好写法）
# ---------------------------------------------------------------------------

# 定义 / 赋值
KW_DEFINE = {'定义'}          # 定义变量或函数
KW_ASSIGN = {'赋值', '设为'}  # 重新赋值

# 条件
KW_IF = {'如果'}
KW_THEN = {'那么'}
KW_ELIF = {'否则如果'}
KW_ELSE = {'否则'}

# 循环
KW_WHILE = {'当'}
KW_FOR = {'遍历'}
KW_IN = {'于'}
KW_FROM = {'从'}
KW_TO = {'到'}
KW_REPEAT = {'重复'}
KW_TIMES = {'次'}
KW_BREAK = {'跳出'}
KW_CONTINUE = {'跳过'}

# 函数
KW_FUNC = {'函数'}
KW_PARAM = {'接收'}
KW_RETURN = {'返回'}

# 面向对象
KW_CLASS = {'类'}
KW_EXTENDS = {'继承'}
KW_CTOR = {'构造'}
KW_METHOD = {'方法'}
KW_NEW = {'新建'}
KW_SELF = {'自身'}
# M10-1：显式父类方法调用。`父类.方法名(参数)`。
# 选双字关键字而不是 `super()` 函数形式，理由：
#   1. 与 `自身` 对称，都是"方法体内的隐式绑定"，学习成本为零；
#   2. 双字关键字天然不与百家姓标识符冲突，无空格分词器不需要额外规则；
#   3. 不引入新的调用语法（`父类` 本身不可调用，只能作为成员访问的接收者）。
KW_SUPER = {'父类'}

# 异常
KW_TRY = {'尝试'}
KW_CATCH = {'捕获'}
KW_FINALLY = {'最终'}
KW_THROW = {'抛出'}

# 模块
KW_IMPORT = {'导入'}
KW_EXPORT = {'导出'}
KW_FILE = {'文件'}
KW_AS = {'作为'}

# 字面量
KW_TRUE = {'真'}
KW_FALSE = {'假'}
KW_NIL = {'空'}

# 结构标记（块起始关键字，会引入缩进块 / 冒号块）
BLOCK_START_KEYWORDS = (
    KW_IF | KW_WHILE | KW_FOR | KW_FUNC | KW_CLASS | KW_CTOR
    | KW_METHOD | KW_TRY | KW_CATCH | KW_FINALLY | KW_REPEAT | KW_ELSE | KW_ELIF
)

# 所有关键字合集
ALL_KEYWORDS = (
    KW_DEFINE | KW_ASSIGN | KW_IF | KW_THEN | KW_ELIF | KW_ELSE
    | KW_WHILE | KW_FOR | KW_IN | KW_FROM | KW_TO | KW_REPEAT | KW_TIMES
    | KW_BREAK | KW_CONTINUE | KW_FUNC | KW_PARAM | KW_RETURN
    | KW_CLASS | KW_EXTENDS | KW_CTOR | KW_METHOD | KW_NEW | KW_SELF | KW_SUPER
    | KW_TRY | KW_CATCH | KW_FINALLY | KW_THROW
    | KW_IMPORT | KW_EXPORT | KW_FILE | KW_AS
    | KW_TRUE | KW_FALSE | KW_NIL
)

# ---------------------------------------------------------------------------
# 动词元数表（VERB -> 参数个数）
#   正数 = 固定元数；-1 = 可变元数（1+）；0 = 零元字面量
# ---------------------------------------------------------------------------

VERB_ARITY = {
    # 算术（二元）
    '加': 2, '减': 2, '乘': 2, '除': 2, '取余': 2, '幂': 2, '整除': 2,
    # 口语化算术
    '加上': 2, '减去': 2, '乘以': 2, '除以': 2,
    # 一元算术
    '负': 1, '绝对值': 1,
    # 比较（二元）
    '等于': 2, '不等于': 2, '大于': 2, '小于': 2,
    '大于等于': 2, '小于等于': 2,
    # 逻辑
    '且': 2, '或': 2, '非': 1,
    # 列表 / 序列
    '列': -1, '长度': 1, '首个': 1, '其余': 1, '末个': 1,
    '追加': 2, '连接': 2, '包含': 2, '反转': 1, '排序': 1, '去重': 1,
    '取值': 2, '范围': -1,
    # 高阶（副词，特殊处理，元数在解析器中定义）
    '皆': -2, '只': -2, '归': -2,
    # 聚合
    '求和': 1, '最大': 1, '最小': 1, '平均': 1,
    # 字符串
    '拼接': -1, '分割': 2, '替换': 3, '子串': 3, '大写': 1, '小写': 1,
    '转字符串': 1, '转整数': 1, '转小数': 1, '去空白': 1,
    # I/O
    '打印': -1, '输入': -1, '读取': 1, '写入': 2,
    # 中国特色
    '人民币': 1,       # 数值 -> 人民币金额
    '农历': 1,         # 公历日期 -> 农历
    '大写金额': 1,     # 数字 -> 中文大写金额（壹贰叁）
    '汉字数字': 1,     # 数字 -> 中文小写数字（一二三）
    # 中国国情校验（M1-1）
    '校验身份证': 1,   # 身份证号 -> True/False
    '提取身份证信息': 1,   # 身份证号 -> 字典
    '校验手机号': 1,       # 手机号 -> True/False
    '判断运营商': 1,       # 手机号 -> 移动/联通/电信
    '校验银行卡': 1,       # 卡号 -> True/False
    '校验车牌': 1,         # 车牌 -> True/False
    '校验社会信用代码': 1, # 18位代码 -> True/False
    # 中国历法（M1-1）
    '公历转农历': 3,   # (年, 月, 日) -> 农历元组
    '干支纪年': 1,     # 年 -> 干支
    '生肖': 1,         # 年 -> 生肖
    '农历完整日期': 3, # (年, 月, 日) -> 完整农历字符串
    # 面向对象反射（M9-4）
    '是否是': 2,       # (实例, 类名) -> True/False，沿继承链判定
    '类名': 1,         # 实例 -> 所属类名字符串
    # 抽象类 / 接口（T6，命名约定驱动，零新关键字）
    '是否实现': 2,     # (实例, 协类名) -> True/False，结构类型（鸭子类型）判定
    # 中文正则动词（T4-1）
    '匹配': 2,         # (文本, 模式) -> 布尔，全匹配
    '查找': 2,         # (文本, 模式) -> 列表，所有匹配
    '替换正则': 3,     # (文本, 模式, 替换) -> 字符串
    '中文字符': 1,     # (文本) -> 列表，提取所有 CJK 字符
    # 成语/歇后语断言动词（T4-2）
    '成语断言': 1,     # (文本) -> 布尔，是否为已知四字成语
    '歇后语断言': 2,   # (前半, 后半) -> 布尔，是否为已知歇后语
    # 字典操作（D-4）
    '值集': 1,         # dict -> list(dict.values())
    '对集': 1,         # dict -> [[k,v] for k,v in dict.items()]
    '合并': 2,         # (dict1, dict2) -> {**dict1, **dict2}
}


ADVERBS = {'皆', '只', '归'}   # 高阶函数副词：map / filter / reduce

# 内建类型名
BUILTIN_TYPES = {'整数', '小数', '字符串', '布尔', '列表', '字典', '人民币', '日期'}

# ---------------------------------------------------------------------------
# 标点符号映射（中文全角 -> ASCII 语义）
# ---------------------------------------------------------------------------

PUNCTUATION = {
    '。': 'PERIOD',      # 语句结束符
    '，': 'COMMA',       # 管道操作符
    '：': 'COLON',       # 块起始
    ':': 'COLON',
    '.': 'DOT',          # 成员访问
    '=': 'EQUALS',       # 赋值
    '（': 'LPAREN', '(': 'LPAREN',
    '）': 'RPAREN', ')': 'RPAREN',
    '【': 'LBRACKET', '[': 'LBRACKET',
    '】': 'RBRACKET', ']': 'RBRACKET',
    '「': 'LBRACE', '{': 'LBRACE',
    '」': 'RBRACE', '}': 'RBRACE',
    '、': 'COMMA',
    ',': 'COMMA',        # ASCII 逗号：与 （/(、【/[ 一致的半角对应写法

}

# ---------------------------------------------------------------------------
# 中文数字表
# ---------------------------------------------------------------------------

CHINESE_DIGITS = {
    '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
}
CHINESE_UNITS = {'十': 10, '百': 100, '千': 1000, '万': 10000, '亿': 100000000}


def chinese_to_number(text):
    """将中文数字串转换为整数。支持 一二三 / 十 / 一百二十三 / 三万五千 等。

    返回 int，若无法解析则返回 None。
    """
    if not text:
        return None
    # 纯数字连写（如 一二三 = 123）
    if all(c in CHINESE_DIGITS for c in text):
        # 单字直接返回；多字视为逐位连写
        if len(text) == 1:
            return CHINESE_DIGITS[text]
        return int(''.join(str(CHINESE_DIGITS[c]) for c in text))

    total = 0
    section = 0      # 万/亿 以下的临时段
    number = 0       # 当前累积的数字
    for ch in text:
        if ch in CHINESE_DIGITS:
            number = CHINESE_DIGITS[ch]
        elif ch in CHINESE_UNITS:
            unit = CHINESE_UNITS[ch]
            if unit >= 10000:   # 万、亿：结算当前段
                section = (section + number) * unit
                total += section
                section = 0
            else:
                if number == 0:
                    number = 1   # 处理“十” = 10
                section += number * unit
            number = 0
        else:
            return None  # 含非数字字符
    return total + section + number


# ---------------------------------------------------------------------------
# 辅助查询函数
# ---------------------------------------------------------------------------

def is_keyword(word):
    return word in ALL_KEYWORDS


def is_verb(word):
    return word in VERB_ARITY


def is_adverb(word):
    return word in ADVERBS


def get_arity(verb):
    return VERB_ARITY.get(verb, 0)
