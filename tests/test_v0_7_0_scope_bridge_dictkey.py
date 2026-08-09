# -*- coding: utf-8 -*-
"""v0.7.0 · ADR-22/23 三项语言能力补齐的回归测试。

ADR-22 —— 类的构造器与方法体采用**词法作用域**：父环境是类定义处，
          而非调用者。跨模块使用对象时，方法能看到定义它的模块里 `导入` 的名字。
ADR-23a —— `蟒:` 桥在标准 `importlib` 找不到模块时，回退到发起导入的
          `.jk` 文件**同目录**的 `<name>.py`（隔离加载，不改 sys.path）。
ADR-23b —— 字典字面量的键必须可哈希，否则给中文 TYPE 诊断而非 Python 原文。

命名注意：极快标识符以百家姓开头；不能夹带内建动词字（加/乘/等/长度/大/小/只/皆/归/…），
否则会被词法器切断。这里的测试文件在极快源码里都规避了这些字。
"""

import io
import contextlib
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from jikuai.evaluator import JiKuaiError
from jikuai.main import run_source

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
_SRC = os.path.join(_ROOT, 'src')


def _run(src):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_source(src)
    return buf.getvalue()


def _run_file(path):
    """子进程执行 .jk 文件，返回 (returncode, stdout, stderr)。"""
    env = os.environ.copy()
    env['PYTHONPATH'] = _SRC
    env['PYTHONIOENCODING'] = 'utf-8'
    proc = subprocess.run(
        [sys.executable, '-X', 'utf8', '-m', 'jikuai', str(path)],
        capture_output=True, env=env, cwd=_ROOT,
    )
    return (proc.returncode,
            proc.stdout.decode('utf-8', errors='replace').replace('\r\n', '\n'),
            proc.stderr.decode('utf-8', errors='replace'))


# ─────────────────────── ADR-22：方法词法作用域 ───────────────────────

def test_adr22_method_sees_defining_module_import(tmp_path):
    """方法体能用**定义它的模块**里 `导入` 进来的名字。"""
    (tmp_path / '工具.jk').write_text(
        '函数 翻倍 接收 赵值:\n'
        '  返回 乘 赵值 2。\n'
        '。\n'
        '导出 翻倍。\n',
        encoding='utf-8')
    (tmp_path / '盒子.jk').write_text(
        '从 工具 导入 翻倍。\n'
        '类 盒子:\n'
        '  构造 接收 赵初值:\n'
        '    自身.值=赵初值。\n'
        '  。\n'
        '  方法 倍值:\n'
        '    返回 翻倍(自身.值)。\n'
        '  。\n'
        '。\n'
        '函数 造盒子 接收 赵初值:\n'
        '  返回 新建 盒子(赵初值)。\n'
        '。\n'
        '导出 盒子 造盒子。\n',
        encoding='utf-8')
    main = tmp_path / 'main.jk'
    main.write_text(
        '从 盒子 导入 造盒子。\n'
        '定义赵盒=造盒子(21)。\n'
        '打印 赵盒.倍值。\n',
        encoding='utf-8')

    rc, out, err = _run_file(main)
    assert rc == 0, err
    assert out.strip() == '42'


def test_adr22_ctor_sees_defining_module_import(tmp_path):
    """构造器体同样以类定义处为父环境。"""
    (tmp_path / '常量.jk').write_text(
        '定义赵基线=100。\n'
        '导出 赵基线。\n',
        encoding='utf-8')
    (tmp_path / '计数.jk').write_text(
        '导出 计数 造计数。\n'
        '从 常量 导入 赵基线。\n'
        '类 计数:\n'
        '  构造 接收 赵增:\n'
        '    自身.总=加 赵基线 赵增。\n'
        '  。\n'
        '。\n'
        '函数 造计数 接收 赵增:\n'
        '  返回 新建 计数(赵增)。\n'
        '。\n',
        encoding='utf-8')
    main = tmp_path / 'main.jk'
    main.write_text(
        '从 计数 导入 造计数。\n'
        '定义赵c=造计数(23)。\n'
        '打印 赵c.总。\n',
        encoding='utf-8')

    rc, out, err = _run_file(main)
    assert rc == 0, err
    assert out.strip() == '123'


def test_adr22_inherited_method_uses_parent_module_scope(tmp_path):
    """继承来的方法用**父类所在模块**的作用域，不是子类的。"""
    (tmp_path / '甲.jk').write_text(
        '导出 甲。\n'
        '函数 标记:\n'
        '  返回 "来自甲"。\n'
        '。\n'
        '类 甲:\n'
        '  方法 报告:\n'
        '    返回 标记()。\n'
        '  。\n'
        '。\n',
        encoding='utf-8')
    (tmp_path / '乙.jk').write_text(
        '导出 乙 造乙。\n'
        '从 甲 导入 甲。\n'
        '类 乙 继承 甲:\n'
        '。\n'
        '函数 造乙:\n'
        '  返回 新建 乙()。\n'
        '。\n',
        encoding='utf-8')
    main = tmp_path / 'main.jk'
    main.write_text(
        '从 乙 导入 造乙。\n'
        '定义赵s=造乙()。\n'
        '打印 赵s.报告。\n',
        encoding='utf-8')

    rc, out, err = _run_file(main)
    assert rc == 0, err
    assert out.strip() == '来自甲'


def test_adr22_method_does_not_see_caller_locals(tmp_path):
    """反向约束：方法**不再**看到调用者作用域的名字（词法作用域的应有之义）。"""
    (tmp_path / '窥.jk').write_text(
        '导出 窥 造窥。\n'
        '类 窥:\n'
        '  方法 取:\n'
        '    返回 赵仅主程序里。\n'
        '  。\n'
        '。\n'
        '函数 造窥:\n'
        '  返回 新建 窥()。\n'
        '。\n',
        encoding='utf-8')
    main = tmp_path / 'main.jk'
    main.write_text(
        '从 窥 导入 造窥。\n'
        '定义赵仅主程序里=7。\n'
        '定义赵w=造窥()。\n'
        '打印 赵w.取。\n',
        encoding='utf-8')

    rc, out, err = _run_file(main)
    assert rc != 0
    assert '赵仅主程序里' in err


def test_adr22_same_file_class_still_sees_toplevel():
    """同文件内定义的类，方法照旧能看到该文件的顶层名字（不回归）。"""
    out = _run(
        '定义赵倍=3。\n'
        '类 乘器:\n'
        '  构造 接收 赵初值:\n'
        '    自身.值=赵初值。\n'
        '  。\n'
        '  方法 算:\n'
        '    返回 乘 自身.值 赵倍。\n'
        '  。\n'
        '。\n'
        '定义赵m=新建 乘器(5)。\n'
        '打印 赵m.算。\n')
    assert out.strip() == '15'


# ─────────────────────── ADR-23a：蟒: 桥同目录兜底 ───────────────────────

def test_adr23_python_bridge_finds_sidecar_module(tmp_path):
    """`导入 蟒:助手。` 应能找到与 .jk 同目录的 助手.py。"""
    (tmp_path / '助手.py').write_text(
        '# -*- coding: utf-8 -*-\n'
        'def calc(a, b):\n'
        '    return a + b\n',
        encoding='utf-8')
    main = tmp_path / 'main.jk'
    main.write_text(
        '导入 蟒:助手。\n'
        '打印 助手.calc(20, 22)。\n',
        encoding='utf-8')

    rc, out, err = _run_file(main)
    assert rc == 0, err
    assert out.strip() == '42'


def test_adr23_sidecar_resolved_relative_to_importing_module(tmp_path):
    """同目录兜底以**发起导入的那个 .jk** 为基准，而非入口脚本。"""
    sub = tmp_path / '子'
    sub.mkdir()
    (sub / '算子.py').write_text(
        'def sq(x):\n    return x * x\n', encoding='utf-8')
    (sub / '桥.jk').write_text(
        '导出 用算子。\n'
        '导入 蟒:算子。\n'
        '函数 用算子 接收 赵x:\n'
        '  返回 算子.sq(赵x)。\n'
        '。\n',
        encoding='utf-8')
    main = tmp_path / 'main.jk'
    main.write_text('导入 蟒:算子。\n', encoding='utf-8')

    # 入口脚本自身目录没有 算子.py → 应当报「找不到 Python 模块」
    rc, out, err = _run_file(main)
    assert rc != 0
    assert '找不到 Python 模块' in err


def test_adr23_missing_module_still_gives_chinese_diagnostic(tmp_path):
    """同目录也没有时，仍是中文诊断，不透出 Python ImportError 原文。"""
    main = tmp_path / 'main.jk'
    main.write_text('导入 蟒:根本不存在的模块名xyz。\n', encoding='utf-8')

    rc, out, err = _run_file(main)
    assert rc != 0
    assert '找不到 Python 模块' in err
    assert 'ModuleNotFoundError' not in err


def test_adr23_sidecar_does_not_pollute_sys_path(tmp_path):
    """隔离加载：不得把脚本目录塞进 sys.path。"""
    (tmp_path / '孤岛.py').write_text('val = 1\n', encoding='utf-8')
    main = tmp_path / 'main.jk'
    main.write_text('导入 蟒:孤岛。\n打印 孤岛.val。\n', encoding='utf-8')

    before = list(sys.path)
    from jikuai.pybridge import py_import
    mod = py_import('孤岛', current_file=str(main))
    assert mod.member('val') == 1
    assert sys.path == before


def test_adr23_dotted_name_never_hits_sidecar(tmp_path):
    """点分名不走同目录兜底，避免用 `蟒:a.b` 拼出目录穿越。"""
    (tmp_path / 'zzpkg.zzmod.py').write_text('x = 1\n', encoding='utf-8')
    main = tmp_path / 'main.jk'
    from jikuai.pybridge import py_import
    # 同目录确实放了名为 `zzpkg.zzmod.py` 的文件，但点分名必须被跳过 → 导入失败
    with pytest.raises(Exception, match='找不到 Python 模块|非法的 Python 模块名'):
        py_import('zzpkg.zzmod', current_file=str(main))


def test_adr23_denylist_still_applies():
    """DENY_LIST 不因兜底加载而失效（安全边界记录）。"""
    from jikuai.pybridge import _is_denied
    assert _is_denied('os', 'system') is True
    assert _is_denied('builtins', 'eval') is True


# ─────────────────────── ADR-23b：字典键可哈希 ───────────────────────

def test_adr23_dict_key_unhashable_gives_chinese_type_error():
    """列表作键 → 中文 TYPE 诊断，而非 Python `unhashable type` 原文。"""
    with pytest.raises(JiKuaiError) as ei:
        _run('定义赵d={(列 1 2): "值"}。\n')
    msg = str(ei.value)
    assert '字典的键' in msg
    assert 'unhashable' not in msg


def test_adr23_dict_key_unhashable_error_category_is_type():
    with pytest.raises(JiKuaiError) as ei:
        _run('定义赵d={(列 1): "值"}。\n')
    assert ei.value.info is not None
    # 按 name 比较：全量跑测时 jikuai.errors 可能被以不同模块名重复加载，
    # 枚举成员的对象身份会不一致，但 name 稳定。
    assert ei.value.info.category.name == 'TYPE'


def test_adr23_dict_key_unhashable_reports_key_position():
    """诊断应带上键所在行号，便于定位。"""
    with pytest.raises(JiKuaiError) as ei:
        _run('打印 "占位"。\n定义赵d={(列 1 2): "值"}。\n')
    assert ei.value.info.line == 2


def test_adr23_hashable_key_kinds_all_accepted():
    """字符串 / 整数 / 小数 / 布尔 / 空 都是合法键。"""
    out = _run('定义赵d={"s": 1, 2: "二", 3.5: "三五", 真: "真", 空: "空"}。\n'
               '打印 长度 赵d。\n'
               '打印 赵d[2]。\n'
               '打印 赵d[3.5]。\n')
    assert out == '5\n二\n三五\n'


def test_adr23_dict_value_may_be_unhashable():
    """只约束键，值可以是列表/字典。"""
    out = _run('定义赵d={"表": 列 1 2 3}。\n打印 (转字符串 赵d.表)。\n')
    assert out.strip() == '[1, 2, 3]'
