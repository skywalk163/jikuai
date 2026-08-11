# -*- coding: utf-8 -*-
"""v0.19.0 · W65-W66 · 块包桥接安装测试（ADR-32）。

覆盖「装一个携带块的包 → 块被双侧看见 → 卸载后消失」全链路：

- **发现侧**：`scan_blocks()` 经 `extra_roots()` 读 `极快_包/.块根.json`
- **执行侧**：`从 blocks.<命名空间>.<领域>.<块> 导入 X` 经
  `module_loader._search_paths()` 读同一份索引取 dirname

W64 桩验证的头号发现是这两侧是**独立的根系统、层级差一级**，所以每个
关键用例都要两侧分别断言——只测一侧的话另一侧断了也不会红。
"""

import json
import os
import sys

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg import blocks as B                      # noqa: E402
from jikuai.pkg import installer as I                   # noqa: E402
from jikuai.pkg.manifest import (                       # noqa: E402
    ManifestError, load_manifest, save_manifest, Manifest)


# ---------------------------------------------------------------------------
# 合成素材
# ---------------------------------------------------------------------------

def _写(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def _造块包(根, 包名, 块名='翻倍', 导出='倍增', 命名空间=None, 领域='数据',
           块根名='blocks'):
    """在 `根/<包名>/` 造一个携带单个 L0 块的块包源码树。返回包目录。

    `命名空间` 缺省取包名（ADR-32 §2.4 推荐做法）；传 `''` 则块直接躺在
    块根下（无命名空间目录），用于测「与内置块争空命名空间」的形态。
    """
    ns = 包名 if 命名空间 is None else 命名空间
    pkg = os.path.join(根, 包名)
    _写(os.path.join(pkg, '包.json'), json.dumps({
        '名称': 包名, '版本': '1.0.0', '块': [块根名],
    }, ensure_ascii=False, indent=2) + '\n')

    段 = [块根名] + ([ns] if ns else []) + [领域, 块名]
    块目录 = os.path.join(pkg, *段)
    _写(os.path.join(块目录, '块.json'), json.dumps({
        '名称': 块名, '版本': '0.1.0', '层级': 0, '领域': [领域],
        '描述': '测试用块：把一个数乘以 2',
        '输入': [{'名': '数值', '类型': '数'}],
        '输出': {'类型': '数'},
        '导出': [导出], '依赖块': [],
        '极快版本': '>=0.19.0',
        '示例': '打印 %s(21)。' % 导出,
        '稳定性': 'experimental',
    }, ensure_ascii=False, indent=2) + '\n')
    _写(os.path.join(块目录, '%s.jk' % 块名),
        '函数 %s 接收 赵数：\n  返回 乘 赵数 2。\n。\n\n导出 %s。\n'
        % (导出, 导出))
    return pkg


def _造宿主(根, 依赖: dict):
    """造一个引用若干本地路径依赖的宿主项目。返回项目根。"""
    proj = os.path.join(根, '宿主')
    _写(os.path.join(proj, '包.json'), json.dumps({
        '名称': '宿主', '版本': '0.1.0',
        '依赖': {名: {'路径': 路径} for 名, 路径 in 依赖.items()},
    }, ensure_ascii=False, indent=2) + '\n')
    return proj


def _装(proj):
    return I.install(load_manifest(proj))


def _索引路径(proj):
    return os.path.join(proj, I.PACKAGES_DIR, I.BLOCK_ROOTS_INDEX)


def _发现(proj, monkeypatch):
    """在 `proj` 里跑发现侧：返回第三方块的 (命名空间, 名称) 列表。

    `extra_roots()` 靠 cwd 上溯找项目根，所以要切到项目目录；同时清掉
    `JIKUAI_PKG_ROOTS`，确保测到的是**索引这一路**而不是环境变量那一路。
    """
    monkeypatch.delenv(B.PKG_ROOTS_ENV, raising=False)
    monkeypatch.chdir(proj)
    return [(b.namespace, b.name) for b in B.scan_blocks() if b.namespace]


def _跑(proj, 源码, monkeypatch):
    """在 `proj` 里跑执行侧：以项目内的 `main.jk` 为当前文件求值源码。"""
    monkeypatch.delenv('JIKUAI_PATH', raising=False)
    monkeypatch.delenv(B.PKG_ROOTS_ENV, raising=False)
    monkeypatch.chdir(proj)
    main = os.path.join(proj, 'main.jk')
    _写(main, 源码)
    from jikuai import run_file
    return run_file(main)


# ---------------------------------------------------------------------------
# manifest 的 `块` 字段
# ---------------------------------------------------------------------------

def test_块字段缺失时不携带块(tmp_path):
    p = tmp_path / '包.json'
    p.write_text(json.dumps({'名称': '普通包', '版本': '1.0.0'},
                            ensure_ascii=False), encoding='utf-8')
    assert load_manifest(str(p)).block_roots == []


def test_块字段读出声明的块根(tmp_path):
    p = tmp_path / '包.json'
    p.write_text(json.dumps({'名称': '块包', '版本': '1.0.0',
                             '块': ['blocks', '第三方块']},
                            ensure_ascii=False), encoding='utf-8')
    assert load_manifest(str(p)).block_roots == ['blocks', '第三方块']


@pytest.mark.parametrize('坏值', [
    'blocks',                 # 不是数组
    ['', 'blocks'],           # 空串
    [123],                    # 非字符串
    ['../逃逸'],               # `..` 逃出包目录
    ['a/../../b'],            # 藏在中段的 `..`
    ['a\\..\\..\\b'],         # 反斜杠形态（POSIX 上也要拦）
    ['/绝对'],                 # 绝对路径
    ['/'],                    # 只有分隔符
])
def test_块字段非法值被拒(tmp_path, 坏值):
    p = tmp_path / '包.json'
    p.write_text(json.dumps({'名称': '块包', '版本': '1.0.0', '块': 坏值},
                            ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ManifestError):
        load_manifest(str(p))


def test_块字段原样写回(tmp_path):
    """未识别字段宽松保留是 ADR-32 §2.1 的向后兼容前提，别被 save 剥掉。"""
    p = tmp_path / '包.json'
    p.write_text(json.dumps({'名称': '块包', '版本': '1.0.0', '块': ['blocks']},
                            ensure_ascii=False), encoding='utf-8')
    m = load_manifest(str(p))
    save_manifest(m)
    assert json.loads(p.read_text(encoding='utf-8'))['块'] == ['blocks']


# ---------------------------------------------------------------------------
# 索引文件生成
# ---------------------------------------------------------------------------

def test_装普通包不生成索引(tmp_path):
    """不携带块的包不该留下 `.块根.json`——没有块包 = 没有文件。"""
    甲 = os.path.join(str(tmp_path), '甲')
    _写(os.path.join(甲, '包.json'),
        json.dumps({'名称': '甲', '版本': '1.0.0'}, ensure_ascii=False))
    _写(os.path.join(甲, 'main.jk'), '打印 1。\n')
    proj = _造宿主(str(tmp_path), {'甲': '../甲'})
    _装(proj)
    assert not os.path.isfile(_索引路径(proj))


def test_装块包生成索引(tmp_path):
    _造块包(str(tmp_path), '示范块集')
    proj = _造宿主(str(tmp_path), {'示范块集': '../示范块集'})
    _装(proj)

    idx = _索引路径(proj)
    assert os.path.isfile(idx)
    data = json.loads(open(idx, encoding='utf-8').read())
    assert data['索引版本'] == I.BLOCK_ROOTS_INDEX_VERSION
    assert data['块根'] == [{'包': '示范块集', '路径': '示范块集/blocks'}]


def test_索引路径用正斜杠且不含绝对路径(tmp_path):
    """`包.锁` 立的规矩：不写机器相关字段。索引同理，换机器要还能用。"""
    _造块包(str(tmp_path), '示范块集')
    proj = _造宿主(str(tmp_path), {'示范块集': '../示范块集'})
    _装(proj)
    text = open(_索引路径(proj), encoding='utf-8').read()
    assert '\\\\' not in text
    assert str(tmp_path).replace('\\', '/') not in text.replace('\\', '/')


def test_声明的块根不存在时跳过而不报错(tmp_path):
    """包声明了 `块` 但没带上目录：跳过这一条，别让整个安装失败。"""
    甲 = os.path.join(str(tmp_path), '空壳')
    _写(os.path.join(甲, '包.json'), json.dumps(
        {'名称': '空壳', '版本': '1.0.0', '块': ['blocks']},
        ensure_ascii=False))
    proj = _造宿主(str(tmp_path), {'空壳': '../空壳'})
    _装(proj)                                  # 不抛
    assert not os.path.isfile(_索引路径(proj))


def test_多个块包各占一条(tmp_path):
    """索引条目顺序是 `sorted(包名)`，即 **Unicode 码点序**（不是中文序数序）。

    乙(U+4E59) < 甲(U+7532)，所以「乙块集」排在「甲块集」前面。这里钉住的是
    「顺序稳定可复现」——索引每次重建都该产出同样的字节，不制造无意义 diff。
    """
    _造块包(str(tmp_path), '甲块集', 块名='翻倍', 导出='倍增')
    _造块包(str(tmp_path), '乙块集', 块名='减半', 导出='折半')
    proj = _造宿主(str(tmp_path), {'甲块集': '../甲块集', '乙块集': '../乙块集'})
    _装(proj)
    data = json.loads(open(_索引路径(proj), encoding='utf-8').read())
    assert [条['包'] for 条 in data['块根']] == sorted(['甲块集', '乙块集'])


def test_read_block_roots_index_返回绝对路径(tmp_path):
    _造块包(str(tmp_path), '示范块集')
    proj = _造宿主(str(tmp_path), {'示范块集': '../示范块集'})
    _装(proj)
    base = os.path.join(proj, I.PACKAGES_DIR)
    roots = I.read_block_roots_index(base)
    assert len(roots) == 1
    assert os.path.isabs(roots[0])
    assert os.path.isdir(roots[0])
    assert os.path.basename(roots[0]) == 'blocks'


def test_索引版本不符时拒读(tmp_path):
    _造块包(str(tmp_path), '示范块集')
    proj = _造宿主(str(tmp_path), {'示范块集': '../示范块集'})
    _装(proj)
    idx = _索引路径(proj)
    data = json.loads(open(idx, encoding='utf-8').read())
    data['索引版本'] = 999
    _写(idx, json.dumps(data, ensure_ascii=False))
    assert I.read_block_roots_index(os.path.join(proj, I.PACKAGES_DIR)) == []


def test_索引损坏时静默返回空(tmp_path):
    """一个可选索引文件不该挡住 `导入`——坏了就当没有，别抛。"""
    _造块包(str(tmp_path), '示范块集')
    proj = _造宿主(str(tmp_path), {'示范块集': '../示范块集'})
    _装(proj)
    _写(_索引路径(proj), '{ 这不是 json')
    assert I.read_block_roots_index(os.path.join(proj, I.PACKAGES_DIR)) == []


# ---------------------------------------------------------------------------
# 发现侧：scan_blocks 经索引看见块
# ---------------------------------------------------------------------------

def test_发现侧_装完块能被scan_blocks发现(tmp_path, monkeypatch):
    _造块包(str(tmp_path), '示范块集')
    proj = _造宿主(str(tmp_path), {'示范块集': '../示范块集'})
    _装(proj)
    assert ('示范块集', '翻倍') in _发现(proj, monkeypatch)


def test_发现侧_命名空间取包名(tmp_path, monkeypatch):
    _造块包(str(tmp_path), '甲块集')
    proj = _造宿主(str(tmp_path), {'甲块集': '../甲块集'})
    _装(proj)
    命名空间集 = {ns for ns, _ in _发现(proj, monkeypatch)}
    assert 命名空间集 == {'甲块集'}


def test_发现侧_环境变量与索引并存时都生效(tmp_path, monkeypatch):
    """`JIKUAI_PKG_ROOTS` 是手工接入通道，索引上线后它必须继续работать。"""
    手工 = _造块包(str(tmp_path), '手工块集', 块名='取整', 导出='凑整')
    _造块包(str(tmp_path), '装来的', 块名='翻倍', 导出='倍增')
    proj = _造宿主(str(tmp_path), {'装来的': '../装来的'})
    _装(proj)

    monkeypatch.setenv(B.PKG_ROOTS_ENV, os.path.join(手工, 'blocks'))
    monkeypatch.chdir(proj)
    命名空间集 = {b.namespace for b in B.scan_blocks() if b.namespace}
    assert 命名空间集 == {'手工块集', '装来的'}


def test_发现侧_内置块不受影响(tmp_path, monkeypatch):
    """装第三方块包不该改变内置块的数量或命名空间。"""
    monkeypatch.delenv(B.PKG_ROOTS_ENV, raising=False)
    内置数 = len([b for b in B.scan_blocks(root=B.blocks_root())])

    _造块包(str(tmp_path), '示范块集')
    proj = _造宿主(str(tmp_path), {'示范块集': '../示范块集'})
    _装(proj)
    monkeypatch.chdir(proj)
    全部 = B.scan_blocks()
    assert len([b for b in 全部 if not b.namespace]) == 内置数
    assert len(全部) == 内置数 + 1


# ---------------------------------------------------------------------------
# 执行侧：module_loader 经索引解析 dotpath
# ---------------------------------------------------------------------------

def test_执行侧_装完块能被导入并跑(tmp_path, monkeypatch, capsys):
    """W64 发现的另一半链路：只挂发现侧的话这里会报 JK-E5001。"""
    _造块包(str(tmp_path), '示范块集')
    proj = _造宿主(str(tmp_path), {'示范块集': '../示范块集'})
    _装(proj)
    _跑(proj, '从 blocks.示范块集.数据.翻倍 导入 倍增。\n打印 倍增(21)。\n',
        monkeypatch)
    assert '42' in capsys.readouterr().out


def test_执行侧_未装时导入报找不到模块(tmp_path, monkeypatch):
    """反例：没装块包时 dotpath 必须解析失败，证明上一条是索引起的作用。"""
    proj = _造宿主(str(tmp_path), {})
    os.makedirs(os.path.join(proj, I.PACKAGES_DIR), exist_ok=True)
    from jikuai.evaluator import JiKuaiError
    with pytest.raises((JiKuaiError, SystemExit)):
        _跑(proj, '从 blocks.示范块集.数据.翻倍 导入 倍增。\n', monkeypatch)


def test_执行侧_块根父目录进搜索路径(tmp_path):
    """直接查 `_search_paths` 的产物：块根的父目录（包目录）必须在里面。"""
    _造块包(str(tmp_path), '示范块集')
    proj = _造宿主(str(tmp_path), {'示范块集': '../示范块集'})
    _装(proj)

    from jikuai.module_loader import ModuleLoader
    loader = ModuleLoader(evaluator=None)
    main = os.path.join(proj, 'main.jk')
    _写(main, '打印 1。\n')
    paths = [os.path.normpath(p) for p in loader._search_paths(main)]
    包目录 = os.path.normpath(os.path.join(proj, I.PACKAGES_DIR, '示范块集'))
    assert 包目录 in paths
    # 必须排在 stdlib 之前：第三方块可被内置块遮蔽（ADR-27 §2.3 内置优先）
    stdlib_idx = next(i for i, p in enumerate(paths)
                      if os.path.basename(p) == 'stdlib')
    assert paths.index(包目录) < stdlib_idx


# ---------------------------------------------------------------------------
# 卸载对称
# ---------------------------------------------------------------------------

def test_卸载后索引条目消失(tmp_path):
    _造块包(str(tmp_path), '示范块集')
    proj = _造宿主(str(tmp_path), {'示范块集': '../示范块集'})
    _装(proj)
    assert os.path.isfile(_索引路径(proj))

    assert I.uninstall(proj, '示范块集') is True
    assert not os.path.isfile(_索引路径(proj))


def test_卸载一个保留另一个(tmp_path):
    _造块包(str(tmp_path), '甲块集', 块名='翻倍', 导出='倍增')
    _造块包(str(tmp_path), '乙块集', 块名='减半', 导出='折半')
    proj = _造宿主(str(tmp_path), {'甲块集': '../甲块集', '乙块集': '../乙块集'})
    _装(proj)

    I.uninstall(proj, '甲块集')
    data = json.loads(open(_索引路径(proj), encoding='utf-8').read())
    assert [条['包'] for 条 in data['块根']] == ['乙块集']


def test_卸载后块不再被发现(tmp_path, monkeypatch):
    _造块包(str(tmp_path), '示范块集')
    proj = _造宿主(str(tmp_path), {'示范块集': '../示范块集'})
    _装(proj)
    assert _发现(proj, monkeypatch) != []

    I.uninstall(proj, '示范块集')
    assert _发现(proj, monkeypatch) == []


def test_依赖移除后重装裁剪掉索引条目(tmp_path):
    """走 `install(prune=True)` 这条路：清单里去掉依赖后重装，索引要跟着瘦。"""
    _造块包(str(tmp_path), '甲块集', 块名='翻倍', 导出='倍增')
    _造块包(str(tmp_path), '乙块集', 块名='减半', 导出='折半')
    proj = _造宿主(str(tmp_path), {'甲块集': '../甲块集', '乙块集': '../乙块集'})
    _装(proj)

    m = load_manifest(proj)
    m.remove_dependency('甲块集')
    save_manifest(m)
    _装(proj)

    data = json.loads(open(_索引路径(proj), encoding='utf-8').read())
    assert [条['包'] for 条 in data['块根']] == ['乙块集']
    assert not os.path.isdir(os.path.join(proj, I.PACKAGES_DIR, '甲块集'))


# ---------------------------------------------------------------------------
# 安全边界
# ---------------------------------------------------------------------------

def test_索引不收逃出包目录的块根(tmp_path):
    """manifest 已拦 `..`，这里测 installer 侧的第二道兜底。

    手工绕过 manifest 校验（直接改磁盘上的清单为合法值、再让 installer
    面对一个指向包外的绝对路径解析结果）——`_收集块根` 的 commonpath
    检查必须把它挡掉。
    """
    pkg = _造块包(str(tmp_path), '示范块集')
    proj = _造宿主(str(tmp_path), {'示范块集': '../示范块集'})
    _装(proj)
    # 装完后篡改已安装副本的清单：块根指到包外（模拟被绕过的场景）
    装后 = os.path.join(proj, I.PACKAGES_DIR, '示范块集')
    坏清单 = os.path.join(装后, '包.json')
    data = json.loads(open(坏清单, encoding='utf-8').read())
    data['块'] = ['blocks']
    _写(坏清单, json.dumps(data, ensure_ascii=False))
    # 直接调收集器，传一个越界的相对路径构造
    条目 = I._收集块根(os.path.join(proj, I.PACKAGES_DIR), {'示范块集'})
    for 条 in 条目:
        abs_p = os.path.normpath(
            os.path.join(proj, I.PACKAGES_DIR, 条['路径']))
        assert abs_p.startswith(os.path.abspath(装后))
