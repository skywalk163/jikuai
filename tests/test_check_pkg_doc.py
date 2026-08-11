# -*- coding: utf-8 -*-
"""v0.19.0 · W62 · G17「包管理文档同步」门禁测试（`scripts/check_pkg_doc.py`）。

正例走真仓库文件（G17 现状必须全绿）；反例用 tmp_path 造合成的
doc / cli.py 对，逐一证明门禁**真的抓得到**这些漂移：

1. 文档漏写代码里有的子命令（W61 纠偏前 `发布`/`搜索`/`注册表` 的真实病症）
2. 文档写了代码里没有的子命令（文档许诺一个不存在的命令）
3. 英文别名对不上（代码加 `rm` 别名、文档没跟）
4. 中文别名对不上（`删除`/`安装` 这类同义主名遗漏）
5. 文档「命令表」小节整节缺失（解析失败而非静默通过）
6. `_ALIASES` 字面量结构变了（解析不出来必须红，不静默跳过）

反例测试是这个门禁的价值所在——只测正例的门禁跟没有门禁没区别
（v0.18.0 W55 起的规矩）。
"""

import os
import sys

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SCRIPTS = os.path.join(_REPO, 'scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import check_pkg_doc as G17  # noqa: E402


# ---------------------------------------------------------------------------
# 合成素材
# ---------------------------------------------------------------------------

def _造cli(tmp_path, 别名对):
    """造一份只含 `_ALIASES` 字面量的最小 cli.py。

    `别名对` 是 `[(键, 规范名), ...]`——刻意保留真 cli.py 的「多键映射同一
    规范名」形态（`'移除': 'remove', '删除': 'remove', 'rm': 'remove'`）。
    """
    项 = ''.join('%r: %r, ' % (k, v) for k, v in 别名对)
    src = ('# -*- coding: utf-8 -*-\n'
           '_ALIASES = {' + 项 + '}\n')
    p = tmp_path / 'cli.py'
    p.write_text(src, encoding='utf-8')
    return str(p)


def _造doc(tmp_path, 中文主名, 英文别名=(), 中文别名=()):
    """造一份只含「## 命令表」小节的最小包管理文档。

    `中文主名` 渲染成真文档同款的 ``- `jk 包 X <参数>` —— 略`` bullet；
    刻意给部分命令带上尾随参数，验证 bullet 抽取不依赖紧跟的闭合反引号。
    """
    行 = ['# 包管理（M8）', '', '## 快速上手', '', '略。', '',
          '## 命令表', '']
    for i, name in enumerate(中文主名):
        # 交替带/不带尾随参数，覆盖两种 bullet 形态
        尾 = ' <名称>' if i % 2 == 0 else ''
        行.append('- `jk 包 %s%s` —— 略。' % (name, 尾))
    if 中文别名:
        行 += ['', '中文别名：' + '、'.join('`%s`' % a for a in 中文别名) + '。']
    if 英文别名:
        行 += ['', '英文别名：' + ' / '.join('`%s`' % a for a in 英文别名) + '。']
    行 += ['', '## 文件布局', '', '略。', '']
    f = tmp_path / '包管理.md'
    f.write_text('\n'.join(行), encoding='utf-8')
    return str(f)


def _跑(doc, cli):
    return G17.main(['--quiet', '--doc', doc, '--cli', cli])


#: 一份自洽的最小命令集（中文主名 / 中文别名 / 英文别名 三者与 cli 对齐）
_基准别名对 = [
    ('初始化', 'init'), ('init', 'init'),
    ('移除', 'remove'), ('删除', 'remove'), ('remove', 'remove'), ('rm', 'remove'),
    ('发布', 'publish'), ('publish', 'publish'),
]
_基准中文主名 = ('初始化', '移除', '发布')
_基准中文别名 = ('删除',)
_基准英文别名 = ('init', 'remove', 'rm', 'publish')


# ---------------------------------------------------------------------------
# 正例
# ---------------------------------------------------------------------------

def test_真仓库现状全绿():
    """G17 上线时实测必须过——W61 已手工把命令表补齐到与 cli.py 一致。"""
    assert G17.main(['--quiet']) == 0


def test_合成一致时通过(tmp_path):
    cli = _造cli(tmp_path, _基准别名对)
    doc = _造doc(tmp_path, _基准中文主名,
                 英文别名=_基准英文别名, 中文别名=_基准中文别名)
    assert _跑(doc, cli) == 0


def test_bullet带尾随参数也能抽到命令名(tmp_path):
    """真文档写的是 ``jk 包 初始化 [名称]``——命令名后有参数，
    抽取不能要求紧跟闭合反引号（G17 初版就栽在这，只抽到无参数的三条）。"""
    cli = _造cli(tmp_path, [('装', 'install'), ('install', 'install')])
    f = tmp_path / '带参.md'
    f.write_text('# x\n\n## 命令表\n\n'
                 '- `jk 包 装 [--含开发] [--其它]` —— 略。\n\n'
                 '英文别名：`install`。\n\n## 下一节\n\n略。\n',
                 encoding='utf-8')
    assert _跑(str(f), cli) == 0


# ---------------------------------------------------------------------------
# 反例：门禁必须抓到
# ---------------------------------------------------------------------------

def test_反例_文档漏写代码里的子命令(tmp_path, capsys):
    """W61 纠偏前的真实病症：`发布`/`搜索`/`注册表` 在 v0.11.0 就落地，
    命令表一直没写，漂了约七个版本。"""
    cli = _造cli(tmp_path, _基准别名对 + [('搜索', 'search'), ('search', 'search')])
    doc = _造doc(tmp_path, _基准中文主名,
                 英文别名=_基准英文别名, 中文别名=_基准中文别名)
    assert _跑(doc, cli) == 1
    err = capsys.readouterr().err
    assert '代码有但文档没写' in err
    assert '搜索' in err
    assert 'search' in err


def test_反例_文档写了代码没有的子命令(tmp_path, capsys):
    """反方向也要抓：文档许诺一个不存在的命令，用户照着敲会撞「未知命令」。"""
    cli = _造cli(tmp_path, _基准别名对)
    doc = _造doc(tmp_path, _基准中文主名 + ('登录',),
                 英文别名=_基准英文别名, 中文别名=_基准中文别名)
    assert _跑(doc, cli) == 1
    err = capsys.readouterr().err
    assert '文档写了但代码没有' in err
    assert '登录' in err


def test_反例_英文别名遗漏(tmp_path, capsys):
    """代码有 `rm` 别名、文档没列——用户不知道能简写。"""
    cli = _造cli(tmp_path, _基准别名对)
    doc = _造doc(tmp_path, _基准中文主名,
                 英文别名=('init', 'remove', 'publish'),   # 漏了 rm
                 中文别名=_基准中文别名)
    assert _跑(doc, cli) == 1
    err = capsys.readouterr().err
    assert '[英文别名]' in err
    assert 'rm' in err


def test_反例_中文别名遗漏(tmp_path, capsys):
    """`删除`（同 `移除`）这类同义主名漏在文档外，也算漂移。"""
    cli = _造cli(tmp_path, _基准别名对)
    doc = _造doc(tmp_path, _基准中文主名,
                 英文别名=_基准英文别名, 中文别名=())   # 漏了 删除
    assert _跑(doc, cli) == 1
    err = capsys.readouterr().err
    assert '[中文命令]' in err
    assert '删除' in err


def test_反例_文档命令表小节缺失时报错而非静默通过(tmp_path, capsys):
    cli = _造cli(tmp_path, _基准别名对)
    f = tmp_path / '无小节.md'
    f.write_text('# 包管理\n\n## 快速上手\n\n略。\n', encoding='utf-8')
    assert _跑(str(f), cli) == 1
    assert '解析失败' in capsys.readouterr().err


def test_反例_cli缺ALIASES字面量时报错(tmp_path, capsys):
    """`_ALIASES` 改成运行时构造（非字面量 dict）时门禁必须红——
    解析不了就是它自己坏了，静默跳过等于门禁形同虚设。"""
    p = tmp_path / 'cli.py'
    p.write_text('_ALIASES = dict(init="init")\n', encoding='utf-8')
    doc = _造doc(tmp_path, _基准中文主名, 英文别名=_基准英文别名)
    assert _跑(doc, str(p)) == 1
    assert '_ALIASES' in capsys.readouterr().err


def test_反例_ALIASES空字典时报错(tmp_path, capsys):
    p = tmp_path / 'cli.py'
    p.write_text('_ALIASES = {}\n', encoding='utf-8')
    doc = _造doc(tmp_path, _基准中文主名, 英文别名=_基准英文别名)
    assert _跑(doc, str(p)) == 1
    assert '解析失败' in capsys.readouterr().err


def test_switch键不计入英文别名(tmp_path):
    """`-h` / `--help` 是 switch 不是子命令名，文档不必列。"""
    cli = _造cli(tmp_path, [
        ('帮助', 'help'), ('help', 'help'), ('-h', 'help'), ('--help', 'help'),
    ])
    f = tmp_path / 'sw.md'
    f.write_text('# x\n\n## 命令表\n\n- `jk 包 帮助` —— 略。\n\n'
                 '英文别名：`help`。\n\n## 下一节\n\n略。\n',
                 encoding='utf-8')
    assert _跑(str(f), cli) == 0


def test_文件不存在时返回1(tmp_path, capsys):
    assert G17.main(['--doc', str(tmp_path / '没有.md'),
                     '--cli', str(tmp_path / '没有.py')]) == 1
    assert '不存在' in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 与门禁主入口的串联
# ---------------------------------------------------------------------------

def test_G17已串进check_stdlib_contract():
    """G17 必须被 `scripts/check_stdlib_contract.py` 调用，否则 CI 跑不到它。"""
    path = os.path.join(_SCRIPTS, 'check_stdlib_contract.py')
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    assert 'check_pkg_doc' in text
    assert 'G17' in text


@pytest.mark.parametrize('键, 归类', [
    ('初始化', '中文'),
    ('注册表', '中文'),
    ('init', '英文'),
    ('rm', '英文'),
    ('-h', '排除'),
    ('--help', '排除'),
])
def test_键归类(tmp_path, 键, 归类):
    cli = _造cli(tmp_path, [(键, 'x')])
    src = G17._读文件(cli)
    if 归类 == '排除':
        # 全部键都被排除 → `_ALIASES` 解析为空 → 报错
        with pytest.raises(ValueError):
            G17._code命令(src)
        return
    中, 英 = G17._code命令(src)
    if 归类 == '中文':
        assert 键 in 中 and 键 not in 英
    else:
        assert 键 in 英 and 键 not in 中
