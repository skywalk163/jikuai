# -*- coding: utf-8 -*-
"""v0.22.0 · W98 · 包管理 CLI（`jk 包 ...`）分支覆盖。

W97 把覆盖率测量口径修正后，`pkg/cli.py` 是全仓唯一的真空洞（41.8%，
且修口径前后一点没变——说明它确实没被测，不是子进程测量假象）。这个文件
按子命令补齐：

  1. `初始化`  —— .gitignore 追加/幂等、非法包名
  2. `添加`    —— `_parse_add_source` 的全部错误分支（缺参/来源互斥/孤立 --标签/非法约束）
  3. `移除`    —— 缺参、依赖不在清单、成功路径
  4. `装`      —— 无清单、无依赖、`--含开发`
  5. `列表`    —— 无清单、空依赖、未安装/已装/版本未知三态、开发/运行标记
  6. `运行`    —— 缺参、脚本名不存在、返回码透传（0 与非 0）
  7. `发布`    —— `--分类`/`--签名` 缺参、默认演练、正式发布（未签名告警 + 带签名）
  8. `搜索`    —— 空注册表两种文案、命中列表、无描述兜底
  9. `注册表`  —— 统计输出
 10. `密钥`    —— 未知子命令、`导出` 缺别名、空密钥根文案、残缺态标记

风格对齐 `test_pkg_signing.py`：模块级函数 + pytest fixture，不用 TestCase；
断言输出统一走 `capsys`（消费型，每次断言后重读）。

刻意**全部进程内调 `cli.run`**：CLI 层只做参数解析 + 打印，起子进程只会
让 260 条用例慢上几十秒，换不到任何额外覆盖。
"""

import json
import os
import sys

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg import keys                                    # noqa: E402
from jikuai.pkg import registry                                # noqa: E402
from jikuai.pkg import trust                                   # noqa: E402
from jikuai.pkg.cli import run                                 # noqa: E402
from jikuai.pkg.installer import PACKAGES_DIR                  # noqa: E402
from jikuai.pkg.manifest import MANIFEST_NAME, load_manifest   # noqa: E402


# ---------------------------------------------------------------------------
# 固定环境
# ---------------------------------------------------------------------------

@pytest.fixture
def 隔离环境(tmp_path, monkeypatch):
    """隔离注册表根 / 密钥根 / 信任库，并把 cwd 切到一个空工程目录。

    cwd 必须切：`_cmd_init` 走 `os.getcwd()`，其余子命令走无参
    `load_manifest()` 向上查找——留在仓库根会读到极快自己的 `包.json`。
    """
    工程 = tmp_path / '工程'
    工程.mkdir()
    monkeypatch.setenv('JIKUAI_REGISTRY', str(tmp_path / '注册表'))
    monkeypatch.setenv(keys.KEY_ROOT_ENV, str(tmp_path / '密钥'))
    monkeypatch.setenv(trust.TRUST_ROOT_ENV, str(tmp_path / '信任'))
    monkeypatch.delenv(trust.TRUSTED_SIGNERS_ENV, raising=False)
    monkeypatch.chdir(工程)
    return 工程


def _写清单(目录, **覆盖):
    """在 `目录` 下写一份 `包.json`，`覆盖` 里的键直接并入。"""
    数据 = {
        '名称': '主', '版本': '0.1.0', '描述': 'W98 CLI 测试用',
        '入口': 'main.jk', '依赖': {},
    }
    数据.update(覆盖)
    (目录 / MANIFEST_NAME).write_text(
        json.dumps(数据, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')
    (目录 / 'main.jk').write_text('打印("好")\n', encoding='utf-8',
                                  newline='\n')
    return 目录


def _造外部包(tmp_path, 名称='外部', 版本='0.1.0'):
    """在工程**之外**造一个可被 `--路径` 引用的包，返回绝对路径。"""
    d = tmp_path / 名称
    d.mkdir(parents=True, exist_ok=True)
    (d / MANIFEST_NAME).write_text(json.dumps({
        '名称': 名称, '版本': 版本, '描述': '', '入口': 'main.jk', '依赖': {},
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    (d / 'main.jk').write_text('打印("外")\n', encoding='utf-8', newline='\n')
    return str(d)


# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------

def test_初始化_追加gitignore(隔离环境, capsys):
    assert run(['初始化', '甲包']) == 0
    assert '已创建' in capsys.readouterr().out
    gi = (隔离环境 / '.gitignore').read_text(encoding='utf-8')
    assert f'{PACKAGES_DIR}/' in gi.split()


def test_初始化_gitignore已忽略时不重复追加(隔离环境):
    (隔离环境 / '.gitignore').write_text(f'{PACKAGES_DIR}/\n',
                                        encoding='utf-8', newline='\n')
    assert run(['初始化', '甲包']) == 0
    内容 = (隔离环境 / '.gitignore').read_text(encoding='utf-8')
    assert 内容.split().count(f'{PACKAGES_DIR}/') == 1


def test_初始化_gitignore无末尾换行时补一个(隔离环境):
    (隔离环境 / '.gitignore').write_text('*.pyc', encoding='utf-8',
                                        newline='\n')
    assert run(['初始化', '甲包']) == 0
    内容 = (隔离环境 / '.gitignore').read_text(encoding='utf-8')
    assert 内容 == f'*.pyc\n{PACKAGES_DIR}/\n'


def test_初始化_非法包名被拒(隔离环境, capsys):
    assert run(['初始化', '带/斜杠']) == 1
    assert '包管理错误' in capsys.readouterr().err
    assert not (隔离环境 / MANIFEST_NAME).exists()


def test_初始化_不给名字时用目录名(隔离环境):
    assert run(['初始化']) == 0
    assert load_manifest(str(隔离环境)).name == '工程'


# ---------------------------------------------------------------------------
# 添加：_parse_add_source 的错误分支
# ---------------------------------------------------------------------------

def test_添加_缺参数(隔离环境, capsys):
    assert run(['添加']) == 1
    assert '用法' in capsys.readouterr().err


@pytest.mark.parametrize('参数,片段', [
    (['甲', '--路径'], '--路径 后面'),
    (['甲', '--仓库'], '--仓库 后面'),
    (['甲', '--仓库', 'https://x.git', '--标签'], '--标签 后面'),
    (['甲', '^1.0.0', '--路径', '../甲'], '只能给一个'),
    (['甲', '--标签', 'v1.0.0'], '--标签 只能与'),
    (['甲', '不是版本约束'], '包管理错误'),
    (['带/斜杠'], '包管理错误'),
])
def test_添加_参数错误(隔离环境, capsys, 参数, 片段):
    _写清单(隔离环境)
    assert run(['添加'] + 参数) == 1
    assert 片段 in capsys.readouterr().err


def test_添加_路径依赖成功(隔离环境, tmp_path, capsys):
    _写清单(隔离环境)
    _造外部包(tmp_path)
    assert run(['添加', '外部', '--路径', '../外部']) == 0
    out = capsys.readouterr().out
    assert '已添加依赖 外部' in out
    assert '+ 外部@0.1.0' in out
    assert '外部' in load_manifest(str(隔离环境)).dependencies()


# ---------------------------------------------------------------------------
# 移除
# ---------------------------------------------------------------------------

def test_移除_缺参数(隔离环境, capsys):
    assert run(['移除']) == 1
    assert '用法' in capsys.readouterr().err


def test_移除_依赖不在清单(隔离环境, capsys):
    _写清单(隔离环境)
    assert run(['移除', '不存在的']) == 1
    assert '清单里没有依赖' in capsys.readouterr().err


def test_移除_成功后清单与目录都干净(隔离环境, tmp_path, capsys):
    _写清单(隔离环境, 依赖={'外部': {'路径': '../外部'}})
    _造外部包(tmp_path)
    assert run(['装']) == 0
    capsys.readouterr()
    assert run(['移除', '外部']) == 0
    assert '已移除依赖 外部' in capsys.readouterr().out
    assert load_manifest(str(隔离环境)).dependencies() == {}
    assert not (隔离环境 / PACKAGES_DIR / '外部').exists()


# ---------------------------------------------------------------------------
# 装
# ---------------------------------------------------------------------------

def test_装_无清单报错(隔离环境, capsys):
    assert run(['装']) == 1
    assert '包管理错误' in capsys.readouterr().err


def test_装_没有依赖(隔离环境, capsys):
    _写清单(隔离环境)
    assert run(['装']) == 0
    assert '没有依赖需要安装' in capsys.readouterr().out


def test_装_含开发依赖(隔离环境, tmp_path, capsys):
    _写清单(隔离环境, 开发依赖={'外部': {'路径': '../外部'}})
    _造外部包(tmp_path)
    # 不给 --含开发：开发依赖不参与安装
    assert run(['装']) == 0
    assert '没有依赖需要安装' in capsys.readouterr().out
    assert run(['装', '--含开发']) == 0
    out = capsys.readouterr().out
    assert '含开发依赖' in out
    assert '+ 外部@0.1.0' in out
    assert (隔离环境 / PACKAGES_DIR / '外部' / MANIFEST_NAME).is_file()


def test_装_第二次是unchanged(隔离环境, tmp_path, capsys):
    _写清单(隔离环境, 依赖={'外部': {'路径': '../外部'}})
    _造外部包(tmp_path)
    assert run(['装']) == 0
    capsys.readouterr()
    assert run(['装']) == 0
    assert '= 外部@0.1.0' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------

def test_列表_无清单报错(隔离环境, capsys):
    assert run(['列表']) == 1
    assert '包管理错误' in capsys.readouterr().err


def test_列表_没有依赖打无(隔离环境, capsys):
    _写清单(隔离环境)
    assert run(['列表']) == 0
    out = capsys.readouterr().out
    assert '主@0.1.0 的依赖：' in out
    assert '（无）' in out


def test_列表_三种安装状态与开发标记(隔离环境, tmp_path, capsys):
    # 先只装一个路径依赖（`没装的`/`坏包` 走注册表会解析失败），装成功后再把
    # 声明补进清单——`列表` 只读声明 + 扫安装目录，不重新解析。
    _写清单(隔离环境, 依赖={'外部': {'路径': '../外部'}})
    _造外部包(tmp_path)
    assert run(['装']) == 0
    capsys.readouterr()
    _写清单(隔离环境,
            依赖={'外部': {'路径': '../外部'}, '没装的': '*'},
            开发依赖={'坏包': '*'})
    # 造一个「有目录、无可读清单」的损坏包，触发「已安装(版本未知)」
    坏 = 隔离环境 / PACKAGES_DIR / '坏包'
    坏.mkdir(parents=True)
    assert run(['列表']) == 0
    out = capsys.readouterr().out
    assert '[运行] 外部' in out and 'v0.1.0' in out
    assert '[运行] 没装的' in out and '未安装' in out
    assert '[开发] 坏包' in out and '已安装(版本未知)' in out


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------

def test_运行_缺参数(隔离环境, capsys):
    assert run(['运行']) == 1
    assert '用法' in capsys.readouterr().err


def test_运行_脚本名不存在时列出可用脚本(隔离环境, capsys):
    _写清单(隔离环境, 脚本={'测试': 'echo hi'})
    assert run(['运行', '没有的']) == 1
    err = capsys.readouterr().err
    assert '没有 没有的' in err
    assert '测试' in err


def test_运行_无脚本表时提示无(隔离环境, capsys):
    _写清单(隔离环境)
    assert run(['运行', '啥都行']) == 1
    assert '（无）' in capsys.readouterr().err


def test_运行_透传返回码(隔离环境, capsys):
    脚本 = f'"{sys.executable}" -c "raise SystemExit(3)"'
    _写清单(隔离环境, 脚本={'好': f'"{sys.executable}" -c "pass"', '坏': 脚本})
    assert run(['运行', '好']) == 0
    assert '>' in capsys.readouterr().out
    assert run(['运行', '坏']) == 3


# ---------------------------------------------------------------------------
# 发布
# ---------------------------------------------------------------------------

def test_发布_分类缺参(隔离环境, capsys):
    _写清单(隔离环境)
    assert run(['发布', '--分类']) == 1
    assert '--分类 后面' in capsys.readouterr().err


def test_发布_签名缺参(隔离环境, capsys):
    _写清单(隔离环境)
    assert run(['发布', '--签名']) == 1
    assert '--签名 后面' in capsys.readouterr().err


def test_发布_默认演练不落盘(隔离环境, capsys):
    _写清单(隔离环境)
    assert run(['发布']) == 0
    out = capsys.readouterr().out
    assert '[演练]' in out
    assert '未落盘' in out
    assert '本地注册表' in out
    assert registry.load_index().get('索引', {}) == {}


def test_发布_确认后落盘且未签名给告警(隔离环境, capsys):
    _写清单(隔离环境)
    assert run(['发布', '--确认', '--分类', '工具']) == 0
    out = capsys.readouterr().out
    assert '已发布 主@0.1.0' in out
    assert '分类：工具' in out
    assert '未签名发布' in out
    assert '主' in registry.load_index()['索引']


def test_发布_带签名(隔离环境, capsys):
    _写清单(隔离环境)
    assert run(['密钥', '生成', '甲']) == 0
    capsys.readouterr()
    assert run(['发布', '--确认', '--签名', '甲']) == 0
    out = capsys.readouterr().out
    assert '签名者：甲' in out
    assert '未签名发布' not in out


def test_发布_允许覆盖(隔离环境, capsys):
    _写清单(隔离环境)
    assert run(['发布', '--确认']) == 0
    capsys.readouterr()
    assert run(['发布', '--确认']) == 1          # 同版本重发被拒
    assert '包管理错误' in capsys.readouterr().err
    assert run(['发布', '--确认', '--允许覆盖']) == 0
    assert '已覆盖发布' in capsys.readouterr().out


def test_发布_签名别名不存在(隔离环境, capsys):
    _写清单(隔离环境)
    assert run(['发布', '--确认', '--签名', '没这个别名']) == 1
    assert '包管理错误' in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 搜索 / 注册表
# ---------------------------------------------------------------------------

def test_搜索_空注册表两种文案(隔离环境, capsys):
    assert run(['搜索']) == 0
    assert '注册表里没有包' in capsys.readouterr().out
    assert run(['搜索', '关键词']) == 0
    assert "包含 '关键词' 的" in capsys.readouterr().out


def test_搜索_命中已发布包(隔离环境, capsys):
    _写清单(隔离环境, 名称='搜得到', 描述='一句话说明')
    assert run(['发布', '--确认']) == 0
    capsys.readouterr()
    assert run(['搜索', '搜得到']) == 0
    out = capsys.readouterr().out
    assert '找到 1 个包' in out
    assert '搜得到@0.1.0' in out
    assert '一句话说明' in out


def test_搜索_无描述兜底(隔离环境, capsys):
    _写清单(隔离环境, 名称='没描述', 描述='')
    assert run(['发布', '--确认']) == 0
    capsys.readouterr()
    assert run(['搜索', '没描述']) == 0
    assert '（无描述）' in capsys.readouterr().out


def test_注册表_打印根目录与统计(隔离环境, capsys):
    _写清单(隔离环境)
    assert run(['发布', '--确认']) == 0
    capsys.readouterr()
    assert run(['注册表']) == 0
    out = capsys.readouterr().out
    assert '注册表根目录：' in out
    assert '总包数：1' in out
    assert '总版本数：1' in out


# ---------------------------------------------------------------------------
# 密钥（补 test_pkg_signing.py 未覆盖的分支）
# ---------------------------------------------------------------------------

def test_密钥_未知子命令(隔离环境, capsys):
    assert run(['密钥', '搞事']) == 1
    assert '未知的密钥子命令' in capsys.readouterr().err


def test_密钥_生成缺别名(隔离环境, capsys):
    assert run(['密钥', '生成']) == 1
    assert '需要一个别名' in capsys.readouterr().err


def test_密钥_导出缺别名(隔离环境, capsys):
    assert run(['密钥', '导出']) == 1
    assert '需要一个别名' in capsys.readouterr().err


def test_密钥_导出不存在的别名(隔离环境, capsys):
    assert run(['密钥', '导出', '没这个']) == 1
    assert '包管理错误' in capsys.readouterr().err


def test_密钥_空密钥根给引导(隔离环境, capsys):
    assert run(['密钥', '列表']) == 0
    out = capsys.readouterr().out
    assert '没有任何密钥' in out
    assert '密钥 生成' in out


def test_密钥_列表标出残缺态(隔离环境, capsys):
    assert run(['密钥', '生成', '全的']) == 0
    assert run(['密钥', '生成', '缺公钥']) == 0
    capsys.readouterr()
    # 密钥是密钥根下的扁平文件（`别名.私钥` / `别名.公钥`），删掉公钥即成残缺态
    os.remove(os.path.join(keys.key_root(), '缺公钥.公钥'))
    assert run(['密钥', '列表']) == 0
    out = capsys.readouterr().out
    assert '全的  [可签名]' in out
    assert '缺公钥  [仅私钥（公钥缺失，需重新生成）]' in out


# ---------------------------------------------------------------------------
# v0.22.0 · W101 · `密钥 信任` / `密钥 撤信`（ADR-36 §2.4）
# ---------------------------------------------------------------------------

def _一把公钥(隔离环境, 别名):
    """生成一对密钥并返回其 base64 公钥。"""
    assert run(['密钥', '生成', 别名]) == 0
    import base64
    return base64.b64encode(keys.load_public_key(别名)).decode('ascii')


@pytest.mark.parametrize('子命令', ['信任', 'trust'])
def test_密钥_信任_追加并幂等(隔离环境, capsys, 子命令):
    pub = _一把公钥(隔离环境, '甲')
    capsys.readouterr()

    assert run(['密钥', 子命令, '乙方', pub]) == 0
    out = capsys.readouterr().out
    assert '已把公钥追加进「乙方」的信任列表' in out
    assert '当前受信公钥数：1' in out
    assert trust.pinned_keys('乙方') == [keys.load_public_key('甲')]

    # 再来一次是幂等的
    assert run(['密钥', 子命令, '乙方', pub]) == 0
    assert '已在「乙方」的信任列表里' in capsys.readouterr().out
    assert len(trust.pinned_keys('乙方')) == 1


def test_密钥_信任_两把公钥并存(隔离环境, capsys):
    """轮换语义：追加不替换，第一行仍是原主公钥。"""
    第一把 = _一把公钥(隔离环境, '旧')
    第二把 = _一把公钥(隔离环境, '新')
    capsys.readouterr()
    assert run(['密钥', '信任', '甲方', 第一把]) == 0
    assert run(['密钥', '信任', '甲方', 第二把]) == 0
    assert '当前受信公钥数：2' in capsys.readouterr().out
    受信 = trust.pinned_keys('甲方')
    assert 受信 == [keys.load_public_key('旧'), keys.load_public_key('新')]


@pytest.mark.parametrize('子命令', ['撤信', 'untrust', 'revoke'])
def test_密钥_撤信_移除并报剩余(隔离环境, capsys, 子命令):
    第一把 = _一把公钥(隔离环境, '甲')
    第二把 = _一把公钥(隔离环境, '乙')
    assert run(['密钥', '信任', '丙方', 第一把]) == 0
    assert run(['密钥', '信任', '丙方', 第二把]) == 0
    capsys.readouterr()

    assert run(['密钥', 子命令, '丙方', 第一把]) == 0
    out = capsys.readouterr().out
    assert '已从「丙方」的信任列表移除该公钥' in out
    assert '剩余受信公钥数：1' in out
    assert trust.pinned_keys('丙方') == [keys.load_public_key('乙')]

    # 撤到一把不剩 → 提示回到 TOFU
    assert run(['密钥', 子命令, '丙方', 第二把]) == 0
    out = capsys.readouterr().out
    assert '剩余受信公钥数：0' in out
    assert '首次信任（TOFU）' in out
    assert trust.pinned_keys('丙方') == []


def test_密钥_撤信_列表里没有这把(隔离环境, capsys):
    pub = _一把公钥(隔离环境, '甲')
    capsys.readouterr()
    assert run(['密钥', '撤信', '没信过', pub]) == 0
    assert '没有这把公钥' in capsys.readouterr().out


@pytest.mark.parametrize('子命令', ['信任', '撤信'])
def test_密钥_信任撤信_缺参(隔离环境, capsys, 子命令):
    assert run(['密钥', 子命令]) == 1
    assert run(['密钥', 子命令, '只有别名']) == 1
    err = capsys.readouterr().err
    assert f'密钥 {子命令} 需要别名与公钥' in err


def test_密钥_信任_坏公钥被拒(隔离环境, capsys):
    assert run(['密钥', '信任', '甲', '这不是base64!!!']) == 1
    assert '公钥' in capsys.readouterr().err
    # 长度对不上也拒（合法 base64 但只有 16 字节）
    import base64
    短 = base64.b64encode(b'\x00' * 16).decode('ascii')
    assert run(['密钥', '信任', '甲', 短]) == 1
    assert '长度异常' in capsys.readouterr().err
    assert trust.pinned_keys('甲') == []


# ---------------------------------------------------------------------------
# 顶层分发
# ---------------------------------------------------------------------------

def test_未知命令打用法到stderr(隔离环境, capsys):
    assert run(['没这个命令']) == 1
    err = capsys.readouterr().err
    assert '未知的包管理命令' in err
    assert '极快包管理 用法' in err


@pytest.mark.parametrize('别名', ['帮助', 'help', '-h', '--help'])
def test_帮助各别名(隔离环境, capsys, 别名):
    assert run([别名]) == 0
    assert '极快包管理 用法' in capsys.readouterr().out


@pytest.mark.parametrize('别名,片段', [
    ('ls', '的依赖：'),
    ('list', '的依赖：'),
    ('i', '没有依赖需要安装'),
    ('install', '没有依赖需要安装'),
])
def test_英文别名归一(隔离环境, capsys, 别名, 片段):
    _写清单(隔离环境)
    assert run([别名]) == 0
    assert 片段 in capsys.readouterr().out
