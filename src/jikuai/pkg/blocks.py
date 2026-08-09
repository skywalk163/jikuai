# -*- coding: utf-8 -*-
"""极快块生态 —— `块.json` 元数据解析与块索引生成（ADR-15 §3.2 / §3.4）。

一个"块"是一个带元数据的 `.jk` 模块。物理形态两种（见 docs/块生态.md §2）：

    stdlib/blocks/<领域>/<块名>/块.json     目录形态（主流，可带 .py 与 测试.jk）
    stdlib/blocks/<领域>/<块名>.块.json     单文件形态（简单块）

本模块不打印任何东西（与 `pkg` 其他非 cli 模块同约定，便于被 `scripts/`、
CI、LSP、测试以库形式复用）。四组能力：

    load_block_metadata(path)   读一份 `块.json` 并做字段校验
    scan_blocks(root)           递归扫描目录，返回按「名称」排序的块列表
    generate_index(root)        构造 `索引.json` 的内存结构
    validate_block(dir)         全面校验一个块目录，返回 (错误, 警告)


为什么索引里只有一个子集字段
--------------------------
`索引.json` 的消费者是 AI Agent（ADR-15 §3.4 桥接协议）与 CLI 检索。它只需要
"这个块叫什么、干什么、吃什么、吐什么"，不需要 `依赖块`/`极快版本`/`示例`
这些安装期与文档期字段——后者按需读原始 `块.json`。索引越窄，一次性读入的
token 成本越低，这正是块生态压缩 token 的关键。

零第三方依赖，与包管理其余模块一致。
"""

import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import semver

__all__ = [
    'BLOCK_METADATA_NAME', 'BLOCK_METADATA_SUFFIX', 'BLOCK_INDEX_NAME',
    'BLOCK_INDEX_VERSION', 'ALLOWED_DOMAINS', 'STABILITY_LEVELS',
    'DEFAULT_STABILITY',
    'VECTOR_INDEX_NAME', 'VECTOR_INDEX_META_NAME',
    'BlockError', 'BlockMetadata',
    'blocks_root', 'index_path', 'load_block_metadata', 'find_block_files',
    'scan_blocks', 'generate_index', 'render_index', 'load_index',
    'save_index', 'index_differs',
    'blocks_content_hash', 'vector_index_meta_path', 'check_vector_index',
    'vector_index_bin_path',
    'check_export_atomicity', 'check_module_segment_atomicity',
    'extract_exports', 'validate_block',
]

#: 目录形态的元数据文件名。
BLOCK_METADATA_NAME = '块.json'

#: 单文件形态的元数据后缀（`读取文件.块.json`）。
BLOCK_METADATA_SUFFIX = '.块.json'

#: 索引文件名。落在 `stdlib/blocks/` 下，进版本控制以便 diff（ADR-15 §4）。
BLOCK_INDEX_NAME = '索引.json'

#: 索引结构版本。**刻意不复用 `jikuai.__version__`**——索引格式的兼容性由
#: 块生态自己的节奏决定，解释器发小版本不该让所有索引失效重生成。
BLOCK_INDEX_VERSION = '0.12.0'

#: 神经检索的向量索引与其 sidecar 元信息（ADR-25 §3.2/§4）。二者都由
#: `tools/ai-bridge/generate_embeddings.py` 生成；本模块只读、只做一致性比对，
#: 不碰 embedding（那需要 torch，属可选依赖层）。
VECTOR_INDEX_NAME = '向量索引.bin'
VECTOR_INDEX_META_NAME = '向量索引.元信息.json'


#: 领域白名单（ADR-15 §2.2 / docs/块生态.md §3）。扩展需走领域注册流程，
#: 不允许贡献者随手造新领域——否则 CLI 的 `--领域` 过滤会退化成自由文本。
#: v0.13.0 M2 注册 `财务`（金额计算层，区别于 `中文` 域的金额表现层）与
#: `历法`（日期数学，区别于 `中文` 域的农历/干支文化表现层）。
ALLOWED_DOMAINS = frozenset({'数据', '中文', '网络', '工具', '财务', '历法'})

#: 稳定性等级。CLI 默认只推荐 `stable`。
STABILITY_LEVELS = frozenset({'experimental', 'stable', 'deprecated'})

#: 未声明 `稳定性` 时的默认值。取最保守的一档：没表态的块不该被 CLI 推荐。
DEFAULT_STABILITY = 'experimental'

#: 块名白名单。规则同 `manifest._NAME_RE`（中文/字母/数字/下划线/连字符），
#: 但**不套用包名的保留字表**——`工具`、`分词` 这类词在块生态里是正常块名，
#: 块不会被安装进 `极快_包/`，与标准库模块不存在遮蔽关系。
_NAME_RE = re.compile(r'^[\u4e00-\u9fffA-Za-z0-9_\-]{1,64}$')

#: 扫描时跳过的目录，避免把缓存/版本库内容当块。
_SKIP_DIRS = frozenset({
    '.git', '.hg', '.svn', '__pycache__', 'node_modules',
    '.mypy_cache', '.pytest_cache', '极快_包',
})

#: 索引条目字段顺序。固定顺序 + 按名称排序 = git diff 稳定。
_INDEX_ENTRY_KEYS = ('名称', '领域', '层级', '描述', '输入', '输出', '稳定性')


class BlockError(Exception):
    """块元数据缺失、不是合法 JSON，或字段不合规。"""


class BlockMetadata:
    """一份 `块.json` 的内存表示。只读访问，不提供写回。"""

    __slots__ = ('_data', 'path')

    def __init__(self, data: dict, path: Optional[str] = None):
        self._data = data
        self.path = path

    # ---- 必填字段 --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._data['名称']

    @property
    def version(self) -> str:
        return self._data['版本']

    @property
    def level(self) -> int:
        """层级：0=原子块，N=聚合了 N-1 级块。是标签，不是目录。"""
        return self._data['层级']

    @property
    def domains(self) -> List[str]:
        return list(self._data['领域'])

    @property
    def description(self) -> str:
        return self._data['描述']

    # ---- 选填字段 --------------------------------------------------------
    @property
    def inputs(self) -> List[dict]:
        return list(self._data.get('输入') or [])

    @property
    def output(self) -> dict:
        return dict(self._data.get('输出') or {})

    @property
    def dep_blocks(self) -> List[str]:
        """`依赖块` 字段。G11 门禁会校验它与 `.jk` 里的 `导入` 一致（W3 上线）。"""
        return list(self._data.get('依赖块') or [])

    @property
    def jikuai_requirement(self) -> Optional[str]:
        return self._data.get('极快版本')

    @property
    def example(self) -> str:
        return self._data.get('示例', '')

    @property
    def stability(self) -> str:
        return self._data.get('稳定性', DEFAULT_STABILITY)

    # ---- 派生 ------------------------------------------------------------
    @property
    def root(self) -> str:
        """块所在目录（元数据文件所在目录）。"""
        if self.path is None:
            raise BlockError('该块元数据没有关联文件路径')
        return os.path.dirname(os.path.abspath(self.path))

    def to_dict(self) -> dict:
        return self._data

    def to_index_entry(self) -> Dict[str, Any]:
        """投影成索引条目（字段与顺序固定，见 `_INDEX_ENTRY_KEYS`）。"""
        entry = {
            '名称': self.name,
            '领域': self.domains,
            '层级': self.level,
            '描述': self.description,
            '输入': self.inputs,
            '输出': self.output,
            '稳定性': self.stability,
        }
        return {k: entry[k] for k in _INDEX_ENTRY_KEYS}

    def __repr__(self):
        return '<块 %s 层级%s %s>' % (self.name, self.level, self.stability)


# ---------------------------------------------------------------------------
# 路径定位
# ---------------------------------------------------------------------------

def blocks_root() -> str:
    """返回内置 `stdlib/blocks/` 的绝对路径。

    定位方式与 `module_loader._search_paths()` 找 stdlib 的方式保持一致：
    从本文件位置上溯到仓库根（`src/jikuai/pkg` → 上三级）。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(here, '..', '..', '..'))
    return os.path.join(repo_root, 'stdlib', 'blocks')


def index_path(root: Optional[str] = None) -> str:
    """索引文件的绝对路径。"""
    return os.path.join(os.path.abspath(root or blocks_root()), BLOCK_INDEX_NAME)


# ---------------------------------------------------------------------------
# 校验与加载
# ---------------------------------------------------------------------------

def _fail(msg: str, path: Optional[str]) -> None:
    where = '（%s）' % path if path else ''
    raise BlockError('%s%s' % (msg, where))


def _validate(data: Any, path: Optional[str]) -> None:
    """校验 `块.json` 字段。不合规直接抛 `BlockError`。"""
    if not isinstance(data, dict):
        _fail('块元数据顶层必须是对象', path)

    for field in ('名称', '版本', '层级', '领域', '描述'):
        if field not in data:
            _fail('块元数据缺少必填字段「%s」' % field, path)

    name = data['名称']
    if not isinstance(name, str) or not _NAME_RE.match(name):
        _fail('块「名称」不合法：%r（只允许中文、字母、数字、下划线、连字符，'
              '1-64 字，且不含点与路径分隔符）' % (name,), path)

    try:
        semver.parse_version(data['版本'])
    except semver.InvalidVersion as e:
        _fail('块「版本」不合法：%s' % e, path)

    level = data['层级']
    # bool 是 int 的子类，`"层级": true` 必须挡掉
    if isinstance(level, bool) or not isinstance(level, int) or level < 0:
        _fail('块「层级」必须是非负整数，得到 %r' % (level,), path)

    domains = data['领域']
    if not isinstance(domains, list) or not domains:
        _fail('块「领域」必须是非空数组', path)
    for d in domains:
        if not isinstance(d, str):
            _fail('块「领域」的每一项必须是字符串，得到 %r' % (d,), path)
        if d not in ALLOWED_DOMAINS:
            _fail('块「领域」%r 不在白名单内（允许：%s），扩展领域需走 ADR-15 '
                  '注册流程' % (d, '/'.join(sorted(ALLOWED_DOMAINS))), path)
    if len(set(domains)) != len(domains):
        _fail('块「领域」有重复项：%r' % (domains,), path)

    desc = data['描述']
    if not isinstance(desc, str) or not desc.strip():
        _fail('块「描述」必须是非空字符串', path)

    inputs = data.get('输入')
    if inputs is not None:
        if not isinstance(inputs, list):
            _fail('块「输入」必须是数组', path)
        for item in inputs:
            if not isinstance(item, dict):
                _fail('块「输入」的每一项必须是对象 {"名": ..., "类型": ...}', path)
            for key in ('名', '类型'):
                if key not in item:
                    _fail('块「输入」的项缺少「%s」：%r' % (key, item), path)
                if not isinstance(item[key], str) or not item[key].strip():
                    _fail('块「输入」的「%s」必须是非空字符串：%r' % (key, item), path)

    output = data.get('输出')
    if output is not None:
        if not isinstance(output, dict):
            _fail('块「输出」必须是对象 {"类型": ...}', path)
        if output and '类型' not in output:
            _fail('块「输出」缺少「类型」', path)

    deps = data.get('依赖块')
    if deps is not None:
        if not isinstance(deps, list):
            _fail('块「依赖块」必须是数组', path)
        for d in deps:
            if not isinstance(d, str) or not d.strip():
                _fail('块「依赖块」的每一项必须是非空字符串，得到 %r' % (d,), path)

    req = data.get('极快版本')
    if req is not None:
        try:
            semver.parse_constraint(req)
        except (semver.InvalidConstraint, semver.InvalidVersion) as e:
            _fail('块「极快版本」不合法：%s' % e, path)

    example = data.get('示例')
    if example is not None and not isinstance(example, str):
        _fail('块「示例」必须是字符串', path)

    stability = data.get('稳定性')
    if stability is not None and stability not in STABILITY_LEVELS:
        _fail('块「稳定性」必须是 %s 之一，得到 %r'
              % ('/'.join(sorted(STABILITY_LEVELS)), stability), path)


def load_block_metadata(path: str) -> BlockMetadata:
    """读取一份块元数据。

    `path` 可以是：
    - `块.json` 文件本身
    - `<块名>.块.json` 文件本身
    - 一个块目录（自动拼 `块.json`）
    """
    if os.path.isdir(path):
        path = os.path.join(path, BLOCK_METADATA_NAME)
    if not os.path.isfile(path):
        raise BlockError('找不到块元数据文件：%s' % path)

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise BlockError('块元数据不是合法 JSON（%s）：第 %d 行 %s'
                         % (path, e.lineno, e.msg)) from None
    except UnicodeDecodeError:
        raise BlockError('块元数据编码不是 UTF-8：%s' % path) from None

    _validate(data, path)
    return BlockMetadata(data, path=os.path.abspath(path))


def _expected_name(path: str) -> str:
    """从元数据文件路径推导应有的块名（用于名称/路径一致性校验）。"""
    base = os.path.basename(path)
    if base == BLOCK_METADATA_NAME:
        return os.path.basename(os.path.dirname(os.path.abspath(path)))
    return base[:-len(BLOCK_METADATA_SUFFIX)]


def find_block_files(root: Optional[str] = None) -> List[str]:
    """递归收集所有块元数据文件的绝对路径，按路径排序。"""
    base = os.path.abspath(root or blocks_root())
    if not os.path.isdir(base):
        return []

    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        # 原地裁剪 dirnames 才能真正阻止 os.walk 下钻
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in filenames:
            if fn == BLOCK_METADATA_NAME or (
                    fn.endswith(BLOCK_METADATA_SUFFIX)
                    and len(fn) > len(BLOCK_METADATA_SUFFIX)):
                found.append(os.path.join(dirpath, fn))
    return sorted(found)


def scan_blocks(root: Optional[str] = None) -> List[BlockMetadata]:
    """扫描 `root`（默认内置 `stdlib/blocks/`），返回按「名称」排序的块列表。

    三重一致性保证，任一不满足直接抛 `BlockError`：
    1. 每份元数据都通过 `_validate` 字段校验
    2. `名称` 与所在目录名（或 `<名>.块.json` 前缀）一致
    3. 全局块名唯一——重名会让 `jk 块 详情 X` 无法定位，也会让索引条目二义
    """
    blocks: List[BlockMetadata] = []
    seen: Dict[str, str] = {}

    for path in find_block_files(root):
        meta = load_block_metadata(path)
        expected = _expected_name(path)
        if meta.name != expected:
            raise BlockError('块「名称」%r 与路径不一致（应为 %r）：%s'
                             % (meta.name, expected, path))
        if meta.name in seen:
            raise BlockError('块名重复：%r 同时出现在 %s 与 %s'
                             % (meta.name, seen[meta.name], path))
        seen[meta.name] = path
        blocks.append(meta)

    # 按名称字典序（Unicode 码点序）排序——确定性排序保证索引 git diff 稳定
    blocks.sort(key=lambda b: b.name)
    return blocks


# ---------------------------------------------------------------------------
# 索引生成 / 读写 / 比对
# ---------------------------------------------------------------------------

def generate_index(root: Optional[str] = None,
                   version: str = BLOCK_INDEX_VERSION,
                   timestamp: Optional[str] = None) -> dict:
    """扫描块目录并构造索引结构（ADR-15 §3.4）。

    `timestamp` 省略时取本地当前时间，秒级精度的 ISO 8601（不带微秒——
    微秒对人没用，只会让 diff 更吵）。
    """
    if timestamp is None:
        timestamp = datetime.now().replace(microsecond=0).isoformat()
    return {
        '版本': version,
        '生成时间': timestamp,
        '块': [b.to_index_entry() for b in scan_blocks(root)],
    }


def render_index(index: dict) -> str:
    """索引的规范化文本形态：UTF-8、`ensure_ascii=False`、2 空格缩进、尾换行。"""
    return json.dumps(index, ensure_ascii=False, indent=2) + '\n'


def load_index(path: Optional[str] = None) -> Optional[dict]:
    """读现有索引。文件不存在返回 `None`；存在但坏了抛 `BlockError`。"""
    target = os.path.abspath(path or index_path())
    if not os.path.isfile(target):
        return None
    try:
        with open(target, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise BlockError('索引不是合法 JSON（%s）：第 %d 行 %s'
                         % (target, e.lineno, e.msg)) from None
    except UnicodeDecodeError:
        raise BlockError('索引编码不是 UTF-8：%s' % target) from None


def save_index(index: dict, path: Optional[str] = None) -> str:
    """原子写索引。返回写入的绝对路径。

    `newline='\\n'` 是必需的：Windows 下默认会把 `\\n` 翻成 `\\r\\n`，
    同一份索引在 Windows 与 Linux 的 CI 上就会产生整文件级 diff。
    """
    target = os.path.abspath(path or index_path())
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(render_index(index))
    os.replace(tmp, target)      # 原子替换，避免中断留下半个索引
    return target


def index_differs(existing: Optional[dict], fresh: dict) -> bool:
    """判断现有索引是否已过期。

    **只比 `版本` 与 `块`，刻意忽略 `生成时间`**。否则每次运行时间戳都变，
    `--check` 门禁必然失败，索引也会在每个 commit 里无意义地翻动一行。
    """
    if not isinstance(existing, dict):
        return True
    return (existing.get('版本') != fresh.get('版本')
            or existing.get('块') != fresh.get('块'))


# ---------------------------------------------------------------------------
# 向量索引一致性（G12，ADR-25 §3.3）
# ---------------------------------------------------------------------------

def blocks_content_hash(blocks: List[Dict[str, Any]]) -> str:
    """对索引条目列表算内容哈希，用于 G12 比对。

    按「名称」排序后逐条 `sort_keys` 序列化再喂 SHA-256——两端必须字节级一致，
    所以排序与键序都要固定，不能依赖 dict 的插入顺序。

    生成端（`tools/ai-bridge/generate_embeddings.py`）与校验端（`scripts/
    check_stdlib_contract.py`）都调本函数，避免两处各写一遍算法后悄悄漂移。
    """
    h = hashlib.sha256()
    for b in sorted(blocks, key=lambda x: x.get('名称', '')):
        h.update(json.dumps(b, ensure_ascii=False, sort_keys=True).encode('utf-8'))
    return 'sha256:%s' % h.hexdigest()


def vector_index_meta_path(root: Optional[str] = None) -> str:
    """`向量索引.元信息.json` 的绝对路径。"""
    return os.path.join(os.path.abspath(root or blocks_root()),
                        VECTOR_INDEX_META_NAME)


def vector_index_bin_path(root: Optional[str] = None) -> str:
    """`向量索引.bin` 的绝对路径。"""
    return os.path.join(os.path.abspath(root or blocks_root()),
                        VECTOR_INDEX_NAME)


def check_vector_index(root: Optional[str] = None) -> tuple:
    """G12：校验向量索引与 `索引.json` 是否同源。返回 `(状态, 说明)`。

    状态取值：

    - ``'缺失'``   —— 两个文件都不在。**不算失败**：ADR-25 §3.1 允许无索引，
      运行时会降级启发式，普通贡献者不该被迫装 torch 重生成索引。
    - ``'一致'``   —— 块数、名称集合、内容哈希三项全对。
    - ``'不一致'`` —— 任一项不符，或只有半套文件。说明里点出差异，提示重跑
      `generate_embeddings.py`。

    只用标准库：本门禁必须能在不装 torch 的常规 CI 上跑（ADR-25 §3.3）。
    """
    root = os.path.abspath(root or blocks_root())
    meta_path = vector_index_meta_path(root)
    bin_path = vector_index_bin_path(root)
    has_meta = os.path.isfile(meta_path)
    has_bin = os.path.isfile(bin_path)

    if not has_meta and not has_bin:
        return ('缺失', '向量索引未生成（允许，运行时降级启发式）')
    if not has_meta:
        return ('不一致', '有 %s 但缺 %s' % (VECTOR_INDEX_NAME,
                                          VECTOR_INDEX_META_NAME))
    if not has_bin:
        return ('不一致', '有 %s 但缺 %s' % (VECTOR_INDEX_META_NAME,
                                          VECTOR_INDEX_NAME))

    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return ('不一致', '%s 不是合法 UTF-8 JSON：%s' % (VECTOR_INDEX_META_NAME, e))

    index = load_index(index_path(root))
    if index is None:
        return ('不一致', '%s 尚未生成，无法比对' % BLOCK_INDEX_NAME)
    blocks = index.get('块') or []

    问题 = []
    if meta.get('块数') != len(blocks):
        问题.append('块数不符（元信息 %r vs 索引 %d）'
                  % (meta.get('块数'), len(blocks)))
    实算 = blocks_content_hash(blocks)
    if meta.get('块哈希') != 实算:
        问题.append('块内容哈希不符（元信息 %s vs 实算 %s）'
                  % (meta.get('块哈希'), 实算))
    if 问题:
        return ('不一致', '；'.join(问题)
                + '。请重跑 python tools/ai-bridge/generate_embeddings.py')
    return ('一致', '向量索引与块索引同源（%d 块，%s 维）'
            % (len(blocks), meta.get('维度')))



# ---------------------------------------------------------------------------
# 词法原子性校验 / 块目录全面校验（ADR-15 §3.7）
# ---------------------------------------------------------------------------
#
# 背景：dogfooding 实测发现，块的**导出名必须整体被 lexer 识别为单个 IDENT**，
# 否则调用方引用不到。调用方在 `导入` 时不知道被导入模块的导出名，ADR-09
# 白名单与成员访问松弛都覆盖不到——非原子名在调用方一侧必然被切碎：
#
#     块求和 → 块(IDENT) + 求和(VERB)   ✗ 报 JK-E5002 模块 X 未导出：块
#     累加   → 累(IDENT) + 加(VERB)     ✗
#     汇总   → 汇总(IDENT)              ✓
#
# **块目录名（点分路径段）另有一套更松的标准**：`parser._read_module_name()`
# 对每一段松弛接受任意单个词形 token（IDENT/VERB/ADVERB/KEYWORD），所以
# `求和`（VERB）作目录名完全合法。只要被切成 >=2 个 token 就死：
#
#     配置加载 → 配置(IDENT)+加(VERB)+载(IDENT)  ✗ `从 blocks.工具.配置加载
#                                                    导入 X` 直接 ParseError
#     求和     → 求和(VERB)                       ✓
#
# 两套判定分别由 `check_export_atomicity`（恰好单 IDENT）与
# `check_module_segment_atomicity`（恰好单 token）表达。

#: `导出 X` 声明的行级正则。允许一行里挤多个（`导出 甲，乙。`），
#: 通过 `_split_export_group` 再切分。
_EXPORT_RE = re.compile(r'导出\s+([^\n。]+)')

#: `blocks.<X>.<Y>...` 点分路径。用于从 `.jk` 里回扫 `导入` / `从 ... 导入`
#: 语句，取叶段名与元数据 `依赖块` 做一致性校验。
_BLOCK_PATH_RE = re.compile(
    r'blocks((?:\.[\u4e00-\u9fffA-Za-z0-9_\-]+)+)'
)

#: 单行导出名分隔符：中英逗号、顿号、空白都算。
_EXPORT_SEP_RE = re.compile(r'[，,、\s]+')


def check_module_segment_atomicity(name):
    """校验一个模块路径段（块目录名）是否词法原子。

    与 `check_export_atomicity` 不同，**目录名松弛接受任何单个词形 token**
    （IDENT / VERB / ADVERB / KEYWORD），因为 `_read_module_name()` 在解析
    点分路径时对每一段都允许这些类型。只有 tokenize 后（去掉 NEWLINE/EOF/PERIOD）
    剩余 >=2 个 token 才是死块——它无法作为点分路径段被导入。

    返回 `(是否原子, 切分结果)`。`切分结果` 是 `[(词形, 文本), ...]`，
    非原子时供报错展示（如 `配置(IDENT)+加(VERB)+载(IDENT)`）。
    """
    from ..lexer import tokenize                       # 延迟导入避环
    from ..tokens import TokenType

    tokens = tokenize(name + '。')
    skip = {TokenType.NEWLINE, TokenType.EOF, TokenType.PERIOD}
    kept = [t for t in tokens if t.type not in skip]
    # 目录名只要切出恰好 1 个 token（无论类型）即合法
    atomic = len(kept) == 1
    pieces = [(t.type.name, t.value) for t in kept]
    return atomic, pieces


def check_export_atomicity(name):
    """校验一个导出名是否词法原子（整体是单个 IDENT）。

    做法：把 `name + '。'` 喂进 lexer，过滤掉 NEWLINE/EOF/PERIOD，
    若剩余恰为 1 个 `TokenType.IDENT` 即视为原子。

    返回 `(是否原子, 切分结果)`。`切分结果` 是 `[(词形, 文本), ...]`，
    非原子时供报错展示（如 `块(IDENT)+求和(VERB)`）。

    **延迟导入 lexer/tokens**：`pkg` 子包不在解释器核心加载路径上，但把
    lexer 提到模块级 import 会让 `jikuai.pkg` 的库使用者（比如 CLI/CI）
    平白拖入分词器代码。放到函数内是最小侵入的懒加载。
    """
    from ..lexer import tokenize                       # 延迟导入避环
    from ..tokens import TokenType

    tokens = tokenize(name + '。')
    skip = {TokenType.NEWLINE, TokenType.EOF, TokenType.PERIOD}
    kept = [t for t in tokens if t.type not in skip]
    atomic = len(kept) == 1 and kept[0].type == TokenType.IDENT
    pieces = [(t.type.name, t.value) for t in kept]
    return atomic, pieces


def extract_exports(jk_path):
    """从一个块的 `.jk` 文件里提取所有 `导出 X` 声明的名字集合。

    行级实现，`--` 行内注释会被剥掉；不解析字符串字面量里的假 `导出`
    ——极快块里出现这种反例的概率约等于零，不值得再套 lexer。
    """
    with open(jk_path, 'r', encoding='utf-8') as f:
        source = f.read()
    names = set()
    for raw in source.splitlines():
        line = raw.split('--', 1)[0]                   # 去掉行内注释
        for m in _EXPORT_RE.finditer(line):
            for part in _EXPORT_SEP_RE.split(m.group(1)):
                part = part.strip()
                if part:
                    names.add(part)
    return names


def _extract_import_deps(source):
    """从 `.jk` 源码提取所有 `blocks.X.Y...` 导入引用的**叶段名**。

    两种形态都命中：
        从 blocks.数据.求和 导入 汇总
        导入 blocks.数据.求和
    """
    deps = set()
    for raw in source.splitlines():
        line = raw.split('--', 1)[0]
        for m in _BLOCK_PATH_RE.finditer(line):
            segs = [s for s in m.group(1).split('.') if s]
            if segs:
                deps.add(segs[-1])
    return deps


def validate_block(block_dir):
    """全面校验一个块目录，返回 `(错误列表, 警告列表)`。

    检查项（顺序即报错优先级）：

    1. `块.json` 存在且合法（复用 `load_block_metadata`）
    2. 块目录名（即 `块.json` 的 `名称` 字段）词法原子——否则该块无法作为
       点分路径段被 `从 blocks.X.名称 导入` 引用，是死块（ADR-15 §3.7）
    3. 主 `.jk` 文件存在（`<块名>.jk` 或 `main.jk`）
    4. 至少有一个 `导出` 声明
    5. 每个导出名词法原子（不原子 → 错误，附切分结果与建议）
    6. `依赖块` 字段与 `.jk` 里的 `从 blocks.X.Y 导入` / `导入 blocks.X.Y`
       语句一致（不一致 → 错误）
    7. 每个 `依赖块` 名字词法原子（不原子 → 警告，非错误：依赖块自身
       的合法性由被依赖块自己的校验负责）
    8. `测试.jk` 存在（缺失 → 警告，非错误）

    元数据都读不出来（步骤 1 失败）时直接返回，后续检查无从谈起。
    """
    errors = []
    warnings = []
    block_dir = os.path.abspath(block_dir)

    # 1. 元数据
    try:
        meta = load_block_metadata(block_dir)
    except BlockError as e:
        errors.append(str(e))
        return errors, warnings

    # 2. 块目录名（即 名称 字段）词法原子性
    # 目录名要能作为 `blocks.<领域>.<名称>` 点分路径段被 lexer 单 token 化，
    # 否则调用方 `从 blocks.X.名称 导入 Y` 直接 ParseError——是死块。
    # 与导出名不同：目录名允许单个 VERB/KEYWORD/ADVERB（如 `求和` 作目录名
    # 合法，由 `_read_module_name()` 的松弛条件保证）。
    dir_atomic, dir_pieces = check_module_segment_atomicity(meta.name)
    if not dir_atomic:
        dir_frag = '+'.join('%s(%s)' % (v, t) for t, v in dir_pieces)
        errors.append(
            '块目录名「%s」非词法原子，切分为 %s，'
            '无法作为点分路径段被导入' % (meta.name, dir_frag))

    # 3. 主 .jk 文件
    candidates = [
        os.path.join(block_dir, meta.name + '.jk'),
        os.path.join(block_dir, 'main.jk'),
    ]
    jk_path = next((p for p in candidates if os.path.isfile(p)), None)
    if jk_path is None:
        errors.append('缺少主 .jk 文件（应为 %s.jk 或 main.jk）' % meta.name)
        return errors, warnings

    with open(jk_path, 'r', encoding='utf-8') as f:
        source = f.read()

    # 4. 至少一个导出
    exports = extract_exports(jk_path)
    if not exports:
        errors.append('%s 没有任何 `导出` 声明' % os.path.basename(jk_path))

    # 5. 每个导出名词法原子
    for name in sorted(exports):
        atomic, pieces = check_export_atomicity(name)
        if not atomic:
            frag = '+'.join('%s(%s)' % (v, t) for t, v in pieces)
            errors.append(
                '导出名「%s」非词法原子，切分为 %s，'
                '建议改用 汇总/合计/聚合' % (name, frag))

    # 6. 依赖块 与 import 一致
    imported = _extract_import_deps(source)
    declared = set(meta.dep_blocks)
    if imported != declared:
        missing = imported - declared      # 代码里导入了但未声明
        extra = declared - imported        # 声明了但代码没用
        detail = []
        if missing:
            detail.append('代码导入但未声明：%s' % '、'.join(sorted(missing)))
        if extra:
            detail.append('声明但代码未导入：%s' % '、'.join(sorted(extra)))
        errors.append('依赖块 与 .jk 导入语句不一致（%s）' % '；'.join(detail))

    # 7. 每个依赖块名词法原子（警告，非错误）
    # 依赖块名也是点分路径段。非原子说明依赖指向一个死块——但那个块的
    # 合法性由它自己的校验负责，这里只提醒，不把本块判为不合格。
    for dep in meta.dep_blocks:
        dep_atomic, dep_pieces = check_module_segment_atomicity(dep)
        if not dep_atomic:
            dep_frag = '+'.join('%s(%s)' % (v, t) for t, v in dep_pieces)
            warnings.append(
                '依赖块名「%s」非词法原子，切分为 %s，可能指向死块'
                % (dep, dep_frag))

    # 8. 测试.jk（警告）
    if not os.path.isfile(os.path.join(block_dir, '测试.jk')):
        warnings.append('缺少 测试.jk')

    return errors, warnings

