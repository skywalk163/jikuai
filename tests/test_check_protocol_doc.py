# -*- coding: utf-8 -*-
"""v0.18.0 · W55 · G16「协议文档同步」门禁测试（`scripts/check_protocol_doc.py`）。

正例走真仓库文件（G16 现状必须全绿）；反例用 tmp_path 造合成的
doc / server.py 对，逐一证明门禁**真的抓得到**四类漂移：

1. 文档漏写代码里有的端点
2. 文档写了代码里没有的端点
3. 同一端点方法写错（doc 记成 POST、代码是 PUT）
4. 文档整节缺失（解析失败而非静默通过）

反例测试是这个门禁的价值所在——只测正例的门禁跟没有门禁没区别。
"""

import os
import sys

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
_SCRIPTS = os.path.join(_REPO, 'scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import check_protocol_doc as G16  # noqa: E402


# ---------------------------------------------------------------------------
# 合成素材
# ---------------------------------------------------------------------------

def _造server(tmp_path, post=('/api/选',), get=('/api/blocks',),
              put=(), delete=()):
    """造一份只含路由清单的最小 server.py。

    刻意保留 `_方案id前缀 + '<id>'` 的拼接形态——G16 的 `_求值` 要能解它，
    这是真 server.py 的写法。
    """
    def _字面(seq):
        return '(' + ''.join('%r, ' % s for s in seq) + ')'

    src = (
        "# -*- coding: utf-8 -*-\n"
        "_方案id前缀 = '/api/方案/'\n"
        "_方案id端点 = _方案id前缀 + '<id>'\n"
        "_POST路由 = {" + ''.join("%r: None, " % s for s in post) + "}\n"
        "_GET路由 = " + _字面(get) + "\n"
        "_PUT路由 = " + _字面(put) + "\n"
        "_DELETE路由 = " + _字面(delete) + "\n"
    )
    p = tmp_path / 'server.py'
    p.write_text(src, encoding='utf-8')
    return str(p)


def _造doc(tmp_path, 条目):
    """造一份只含「三、通道 × schema 对应关系」小节的最小协议文档。

    `条目` 是 `[(方法, 路径), ...]`，渲染成真文档同款的 `- **Web `M /p`** → x` 行。
    """
    行 = ['# 三通道统一 JSON 协议', '', '## 二、两个信封', '', '略。', '',
          '## 三、通道 × schema 对应关系', '']
    for m, p in 条目:
        行.append('- **Web `%s %s`** → 略' % (m, p))
    行 += ['', '## 四、契约变更史', '', '略。', '']
    f = tmp_path / '协议.md'
    f.write_text('\n'.join(行), encoding='utf-8')
    return str(f)


def _跑(doc, server):
    return G16.main(['--quiet', '--doc', doc, '--server', server])


# ---------------------------------------------------------------------------
# 正例
# ---------------------------------------------------------------------------

def test_真仓库现状全绿():
    """G16 上线时实测必须过——W47 已手工同步过一次。"""
    assert G16.main(['--quiet']) == 0


def test_合成一致时通过(tmp_path):
    server = _造server(tmp_path,
                       post=('/api/选', '/api/方案/存'),
                       get=('/api/blocks', '/api/方案/<id>'),
                       put=('/api/方案/<id>',),
                       delete=('/api/方案/<id>',))
    doc = _造doc(tmp_path, [
        ('POST', '/api/选'), ('POST', '/api/方案/存'),
        ('GET', '/api/blocks'), ('GET', '/api/方案/<id>'),
        ('PUT', '/api/方案/<id>'), ('DELETE', '/api/方案/<id>'),
    ])
    assert _跑(doc, server) == 0


# ---------------------------------------------------------------------------
# 反例：门禁必须抓到
# ---------------------------------------------------------------------------

def test_反例_文档漏写代码里的端点(tmp_path, capsys):
    """W31/W46 的真实病症：代码加了端点，文档没跟。"""
    server = _造server(tmp_path, post=('/api/选', '/api/跑'))
    doc = _造doc(tmp_path, [('POST', '/api/选')])       # 漏了 /api/跑
    assert _跑(doc, server) == 1
    err = capsys.readouterr().err
    assert '代码有但文档没写' in err
    assert '/api/跑' in err


def test_反例_文档写了代码没有的端点(tmp_path, capsys):
    """反方向也要抓：文档许诺了一个不存在的端点，调用方会照着写然后 404。"""
    server = _造server(tmp_path, post=('/api/选',))
    doc = _造doc(tmp_path, [('POST', '/api/选'), ('POST', '/api/不存在')])
    assert _跑(doc, server) == 1
    err = capsys.readouterr().err
    assert '文档写了但代码没有' in err
    assert '/api/不存在' in err


def test_反例_方法写错(tmp_path, capsys):
    """同一路径但方法不符——按方法分桶比对才抓得到。"""
    server = _造server(tmp_path, post=(), put=('/api/方案/<id>',))
    doc = _造doc(tmp_path, [('POST', '/api/方案/<id>')])
    assert _跑(doc, server) == 1
    err = capsys.readouterr().err
    assert '[POST]' in err
    assert '[PUT]' in err


def test_反例_id占位不同写法视为同一端点(tmp_path):
    """`<方案id>` / `<id>` 都归一——占位命名不该让门禁误报。"""
    server = _造server(tmp_path, post=(), get=('/api/方案/<id>',))
    doc = _造doc(tmp_path, [('GET', '/api/方案/<方案id>')])
    assert _跑(doc, server) == 0


def test_反例_文档小节缺失时报错而非静默通过(tmp_path, capsys):
    server = _造server(tmp_path, post=('/api/选',))
    f = tmp_path / '无小节.md'
    f.write_text('# 协议\n\n## 一、核心 schema\n\n略。\n', encoding='utf-8')
    assert _跑(str(f), server) == 1
    assert '解析失败' in capsys.readouterr().err


def test_反例_server缺路由清单时报错(tmp_path, capsys):
    p = tmp_path / 'server.py'
    p.write_text("_POST路由 = {'/api/选': None}\n", encoding='utf-8')
    doc = _造doc(tmp_path, [('POST', '/api/选')])
    assert _跑(doc, str(p)) == 1
    assert '路由清单' in capsys.readouterr().err


def test_文件不存在时返回1(tmp_path, capsys):
    assert G16.main(['--doc', str(tmp_path / '没有.md'),
                     '--server', str(tmp_path / '没有.py')]) == 1
    assert '不存在' in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 与 G10 主入口的串联
# ---------------------------------------------------------------------------

def test_G16已串进check_stdlib_contract():
    """G16 必须被 `scripts/check_stdlib_contract.py` 调用，否则 CI 跑不到它。"""
    path = os.path.join(_SCRIPTS, 'check_stdlib_contract.py')
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    assert 'check_protocol_doc' in text
    assert 'G16' in text


@pytest.mark.parametrize('路径, 期望', [
    ('/api/方案/<id>', '/api/方案/<id>'),
    ('/api/方案/<方案id>', '/api/方案/<id>'),
    ('/api/选。', '/api/选'),
    ('/api/跑、', '/api/跑'),
])
def test_归一路径(路径, 期望):
    assert G16._归一路径(路径) == 期望
