# -*- coding: utf-8 -*-
"""`jikuai.main` CLI 入口与文件执行路径的覆盖。

**为什么单开这个文件**：`main.py` 是 `docs/覆盖率下限.json` 点名保护的文件
之一（路线图 §五 成功度量第 1 条：main.py ≥70%），但它此前几乎没有直接测试
——本机实测只有 71.7%，只比 70.0 的下限高 1.7 个点。这点余量太薄：一旦 CI
（ubuntu/3.12，与本机 Windows/3.14 不同）测出来低一点点，门禁就会红，而那并
不代表真出了问题。

**处理方式遵循挂账里立的原则**：余量薄要「补测试，不是调阈值」。`main.py` 全
是平台中立的分派逻辑（无 `sys.platform`/`os.name` 分支），所以这里补的直接
调用型测试在任何平台都能把它的覆盖抬上去，给下限留出真实余量。

这些测试直接调用 `main()` / `run_file()` / `run_source()`，不走子进程——快，
且覆盖能记进主进程。"""

import os
import sys
from importlib import import_module

import pytest

from jikuai.main import main, run_file, run_source, VERSION
from jikuai._version import __version__

# 注意不能写 `from jikuai import main as jkmain`：包的 `__init__` 导出了同名的
# **函数** `main`，会盖住 `jikuai.main` 这个**子模块**，拿到的是函数而非模块。
# 要 monkeypatch 模块属性，必须显式按模块名导入。
jkmain = import_module('jikuai.main')



# ---------- main() 参数分派 ----------

def test_版本号别名与真源一致():
    """VERSION 是 `from jikuai.main import VERSION` 旧引用的过渡别名，
    必须与 `_version.__version__` 同值，否则 -v 会打错版本。"""
    assert VERSION == __version__


def test_无参数进入repl(monkeypatch):
    """不带参数应进 REPL。REPL 是交互式的，这里只验证它被调用，不真的跑。"""
    调用 = {}
    monkeypatch.setattr(jkmain, 'repl', lambda: 调用.setdefault('进', True))
    monkeypatch.setattr(sys, 'argv', ['jk'])
    main()
    assert 调用.get('进') is True


@pytest.mark.parametrize('旗标', ['-h', '--help', '帮助'])
def test_帮助三种写法都打印用法(monkeypatch, capsys, 旗标):
    monkeypatch.setattr(sys, 'argv', ['jk', 旗标])
    main()
    out = capsys.readouterr().out
    assert '用法' in out
    assert 'REPL' in out


@pytest.mark.parametrize('旗标', ['-v', '--version', '版本'])
def test_版本三种写法都打印版本号(monkeypatch, capsys, 旗标):
    monkeypatch.setattr(sys, 'argv', ['jk', 旗标])
    main()
    out = capsys.readouterr().out
    assert __version__ in out


@pytest.mark.parametrize('词', ['包', 'pkg', '包管理'])
def test_包子命令分派并透传退出码(monkeypatch, 词):
    """`jk 包 ...` 应把余下参数交给 pkg.cli.run，并用其返回值作为退出码。"""
    收到 = {}

    def 假run(argv):
        收到['argv'] = argv
        return 7

    monkeypatch.setattr('jikuai.pkg.cli.run', 假run)
    monkeypatch.setattr(sys, 'argv', ['jk', 词, '列表', '--全部'])
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 7
    assert 收到['argv'] == ['列表', '--全部']


@pytest.mark.parametrize('词', ['块', 'blocks', 'block'])
def test_块子命令分派并透传退出码(monkeypatch, 词):
    def 假run(argv):
        return 0

    monkeypatch.setattr('jikuai.pkg.blocks_cli.run', 假run)
    monkeypatch.setattr(sys, 'argv', ['jk', 词, '列表'])
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 0


def test_其它参数当作文件执行(monkeypatch, tmp_path):
    """既不是旗标也不是子命令 → 当文件路径交给 run_file。"""
    调用 = {}
    monkeypatch.setattr(jkmain, 'run_file',
                        lambda p: 调用.setdefault('路径', p))
    monkeypatch.setattr(sys, 'argv', ['jk', '某程序.jk'])
    main()
    assert 调用['路径'] == '某程序.jk'


# ---------- run_file() 正常与错误路径 ----------

def test_执行文件正常(tmp_path, capsys):
    f = tmp_path / '你好.jk'
    f.write_text('打印 拼接 "极" "快"。\n', encoding='utf-8')
    run_file(str(f))
    assert '极快' in capsys.readouterr().out


def test_文件不存在报中文错并退出1(capsys):
    with pytest.raises(SystemExit) as e:
        run_file('这个文件不存在.jk')
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert '找不到文件' in err
    # ADR-09：不得泄漏 Python 异常类名
    assert 'FileNotFoundError' not in err


def test_文件非utf8报中文错并退出1(tmp_path, capsys):
    f = tmp_path / '乱码.jk'
    f.write_bytes(b'\xff\xfe\x00 not utf-8')
    with pytest.raises(SystemExit) as e:
        run_file(str(f))
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert 'UTF-8' in err
    assert 'UnicodeDecodeError' not in err


def test_运行时错误退出1且不泄漏python细节(tmp_path, capsys):
    """除零是运行时 JiKuaiError，应格式化成中文诊断后退出 1。"""
    f = tmp_path / '除零.jk'
    f.write_text('除 10 0。\n', encoding='utf-8')
    with pytest.raises(SystemExit) as e:
        run_file(str(f))
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert err.strip()
    assert 'Traceback' not in err
    assert 'ZeroDivisionError' not in err


def test_语法错误退出1(tmp_path, capsys):
    f = tmp_path / '残句.jk'
    f.write_text('定义\n', encoding='utf-8')
    with pytest.raises(SystemExit) as e:
        run_file(str(f))
    assert e.value.code == 1
    assert capsys.readouterr().err.strip()


# ---------- run_source() 的诊断分派 ----------

def test_合法源不产生诊断噪音(monkeypatch, capsys):
    """诊断默认开启时，合法源不应往 stderr 写东西（没有非错误级诊断可报）。"""
    monkeypatch.delenv('JIKUAI_DIAGNOSTICS', raising=False)
    run_source('打印 加 1 2。\n')
    captured = capsys.readouterr()
    assert captured.out.strip() == '3'
    assert captured.err == ''


def test_诊断开关off时不打印(monkeypatch, capsys):
    """JIKUAI_DIAGNOSTICS=off → make_default_sink() 是 NullSink，直接返回不打印。"""
    monkeypatch.setenv('JIKUAI_DIAGNOSTICS', 'off')
    run_source('打印 1。\n')
    assert capsys.readouterr().err == ''

