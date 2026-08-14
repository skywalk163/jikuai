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

v0.22.0 · W103 加第二层（`密钥` 子子命令 ↔ `_cmd_key` 分派分支）：

7. 代码加子子命令、文档没写；文档写了、代码没有（两个方向）
8. **顶层与子级别名同名**（`list`/`ls` 两层都有）时两层互不填补、互不误报
9. `_cmd_key` 整个函数没了 / 分派变量找不到（解析失败必须红）

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

#: `密钥` 子子命令基准：每组是 `(中文名, 英文写法...)`，刻意让 `列表` 组带上
#: `list`/`ls`——**与顶层 `列表` 命令的英文别名同名**，两层隔离全靠这组素材证明。
_基准密钥子命令 = (
    ('生成', 'generate', 'gen'),
    ('列表', 'list', 'ls'),
    ('导出', 'export'),
)


def _密钥中文(组集):
    return [组[0] for 组 in 组集]


def _密钥英文(组集):
    return [w for 组 in 组集 for w in 组[1:]]


def _密钥bullet(组集):
    """`密钥` 子子命令在命令表小节里的 bullet 声明。"""
    return ['- `jk 包 密钥 %s <别名>` —— 略。' % 名 for 名 in _密钥中文(组集)]


def _密钥英文段(英文写法):
    """子级专属段落头——刻意与顶层「英文别名：」区分开。"""
    if not 英文写法:
        return []
    return ['', '密钥子命令英文别名：'
            + ' / '.join('`%s`' % w for w in 英文写法) + '。']


def _cmd_key源码(组集):
    """造一份 `_cmd_key`，形态照抄真 cli.py：`sub = args[0]` + if/elif 链。

    刻意混进 `len(rest)` 这类与子命令名无关的比较，验证收集器不会把它们
    当成子命令字面量。
    """
    行 = ['def _cmd_key(args):',
          '    if not args:',
          '        return 1',
          '    sub = args[0]',
          '    rest = args[1:]']
    for i, 组 in enumerate(组集):
        元组 = ', '.join('%r' % w for w in 组)
        行.append('    %s sub in (%s,):' % ('if' if i == 0 else 'elif', 元组))
        行.append('        if len(rest) < 2:')
        行.append('            return 1')
    行.append('    return 0')
    return '\n'.join(行) + '\n'


def _造cli(tmp_path, 别名对, 密钥子命令=_基准密钥子命令):
    """造一份含 `_ALIASES` 字面量（+ `_cmd_key` 分派）的最小 cli.py。

    `别名对` 是 `[(键, 规范名), ...]`——刻意保留真 cli.py 的「多键映射同一
    规范名」形态（`'移除': 'remove', '删除': 'remove', 'rm': 'remove'`）。

    `密钥子命令` 非空时自动补上顶层 `密钥`/`key` 别名：子子命令 bullet
    (`jk 包 密钥 X`) 在文档侧同时会给顶层贡献 `密钥`，两侧得对齐才算自洽。
    传 `()` 则连 `_cmd_key` 都不生成，用于单测顶层解析。
    """
    对 = list(别名对)
    if 密钥子命令:
        对 += [('密钥', 'key'), ('key', 'key')]
    项 = ''.join('%r: %r, ' % (k, v) for k, v in 对)
    src = ('# -*- coding: utf-8 -*-\n'
           '_ALIASES = {' + 项 + '}\n')
    if 密钥子命令:
        src += '\n\n' + _cmd_key源码(密钥子命令)
    p = tmp_path / 'cli.py'
    p.write_text(src, encoding='utf-8')
    return str(p)


def _造doc(tmp_path, 中文主名, 英文别名=(), 中文别名=(),
          密钥子命令=_基准密钥子命令, 密钥英文=None):
    """造一份只含「## 命令表」小节的最小包管理文档。

    `中文主名` 渲染成真文档同款的 ``- `jk 包 X <参数>` —— 略`` bullet；
    刻意给部分命令带上尾随参数，验证 bullet 抽取不依赖紧跟的闭合反引号。

    `密钥英文` 显式传入时不跟 `密钥子命令` 联动——反例要靠这个错位来造。
    """
    行 = ['# 包管理（M8）', '', '## 快速上手', '', '略。', '',
          '## 命令表', '']
    for i, name in enumerate(中文主名):
        # 交替带/不带尾随参数，覆盖两种 bullet 形态
        尾 = ' <名称>' if i % 2 == 0 else ''
        行.append('- `jk 包 %s%s` —— 略。' % (name, 尾))
    行 += _密钥bullet(密钥子命令)
    if 中文别名:
        行 += ['', '中文别名：' + '、'.join('`%s`' % a for a in 中文别名) + '。']
    顶层英文 = list(英文别名) + (['key'] if 密钥子命令 else [])
    if 顶层英文:
        行 += ['', '英文别名：' + ' / '.join('`%s`' % a for a in 顶层英文) + '。']
    行 += _密钥英文段(_密钥英文(密钥子命令) if 密钥英文 is None else 密钥英文)
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
    行 = ['# x', '', '## 命令表', '',
          '- `jk 包 装 [--含开发] [--其它]` —— 略。']
    行 += _密钥bullet(_基准密钥子命令)
    行 += ['', '英文别名：`install` / `key`。']
    行 += _密钥英文段(_密钥英文(_基准密钥子命令))
    行 += ['', '## 下一节', '', '略。', '']
    f = tmp_path / '带参.md'
    f.write_text('\n'.join(行), encoding='utf-8')
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
    行 = ['# x', '', '## 命令表', '', '- `jk 包 帮助` —— 略。']
    行 += _密钥bullet(_基准密钥子命令)
    行 += ['', '英文别名：`help` / `key`。']
    行 += _密钥英文段(_密钥英文(_基准密钥子命令))
    行 += ['', '## 下一节', '', '略。', '']
    f = tmp_path / 'sw.md'
    f.write_text('\n'.join(行), encoding='utf-8')
    assert _跑(str(f), cli) == 0



def test_文件不存在时返回1(tmp_path, capsys):
    assert G17.main(['--doc', str(tmp_path / '没有.md'),
                     '--cli', str(tmp_path / '没有.py')]) == 1
    assert '不存在' in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 第二层：`密钥` 子子命令（W103）
# ---------------------------------------------------------------------------

def test_反例_代码加子子命令文档没写(tmp_path, capsys):
    """`_cmd_key` 里多一个 `密钥 轮换`，命令表没跟——正是 W101 那类深一层的漂移。"""
    cli = _造cli(tmp_path, _基准别名对,
                 密钥子命令=_基准密钥子命令 + (('轮换', 'rotate'),))
    doc = _造doc(tmp_path, _基准中文主名,
                 英文别名=_基准英文别名, 中文别名=_基准中文别名)  # 仍是旧的密钥集
    assert _跑(doc, cli) == 1
    err = capsys.readouterr().err
    assert '密钥子命令(中文)' in err
    assert '轮换' in err
    assert '密钥子命令(英文)' in err
    assert 'rotate' in err
    assert '代码有但文档没写' in err


def test_反例_文档写子子命令代码没有(tmp_path, capsys):
    """反方向：命令表写了 `密钥 冻结`，`_cmd_key` 没这个分支。"""
    cli = _造cli(tmp_path, _基准别名对)  # 只有基准三组
    doc = _造doc(tmp_path, _基准中文主名,
                 英文别名=_基准英文别名, 中文别名=_基准中文别名,
                 密钥子命令=_基准密钥子命令 + (('冻结', 'freeze'),))
    assert _跑(doc, cli) == 1
    err = capsys.readouterr().err
    assert '密钥子命令(中文)' in err
    assert '冻结' in err
    assert 'freeze' in err
    assert '文档写了但代码没有' in err


def test_顶层与子级别名同名不互相污染(tmp_path):
    """最容易写错的一条：顶层 `列表`/`list`/`ls` 与 `密钥 列表`/`list`/`ls`
    两层都有同名别名。两层各锁各的集合，同名项各归各层、互不填补——
    只要各自双向对齐，门禁就该绿，不能因为「顶层有 list，子级没显式列」误报。"""
    # 顶层 _ALIASES 有 列表/list/ls；密钥子命令基准也含 列表/list/ls
    cli = _造cli(tmp_path, _基准别名对 + [
        ('列表', 'list'), ('list', 'list'), ('ls', 'list')])
    doc = _造doc(tmp_path, _基准中文主名 + ('列表',),
                 英文别名=_基准英文别名 + ('list', 'ls'),
                 中文别名=_基准中文别名)
    assert _跑(doc, cli) == 0


def test_顶层缺同名别名时子级不替它补(tmp_path, capsys):
    """隔离的另一面：子级有 `list`/`ls` 不代表顶层的 `list`/`ls` 就算数。
    顶层 `_ALIASES` 加了 `列表`/`list`/`ls` 但文档顶层「英文别名：」漏了 `ls`，
    必须在 [英文别名] 维度报红——不能被子级的 `ls` 顶替过去。"""
    cli = _造cli(tmp_path, _基准别名对 + [
        ('列表', 'list'), ('list', 'list'), ('ls', 'list')])
    doc = _造doc(tmp_path, _基准中文主名 + ('列表',),
                 英文别名=_基准英文别名 + ('list',),   # 顶层漏了 ls
                 中文别名=_基准中文别名)
    assert _跑(doc, cli) == 1
    err = capsys.readouterr().err
    assert '[英文别名]' in err   # 顶层维度报，而非子级维度
    assert 'ls' in err


def test_反例_子级英文别名遗漏(tmp_path, capsys):
    """`密钥 生成` 的 `gen` 简写代码有、文档「密钥子命令英文别名：」没列。"""
    cli = _造cli(tmp_path, _基准别名对)
    # 文档侧密钥英文段漏掉 gen
    doc = _造doc(tmp_path, _基准中文主名,
                 英文别名=_基准英文别名, 中文别名=_基准中文别名,
                 密钥英文=['generate', 'list', 'ls', 'export'])  # 少 gen
    assert _跑(doc, cli) == 1
    err = capsys.readouterr().err
    assert '密钥子命令(英文)' in err
    assert 'gen' in err


def test_反例_cmd_key函数缺失时报错(tmp_path, capsys):
    """`_cmd_key` 整个没了 → 子命令分派结构变了，解析失败必须红，不静默跳过。"""
    p = tmp_path / 'cli.py'
    p.write_text('# -*- coding: utf-8 -*-\n'
                 '_ALIASES = {"密钥": "key", "key": "key"}\n', encoding='utf-8')
    doc = _造doc(tmp_path, (), 密钥子命令=_基准密钥子命令)
    assert _跑(doc, str(p)) == 1
    assert '解析失败' in capsys.readouterr().err


def test_反例_cmd_key分派变量找不到时报错(tmp_path, capsys):
    """`_cmd_key` 不再从 `args[0]` 取子命令 → 找不到分派变量，报错而非收集出错集。"""
    p = tmp_path / 'cli.py'
    p.write_text('# -*- coding: utf-8 -*-\n'
                 '_ALIASES = {"密钥": "key", "key": "key"}\n\n'
                 'def _cmd_key(args):\n'
                 '    if args == ("生成",):\n'   # 没有 sub = args[0] 这一步
                 '        return 0\n'
                 '    return 1\n', encoding='utf-8')
    doc = _造doc(tmp_path, (), 密钥子命令=_基准密钥子命令)
    assert _跑(doc, str(p)) == 1
    assert '解析失败' in capsys.readouterr().err


def test_子级解析不吃无关比较(tmp_path):
    """`_cmd_key` 里的 `len(rest) < 2`、`剩余 == 0` 不能被当成子命令名收集。
    `_cmd_key源码` 恰好会写出 `len(rest) < 2`——若收集器把它吃进去，中文/英文
    集就会多出 `rest`/数字之类的脏值而与文档对不上、误红。这里断言干净通过。"""
    cli = _造cli(tmp_path, _基准别名对)
    doc = _造doc(tmp_path, _基准中文主名,
                 英文别名=_基准英文别名, 中文别名=_基准中文别名)
    assert _跑(doc, cli) == 0
    # 直接验证解析结果不含无关 token
    中, 英 = G17._code密钥子命令(G17._读文件(cli))
    assert 'rest' not in 英 and 'rest' not in 中
    assert 中 == {'生成', '列表', '导出'}


def test_段落头按行首锚定_两层不串味():
    """`密钥子命令英文别名：` 含有 `英文别名：` 子串。裸 `find` 会让顶层段落头
    命中子级段落（W101 被这类混淆坑过），所以段落头必须按行首锚定匹配——
    这里刻意把子级段落**放在顶层段落之前**，两层仍各取各的。"""
    小节 = ('## 命令表\n\n- `jk 包 密钥 生成 <别名>` —— 略。\n\n'
           '密钥子命令英文别名：`generate` / `gen`。\n\n'
           '英文别名：`key`。\n')
    顶层 = G17._取别名段(小节, G17._ALIAS_LINE_HEAD_EN)
    子级 = G17._取别名段(小节, G17._ALIAS_LINE_HEAD_KEY_EN)
    assert 顶层.startswith('英文别名：')
    assert 'generate' not in 顶层
    assert 子级.startswith('密钥子命令英文别名：')
    assert 'key' not in 子级.replace('密钥子命令英文别名：', '')


def test_sub等号写法也能收集(tmp_path):
    """`_cmd_key` 的分支既可能写成 `sub in (...)` 也可能写成 `sub == '...'`，
    两种都得认——否则改个写法门禁就悄悄少锁一个子命令。"""
    p = tmp_path / 'cli.py'
    p.write_text('# -*- coding: utf-8 -*-\n'
                 '_ALIASES = {"密钥": "key", "key": "key"}\n\n'
                 'def _cmd_key(args):\n'
                 '    sub = args[0]\n'
                 '    if sub == "生成":\n'
                 '        return 0\n'
                 '    elif sub == "export":\n'
                 '        return 0\n'
                 '    return 1\n', encoding='utf-8')
    中, 英 = G17._code密钥子命令(G17._读文件(p))
    assert 中 == {'生成'}
    assert 英 == {'export'}



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
    cli = _造cli(tmp_path, [(键, 'x')], 密钥子命令=())
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
