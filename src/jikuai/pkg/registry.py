# -*- coding: utf-8 -*-
"""极快包管理 - 本地注册表与发布（M11-1）。

为什么先做「本地注册表」而不是直接上中央服务
--------------------------------------------
调研了段言（duanpub / duan）那套方案后得出两条结论：

1. **双层 JSON 索引值得照抄。** 主索引只存路由（`包名 → 分片文件`），
   详情下沉到 `分类/<分类>.json` 分片。客户端首次只拉几十 KB 主索引，
   按需拉分片；配 `校验和` 做完整性核对。比单文件全量索引可扩展，
   又比上数据库简单——对中文脚本生态的体量刚好。

2. **它的 HTTP 注册中心不能照抄。** `registry_server.py` 的
   `POST /api/v1/packages` 与 `DELETE` 完全没有鉴权，任何人可发布或
   删除任意包，且开放 CORS。这不是"原型阶段可接受"，而是一上公网就
   会被投毒的设计。所以本模块**只做本地/内网文件系统注册表**，
   HTTP 分发留到接入 token 鉴权 + 包签名之后。

目录布局
--------
    <注册表根>/
      索引.json                    主索引（路由 + 统计 + 校验和）
      分类/<分类>.json              分片（包名 → 版本 → 条目详情）
      包/<名称>/<版本>/             已发布的源码快照

`<注册表根>` 解析顺序：
    1. 显式传入的 `root` 参数
    2. 环境变量 `JIKUAI_REGISTRY`
    3. `~/.jikuai/注册表`

安全底线
--------
- 包名经 `validate_package_name` 校验（禁点、禁路径分隔符），版本经
  `semver.parse_version` 校验，两者都会拼进目录路径，是唯一的注入面。
- 所有写入路径再过一遍 `_ensure_within`，双重保险。
- 发布默认**拒绝覆盖**已存在的 `名称@版本`：已发布版本被静默改内容是
  供应链攻击的经典入口，要改必须显式 `允许覆盖=True`。
"""

import base64
import json
import os
import shutil
from typing import Dict, List, Optional, Tuple

from . import semver
from .manifest import (
    MANIFEST_NAME, Manifest, ManifestError,
    load_manifest, validate_package_name,
)

__all__ = [
    'RegistryError', 'PublishReport',
    'INDEX_NAME', 'CATEGORY_DIR', 'PACKAGE_DIR', 'KEY_DIR', 'DEFAULT_CATEGORY',
    'registry_root', 'load_index', 'save_index', 'registry_key_path',
    'publish', 'lookup', 'list_packages', 'search', 'unpublish',
]

#: 主索引文件名。
INDEX_NAME = '索引.json'
#: 分片目录名。
CATEGORY_DIR = '分类'
#: 源码快照目录名。
PACKAGE_DIR = '包'
#: 签名者公钥目录名（ADR-33 §2.5）。发布方的公钥随包进注册表，
#: 装包端 TOFU 首次拉取后记进本地信任库。
KEY_DIR = '密钥'
#: 清单未声明「分类」时的归属。
DEFAULT_CATEGORY = '通用'
#: 索引格式版本。索引结构演进时递增，读到更高版本直接拒绝而非猜测。
INDEX_VERSION = 1

#: 分类白名单。与包名同理——分类会拼进分片文件路径。
_VALID_CATEGORIES = frozenset({
    '通用', '基础', '数据', '网络', '文件', '文本', '数学',
    '历法', '金融', '校验', '工具', '测试', '界面', '人工智能',
})


class RegistryError(Exception):
    """注册表读写失败、索引损坏、发布冲突等。"""


class PublishReport:
    """一次发布的结果摘要。`演练` 为真时不落盘，其余字段照常填。"""

    __slots__ = ('name', 'version', 'category', 'checksum', 'file_count',
                 'target', 'dry_run', 'overwritten', 'warnings',
                 'signer', 'signature')

    def __init__(self, name: str, version: str, category: str,
                 checksum: str, file_count: int, target: str,
                 dry_run: bool, overwritten: bool, warnings: List[str],
                 signer: str = '', signature: str = ''):
        self.name = name
        self.version = version
        self.category = category
        self.checksum = checksum
        self.file_count = file_count
        self.target = target
        self.dry_run = dry_run
        self.overwritten = overwritten
        self.warnings = warnings
        #: 签名者别名（ADR-33）。未签名发布时是空串。
        self.signer = signer
        #: base64 的 64 字节 Ed25519 签名。未签名发布时是空串。
        self.signature = signature


# ---- 路径解析 ---------------------------------------------------------

def registry_root(root: Optional[str] = None) -> str:
    """解析注册表根目录（不创建）。"""
    if root:
        return os.path.abspath(root)
    env = os.environ.get('JIKUAI_REGISTRY')
    if env:
        return os.path.abspath(env)
    return os.path.join(os.path.expanduser('~'), '.jikuai', '注册表')


def _ensure_within(base: str, target: str) -> str:
    """确保 `target` 落在 `base` 树下。防目录穿越（与 sources 同策略）。"""
    base_abs = os.path.abspath(base)
    target_abs = os.path.abspath(target)
    try:
        common = os.path.commonpath([base_abs, target_abs])
    except ValueError:
        raise RegistryError(f'路径 {target!r} 与注册表根 {base!r} 不在同一根下')
    if common != base_abs:
        raise RegistryError(f'路径 {target!r} 越出注册表根 {base!r}')
    return target_abs


def _index_path(root: str) -> str:
    return os.path.join(root, INDEX_NAME)


def _category_path(root: str, category: str) -> str:
    _validate_category(category)
    return _ensure_within(root, os.path.join(root, CATEGORY_DIR, f'{category}.json'))


def _package_path(root: str, name: str, version: str) -> str:
    validate_package_name(name)
    semver.parse_version(version)
    return _ensure_within(root, os.path.join(root, PACKAGE_DIR, name, version))


def registry_key_path(root: str, signer: str) -> str:
    """签名者公钥在注册表内的落点：`<注册表根>/密钥/<签名者>.公钥`。

    签名者别名与包名同一注入面（要拼进路径），复用 `validate_package_name`
    的字符白名单：不允许点、路径分隔符、控制字符。
    """
    validate_package_name(signer)
    return _ensure_within(root, os.path.join(root, KEY_DIR, signer + '.公钥'))


def _validate_category(category: str) -> str:
    if category not in _VALID_CATEGORIES:
        allowed = '、'.join(sorted(_VALID_CATEGORIES))
        raise RegistryError(f'分类 {category!r} 不在白名单内；可用分类：{allowed}')
    return category


# ---- 原子写 -----------------------------------------------------------

def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, path)


def _read_json(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise RegistryError(f'{path} 不是合法 JSON：第 {e.lineno} 行 {e.msg}') from None
    except UnicodeDecodeError:
        raise RegistryError(f'{path} 编码不是 UTF-8') from None


# ---- 主索引 -----------------------------------------------------------

def _empty_index() -> dict:
    return {
        '格式版本': INDEX_VERSION,
        '索引': {},
        '统计': {'总包数': 0, '总版本数': 0},
    }


def load_index(root: Optional[str] = None) -> dict:
    """读取主索引。不存在时返回一份空索引（不落盘）。"""
    base = registry_root(root)
    data = _read_json(_index_path(base))
    if data is None:
        return _empty_index()
    if not isinstance(data, dict) or not isinstance(data.get('索引'), dict):
        raise RegistryError(f'主索引结构损坏：{_index_path(base)}')
    fmt = data.get('格式版本', 1)
    if not isinstance(fmt, int) or fmt > INDEX_VERSION:
        raise RegistryError(
            f'主索引格式版本 {fmt} 高于本工具支持的 {INDEX_VERSION}，请升级极快')
    return data


def save_index(index: dict, root: Optional[str] = None) -> str:
    """刷新统计后原子写回主索引。返回写入路径。"""
    base = registry_root(root)
    entries = index.setdefault('索引', {})
    index['格式版本'] = INDEX_VERSION
    index['统计'] = {
        '总包数': len(entries),
        '总版本数': sum(len(v.get('版本', ())) for v in entries.values()),
    }
    path = _index_path(base)
    _write_json(path, index)
    return path


def _load_category(root: str, category: str) -> dict:
    data = _read_json(_category_path(root, category))
    return data if isinstance(data, dict) else {}


# ---- 发布 -------------------------------------------------------------

def _copy_source(src_root: str, dest: str) -> Tuple[int, List[str]]:
    """把包源码拷进注册表快照目录。返回 (文件数, 警告列表)。

    只搬 `.jk` / `.py` / `.json` / `.md` 与 `包.json`，跳过 `.git`、
    `__pycache__`、`极快_包/`（已安装依赖不该进快照，否则套娃）。
    """
    keep_ext = ('.jk', '.py', '.json', '.md', '.txt')
    skip_dirs = {'.git', '__pycache__', '极快_包', 'node_modules', '.venv'}
    warnings: List[str] = []
    count = 0
    src_root = os.path.abspath(src_root)
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith('.') and d not in skip_dirs)
        for fn in sorted(filenames):
            if not fn.endswith(keep_ext):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, src_root)
            target = _ensure_within(dest, os.path.join(dest, rel))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(full, target)
            count += 1
    if count == 0:
        warnings.append('快照里一个源文件都没有，确认包目录是否正确')
    return count, warnings


def _publish_checklist(manifest: Manifest) -> List[str]:
    """发布前体检。返回警告列表（不阻断），硬错误直接抛。"""
    warnings: List[str] = []
    root = manifest.root
    if not manifest.description:
        warnings.append('清单缺少「描述」，发布后别人搜不到你的包')
    entry = os.path.join(root, manifest.entry)
    if not os.path.isfile(entry):
        raise RegistryError(
            f'清单「入口」指向的文件不存在：{manifest.entry}（在 {root} 下）')
    for dep_name, dep in manifest.dependencies(include_dev=False).items():
        if dep.kind == '路径':
            raise RegistryError(
                f'依赖 {dep_name} 是本地路径来源，发布后别人装不上；'
                f'请先把它也发布到注册表，或改用「仓库」来源')
    if not os.path.isfile(os.path.join(root, 'README.md')):
        warnings.append('没有 README.md，建议补一份用法说明')
    return warnings


def publish(manifest: Optional[Manifest] = None, root: Optional[str] = None,
            category: Optional[str] = None, dry_run: bool = True,
            allow_overwrite: bool = False,
            signer: Optional[str] = None) -> PublishReport:
    """把一个包发布到本地注册表。

    `dry_run` **默认为真**：发布是不可逆动作，默认演练、显式才落盘。
    `allow_overwrite` 为假（默认）时，`名称@版本` 已存在即报错。
    `signer` 非 None 时用该别名的私钥签校验和，签名与签名者写入索引条目。
    """
    if manifest is None:
        manifest = load_manifest()
    base = registry_root(root)
    name = validate_package_name(manifest.name)
    version = manifest.version
    semver.parse_version(version)
    category = _validate_category(
        category or manifest.to_dict().get('分类') or DEFAULT_CATEGORY)

    warnings = _publish_checklist(manifest)

    index = load_index(base)
    entries = index.setdefault('索引', {})
    existing = entries.get(name)
    overwritten = bool(existing and version in (existing.get('版本') or ()))
    if overwritten and not allow_overwrite:
        raise RegistryError(
            f'{name}@{version} 已在注册表里；已发布版本不允许静默覆盖。'
            f'请提升版本号，或显式传 允许覆盖=True')

    dest = _package_path(base, name, version)

    # 先把快照做到临时目录，再算校验和；演练模式下算完即弃。
    staging = dest + '.staging'
    if os.path.isdir(staging):
        shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    try:
        file_count, copy_warnings = _copy_source(manifest.root, staging)
        warnings.extend(copy_warnings)
        from .sources import compute_checksum
        digest, _size = compute_checksum(staging)
        # v0.20.0 W73：统一带 `sha256:` 前缀，与 installer 往 `包.锁` 写的格式
        # 一致（此前 publish 存裸 hex、installer 存 `sha256:<hex>`，HTTP 分发
        # 跨端比对会因格式不同误判不匹配）。前缀即算法标识，为将来换算法留位。
        checksum = 'sha256:' + digest

        # v0.20.0 W74（ADR-33）：签名对象是**校验和字符串**（含前缀），
        # 不是快照字节。签名输入定长 71 字节，与包体积解耦；校验和已在
        # 索引条目里，验签方比对字符串即可，无需重跑 sha256。
        signature_b64 = ''
        pubkey_bytes = b''
        if signer is not None:
            from . import keys as _keys
            from . import _ed25519 as _ed
            _keys.validate_alias(signer)
            seed = _keys.load_private_key(signer)
            pubkey_bytes = _keys.load_public_key(signer)
            signature_b64 = base64.b64encode(
                _ed.sign(seed, checksum.encode('utf-8'))).decode('ascii')

        if dry_run:
            return PublishReport(name, version, category, checksum,
                                 file_count, dest, True, overwritten, warnings,
                                 signer or '', signature_b64)

        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.replace(staging, dest)
        staging = None            # 已消费，finally 不再清理
    finally:
        if staging and os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)

    # 有签名：把公钥落到注册表 `密钥/<签名者>.公钥`（TOFU 首次拉取源）。
    # 与已存在的公钥字节不等 → 拒写，避免同一别名被静默改身份 —— 别名换身份
    # 必须换名字，管理员端做的动作应是「删旧公钥、审新公钥」而非静默覆盖。
    if signer is not None and signature_b64:
        pk_dest = registry_key_path(base, signer)
        os.makedirs(os.path.dirname(pk_dest), exist_ok=True)
        pk_b64_line = base64.b64encode(pubkey_bytes).decode('ascii') + '\n'
        if os.path.isfile(pk_dest):
            with open(pk_dest, 'r', encoding='utf-8') as f:
                if f.read().strip() != pk_b64_line.strip():
                    raise RegistryError(
                        f'注册表里已有别名 {signer!r} 的公钥，且与本次不一致；'
                        f'签名身份不允许静默替换。要换身份请先删 {pk_dest}，'
                        f'或改用另一个别名')
        else:
            with open(pk_dest, 'w', encoding='utf-8', newline='\n') as f:
                f.write(pk_b64_line)

    # 写分片：包名 → 版本 → 条目详情
    shard = _load_category(base, category)
    pkg_shard = shard.setdefault(name, {})
    entry_detail = {
        '名称': name,
        '版本': version,
        '描述': manifest.description,
        '入口': manifest.entry,
        '分类': category,
        '校验和': checksum,
        '文件数': file_count,
        '依赖': {n: d.to_spec() for n, d
                 in manifest.dependencies(include_dev=False).items()},
        '极快版本': manifest.jikuai_requirement,
        '快照': os.path.relpath(dest, base).replace(os.sep, '/'),
    }
    if signer is not None and signature_b64:
        # 签名字段与旧条目**共存不冲突**：老版本没有这两个字段，装包端读到
        # 空/缺失时走「未签名」路径（v0.20.0 Warn，v0.21.0 拒装）。
        entry_detail['签名者'] = signer
        entry_detail['签名'] = signature_b64
    pkg_shard[version] = entry_detail
    _write_json(_category_path(base, category), shard)

    # 写主索引：只存路由信息，详情在分片里
    versions = sorted(set((existing or {}).get('版本') or ()) | {version},
                      key=semver.parse_version)
    entries[name] = {
        '分类': category,
        '最新版本': versions[-1],
        '版本': versions,
        '文件': f'{CATEGORY_DIR}/{category}.json',
    }
    save_index(index, base)

    return PublishReport(name, version, category, checksum, file_count,
                         dest, False, overwritten, warnings,
                         signer or '', signature_b64)


def unpublish(name: str, version: Optional[str] = None,
              root: Optional[str] = None) -> List[str]:
    """从注册表移除一个包（或它的某个版本）。返回被移除的版本列表。

    仅用于本地注册表维护与测试清理。中央注册表上线后**不会**提供这个
    能力——已发布版本被撤回会让下游锁文件失效（left-pad 事件的教训）。
    """
    base = registry_root(root)
    validate_package_name(name)
    index = load_index(base)
    entries = index.setdefault('索引', {})
    entry = entries.get(name)
    if entry is None:
        raise RegistryError(f'注册表里没有包 {name}')
    category = entry.get('分类') or DEFAULT_CATEGORY
    all_versions = list(entry.get('版本') or ())
    targets = all_versions if version is None else [version]
    for v in targets:
        if v not in all_versions:
            raise RegistryError(f'{name} 没有版本 {v}')

    shard = _load_category(base, category)
    for v in targets:
        shutil.rmtree(_package_path(base, name, v), ignore_errors=True)
        shard.get(name, {}).pop(v, None)
    if not shard.get(name):
        shard.pop(name, None)
    _write_json(_category_path(base, category), shard)

    remaining = [v for v in all_versions if v not in targets]
    if remaining:
        entries[name] = dict(entry, **{
            '版本': remaining,
            '最新版本': sorted(remaining, key=semver.parse_version)[-1],
        })
    else:
        entries.pop(name, None)
    save_index(index, base)
    return targets


# ---- 查询 -------------------------------------------------------------

def lookup(name: str, constraint: Optional[str] = None,
           root: Optional[str] = None) -> Tuple[str, str]:
    """在注册表里选一个满足约束的版本。返回 `(版本, 快照目录)`。

    选版策略：满足约束的**最高**版本。没有任何版本满足时报错并列出
    实际可用版本——比笼统的「找不到包」有用得多。
    """
    base = registry_root(root)
    validate_package_name(name)
    index = load_index(base)
    entry = (index.get('索引') or {}).get(name)
    if entry is None:
        raise RegistryError(f'注册表里没有包 {name}（注册表根：{base}）')

    versions = list(entry.get('版本') or ())
    if not versions:
        raise RegistryError(f'包 {name} 在注册表里没有任何已发布版本')

    if constraint in (None, '*'):
        candidates = versions
    else:
        candidates = [v for v in versions if semver.matches(v, constraint)]
    if not candidates:
        have = '、'.join(sorted(versions, key=semver.parse_version))
        raise RegistryError(
            f'包 {name} 没有满足 {constraint} 的版本；已发布版本：{have}')

    chosen = sorted(candidates, key=semver.parse_version)[-1]
    snapshot = _package_path(base, name, chosen)
    if not os.path.isdir(snapshot):
        raise RegistryError(
            f'索引声称 {name}@{chosen} 存在，但快照目录缺失：{snapshot}。'
            f'注册表可能损坏，考虑重新发布')
    if not os.path.isfile(os.path.join(snapshot, MANIFEST_NAME)):
        raise RegistryError(f'{name}@{chosen} 的快照里缺少 {MANIFEST_NAME}')
    return chosen, snapshot


def list_packages(root: Optional[str] = None) -> Dict[str, dict]:
    """列出注册表里所有包的路由条目（不读分片，很快）。"""
    index = load_index(registry_root(root))
    return dict(index.get('索引') or {})


def search(keyword: str, root: Optional[str] = None) -> List[dict]:
    """按包名/描述搜索。命中时读对应分片取描述，未命中不读盘。"""
    base = registry_root(root)
    index = load_index(base)
    entries = index.get('索引') or {}
    kw = (keyword or '').strip()
    results: List[dict] = []
    shard_cache: Dict[str, dict] = {}
    for name in sorted(entries):
        entry = entries[name]
        category = entry.get('分类') or DEFAULT_CATEGORY
        if category not in shard_cache:
            try:
                shard_cache[category] = _load_category(base, category)
            except RegistryError:
                shard_cache[category] = {}
        latest = entry.get('最新版本') or ''
        detail = (shard_cache[category].get(name) or {}).get(latest) or {}
        description = detail.get('描述', '')
        if not kw or kw in name or kw in description:
            results.append({
                '名称': name,
                '最新版本': latest,
                '分类': category,
                '描述': description,
                '版本': list(entry.get('版本') or ()),
            })
    return results
