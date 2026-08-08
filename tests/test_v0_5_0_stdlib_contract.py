# -*- coding: utf-8 -*-
"""v0.5.0 · M4 · T-M4-S06：标准库契约测试（AC-M4-03-01/02/03、G10）。"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from jikuai import stdlib_contract
from jikuai.diagnostics import codes
from jikuai.evaluator import Evaluator, JiKuaiError
from jikuai.main import run_source

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPTS_DIR = os.path.join(REPO_ROOT, 'scripts')
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import check_stdlib_contract  # noqa: E402


# ===================== stdlib_contract 单元测试（T-M4-S01） =====================

def test_default_stdlib_dir_是仓库根下的_stdlib():
    d = stdlib_contract.default_stdlib_dir()
    assert os.path.isdir(d)
    assert os.path.basename(d) == 'stdlib'
    # 与 module_loader._search_paths 同一算法，不允许两处漂移
    from jikuai.module_loader import ModuleLoader
    loader = ModuleLoader(Evaluator())
    assert d in [os.path.normpath(p) for p in loader._search_paths(None)]


def test_parse_exports_基本形态():
    src = '导出 甲 乙 丙。'
    assert stdlib_contract.parse_exports(src) == {'甲', '乙', '丙'}


def test_parse_exports_多条语句():
    src = '导出 甲。\n函数 乙 接收 赵一：\n  返回 赵一。\n。\n导出 乙。'
    assert stdlib_contract.parse_exports(src) == {'甲', '乙'}


def test_parse_exports_忽略行注释():
    src = '-- 导出 假的\n导出 真的。'
    assert stdlib_contract.parse_exports(src) == {'真的'}


def test_parse_exports_忽略字符串内的伪导出():
    src = '定义 赵文 = "导出 假的。"。\n导出 真的。'
    assert stdlib_contract.parse_exports(src) == {'真的'}


def test_parse_exports_空源码():
    assert stdlib_contract.parse_exports('') == set()
    assert stdlib_contract.parse_exports('打印 1。') == set()


def test_list_stdlib_modules_覆盖已有与新增模块():
    mods = stdlib_contract.list_stdlib_modules()
    for name in ('工具', '校验', '简繁', '排版'):
        assert name in mods
    # 历法 是纯 .py 模块，没有 .jk 门面
    assert '历法' not in mods


def test_混合模块判定():
    assert stdlib_contract.has_python_backing('校验') is True
    assert stdlib_contract.has_python_backing('简繁') is True
    assert stdlib_contract.has_python_backing('排版') is True
    assert stdlib_contract.has_python_backing('工具') is False


def test_declared_exports_不存在的模块返回空集():
    assert stdlib_contract.declared_exports('绝不存在的模块') == set()


# ===================== AC-M4-03-01：导出集合 == 文档声明（G10） =====================

def test_G10_契约脚本退出码为0():
    report = check_stdlib_contract.build_report()
    assert report['ok'] is True, check_stdlib_contract.format_text(report)


@pytest.mark.parametrize('module_name', ['工具', '校验', '简繁', '排版'])
def test_AC_M4_03_01_导出集合等于文档声明(module_name):
    doc = check_stdlib_contract.parse_doc_symbols()
    actual = stdlib_contract.declared_exports(module_name)
    assert doc[module_name] == actual


def test_契约脚本_json_输出可解析():
    import json
    import subprocess
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, 'check_stdlib_contract.py'),
         '--json'],
        capture_output=True, text=True, encoding='utf-8', cwd=REPO_ROOT)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload['ok'] is True
    assert payload['modules']['简繁']['mixed_module'] is True
    assert payload['modules']['工具']['mixed_module'] is False


# ===================== AC-M4-03-02：未导出符号访问 → JK-E5002 =====================

def test_AC_M4_03_02_访问未声明符号抛_JK_E5002():
    with pytest.raises(JiKuaiError) as excinfo:
        run_source('导入 工具。\n打印 工具.绝不存在的符号。')
    msg = str(excinfo.value)
    assert 'JK-E5002' in msg
    assert '绝不存在的符号' in msg
    assert '工具' in msg


def test_JK_E5002_保留原有消息主体():
    with pytest.raises(JiKuaiError) as excinfo:
        run_source('导入 简繁。\n打印 简繁.未导出的名字。')
    msg = str(excinfo.value)
    assert '未导出' in msg
    assert codes.JK_E5002 in msg


def test_混合模块内部实现不经导出不可见():
    # 简繁.py 的英文 API 被注入模块环境，但没有 `导出`，因此对外不可见
    with pytest.raises(JiKuaiError) as excinfo:
        run_source('导入 简繁。\n打印 简繁.to_traditional("中国")。')
    assert codes.JK_E5002 in str(excinfo.value)


# ===================== AC-M4-03-03：导入不存在模块 → JK-E5001 =====================

def test_AC_M4_03_03_导入不存在模块抛_JK_E5001():
    with pytest.raises(JiKuaiError) as excinfo:
        run_source('导入 绝不存在的模块。')
    msg = str(excinfo.value)
    assert 'JK-E5001' in msg
    assert '绝不存在的模块' in msg


def test_JK_E5001_保留原有消息主体():
    with pytest.raises(JiKuaiError) as excinfo:
        run_source('导入 另一个不存在的模块。')
    msg = str(excinfo.value)
    assert '找不到模块' in msg
    assert codes.JK_E5001 in msg


def test_非法模块名仍走原有错误分支_不带模块错误码():
    from jikuai.module_loader import ModuleLoader
    loader = ModuleLoader(Evaluator())
    with pytest.raises(JiKuaiError) as excinfo:
        loader.resolve('../逃逸')
    assert '非法模块名' in str(excinfo.value)


# ===================== 混合模块加载语义（ADR-16 §3.3） =====================

def test_混合模块以_jk_为门面且能调用_py_实现():
    # 简繁.jk 只有 `导出` 语句，转繁体 的实现来自 简繁.py 的注入
    assert run_source('导入 简繁。\n简繁.转繁体("国")。') == '國'


def test_从模块导入名字后可直接调用():
    assert run_source('从 排版 导入 规范化文本。\n规范化文本("中文A")。') == '中文 A'


def test_纯py模块不参与模块名解析():
    # 历法 只有 .py，没有 .jk 门面，因此 `导入 历法` 应报找不到模块
    with pytest.raises(JiKuaiError) as excinfo:
        run_source('导入 历法。')
    assert codes.JK_E5001 in str(excinfo.value)


def test_薄封装模块的内建动词_fallback_仍然可用():
    # 校验.jk 无任何函数定义，导出名经 verbs fallback 解析（ADR-16 §3.6）
    assert run_source('从 校验 导入 校验手机号。\n校验手机号("13800138000")。') is True
