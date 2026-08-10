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
    'DEFAULT_STABILITY', 'MAX_BLOCK_LEVEL', 'AGGREGATE_LEVEL', 'L3_LEVEL',
    'SCALAR_TYPES', 'CONTAINER_TYPE_NAMES', 'UNION_TYPE_NAME',
    'VECTOR_INDEX_NAME', 'VECTOR_INDEX_META_NAME',
    'BlockError', 'BlockMetadata',
    'NAMESPACE_KEY', 'BUILTIN_NAMESPACE', 'PKG_ROOTS_ENV',
    'blocks_root', 'extra_roots', 'index_path', 'load_block_metadata',
    'find_block_files',
    'scan_blocks', 'generate_index', 'render_index', 'load_index',
    'save_index', 'index_differs',
    'blocks_content_hash', 'vector_index_meta_path', 'check_vector_index',
    'vector_index_bin_path',
    'check_export_atomicity', 'check_module_segment_atomicity',
    'extract_exports', 'block_exports', 'validate_block',
    'check_type_annotation', 'check_stdlib_type_annotations',
    'check_export_globally_unique',
    'build_dependency_graph', 'find_dependency_cycles',
    'check_dependency_acyclic', 'check_level_consistency',
    'check_stability_propagation',
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

#: 类型词表（ADR-26）。标量类型：可直接作为 `类型` 字段的字符串值。
SCALAR_TYPES = frozenset({'数', '字符串', '布尔', '函数', '任意'})

#: 容器类型名。作为结构化 `类型` 对象的 `类型` 字段值，或作为向后兼容的裸
#: 字符串（裸 `列表` 视为 `列表<任意>`，裸 `字典` 视为 `字典<字符串,任意>`）。
CONTAINER_TYPE_NAMES = frozenset({'列表', '字典', '元组'})

#: 联合类型名。只有结构化形态：`{"类型": "联合", "候选": [...]}`。
#: 裸 `联合` 无意义（等同 `任意`），故不进 `CONTAINER_TYPE_NAMES`。
UNION_TYPE_NAME = '联合'

#: 未声明 `稳定性` 时的默认值。取最保守的一档：没表态的块不该被 CLI 推荐。
DEFAULT_STABILITY = 'experimental'

#: 层级上限（ADR-28 §3.3）。W29 起把 `层级` 从"任意非负整数"收紧到 `0..3`。
#:
#: **只开到 L3，刻意不开 L4**：层级每深一层，检索侧就多一层"为什么给你选这个块"
#: 的解释负担（ADR-25 的召回解释链），粘合器的类型链推导分支也跟着涨。先用 L3
#: 证明跨域场景聚合确有价值，再谈更深。要开 L4 必须另立 ADR + 拿出度量。
MAX_BLOCK_LEVEL = 3

#: 「聚合块」的层级门槛：`层级 >= 2` 视为聚合了子块的复合/场景块。
#: L3 判定与稳定性传递都只对 L2+ 依赖计数（见 ADR-28 §3.1/§3.2）。
AGGREGATE_LEVEL = 2

#: L3（跨 L2 场景块）的层级值。单独起个名字，避免判定逻辑里散落魔法数字 3。
L3_LEVEL = 3


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
#: W7 起带上 `导出`：AI 桥接选块后要立刻拼出 `从 blocks.X.Y 导入 Z。`，
#: 没有它就得为每个候选块再读一次 `.jk`。`兼容` **不进索引**——它是安装期
#: 字段，检索期用不上，进索引只会白涨 token。
#: W22（ADR-27）起追加 `命名空间`：第三方块的完整引用是
#: `<命名空间>.<领域>.<块名>`，内置块为空串。**刻意追加在末尾**——旧条目
#: 只多一个尾字段，已有 7 个字段的行位不动，`索引.json` 的 git diff 最小。
_INDEX_ENTRY_KEYS = ('名称', '领域', '层级', '描述', '输入', '输出', '导出',
                     '稳定性', '命名空间')

#: 索引条目 / `BlockMetadata._data` 里承载命名空间的键名。
#: **不进 `块.json` schema**——命名空间由目录布局决定，不该让发布者手填
#: （手填必然与目录漂移，且 `_validate` 也没法交叉验证）。扫描时注入。
NAMESPACE_KEY = '命名空间'

#: 内置块（`stdlib/blocks/`）的命名空间。空串，保证 `blocks.数据.求和`
#: 这类既有引用形态一字不变（ADR-27 §2.2）。
BUILTIN_NAMESPACE = ''

#: 第三方块根的环境变量名（ADR-27 §2.1）。语义是**块目录根**，即与
#: `blocks_root()` 同层级、直接指向 `blocks/` 那一级；多路径按 `os.pathsep`
#: 分隔（Windows `;`，POSIX `:`），与 `PYTHONPATH` 的习惯一致。
PKG_ROOTS_ENV = 'JIKUAI_PKG_ROOTS'



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

    @property
    def exports(self) -> List[str]:
        """`导出` 字段（ADR-24 §5 / W7）。缺省 `[]`——回退读 `.jk` 由 `extract_exports` 负责。"""
        return list(self._data.get('导出') or [])

    @property
    def compat(self) -> dict:
        """`兼容` 字段（W7）。环境兼容性约束表，如 `{"极快": ">=0.13"}`。缺省 `{}`。"""
        return dict(self._data.get('兼容') or {})

    @property
    def namespace(self) -> str:
        """命名空间（ADR-27 §2.2）。内置块为空串，第三方块为其注册表目录名。

        **不是 `块.json` 的字段**——由 `scan_blocks` 按目录布局注入，见
        `_infer_namespace`。单独 `load_block_metadata` 出来的块拿不到路径
        上下文，因此落到缺省空串（等同"当内置块看"）。
        """
        return self._data.get(NAMESPACE_KEY) or BUILTIN_NAMESPACE

    @property
    def qualified_name(self) -> str:
        """完整引用名 `<命名空间>.<领域>.<块名>`（内置块省掉命名空间段）。

        领域取 `领域[0]`——多领域块的物理目录只可能落在一个领域下，第一个
        就是它的所属目录（`scan_blocks` 的名称/路径一致性已经保证）。
        """
        域 = self.domains[0] if self.domains else ''
        段 = [s for s in (self.namespace, 域, self.name) if s]
        return '.'.join(段)

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
            '导出': self.exports,
            '稳定性': self.stability,
            NAMESPACE_KEY: self.namespace,
        }
        return {k: entry[k] for k in _INDEX_ENTRY_KEYS}

    def __repr__(self):
        if self.namespace:
            return '<块 %s/%s 层级%s %s>' % (self.namespace, self.name,
                                           self.level, self.stability)
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


def extra_roots() -> List[str]:
    """第三方块根列表（ADR-27 §2.1），读自环境变量 `JIKUAI_PKG_ROOTS`。

    语义与 `blocks_root()` 同层级——**每条路径直接指向 `blocks/` 那一级**，
    其下第一级子目录即命名空间。多路径按 `os.pathsep` 分隔（Windows `;`，
    POSIX `:`），与 `PYTHONPATH` 习惯一致。

    过滤规则：空串跳过；不是已存在目录的跳过（配错路径不该让整个块生态崩，
    只是那个根不生效）；去重但保序（先出现的优先，与合并规则的"先到先得"
    一致）。返回绝对路径列表。
    """
    raw = os.environ.get(PKG_ROOTS_ENV, '')
    结果: List[str] = []
    见过 = set()
    for 段 in raw.split(os.pathsep):
        段 = 段.strip()
        if not 段:
            continue
        abs_p = os.path.abspath(段)
        if abs_p in 见过:
            continue
        见过.add(abs_p)
        if os.path.isdir(abs_p):
            结果.append(abs_p)
    return 结果


def _infer_namespace(meta_path: str, root: str, is_builtin: bool) -> str:
    """按目录布局推断一个块的命名空间（ADR-27 §2.2）。

    - 内置根（`is_builtin=True`）：一律空串，`blocks.数据.求和` 形态不变。
    - 第三方根：相对 `root` 的**第一段目录名**即命名空间
      （`<root>/<命名空间>/<领域>/<块名>/块.json`）。

    第三方块若没套命名空间目录（元数据直接躺在 root 下），相对路径只有文件名
    一段，此时视为空命名空间——它会和内置块一起参与"同命名空间唯一"检查，
    等于要求它别和内置块重名，属合理兜底。
    """
    if is_builtin:
        return BUILTIN_NAMESPACE
    rel = os.path.relpath(os.path.abspath(meta_path), os.path.abspath(root))
    段 = [s for s in rel.split(os.sep) if s and s != os.pardir]
    # 至少要有 命名空间/.../块.json 两段才谈得上命名空间；只有一段说明块元数据
    # 直接躺在 root 下，没有命名空间目录。
    if len(段) < 2:
        return BUILTIN_NAMESPACE
    return 段[0]



def index_path(root: Optional[str] = None) -> str:
    """索引文件的绝对路径。"""
    return os.path.join(os.path.abspath(root or blocks_root()), BLOCK_INDEX_NAME)


# ---------------------------------------------------------------------------
# 校验与加载
# ---------------------------------------------------------------------------

def _fail(msg: str, path: Optional[str]) -> None:
    where = '（%s）' % path if path else ''
    raise BlockError('%s%s' % (msg, where))


def _validate_type_annotation(value: Any, where: str, path: Optional[str]) -> None:
    """校验一个类型标注（ADR-26 类型词表）。不合规抛 `BlockError`。

    合法形态：
    - 标量字符串：`SCALAR_TYPES` 之一（数/字符串/布尔/函数/任意）
    - 容器裸字符串：`列表`/`字典`/`元组`（向后兼容，未标细化视为通配元素）
    - 结构化容器对象：
      - `{"类型": "列表", "元素类型": <类型>}`
      - `{"类型": "字典", "键类型": <类型>, "值类型": <类型>}`
      - `{"类型": "元组", "元数": [<类型>, ...]}`
      - `{"类型": "联合", "候选": [<类型>, <类型>, ...]}`

    `where` 是给报错用的上下文串（如 `块「输出」的类型`）。
    """
    if isinstance(value, str):
        if value in SCALAR_TYPES or value in CONTAINER_TYPE_NAMES:
            return
        _fail('%s「%s」不是合法类型（标量取 %s，容器取 %s，或用结构化对象细化）'
              % (where, value, '/'.join(sorted(SCALAR_TYPES)),
                 '/'.join(sorted(CONTAINER_TYPE_NAMES))), path)

    if not isinstance(value, dict):
        _fail('%s必须是类型字符串或结构化类型对象，得到 %r' % (where, value), path)

    kind = value.get('类型')
    if not isinstance(kind, str) or not kind.strip():
        _fail('%s的结构化类型对象缺少合法「类型」字段：%r' % (where, value), path)
    if kind not in CONTAINER_TYPE_NAMES and kind != UNION_TYPE_NAME:
        _fail('%s的结构化类型「%s」只能是容器或联合（%s/%s）；标量请用字符串'
              % (where, kind, '/'.join(sorted(CONTAINER_TYPE_NAMES)),
                 UNION_TYPE_NAME), path)

    if kind == '列表':
        if '元素类型' not in value:
            _fail('%s的 列表 缺少「元素类型」' % where, path)
        _validate_type_annotation(value['元素类型'], '%s→元素类型' % where, path)
    elif kind == '字典':
        for k in ('键类型', '值类型'):
            if k not in value:
                _fail('%s的 字典 缺少「%s」' % (where, k), path)
            _validate_type_annotation(value[k], '%s→%s' % (where, k), path)
    elif kind == '元组':
        元数 = value.get('元数')
        if not isinstance(元数, list) or not 元数:
            _fail('%s的 元组 的「元数」必须是非空数组' % where, path)
        for i, item in enumerate(元数):
            _validate_type_annotation(item, '%s→元数[%d]' % (where, i), path)
    else:
        候选 = value.get('候选')
        if not isinstance(候选, list) or len(候选) < 2:
            _fail('%s的 联合 的「候选」必须是至少 2 项的数组' % where, path)
        for i, item in enumerate(候选):
            _validate_type_annotation(item, '%s→候选[%d]' % (where, i), path)


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
    # W29（ADR-28 §3.3）起加上限：`层级` 只允许 0..MAX_BLOCK_LEVEL。
    # v0.15.0 及以前不设上限（`"层级": 7` 也能过），但深层聚合既没有块能证明
    # 价值，又会拖垮检索解释与粘合器链式推导，故本轮显式封到 L3。
    # 走到这里 level 已经是非负 int（上面不合规会 `_fail` 抛出）。
    if level > MAX_BLOCK_LEVEL:
        _fail('块「层级」最大 %d（L0 原子 / L1 复合 / L2 场景 / L3 跨域场景），'
              '得到 %r；本轮不开 L4，见 ADR-28 §3.3'
              % (MAX_BLOCK_LEVEL, level), path)

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
            if not isinstance(item['名'], str) or not item['名'].strip():
                _fail('块「输入」的「名」必须是非空字符串：%r' % (item,), path)
            _validate_type_annotation(item['类型'],
                                      '块「输入」的项 %r 的类型' % item.get('名'),
                                      path)

    output = data.get('输出')
    if output is not None:
        if not isinstance(output, dict):
            _fail('块「输出」必须是对象 {"类型": ...}', path)
        if output:
            if '类型' not in output:
                _fail('块「输出」缺少「类型」', path)
            _validate_type_annotation(output['类型'], '块「输出」的类型', path)

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

    # `导出`（选填，ADR-24 §5 / W7）：与 `.jk` 里的 `导出 X。` 一致的名字数组。
    # 有了它，反哺白名单与 AI 桥接都不必再读 `.jk` 源码。逐项过原子性校验——
    # 非原子导出名在调用方一侧必被切碎（ADR-15 §3.7），写进元数据也救不回来。
    exports = data.get('导出')
    if exports is not None:
        if not isinstance(exports, list) or not exports:
            _fail('块「导出」必须是非空数组', path)
        for name in exports:
            if not isinstance(name, str) or not name.strip():
                _fail('块「导出」的每一项必须是非空字符串，得到 %r' % (name,), path)
            atomic, pieces = check_export_atomicity(name)
            if not atomic:
                frag = '+'.join('%s(%s)' % (v, t) for t, v in pieces)
                _fail('块「导出」项「%s」非词法原子，切分为 %s' % (name, frag), path)
        if len(set(exports)) != len(exports):
            _fail('块「导出」有重复项：%r' % (exports,), path)

    # `兼容`（选填，W7）：环境兼容性约束表，如 {"极快": ">=0.13"}。
    # 与 `极快版本` 的分工：后者是**安装期**的解释器版本门槛（单一约束串）；
    # `兼容` 是**可扩展**的多维声明，未来可加 Python/OS 维度而不动 schema。
    compat = data.get('兼容')
    if compat is not None:
        if not isinstance(compat, dict) or not compat:
            _fail('块「兼容」必须是非空对象，如 {"极快": ">=0.13"}', path)
        for k, v in compat.items():
            if not isinstance(k, str) or not k.strip():
                _fail('块「兼容」的键必须是非空字符串，得到 %r' % (k,), path)
            if not isinstance(v, str) or not v.strip():
                _fail('块「兼容」的「%s」必须是非空版本约束串' % (k,), path)
            try:
                semver.parse_constraint(v)
            except (semver.InvalidConstraint, semver.InvalidVersion) as e:
                _fail('块「兼容」的「%s」不是合法版本约束：%s' % (k, e), path)


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


def scan_blocks(root: Optional[str] = None,
                roots: Optional[List[str]] = None) -> List[BlockMetadata]:
    """扫描块目录，返回按（命名空间, 名称）排序的块列表。

    三种调用形态（ADR-27 §3.1）：

    1. ``scan_blocks()``           扫内置 `blocks_root()` + `extra_roots()`。
       默认合并规则：内置在前，第三方按 `JIKUAI_PKG_ROOTS` 顺序追加。
    2. ``scan_blocks(root=X)``     单根扫描，命名空间一律为空
       （**向后兼容**：v0.14.0 及以前的调用者与既有测试用例都走这里）。
    3. ``scan_blocks(roots=[...])``  显式多根扫描；**第一个视为内置根**
       （命名空间空串），其余为第三方根（按目录布局推断命名空间）。

    一致性保证：

    - 每份元数据都通过 `_validate` 字段校验（`load_block_metadata`）。
    - `名称` 与所在目录名（或 `<名>.块.json` 前缀）一致。
    - **同一命名空间内块名唯一**（内置块也算同一"空命名空间"）；跨命名空间
      允许同名——`blocks.数据.求和` 与 `blocks.社区.数据.求和` 是两个块。
    - 合并规则（ADR-27 §2.3）：先扫内置、再追加第三方；第三方块若与已扫入的
      任一块**同（命名空间, 名称）**冲突则跳过（内置优先）。跨命名空间同名
      共存，不视为冲突。
    """
    if root is not None and roots is not None:
        raise BlockError('scan_blocks: `root` 与 `roots` 不可同时指定')

    # 组装扫描目标：(绝对路径, 是否内置)
    if root is not None:
        targets = [(os.path.abspath(root), True)]
    elif roots is not None:
        if not roots:
            targets = []
        else:
            targets = [(os.path.abspath(roots[0]), True)]
            targets += [(os.path.abspath(r), False) for r in roots[1:]]
    else:
        targets = [(os.path.abspath(blocks_root()), True)]
        targets += [(r, False) for r in extra_roots()]

    blocks: List[BlockMetadata] = []
    # 键是 (命名空间, 名称)：同一命名空间内不允许重名；跨命名空间可以同名。
    seen: Dict[tuple, str] = {}

    for base, is_builtin in targets:
        for path in find_block_files(base):
            meta = load_block_metadata(path)
            expected = _expected_name(path)
            if meta.name != expected:
                raise BlockError('块「名称」%r 与路径不一致（应为 %r）：%s'
                                 % (meta.name, expected, path))
            ns = _infer_namespace(path, base, is_builtin)
            # 命名空间不进 `块.json` schema，扫描时按路径注入；`_validate` 已
            # 过，此处直接改 `_data` 让 `to_index_entry` / `qualified_name`
            # 拿得到。
            meta._data[NAMESPACE_KEY] = ns
            key = (ns, meta.name)
            if key in seen:
                if is_builtin:
                    # 内置扫描阶段的重名是硬错误（stdlib 自己不该出重名块）。
                    raise BlockError(
                        '块名重复（命名空间 %r 内）：%r 同时出现在 %s 与 %s'
                        % (ns, meta.name, seen[key], path))
                # 第三方阶段的同（命名空间, 名称）冲突：内置优先，静默跳过。
                # ADR-27 §2.3 明确"同名冲突警告不失败"——这里选择"跳过"这条
                # 具体策略，避免让 `jk 块 列表` 出现二义条目。
                continue
            seen[key] = path
            blocks.append(meta)

    # 排序键：(命名空间, 名称)。内置块命名空间为空串一律排在最前，保持既有
    # 索引里的字典序不变（避免让 stdlib 的 `索引.json` 因排序变化整体翻动）。
    blocks.sort(key=lambda b: (b.namespace, b.name))
    return blocks


# ---------------------------------------------------------------------------
# 索引生成 / 读写 / 比对
# ---------------------------------------------------------------------------

def generate_index(root: Optional[str] = None,
                   version: str = BLOCK_INDEX_VERSION,
                   timestamp: Optional[str] = None,
                   roots: Optional[List[str]] = None) -> dict:
    """扫描块目录并构造索引结构（ADR-15 §3.4 / ADR-27 §2.4）。

    `timestamp` 省略时取本地当前时间，秒级精度的 ISO 8601（不带微秒——
    微秒对人没用，只会让 diff 更吵）。

    `root` / `roots` 与 `scan_blocks` 同义：单根或多根。二者都不传时按
    `scan_blocks()` 的缺省合并内置 + `JIKUAI_PKG_ROOTS`。**内置 stdlib 索引
    刻意只传 `root=blocks_root()`**（见 `scripts/generate_block_index.py`），
    避免把某台机器上配的第三方块写进版本控制的 `索引.json`。
    """
    if timestamp is None:
        timestamp = datetime.now().replace(microsecond=0).isoformat()
    return {
        '版本': version,
        '生成时间': timestamp,
        '块': [b.to_index_entry() for b in scan_blocks(root, roots)],
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

#: G12 内容哈希**刻意排除**的索引条目字段。
#:
#: 哈希的用途只有一个：回答「embedding 需不需要重生成」。
#: `generate_embeddings.py::_build_texts` 的嵌入语料是 `名称 + 领域 + 描述`，
#: `命名空间` 一个字都没进去——它是**注册表位置**属性，不是语义内容。把它算进
#: 哈希会让「块换个命名空间目录」这种零语义变更也逼着重跑一次 GPU 编码
#: （本机还未必连得上模型仓库），纯属自找麻烦。
_HASH_EXCLUDED_KEYS = frozenset({NAMESPACE_KEY})


def blocks_content_hash(blocks: List[Dict[str, Any]]) -> str:
    """对索引条目列表算内容哈希，用于 G12 比对。

    按「名称」排序后逐条 `sort_keys` 序列化再喂 SHA-256——两端必须字节级一致，
    所以排序与键序都要固定，不能依赖 dict 的插入顺序。

    生成端（`tools/ai-bridge/generate_embeddings.py`）与校验端（`scripts/
    check_stdlib_contract.py`）都调本函数，避免两处各写一遍算法后悄悄漂移。

    `_HASH_EXCLUDED_KEYS` 里的字段（W22 起的 `命名空间`）不参与哈希：它们不进
    嵌入语料，改了也不影响向量，没有触发重生成的必要。
    """
    h = hashlib.sha256()
    for b in sorted(blocks, key=lambda x: x.get('名称', '')):
        参与 = {k: v for k, v in b.items() if k not in _HASH_EXCLUDED_KEYS}
        h.update(json.dumps(参与, ensure_ascii=False,
                            sort_keys=True).encode('utf-8'))
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
# 类型标注精度（G14，ADR-26 §4.3）
# ---------------------------------------------------------------------------
#
# `_validate` 与 G14 分工：
#   `_validate` —— schema 合法性。裸 `列表`/`字典`/`元组` 通过（向后兼容，
#                  第三方块库不该因为 v0.14.0 收紧词表而整体失效）。
#   G14         —— **内置 stdlib 块库的精度政策**。裸容器一律拒，逼着每个块
#                  把元素类型/元数写清楚，否则 W3-W4 的类型图粘合器只能拿到
#                  `列表<任意>`，推不出任何有效传参链。

def _bare_container_issues(value, where):
    """递归找出类型标注里所有裸容器。返回人类可读的问题串列表。"""
    if isinstance(value, str):
        if value in CONTAINER_TYPE_NAMES:
            提示 = {
                '列表': '补 {"类型": "列表", "元素类型": ...}',
                '字典': '补 {"类型": "字典", "键类型": ..., "值类型": ...}',
                '元组': '补 {"类型": "元组", "元数": [...]}',
            }[value]
            return ['%s 是裸「%s」，缺细化：%s' % (where, value, 提示)]
        return []
    if not isinstance(value, dict):
        return []

    kind = value.get('类型')
    if kind == '列表':
        return _bare_container_issues(value.get('元素类型'), '%s→元素类型' % where)
    if kind == '字典':
        问题 = []
        for k in ('键类型', '值类型'):
            问题 += _bare_container_issues(value.get(k), '%s→%s' % (where, k))
        return 问题
    if kind == '元组':
        问题 = []
        for i, item in enumerate(value.get('元数') or []):
            问题 += _bare_container_issues(item, '%s→元数[%d]' % (where, i))
        return 问题
    if kind == UNION_TYPE_NAME:
        问题 = []
        for i, item in enumerate(value.get('候选') or []):
            问题 += _bare_container_issues(item, '%s→候选[%d]' % (where, i))
        return 问题
    return []


def check_type_annotation(block):
    """G14：校验一个块的类型标注是否达到 ADR-26 精度。

    `block` 接受 `BlockMetadata` 或索引条目 `dict`（两者都有 `输入`/`输出`）。
    返回问题列表，空列表表示通过。

    只查精度、不查合法性——后者是 `_validate` 的职责，走 `load_block_metadata`
    时已经过了。
    """
    if isinstance(block, BlockMetadata):
        名, inputs, output = block.name, block.inputs, block.output
    else:
        名 = block.get('名称', '?')
        inputs = block.get('输入') or []
        output = block.get('输出') or {}

    问题 = []
    for item in inputs:
        if not isinstance(item, dict) or '类型' not in item:
            continue
        问题 += _bare_container_issues(
            item['类型'], '块「%s」输入「%s」' % (名, item.get('名')))
    if output and '类型' in output:
        问题 += _bare_container_issues(output['类型'], '块「%s」输出' % 名)
    return 问题


def check_stdlib_type_annotations(root=None, roots=None):
    """G14 全量入口：扫描块库，汇总所有精度问题。空列表 = 门禁绿。

    缺省只扫**内置**块库（`root=None, roots=None` → `scan_blocks(None, None)`
    会带上 `JIKUAI_PKG_ROOTS`，所以这里显式传 `blocks_root()`）。G14 是
    stdlib 的精度政策，第三方块走 `_validate` 的宽松兼容路径，不该因为本机
    配了个第三方块根就让内置门禁变红。要跨命名空间查时显式传 `roots`。
    """
    问题 = []
    if root is None and roots is None:
        root = blocks_root()
    for meta in scan_blocks(root, roots):
        问题 += check_type_annotation(meta)
    return 问题


# ---------------------------------------------------------------------------
# 导出名全局唯一（G13，W8）
# ---------------------------------------------------------------------------
#
# 短名跨块碰撞是块生态的隐雷：`转义编码→转义` 和 `环境值→环境` 这类导出名很容易
# 撞车。运行时不会崩（各块自己的 `_exports` 独立隔离），但 AI 桥接选块时会把
# 「导出名」当唯一键——一旦冲突，候选合并、代码生成会指错块。G13 的做法是把
# 冲突当**门禁失败**，逼贡献者在 PR 阶段改名，而不是等 dogfooding 才发现。

def check_export_globally_unique(index=None):
    """G13：扫描块索引里的 `导出` 字段，找出所有跨块重名（ADR-27：跨命名空间）。

    `index` 缺省时读盘的 `stdlib/blocks/索引.json`。返回 `[(名, [块1, 块2, ...])]`
    冲突列表，空列表表示门禁通过。**只看有 `导出` 字段的条目**——W7 之前生成的
    索引没这个字段，本门禁自然静默通过（与 G14 的向后兼容策略一致）。

    ADR-27 §2.3 规定：**无论哪个命名空间，导出名全局不重名**——AI 桥接选块时
    把「导出名」当唯一键，跨命名空间碰撞一样会让候选合并指错块。冲突报告里
    带命名空间前缀以方便定位。
    """
    if index is None:
        index = load_index()
    if index is None:
        return []
    反向 = {}
    for entry in index.get('块') or []:
        名 = entry.get('名称')
        ns = entry.get(NAMESPACE_KEY) or BUILTIN_NAMESPACE
        # 报告格式：有命名空间时显示 `命名空间/块名`，内置块只显示块名。
        display = ('%s/%s' % (ns, 名)) if ns else 名
        for e in (entry.get('导出') or []):
            反向.setdefault(e, []).append(display)
    冲突 = [(k, sorted(v)) for k, v in 反向.items() if len(v) > 1]
    冲突.sort()
    return 冲突


# ---------------------------------------------------------------------------
# 依赖图 / 环检测 / L3 层级一致性 / 稳定性传递（G13 扩展，ADR-28 · W29）
# ---------------------------------------------------------------------------
#
# ADR-28 把 `层级` 开到 L3（聚合 L2 的跨域场景块）。三条新约束都长在同一张
# 「块 --依赖块--> 块」的有向图上，所以放在一节里：
#
#   依赖环检测   `check_dependency_acyclic`     A→B→A 这类环
#   层级一致性   `check_level_consistency`      声明 L3 但依赖够不上 L3 判定
#   稳定性传递   `check_stability_propagation`  stable L3 依赖 experimental L2/L3
#
# **图的节点键是块的 `名称`（叶名），不是"块全名"**。理由：`依赖块` 字段自
# v0.14.0 起装的就是叶名（`["税单", "金额报表", "周岁"]`），且 `validate_block`
# 步骤 6 的 G11 对账正是拿它与 `.jk` 里 `blocks.X.Y` 的**叶段**比对
# （见 `_extract_import_deps`）。若在这里改用全名，G11 与全部现存 L2 块的
# 元数据都得跟着翻——收益为零，故沿用叶名，不新造字段也不改语义。
#
# 叶名在单个命名空间内唯一（`scan_blocks` 的 `(命名空间, 名称)` 去重保证），
# 因此在只扫内置块库的门禁场景下解析无歧义。跨命名空间同名块会被并成一个
# 节点——已知局限，记在 ADR-28 §5，等第三方 L3 真出现再上全名解析。

def build_dependency_graph(blocks: List[BlockMetadata]) -> Dict[str, List[str]]:
    """把块列表构造成 `名称 -> [依赖块名, ...]` 的有向图（ADR-28 §3.4）。

    边取自每个块的 `依赖块` 字段。值保序且**不做存在性过滤**——指向图外的边
    （依赖了没扫进来的块）保留下来，由调用方决定怎么解释：环检测会自动忽略
    它们（不成环），层级一致性把它们算作"无法判定"。
    """
    graph: Dict[str, List[str]] = {}
    for b in blocks:
        graph.setdefault(b.name, [])
        for dep in b.dep_blocks:
            graph[b.name].append(dep)
    return graph


def find_dependency_cycles(graph: Dict[str, List[str]]) -> List[List[str]]:
    """在有向图里找出所有环。返回 `[[n1, n2, ..., n1], ...]`（闭合序列）。

    三色 DFS：白（未访问）→ 灰（在当前递归栈上）→ 黑（子树已走完）。碰到一条
    指向**灰**节点的边就是后向边，此时递归栈上从那个灰节点到当前节点的一段
    正好是环体，闭合一下即为环。自环（A 依赖 A）返回 `['A', 'A']`。

    为什么不用 Kahn 拓扑排序：拓扑排序只能回答"有没有环"，剩下的节点集是
    所有环的并集，报错时指不出具体环路。贡献者需要的是「税单 → 工资册 →
    税单」这样能照着改的路径。

    去重按"环体的字典序最小旋转"：同一个环从不同起点走到会重复发现，
    `A→B→A` 与 `B→A→B` 只报一次。返回结果按环体排序，保证报错稳定可 diff。
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    stack: List[str] = []
    cycles: List[List[str]] = []
    seen = set()

    def 规范化(环体):
        """取字典序最小旋转做去重键。"""
        best = min(range(len(环体)), key=lambda i: 环体[i:] + 环体[:i])
        return tuple(环体[best:] + 环体[:best])

    def dfs(node):
        color[node] = GRAY
        stack.append(node)
        for nb in graph.get(node, ()):
            if nb not in color:
                continue                    # 指向图外，不参与成环
            if color[nb] == GRAY:
                环体 = stack[stack.index(nb):]
                键 = 规范化(环体)
                if 键 not in seen:
                    seen.add(键)
                    cycles.append(list(键) + [键[0]])
            elif color[nb] == WHITE:
                dfs(nb)
        stack.pop()
        color[node] = BLACK

    # 按名称排序遍历起点，让同一份块库每次跑出同样的环列表（否则 dict 顺序
    # 一变，报错文本就抖，CI 日志没法比对）。
    for n in sorted(graph):
        if color[n] == WHITE:
            dfs(n)
    cycles.sort()
    return cycles


def _resolve_blocks(root=None, roots=None, blocks=None) -> List[BlockMetadata]:
    """三个门禁函数共用的块列表解析：给了 `blocks` 就用，否则扫盘。

    `root`/`roots` 都为 None 时**显式只扫内置块库**（与 G14 的
    `check_stdlib_type_annotations` 同策略）——本机配了 `JIKUAI_PKG_ROOTS`
    不该让内置门禁变红。
    """
    if blocks is not None:
        return list(blocks)
    if root is None and roots is None:
        root = blocks_root()
    return scan_blocks(root, roots)


def check_dependency_acyclic(root=None, roots=None, blocks=None) -> List[List[str]]:
    """G13 扩展：块依赖图必须无环（ADR-28 §3.4）。

    返回环列表（每项是闭合节点序列 `[n1, ..., n1]`），空列表 = 门禁绿。

    为什么环必须拒：`依赖块` 成环意味着两个块互相"聚合"对方，语义上讲不通；
    工程上会让 W3-W4 粘合器的链式推导在候选图上打转，也让 `层级` 字段失去
    偏序含义（谁聚合谁？）。运行时未必崩（`.jk` 的 `导入` 有模块缓存），
    所以只有门禁能在 PR 阶段挡住。
    """
    return find_dependency_cycles(build_dependency_graph(
        _resolve_blocks(root, roots, blocks)))


def _tally_deps(block: BlockMetadata, by_name: Dict[str, BlockMetadata]):
    """统计一个块的直接依赖，返回 `(聚合依赖数, 领域集合, 未解析依赖名列表)`。

    - 聚合依赖数：`层级 >= AGGREGATE_LEVEL` 的依赖个数（L2 及以上）
    - 领域集合：所有**可解析**依赖的 `领域` 并集（判"跨域"用）
    - 未解析：名字在 `by_name` 里查不到的依赖（无法参与判定）

    **按名字去重**：`依赖块` 里把同一个块写两遍在 G11 那边是过得去的
    （对账用集合比较），但要是让它在这里记两次聚合依赖，`["L2甲","L2甲"]`
    就能白蹭出一个"依赖 2 个 L2"的假 L3。
    """
    聚合数 = 0
    领域 = set()
    未解析 = []
    for dep in sorted(set(block.dep_blocks)):
        meta = by_name.get(dep)
        if meta is None:
            未解析.append(dep)
            continue
        领域.update(meta.domains)
        if meta.level >= AGGREGATE_LEVEL:
            聚合数 += 1
    return 聚合数, 领域, 未解析


def check_level_consistency(root=None, roots=None, blocks=None) -> List[str]:
    """G13 扩展：声明 L3 的块，依赖结构必须真的够 L3（ADR-28 §3.1）。

    L3 判定（满足其一即通过）：

    1. 直接依赖 **>= 2 个 L2+ 聚合块**；或
    2. 直接依赖 **>= 1 个 L2+ 聚合块**，且这些依赖**跨 >= 2 个领域**。

    返回问题串列表，空 = 门禁绿。

    **只查 `层级 == 3` 的块**：L0/L1/L2 的层级判定不在 ADR-28 范围内。既有
    83 个 L0 / 19 个 L1 / 3 个 L2 块是在没有判定规则的年代写的，一刀切追溯
    会把本轮变成大规模元数据返工，与 W29「只定规范 + 门禁」的边界不符。
    L2 判定留待后续 ADR（真需要时连同存量一起收）。
    """
    blocks = _resolve_blocks(root, roots, blocks)
    by_name = {b.name: b for b in blocks}
    问题 = []
    for b in blocks:
        if b.level != L3_LEVEL:
            continue
        聚合数, 领域, 未解析 = _tally_deps(b, by_name)
        通过 = (聚合数 >= 2) or (聚合数 >= 1 and len(领域) >= 2)
        if 通过:
            continue
        细节 = ['L2+ 聚合依赖 %d 个' % 聚合数,
                '依赖覆盖领域 %s' % ('/'.join(sorted(领域)) if 领域 else '无')]
        if 未解析:
            细节.append('未解析依赖 %s' % '、'.join(sorted(未解析)))
        问题.append(
            '块「%s」声明层级 %d（L3）但依赖结构不满足 L3 判定'
            '（需 >=2 个 L2+ 依赖，或 >=1 个 L2+ 依赖且跨 >=2 领域；实测 %s）'
            % (b.name, b.level, '，'.join(细节)))
    return 问题


def check_stability_propagation(root=None, roots=None, blocks=None) -> List[str]:
    """G13 扩展：stable 的**聚合块（L2+）不得依赖任何非 stable 块**（ADR-28 §3.2）。

    这是 ADR-27 §2.5「stable 不得依赖 experimental」的落地。v0.17.0 W44 把
    ADR-28 原先刻意收窄的两处一并放开（原规则只查「依赖方是 L3 且被依赖方是
    L2+」，见下方历史说明）：

    - **依赖方**从「恰好 L3」放宽到 `层级 >= AGGREGATE_LEVEL`（L2 与 L3）
    - **被依赖方**从「L2+」放宽到**任意层级**（含 L0/L1 叶子块）

    `deprecated` 依赖同样拒——它比 experimental 更糟（承诺要移除的东西，
    stable 块不该压在上面）。返回问题串列表，空 = 门禁绿。

    **为什么 W44 敢放开**：ADR-28 §3.2 当年收窄的唯一理由是「门禁上线即红」——
    存量有 3 处违规（`工资条`→`税单`、`用户档案`→`姓名拆分`/`地址剖解`），
    会逼着 W29 做无关的存量治理。W44 的任务本身就是清这笔账：三个被依赖块的
    接口形状（元组元数）已定型不打算再改，`稳定性` 字段承诺的是**接口兼容**
    而不是「解析准确率」，故一并提为 stable，规则随之放开到全量强度。

    **依赖方仍只查 L2+**：叶子块之间（stable L0 依赖 experimental L0）不查。
    `稳定性` 对叶子的意义是「这个工具函数的签名会不会变」，把传递性一路压到
    L0 会让 83 个原子块互相绑死，收益远小于代价。要收这一层得另立 ADR。
    """
    blocks = _resolve_blocks(root, roots, blocks)
    by_name = {b.name: b for b in blocks}
    问题 = []
    for b in blocks:
        if b.level < AGGREGATE_LEVEL or b.stability != 'stable':
            continue
        for dep in sorted(b.dep_blocks):
            meta = by_name.get(dep)
            if meta is None:
                continue
            if meta.stability != 'stable':
                问题.append(
                    'stable 的 L%d 块「%s」依赖 %s 的 L%d 块「%s」'
                    '（稳定性传递违规，ADR-28 §3.2：stable 聚合块的依赖'
                    '必须也是 stable）'
                    % (b.level, b.name, meta.stability, meta.level, dep))
    return 问题



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


def block_exports(jk_path):
    """读取一个块的导出名集合，**优先走元数据**（ADR-24 §5 / W7）。

    与 `extract_exports` 的分工：

    - `extract_exports(jk_path)` —— 永远读 `.jk` 源码，是**唯一事实源**。
      `validate_block` 用它跟 `块.json.导出` 对账，保证元数据不会悄悄漂移。
    - `block_exports(jk_path)`   —— 热路径快通道。同目录有 `块.json` 且带
      `导出` 字段就直接用（省一次整文件读 + 正则扫描），否则回退读 `.jk`。

    调用方是 `frontend._cached_exports`（每次编译对每个 `导入` 都要问一次）
    与 AI 桥接选块器。返回 `set[str]`。
    """
    meta_path = os.path.join(os.path.dirname(os.path.abspath(jk_path)),
                             BLOCK_METADATA_NAME)
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                declared = (json.load(f) or {}).get('导出')
            if isinstance(declared, list) and declared:
                return {n for n in declared if isinstance(n, str) and n.strip()}
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass                    # 元数据坏了不该阻断编译，回退读 .jk
    return extract_exports(jk_path)


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

    # 5.5 `块.json.导出` 若存在，必须与 `.jk` 实际导出**完全一致**（W7）
    # 允许缺省——缺省时热路径回退读 `.jk`；一旦声明就必须对账，否则会让
    # 反哺白名单读到陈旧的导出名。
    declared = set(meta.exports)
    if declared and declared != exports:
        缺 = declared - exports
        多 = exports - declared
        detail = []
        if 缺:
            detail.append('声明有但 .jk 没：%s' % '、'.join(sorted(缺)))
        if 多:
            detail.append('.jk 有但 元数据 没：%s' % '、'.join(sorted(多)))
        errors.append('块.json 的「导出」字段与 %s 不一致（%s）'
                      % (os.path.basename(jk_path), '；'.join(detail)))

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

