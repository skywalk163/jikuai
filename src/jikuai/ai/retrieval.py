# -*- coding: utf-8 -*-
"""极快语义块检索 —— ADR-25 运行时层（纯标准库实现）。

本模块实现两条检索路径：

1. **神经检索**（PATH_NEURAL）：读 `stdlib/blocks/向量索引.bin`，纯 Python
   反量化 + 余弦相似度。命中率目标 ≥80%。
2. **启发式检索**（PATH_HEURISTIC）：TF-IDF + 同义词扩展 + 领域先验。无需
   索引文件，命中率 60-70%，作为 fallback 始终可用。

**神经路径需要调用方提供查询向量。** ADR-25 §3.1 只锁了「读索引 + 纯 Python
余弦」，没有解决一个前提问题：查询文本怎么变成向量。运行时不能推理模型（那
就破了零依赖），所以 `retrieve(query, query_vector=...)` 把查询向量作为入参
交给调用方（`tools/ai-bridge/` 或云端 API）。不给向量就走启发式——这样离线
和无索引场景都自然可用，不需要额外分支。

切换逻辑（ADR-25 §3.1 / §8）：
- 默认 AUTO：有索引 + 有查询向量走神经，否则启发式
- 环境变量 JIKUAI_AI_RETRIEVAL=heuristic 强制走启发式
- 魔数/版本不匹配的索引当作不存在，自动降级

硬约束：**只 import 标准库**。核心包零运行时依赖优先于检索质量（ADR-25 §2）。
"""

import array
import json
import logging
import math
import os
import struct
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 向量索引魔数（ADR-25 §4）。
MAGIC = b'JKBV'

#: 当前支持的索引格式版本。
FORMAT_VERSION = 1

#: 检索模式常量。
MODE_NEURAL = 'neural'
MODE_HEURISTIC = 'heuristic'
MODE_AUTO = 'auto'
MODE_ENV = 'env'  # 由环境变量决定

#: 检索路径标签（供 CLI/桥接输出用）。
PATH_NEURAL = '[神经]'
PATH_HEURISTIC = '[启发式]'

#: 索引条目里承载命名空间的键名。**必须与 `pkg.blocks.NAMESPACE_KEY` 同值**——
#: 这里不 import 那个常量，是为了守住「不用检索就不拖块子系统」的惰性边界
#: （见 `_load_third_party_blocks` 的延迟导入注释）。有 `test_命名空间键名与块子系统同源`
#: 在测试侧钉住两者一致，改一边漏另一边会当场红。
_NAMESPACE_KEY = '命名空间'


#: 环境变量名。
_ENV_MODE = 'JIKUAI_AI_RETRIEVAL'

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


class RetrievalError(Exception):
    """检索配置或索引格式错误。"""


@dataclass(frozen=True, order=True)
class Hit:
    """单条检索结果。按分数降序排列时 order=True 配合 `-score` 使用。"""
    score: float
    name: str
    domain: str
    description: str
    path: str = field(default='', compare=False)  # 检索路径标签
    #: 块 `示例`（v0.18.0 · 集成反馈 P2）。**只有当块库条目自带 `示例` 时才非空**
    #: ——即索引由 `generate_index(含示例=True)` / `--with-examples` 生成的「胖索引」。
    #: 默认索引不含 `示例`（token 成本优先，见 `pkg.blocks._INDEX_ENTRY_KEYS`），
    #: 此时保持空串。`compare=False`：示例不参与排序/相等语义。
    example: str = field(default='', compare=False)
    #: 块所属命名空间（v0.19.0 W69）。**内置块恒为空串**，第三方块是其来源包名。
    #: 这是「导入路径」的第一段真源：`从 blocks.<命名空间>.<领域>.<块> 导入 X`
    #: 少了它，第三方块的命中就永远拼不出能跑的导入行（失败发生在使用方，
    #: 块作者自己测不出来）。`compare=False`：命名空间不参与排序/相等语义，
    #: 否则同名块跨命名空间会被当成「不同分数」影响 order。
    namespace: str = field(default='', compare=False)

    def as_dict(self) -> dict:
        d = {
            '名称': self.name,
            '领域': self.domain,
            '描述': self.description,
            '分数': round(self.score, 4),
            '路径': self.path,
        }
        # 空示例不进字典：`选响应.候选` 的既有形状不变（schema 校验按可选字段
        # 处理），token 敏感的调用方也不会平白多收一个空键。
        if self.example:
            d['示例'] = self.example
        # 同理：内置块（空命名空间）不写这个键，旧调用方的字典形状一字不变。
        if self.namespace:
            d['命名空间'] = self.namespace
        return d


@dataclass
class VectorIndex:
    """从 `向量索引.bin` 加载的内存结构。"""
    version: int
    dim: int
    count: int
    qmin: float
    qmax: float
    names: List[str]
    vectors: List[array.array]  # 每项是 dim 个 int16 的 array('h', ...)


# ---------------------------------------------------------------------------
# 向量索引 I/O
# ---------------------------------------------------------------------------


def vector_index_path() -> str:
    """返回 `stdlib/blocks/向量索引.bin` 的绝对路径。

    W115（v0.24.0 · ADR-39）：定位收敛到 `resources`，不再回溯到仓库根。
    """
    from .. import resources
    return resources.stdlib_path('blocks', '向量索引.bin')


def load_vector_index(path: Optional[str] = None) -> Optional[VectorIndex]:
    """读取向量索引文件。文件不存在或格式不兼容返回 None（不抛异常）。"""
    target = path or vector_index_path()
    if not os.path.isfile(target):
        return None
    try:
        with open(target, 'rb') as f:
            magic = f.read(4)
            if magic != MAGIC:
                return None
            ver, dim = struct.unpack('<HH', f.read(4))
            if ver != FORMAT_VERSION:
                return None
            (count,) = struct.unpack('<I', f.read(4))
            qmin, qmax = struct.unpack('<ff', f.read(8))

            names: List[str] = []
            vectors: List[array.array] = []
            for _ in range(count):
                (name_len,) = struct.unpack('<H', f.read(2))
                name = f.read(name_len).decode('utf-8')
                raw = f.read(dim * 2)
                vec = array.array('h')
                vec.frombytes(raw)
                # W119 · v0.24.0：向量载荷此前用 `array.frombytes` 直接吃字节，也就是
                # **原生字节序**，而文件头一直是显式小端（'<HH' / '<I' / '<ff' / '<H'）——
                # 格式自己跟自己不一致。仓库里提交的 `向量索引.bin` 是在 x86（小端）上
                # 生成的，而 jikuai 发的是 `py3-none-any` wheel，装到大端平台（s390x 等）
                # 上时 int16 会被逐个字节翻转：**不抛任何异常，只是余弦相似度全部算错，
                # 静默返回错误的检索结果**。格式口径现定为「全小端」（头部已是小端，载荷
                # 跟上），所以在大端机器上读完要翻回来。
                if sys.byteorder == 'big':
                    vec.byteswap()
                names.append(name)
                vectors.append(vec)

        return VectorIndex(
            version=ver, dim=dim, count=count,
            qmin=qmin, qmax=qmax,
            names=names, vectors=vectors,
        )
    except (OSError, struct.error, UnicodeDecodeError, ValueError):
        return None


def _cosine_sim_quantized(query_vec: Sequence[float], idx_vec: array.array,
                          qmin: float, qmax: float) -> float:
    """查询向量（float）与索引向量（int16 量化）的余弦相似度。

    反量化与打分融在一个循环里，避免为每个块建中间 list —— 52 块 × 384 维在
    纯 Python 下这点分配是可观的常数开销。
    """
    scale = (qmax - qmin) / 65535.0
    offset = qmin + 32768 * scale
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, v_int in zip(query_vec, idx_vec):
        y = v_int * scale + offset
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    if denom < 1e-12:
        return 0.0
    return dot / denom


# ---------------------------------------------------------------------------
# TF-IDF 启发式检索
# ---------------------------------------------------------------------------

#: 同义词表：口语化查询词 → 块名/描述中可能出现的正式词。
#: 这张表是人工维护的，目的是弥补字符重叠打分对同义改写的盲区。
_SYNONYMS: Dict[str, FrozenSet[str]] = {
    '加': frozenset({'求和', '累加', '总和', '汇总'}),
    '求和': frozenset({'加', '累加', '总和', '汇总', '合计'}),
    '总和': frozenset({'求和', '累加', '汇总'}),
    '平均': frozenset({'均值', '平均值', '算术平均'}),
    '均值': frozenset({'平均', '平均值'}),
    '排序': frozenset({'升序', '降序', '排列', '排'}),
    # 单字 `排` / 短语 `从大到小` / `从小到大` 均需能扩展到升/降序块名
    '排': frozenset({'升序', '降序', '排序', '排列'}),
    '排列': frozenset({'升序', '降序', '排序'}),
    '升序': frozenset({'升序', '排序', '正序'}),
    '降序': frozenset({'降序', '排序', '倒序'}),
    '倒序': frozenset({'降序'}),
    '正序': frozenset({'升序'}),
    '最大': frozenset({'极值', '最高', '峰值'}),
    '最小': frozenset({'极值', '最低', '谷值'}),
    '极值': frozenset({'最大', '最小', '峰值', '谷值'}),
    '去重': frozenset({'唯一', '不重复', '去除重复'}),
    '唯一': frozenset({'去重', '不重复', '唯一码'}),
    # `重复的元素清理掉` → 去重；`别重复算了` → 缓存表；两个块并行覆盖
    '重复': frozenset({'去重', '不重复', '去除重复', '缓存表'}),
    '编号': frozenset({'唯一码', '随机码', '序号'}),
    '生成': frozenset({'唯一码', '随机码'}),
    '清理': frozenset({'去重', '文本清洗', '净化'}),
    '保存': frozenset({'存文', '写入', '落盘', '持久化'}),
    '写入': frozenset({'存文', '保存', '落盘'}),
    '读取': frozenset({'载入', '读入', '加载', '读'}),
    '加载': frozenset({'载入', '读取', '读入'}),
    '载入': frozenset({'读取', '加载', '读入'}),
    '身份证': frozenset({'证号', '证件号', '身份'}),
    '手机': frozenset({'手机号', '电话', '号码'}),
    '电话': frozenset({'手机号', '手机', '号码'}),
    '繁体': frozenset({'繁简', '简繁', '繁体字', '简体'}),
    '简体': frozenset({'繁简', '简繁', '繁体'}),
    '数字': frozenset({'数字中文', '中文数字', '大写数字'}),
    '金额': frozenset({'金额雅写', '金额报表', '大写金额', '人民币'}),
    '钱': frozenset({'金额', '金额雅写', '金额报表'}),
    '清洗': frozenset({'文本清洗', '清理', '净化'}),
    '空格': frozenset({'文本清洗', '排版规整', '去空白'}),
    '空白': frozenset({'文本清洗', '排版规整', '去空白'}),
    '切分': frozenset({'文本切分', '分割', '分词', '切割', '分段'}),
    '拆': frozenset({'文本切分', '词语切分'}),
    '拆成': frozenset({'文本切分', '词语切分'}),
    '分段': frozenset({'文本切分', '词语切分'}),
    '逗号': frozenset({'文本切分', '词语切分'}),
    '分词': frozenset({'词语切分', '切分', '分割'}),
    '合并': frozenset({'文本合成', '拼接', '合成', '连接'}),
    '拼接': frozenset({'文本合成', '合并', '合成'}),
    '接成': frozenset({'文本合成', '拼接'}),
    '字符串': frozenset({'文本合成', '文本切分', '文本清洗', '拼接'}),
    '统计': frozenset({'批量统计', '计数', '汇总'}),
    '网址': frozenset({'网址拼装', '网址剖解', 'URL', '链接'}),
    'URL': frozenset({'网址', '网址拼装', '链接'}),
    '链接': frozenset({'网址', 'URL', '网址拼装'}),
    '请求': frozenset({'请求组装', '发送请求', 'HTTP'}),
    '重试': frozenset({'重试策略', '重新尝试', '失败重试'}),
    '响应': frozenset({'响应处理', '返回', '状态码'}),
    '日志': frozenset({'记录', '打印', '输出'}),
    '计时': frozenset({'耗时', '用时', '时间', '性能'}),
    '时间': frozenset({'时戳', '计时', '时间戳', '当前时间'}),
    '哈希': frozenset({'摘要', '散列', '校验'}),
    '摘要': frozenset({'哈希', '散列', 'hash', 'MD5', 'SHA'}),
    '编码': frozenset({'转码', '转义编码', '编解码'}),
    '解码': frozenset({'转码', '编码', '编解码'}),
    '环境变量': frozenset({'环境值', '配置', 'env'}),
    '配置': frozenset({'配置集', '环境值', '设置'}),
    '农历': frozenset({'阴历', '阴历日程', '干支', '属相', '历法'}),
    '阴历': frozenset({'农历', '阴历日程', '干支'}),
    '日期': frozenset({'农历', '阴历', '历法', '干支'}),
    '属相': frozenset({'生肖', '属相表', '十二生肖'}),
    '生肖': frozenset({'属相', '属相表'}),
    '天干': frozenset({'干支', '地支', '天干地支'}),
    '干支': frozenset({'天干', '地支', '天干地支', '农历'}),
    '断言': frozenset({'测试', '验证', '检查', 'assert'}),
    '测试': frozenset({'断言', '验证'}),
    '路径': frozenset({'路径查验', '文件路径', '目录'}),
    '目录': frozenset({'目录清单', '文件夹', '路径'}),
    '文件夹': frozenset({'目录', '目录清单'}),
    '批量': frozenset({'批处理', '批量统计', '循环'}),
    '分页': frozenset({'分页参数', '翻页', '页码'}),
    '翻页': frozenset({'批量翻页', '分页', '分页参数'}),
    # --- v0.13.0 M2 B1/B2：财务 / 历法域口语入口 ---
    '税': frozenset({'个税', '增值税', '税单'}),
    '工资': frozenset({'个税', '缴税'}),
    '扣税': frozenset({'个税', '缴税'}),
    '所得税': frozenset({'个税'}),
    '账面': frozenset({'折旧', '折价'}),
    '设备': frozenset({'折旧', '固定资产'}),
    '摊销': frozenset({'折旧'}),
    '利息': frozenset({'单利', '复利', '贴现'}),
    '月供': frozenset({'等额本息', '分期', '年金'}),
    '汇率': frozenset({'换汇'}),
    '分位': frozenset({'保留分'}),
    '几天': frozenset({'日差', '月长', '天距'}),
    '天数': frozenset({'日差', '月长'}),
    '相差': frozenset({'日差', '天距'}),
    '星期': frozenset({'周几'}),
    '年龄': frozenset({'周岁', '生辰'}),
    # --- v0.13.0 M2 B3：数据 / 工具域口语入口 ---
    '每批': frozenset({'分组', '批处理'}),
    '一批': frozenset({'分组', '批处理'}),
    '切成': frozenset({'分组', '切片', '文本切分'}),
    '离散': frozenset({'方差', '标准差'}),
    '中间': frozenset({'中位数'}),
    '嵌套': frozenset({'扁平', '摊平'}),
    '缓存': frozenset({'缓存表', '存表'}),
    '算过': frozenset({'缓存表'}),
    'key': frozenset({'缓存表', '配置集'}),
    '副本': frozenset({'深拷贝', '深摹'}),
    '类型': frozenset({'型名', '型别'}),
    '验证码': frozenset({'随机码', '唯一码'}),
    # --- v0.13.0 M2 B3：中文域新块 ---
    '序数': frozenset({'序数中文', '第几', '排名', '名次'}),
    '第几': frozenset({'序数中文', '序数', '排名'}),
    '量词': frozenset({'量词', '单位', '数量词', '几个'}),
    '单位': frozenset({'量词', '数量词'}),
    '简称': frozenset({'简称', '缩写', '缩略', '略称'}),
    '缩写': frozenset({'简称', '缩略', '略称'}),
    '姓名': frozenset({'姓名拆分', '姓氏', '名字', '百家姓'}),
    '姓氏': frozenset({'姓名拆分', '姓名', '百家姓'}),
    '地址': frozenset({'地址剖解', '省市区', '行政区', '收货地址'}),
    '省市区': frozenset({'地址剖解', '地址', '行政区'}),
    '叠词': frozenset({'叠词', '叠字', '重叠', '重复词'}),
    '叠字': frozenset({'叠词', '叠字', '重叠'}),
    # --- v0.13.0 M2 B3：网络域新块 ---
    '域名': frozenset({'域名剖解', '子域', 'domain', '主机名'}),
    '子域': frozenset({'域名剖解', '域名', '主机名'}),
    '端口': frozenset({'端口判定', '端口号', 'port'}),
    'port': frozenset({'端口判定', '端口', '端口号'}),
    '校验': frozenset({'网址校验', '完整性', '验证', '合法性'}),
    '合法': frozenset({'网址校验', '校验', '合法性'}),
    '表单': frozenset({'表单串', 'form', 'urlencode', '表单编码'}),
    'form': frozenset({'表单串', '表单', '表单编码'}),
    '超时': frozenset({'超时策略', 'timeout', '等待时长', '退避'}),
    '退避': frozenset({'超时策略', '重试策略', '指数退避'}),
    '跳转': frozenset({'跳转链', '重定向', 'redirect'}),
    '重定向': frozenset({'跳转链', '跳转', 'redirect'}),
}

#: 领域关键词：查询中出现这些词时，对应领域的块获得先验加分。
_DOMAIN_KEYWORDS: Dict[str, FrozenSet[str]] = {
    '数据': frozenset({
        '数据', '数字', '数值', '列表', '数组', '排序', '统计', '计算',
        '求和', '均值', '平均', '去重', '极值', '最大', '最小', '保存',
        '读取', '写入', '文件', '切分', '合并', '清洗', '文本',
    }),
    '中文': frozenset({
        '中文', '中国', '汉字', '身份证', '手机', '繁体', '简体', '农历',
        '阴历', '干支', '属相', '生肖', '金额', '人民币', '大写', '清洗',
        '分词', '雅写', '雅正', '排版',
    }),
    '网络': frozenset({
        '网络', '网址', 'URL', 'HTTP', '请求', '响应', '接口', 'API',
        '分页', '翻页', '重试', '编码', '解码', '转义', '查询串', '头部',
        '状态码',
    }),
    '工具': frozenset({
        '工具', '断言', '测试', '日志', '计时', '时间', '哈希', '摘要',
        '唯一', 'UUID', '编码', '解码', '环境', '配置', '批处理', '路径',
        '目录', '文件夹',
    }),
    # v0.13.0 M2 新增两个领域。刻意与「中文」域区分：
    #   中文域是表现层（金额→大写、公历→农历字符串）
    #   财务/历法域是计算层（利息、税额、日期算术）
    '财务': frozenset({
        '财务', '利息', '利率', '本金', '复利', '单利', '贴现', '折现',
        '年金', '月供', '房贷', '贷款', '分期', '还款', '税', '个税',
        '缴税', '交税', '增值税', '税额', '折旧', '摊销', '汇率', '换汇',
        '投资', '收益', '现值', '终值', '账', '金额', '钱', '元',
    }),
    '历法': frozenset({
        '历法', '日期', '闰年', '闰', '月长', '天数', '日差', '相差',
        '星期', '周几', '周岁', '实岁', '年龄', '岁', '节气', '时辰',
        '旬', '纪年', '干支', '生肖', '属相', '农历', '阴历', '生辰',
        '月历', '日历', '几号', '几天', '多少天',
    }),
}

#: 领域先验加分权重。
_DOMAIN_BOOST = 1.5

#: 块名完全匹配加分。
_NAME_EXACT_BOOST = 6.0

#: 块名包含在查询中的加分（子串匹配）。
_NAME_SUBSTR_BOOST = 4.0

#: 同义词命中加分。
_SYNONYM_BOOST = 2.5


def _tokenize_chinese(text: str) -> List[str]:
    """极简中文 tokenizer：按连续汉字/字母数字切 token。

    不引入分词库。对 2-3 字块名足够区分（配合同义词表弥补）。
    产出 unigram + bigram 混合（bigram 捕获"求和""去重"这类双字词）。
    """
    tokens: List[str] = []
    buf: List[str] = []

    def _flush():
        if buf:
            word = ''.join(buf)
            # unigram
            for ch in word:
                tokens.append(ch)
            # bigram
            for i in range(len(word) - 1):
                tokens.append(word[i:i+2])
            # trigram（捕获三字词如"身份证""批处理"）
            for i in range(len(word) - 2):
                tokens.append(word[i:i+3])
            buf.clear()

    for ch in text:
        if '\u4e00' <= ch <= '\u9fff' or ch.isalnum():
            buf.append(ch)
        else:
            _flush()
    _flush()
    return tokens


def _expand_synonyms(tokens: List[str]) -> List[str]:
    """用同义词表扩展 token 列表。"""
    expanded = list(tokens)
    seen = set(tokens)
    for t in tokens:
        syns = _SYNONYMS.get(t)
        if syns:
            for s in syns:
                if s not in seen:
                    expanded.append(s)
                    seen.add(s)
    return expanded


class _TFIDFIndex:
    """块语料的 TF-IDF 倒排索引。纯内存构建，进程级缓存。"""

    __slots__ = ('_blocks', '_doc_tokens', '_idf', '_doc_norms', '_N')

    def __init__(self, blocks: List[dict]):
        self._blocks = blocks
        self._N = len(blocks)
        self._doc_tokens: List[Counter] = []
        df: Counter = Counter()

        for block in blocks:
            text = ' '.join([
                block.get('名称', ''),
                block.get('描述', ''),
                ' '.join(block.get('领域', [])),
            ])


            tf = Counter(_tokenize_chinese(text))
            self._doc_tokens.append(tf)
            for term in tf:
                df[term] += 1

        # IDF: log((N + 1) / (df + 1)) + 1（平滑，避免零除和极端值）
        self._idf: Dict[str, float] = {}
        for term, freq in df.items():
            self._idf[term] = math.log((self._N + 1) / (freq + 1)) + 1.0

        # 文档 L2 范数（预计算加速检索）
        self._doc_norms: List[float] = []
        for tf in self._doc_tokens:
            norm_sq = 0.0
            for term, count in tf.items():
                w = count * self._idf.get(term, 1.0)
                norm_sq += w * w
            self._doc_norms.append(math.sqrt(norm_sq) if norm_sq > 0 else 1.0)

    def search(self, query: str, top: int = 5) -> List[Tuple[int, float]]:
        """返回 (块索引, 分数) 列表，按分数降序。"""
        q_tokens = _tokenize_chinese(query)
        q_expanded = _expand_synonyms(q_tokens)
        q_tf = Counter(q_expanded)

        # query 向量范数
        q_norm_sq = 0.0
        for term, count in q_tf.items():
            idf = self._idf.get(term, 1.0)
            w = count * idf
            q_norm_sq += w * w
        q_norm = math.sqrt(q_norm_sq) if q_norm_sq > 0 else 1.0

        scores: List[Tuple[int, float]] = []
        for i, doc_tf in enumerate(self._doc_tokens):
            dot = 0.0
            for term, q_count in q_tf.items():
                d_count = doc_tf.get(term, 0)
                if d_count > 0:
                    idf = self._idf.get(term, 1.0)
                    dot += (q_count * idf) * (d_count * idf)
            if dot <= 0:
                continue
            sim = dot / (q_norm * self._doc_norms[i])

            # 加分项
            block = self._blocks[i]
            name = block.get('名称', '')
            domains = block.get('领域', [])

            # 块名精确/子串匹配
            if name and name == query:
                sim += _NAME_EXACT_BOOST
            elif name and name in query:
                sim += _NAME_SUBSTR_BOOST
            elif name and query in name:
                sim += _NAME_SUBSTR_BOOST * 0.5

            # 同义词命中块名
            for t in q_tokens:
                syns = _SYNONYMS.get(t)
                if syns and name in syns:
                    sim += _SYNONYM_BOOST
                    break

            # 领域先验
            for domain in domains:
                keywords = _DOMAIN_KEYWORDS.get(domain)
                if keywords:
                    overlap = sum(1 for t in q_tokens if t in keywords)
                    if overlap > 0:
                        sim += _DOMAIN_BOOST * min(overlap, 3) / 3.0

            scores.append((i, sim))

        scores.sort(key=lambda x: -x[1])
        return scores[:top]


# ---------------------------------------------------------------------------
# Retriever 主类
# ---------------------------------------------------------------------------


class Retriever:
    """语义块检索器。

    用法::

        r = Retriever(blocks)          # blocks = 索引.json 的 '块' 列表
        hits = r.retrieve("求个总和")   # -> List[Hit]

    或带向量索引::

        r = Retriever(blocks, vector_index=vi)
        hits = r.retrieve("求个总和")   # 走神经路径
    """

    def __init__(self, blocks: List[dict],
                 vector_index: Optional[VectorIndex] = None,
                 mode: str = MODE_AUTO):
        self._blocks = blocks
        self._vi = vector_index
        self._mode = mode
        self._tfidf: Optional[_TFIDFIndex] = None
        self._block_name_map: Dict[str, int] = {
            b.get('名称', ''): i for i, b in enumerate(blocks)
        }

    @property
    def mode(self) -> str:
        return self._resolve_mode()

    def _resolve_mode(self) -> str:
        if self._mode == MODE_ENV or self._mode == MODE_AUTO:
            env_val = os.environ.get(_ENV_MODE, '').strip().lower()
            if env_val == 'heuristic':
                return MODE_HEURISTIC
            if env_val == 'neural':
                return MODE_NEURAL
            # AUTO: 有索引走神经，否则启发式
            if self._mode == MODE_AUTO:
                if self._vi is not None and self._vi.count > 0:
                    return MODE_NEURAL
                return MODE_HEURISTIC
            return MODE_HEURISTIC
        return self._mode

    def _get_tfidf(self) -> _TFIDFIndex:
        if self._tfidf is None:
            self._tfidf = _TFIDFIndex(self._blocks)
        return self._tfidf

    def retrieve(self, query: str, top: int = 5,
                 query_vector: Optional[Sequence[float]] = None) -> List[Hit]:
        """执行检索，返回 top-K 结果。

        Args:
            query: 自然语言需求文本。
            top: 候选数上限。
            query_vector: 查询的 embedding。**神经路径的必要条件** —— 运行时
                自身不做模型推理（零依赖），向量必须由调用方（`tools/ai-bridge/`
                或云端 API）给出。缺省时无论 mode 如何都走启发式。
        """
        if not query or not self._blocks:
            return []
        if self._resolve_mode() == MODE_NEURAL and query_vector is not None:
            return self._retrieve_neural(query_vector, top)
        return self._retrieve_heuristic(query, top)

    def _retrieve_heuristic(self, query: str, top: int) -> List[Hit]:
        """TF-IDF + 同义词 + 领域先验。"""
        tfidf = self._get_tfidf()
        results = tfidf.search(query, top)
        hits: List[Hit] = []
        for idx, score in results:
            block = self._blocks[idx]
            hits.append(Hit(
                score=score,
                name=block.get('名称', ''),
                domain=(block.get('领域') or ['?'])[0],
                description=block.get('描述', ''),
                path=PATH_HEURISTIC,
                example=block.get('示例', ''),
                namespace=block.get(_NAMESPACE_KEY) or '',
            ))
        return hits

    def _retrieve_neural(self, query_vector: Sequence[float],
                         top: int) -> List[Hit]:
        """对 `向量索引.bin` 做余弦相似度检索。

        维度不匹配时降级返回空列表的调用方语义不友好，所以这里抛
        `RetrievalError` —— 维度错了是调用方用错了模型，属于编程错误而非
        运行时环境问题，静默降级只会让人查半天。
        """
        vi = self._vi
        if vi is None or vi.count == 0:
            return []
        if len(query_vector) != vi.dim:
            raise RetrievalError(
                '查询向量维度 %d 与索引维度 %d 不符（模型不一致？）'
                % (len(query_vector), vi.dim))

        scored: List[Tuple[float, int]] = []
        for i, vec in enumerate(vi.vectors):
            sim = _cosine_sim_quantized(query_vector, vec, vi.qmin, vi.qmax)
            scored.append((sim, i))
        scored.sort(key=lambda x: -x[0])

        hits: List[Hit] = []
        for sim, i in scored[:top]:
            name = vi.names[i]
            block = self._blocks[self._block_name_map[name]] \
                if name in self._block_name_map else {}
            hits.append(Hit(
                score=sim,
                name=name,
                domain=(block.get('领域') or ['?'])[0],
                description=block.get('描述', ''),
                path=PATH_NEURAL,
                example=block.get('示例', ''),
                namespace=block.get(_NAMESPACE_KEY) or '',
            ))
        return hits


# ---------------------------------------------------------------------------
# 模块级便捷 API（进程级缓存）
# ---------------------------------------------------------------------------

_cached_retriever: Optional[Retriever] = None


def _load_builtin_blocks() -> List[dict]:
    """加载 `stdlib/blocks/索引.json` 的内置块列表。"""
    from .. import resources
    idx_path = resources.stdlib_path('blocks', '索引.json')
    if not os.path.isfile(idx_path):
        _log.warning(
            '块索引未找到：%s——内置块检索将为空。'
            '若 stdlib 不在包内默认位置，请设 JIKUAI_STDLIB 或显式传入 blocks 列表。',
            idx_path)
        return []
    with open(idx_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('块', [])


def _load_third_party_blocks() -> List[dict]:
    """现扫已装块包的第三方块，投影成与内置索引同构的条目（ADR-32 §2.3 检索侧）。

    **为什么不读持久化索引**：`stdlib/blocks/索引.json` 是版本控制里的产物，
    `generate_index` 刻意只传 `root=blocks_root()` 以免把某台机器上装的第三方块
    写进仓库。第三方块因此没有、也不该有一份提交进 git 的索引——只能现扫。
    代价是每个新进程首次 `retrieve()` 多一次目录遍历，靠 `_cached_retriever`
    的进程级缓存摊掉。

    **为什么不传 `roots=`**：`scan_blocks(roots=[...])` 会**跳过** `extra_roots`
    的注册逻辑，扫到的块 `namespace=''`——因为 namespace 只在遍历 `_registered_roots`
    的名义映射时才被填。想要「内置 + 装了的第三方」这套完整聚合，只能用无参
    `scan_blocks()`，再靠 `namespace` 非空过滤掉内置。改成传 roots 会静默漏掉命名
    空间，检索出来的块名字冲突时无法定位来源。

    **失败必须隔离**：第三方包的 `块.json` 是外部输入，坏一个不该让内置块也搜
    不到。这里与门禁的「解析不了就是它自己坏了」philosophy 相反——门禁是 CI 期
    的守卫，本函数是用户运行期的路径，可用性优先于严格性。故整段兜 Exception
    并降级为空列表 + 一条 warning。
    """
    try:
        from ..pkg import blocks as _B          # 延迟导入：不用检索就不拖块子系统
        return [b.to_index_entry() for b in _B.scan_blocks()
                if b.namespace]
    except Exception as e:                      # noqa: BLE001 —— 见 docstring
        _log.warning('扫描第三方块失败，本次检索只含内置块：%s: %s',
                     type(e).__name__, e)
        return []


def _load_blocks() -> List[dict]:
    """内置块 + 已装块包的第三方块，内置优先。

    去重键取 `(命名空间, 名称)`：内置块命名空间是空串，第三方块取包名
    （ADR-32 §2.4），所以同名不同源不会互相遮蔽；真撞上同键时**内置先入为主**，
    与发现侧 `extra_roots()` 的合并顺序和执行侧「第三方块挂在 stdlib 之前搜索路径」
    保持同一套优先级语义。
    """
    合并: List[dict] = []
    见过 = set()
    for entry in list(_load_builtin_blocks()) + _load_third_party_blocks():
        键 = (entry.get('命名空间') or '', entry.get('名称'))
        if 键 in 见过:
            continue
        见过.add(键)
        合并.append(entry)
    return 合并


def _get_retriever() -> Retriever:
    """取进程级缓存的检索器。

    **装包后需显式 `reset_cache()`**：第三方块是现扫的，但扫描结果被缓存在
    `_cached_retriever` 里，同一进程内装完包不会自动生效。CLI 每条命令一个新
    进程所以无感；库调用方（含测试）装完包要自己清缓存。
    """
    global _cached_retriever
    if _cached_retriever is None:
        blocks = _load_blocks()
        vi = load_vector_index()
        _cached_retriever = Retriever(blocks, vector_index=vi)
    return _cached_retriever


def retrieve(query: str, top: int = 5,
             query_vector: Optional[Sequence[float]] = None) -> List[Hit]:
    """便捷入口：检索语义最相关的块。

    首次调用会加载索引（进程级缓存）。线程安全问题：GIL 保护下的简单赋值
    足够——最坏情况多加载一次，不影响正确性。

    Args:
        query: 自然语言需求文本（中文）。
        top: 返回候选数上限。
        query_vector: 查询 embedding，由调用方提供才走神经路径（见
            `Retriever.retrieve`）。

    Returns:
        Hit 列表，按相关度降序。
    """
    return _get_retriever().retrieve(query, top, query_vector)


def reset_cache() -> None:
    """清除进程级缓存（测试用）。"""
    global _cached_retriever
    _cached_retriever = None


def describe() -> dict:
    """返回当前检索器状态描述（供 CLI 诊断用）。"""
    r = _get_retriever()
    vi = r._vi
    return {
        '模式': r.mode,
        '块数': len(r._blocks),
        '向量索引': {
            '可用': True,
            '维度': vi.dim,
            '块数': vi.count,
        } if vi else {'可用': False},
        '说明': '神经路径还需调用方提供查询向量，否则仍走启发式',
    }
