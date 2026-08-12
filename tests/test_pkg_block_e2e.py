# -*- coding: utf-8 -*-
"""v0.19.0 · W68 · 端到端钉板（ADR-32 全链路）。

覆盖第三方块包的完整生命周期：**发布到本地注册表 → 装到工程 → 检索能命中
→ 导入并跑通** + checksum 篡改负例。

W68 起 `retrieval._load_blocks` 合并了 `extra_roots()` 的第三方块（本文件是
它的第一个用户），所以「发现/执行/检索」三环终于同时闭合——W64 曾判定的两
根系统实际是三根，本轮补齐第三根。

**为什么全流程走 API 而不是 subprocess**：CLI 是薄壳（`_cmd_publish` /
`_cmd_install` 各二十行），去掉 argv 解析后剩下的就是 `registry.publish` /
`installer.install`。走 API 一是可以在同一进程里跨步骤检查中间状态（快照目录、
`.块根.json`、包.锁 内容），二是给测试报错留完整栈；CLI 层的 argv 解析已经在
`test_pkg_cli.py` 覆盖，不必在这里重跑。
"""

import hashlib
import json
import os
import shutil
import sys

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg import blocks as B                        # noqa: E402
from jikuai.pkg import installer as I                     # noqa: E402
from jikuai.pkg import registry                           # noqa: E402
from jikuai.pkg import sources                            # noqa: E402
from jikuai.pkg.manifest import load_manifest, Manifest   # noqa: E402
from jikuai.ai import retrieval                           # noqa: E402


# ---------------------------------------------------------------------------
# 素材生成——一个携带单个「乘 2」块的极简包，够跑全链路又不至于遮蔽内置块
# ---------------------------------------------------------------------------

def _写(路径, 内容):
    os.makedirs(os.path.dirname(路径), exist_ok=True)
    with open(路径, 'w', encoding='utf-8', newline='\n') as f:
        f.write(内容)


def _造块包(base):
    """在 `base/e2e试包/` 造一个块包，返回包目录。

    命名刻意用「钉板包」这种不与内置块重名的形态，避免检索侧同键去重把
    「测第三方块被找到」误变成「测内置块被找到」。
    名字必须是单 token（`check_module_segment_atomicity` 已验证），因为它会
    进入点分模块路径 `从 blocks.钉板包.数据.试倍 导入 翻倍数`。
    """
    pkg = os.path.join(base, '钉板包')
    _写(os.path.join(pkg, '包.json'), json.dumps({
        '名称': '钉板包', '版本': '1.0.0',
        '描述': 'W68 端到端测试用第三方块包',
        '块': ['blocks'],
    }, ensure_ascii=False, indent=2) + '\n')

    块目录 = os.path.join(pkg, 'blocks', '钉板包', '数据', '试倍')
    _写(os.path.join(块目录, '块.json'), json.dumps({
        '名称': '试倍', '版本': '0.1.0', '层级': 0, '领域': ['数据'],
        '描述': 'W68 端到端测试专用：把一个数乘以 2 再返回，不做真事',
        '输入': [{'名': '数值', '类型': '数'}],
        '输出': {'类型': '数'},
        '导出': ['翻倍数'], '依赖块': [],
        '极快版本': '>=0.19.0',
        '示例': '打印 翻倍数(21)。',
        '稳定性': 'experimental',
    }, ensure_ascii=False, indent=2) + '\n')
    _写(os.path.join(块目录, '试倍.jk'),
        '函数 翻倍数 接收 赵数：\n'
        '  返回 乘 赵数 2。\n'
        '。\n\n'
        '导出 翻倍数。\n')
    # 发布前体检 `_publish_checklist` 要求「入口」文件存在（默认 main.jk）。
    # 块包也是包，给个最小入口满足门槛——真实块包一样要有个可跑的门面。
    _写(os.path.join(pkg, 'main.jk'), '打印 "钉板包：块载体包"。\n')
    return pkg


def _造宿主(base, 依赖路径):
    """造一个引用块包为「路径依赖」的宿主工程。返回工程根。"""
    proj = os.path.join(base, '宿主工程')
    _写(os.path.join(proj, '包.json'), json.dumps({
        '名称': '宿主工程', '版本': '0.1.0',
        '依赖': {'钉板包': {'路径': 依赖路径}},
    }, ensure_ascii=False, indent=2) + '\n')
    return proj


@pytest.fixture
def 隔离环境(tmp_path, monkeypatch):
    """W68 端到端夹具：干净的注册表根 + 清空所有相关环境变量 + 检索缓存重置。

    发现/执行/检索三侧都是**进程级**状态：`extra_roots()` 读环境变量 +
    cwd 上溯，`retrieval._cached_retriever` 是进程缓存。任一残留都会让下一个
    测试拿到上一个测试的状态。
    """
    monkeypatch.setenv('JIKUAI_REGISTRY', str(tmp_path / 'reg'))
    monkeypatch.delenv(B.PKG_ROOTS_ENV, raising=False)
    monkeypatch.delenv('JIKUAI_PATH', raising=False)
    retrieval.reset_cache()
    yield tmp_path
    retrieval.reset_cache()


# ---------------------------------------------------------------------------
# 端到端主链路：发布 → 装 → 检索命中 → 导入并跑
# ---------------------------------------------------------------------------

def test_发布装检索跑_全链路闭合(隔离环境, monkeypatch):
    源 = _造块包(隔离环境 / '源码')
    manifest = load_manifest(源)

    # 1) 发布：先演练看校验和/文件数，再落盘
    演练 = registry.publish(manifest, dry_run=True)
    assert 演练.dry_run is True
    assert 演练.file_count >= 2         # 至少 包.json + 块.json + .jk

    # v0.20.0 W73：`registry.publish` 与 `installer` 的校验和格式**已统一**为
    # `sha256:<hex>`（此前 publish 存裸 hex、installer 往 `包.锁` 存带前缀，
    # 底层同一个 `sources.compute_checksum`，只有包装层不同）。统一的动因不是
    # 好看：M19 做包签名 + M20 做 HTTP 分发时要跨端比对校验和，两种格式会
    # 误判成「不匹配」。前缀即算法标识，将来换算法有位置放。
    assert 演练.checksum.startswith('sha256:')
    assert len(演练.checksum) == len('sha256:') + 64

    正式 = registry.publish(manifest, dry_run=False)
    assert 正式.dry_run is False
    assert 正式.overwritten is False
    assert 正式.checksum == 演练.checksum   # 校验和是幂等的：同源发布必然同和
    assert os.path.isdir(正式.target)

    # 快照里必须原样带着「块」字段——registry.publish 不会把它写进条目详情，
    # 但 _copy_source 会把 包.json 拷进快照，装的时候靠这个反查（ADR-32 §2.3）
    快照清单 = load_manifest(正式.target)
    assert 快照清单.block_roots == ['blocks']

    # 2) 装：从注册表取回同一个包到工程的 极快_包/ 下
    proj = _造宿主(隔离环境, 依赖路径=源)   # 走本地路径依赖，绕过 registry.lookup
    报告 = I.install(load_manifest(proj))
    assert 报告.total == 1
    装成 = os.path.join(proj, I.PACKAGES_DIR, '钉板包')
    assert os.path.isdir(装成)

    # `.块根.json` 是 W65-W66 的产物——检查发现侧一路的接线仍然通
    索引路径 = os.path.join(proj, I.PACKAGES_DIR, I.BLOCK_ROOTS_INDEX)
    with open(索引路径, encoding='utf-8') as f:
        idx = json.load(f)
    assert idx['索引版本'] == I.BLOCK_ROOTS_INDEX_VERSION
    assert idx['块根'] == [{'包': '钉板包', '路径': '钉板包/blocks'}]

    # 3) 检索：装完包后 `retrieval.retrieve` 能命中第三方块（W68 新接的第三根）
    monkeypatch.chdir(proj)               # extra_roots() 靠 cwd 上溯找项目
    retrieval.reset_cache()               # 装包后必须清缓存（见 _get_retriever docstring）
    命中 = retrieval.retrieve('把数值乘以 2', top=20)
    命中名 = {h.name for h in 命中}
    assert '试倍' in 命中名, f'检索没找到第三方块，实际命中：{命中名}'

    # 内置块不该被第三方块挤掉——去重键是 (命名空间, 名称)，第三方块命名
    # 空间是 `钉板包`，内置块是空串，同名不同源不互斥
    描述 = retrieval.describe()
    assert 描述['块数'] > 100, '合并后总数应包含 100+ 内置块 + 1 第三方块'

    # W69：命中里必须带上命名空间——这是 glue 拼三段导入路径的唯一来源
    第三方 = next(h for h in 命中 if h.name == '试倍')
    assert 第三方.namespace == '钉板包'
    assert 第三方.as_dict()['命名空间'] == '钉板包'

    # 4) 跑：写一个宿主 .jk 导入并调用第三方块，验证执行侧仍然通
    main = os.path.join(proj, 'main.jk')
    _写(main, '从 blocks.钉板包.数据.试倍 导入 翻倍数。\n打印 翻倍数(21)。\n')
    from jikuai import run_file
    run_file(main)     # 不抛就算过；输出 42 到 stdout


# ---------------------------------------------------------------------------
# checksum 篡改负例：装完后手动改动源码，重装应看见新的 checksum
# ---------------------------------------------------------------------------

def test_源码篡改_checksum立即变化(隔离环境):
    """W68 checksum 门神：packages 目录里的源码被改后，重跑 install 时
    对同一源码新算的 checksum 必然不等于篡改前的旧值，`包.锁` 也随之更新。

    **本轮范围内不测「注册表快照被篡改后 装 应拒绝」**——那需要另加一条
    verify-on-read 校验，目前 install 只在 resolve 时计算 checksum 不做比对
    （见 installer.install:263 `digest, _size = compute_checksum(node.source.root)`），
    真正的完整性校验属于 W70 加固清单。这里先把「篡改 → checksum 数字变化」
    钉住，防止哪次重构把 sha256 换成了对文件名不敏感的东西。
    """
    源 = _造块包(隔离环境 / '源码')
    proj = _造宿主(隔离环境, 依赖路径=源)
    I.install(load_manifest(proj))

    # 取当前 包.锁 里记录的 e2e试包 checksum（包 是排序后的列表，不是字典）
    锁路径 = os.path.join(proj, '包.锁')
    with open(锁路径, encoding='utf-8') as f:
        锁 = json.load(f)
    条目 = next(p for p in 锁['包'] if p['名称'] == '钉板包')
    原和 = 条目['校验和']
    assert 原和.startswith('sha256:')

    # 篡改源码目录里的 .jk（把「乘 2」改成「乘 3」——语义完全不同的块）
    篡改点 = os.path.join(源, 'blocks', '钉板包', '数据', '试倍', '试倍.jk')
    with open(篡改点, encoding='utf-8') as f:
        原文 = f.read()
    with open(篡改点, 'w', encoding='utf-8', newline='\n') as f:
        f.write(原文.replace('乘 赵数 2', '乘 赵数 3'))

    # 重跑 install → 新 checksum 必然不等
    I.install(load_manifest(proj))
    with open(锁路径, encoding='utf-8') as f:
        锁_2 = json.load(f)
    条目_2 = next(p for p in 锁_2['包'] if p['名称'] == '钉板包')
    新和 = 条目_2['校验和']
    assert 新和.startswith('sha256:')
    assert 新和 != 原和, 'checksum 未变——sha256 对 .jk 内容变化不敏感了？'


# ---------------------------------------------------------------------------
# 检索侧回归防护：第三方块扫描失败不该拖垮内置块检索
# ---------------------------------------------------------------------------

def test_第三方块扫描失败时降级为只含内置块(隔离环境, monkeypatch, caplog):
    """`_load_third_party_blocks` 用宽 try/except 兜异常并降级——本用例故意
    让 `extra_roots()` 抛错，验证内置块检索仍然可用（W68 集成反馈的隐含 SLO）。
    """
    def 炸(*a, **kw):
        raise RuntimeError('故意炸给测试看的：extra_roots 挂了')
    monkeypatch.setattr(B, 'extra_roots', 炸)
    retrieval.reset_cache()

    命中 = retrieval.retrieve('计算均值', top=5)
    # 就算第三方那路挂了，内置的「均值/平均/统计」类块也应该出来
    assert len(命中) >= 1


# ---------------------------------------------------------------------------
# W69 · 命名空间隔离：与内置同名的第三方块不该顶掉内置块，两者靠命名空间并存
# ---------------------------------------------------------------------------

def _造同名块包(base, 内置块名, 领域名):
    """造一个块**故意与某内置块同名同域**的第三方包，命名空间为 `甲包`。

    去重键是 (命名空间, 名称)：内置块命名空间空串、第三方是 `甲包`，键不同，
    所以两条都该活下来。这正是「跨命名空间同名合法」在检索侧的落地验证。
    """
    pkg = os.path.join(base, '甲包')
    _写(os.path.join(pkg, '包.json'), json.dumps({
        '名称': '甲包', '版本': '1.0.0', '描述': 'W69 命名空间隔离测试包',
        '块': ['blocks'],
    }, ensure_ascii=False, indent=2) + '\n')
    块目录 = os.path.join(pkg, 'blocks', '甲包', 领域名, 内置块名)
    _写(os.path.join(块目录, '块.json'), json.dumps({
        '名称': 内置块名, '版本': '0.1.0', '层级': 0, '领域': [领域名],
        '描述': 'W69 隔离测试：与内置同名的第三方块，靠命名空间区分',
        '输入': [{'名': '数值', '类型': '数'}],
        '输出': {'类型': '数'},
        '导出': ['甲值'], '依赖块': [],
        '极快版本': '>=0.19.0',
        '稳定性': 'experimental',
    }, ensure_ascii=False, indent=2) + '\n')
    _写(os.path.join(块目录, '%s.jk' % 内置块名),
        '函数 甲值 接收 赵数：\n  返回 赵数。\n。\n\n导出 甲值。\n')
    _写(os.path.join(pkg, 'main.jk'), '打印 "甲包"。\n')
    return pkg


def test_命名空间键名与块子系统同源():
    """`retrieval._NAMESPACE_KEY` 刻意不 import `blocks.NAMESPACE_KEY`（守惰性
    导入边界），本用例是那份「同值」承诺的唯一执行者——改一边漏另一边就红。
    """
    assert retrieval._NAMESPACE_KEY == B.NAMESPACE_KEY


def test_命名空间隔离_同名第三方块不顶掉内置块(隔离环境, monkeypatch):
    """选一个真实内置块名，造一个同名同域的第三方块，装完后检索这个名字：
    内置块（空命名空间）与第三方块（命名空间=甲包）必须**两条都在**。
    """
    # 先无第三方地问一次，拿一个真实内置块名当靶子——避免把测试钉死在某个块名上
    retrieval.reset_cache()
    内置命中 = retrieval.retrieve('求和 累加 汇总 均值 统计', top=10)
    assert 内置命中, '内置检索为空，环境异常'
    靶 = 内置命中[0]
    靶名, 靶域 = 靶.name, 靶.domain
    assert 靶.namespace == '', '内置块命名空间应为空串'

    源 = _造同名块包(隔离环境 / '源码', 靶名, 靶域)
    proj = _造宿主(隔离环境, 依赖路径=源)
    # 改宿主依赖名为 甲包
    with open(os.path.join(proj, '包.json'), encoding='utf-8') as f:
        宿主清单 = json.load(f)
    宿主清单['依赖'] = {'甲包': {'路径': 源}}
    with open(os.path.join(proj, '包.json'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(宿主清单, ensure_ascii=False, indent=2) + '\n')

    I.install(load_manifest(proj))
    monkeypatch.chdir(proj)
    retrieval.reset_cache()

    命中 = retrieval.retrieve('%s %s' % (靶名, 靶域), top=30)
    同名命中 = [h for h in 命中 if h.name == 靶名]
    命名空间集 = {h.namespace for h in 同名命中}
    assert '' in 命名空间集, '内置块被第三方同名块顶掉了：%s' % 命名空间集
    assert '甲包' in 命名空间集, '第三方同名块没进检索：%s' % 命名空间集
