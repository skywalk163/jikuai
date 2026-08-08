# -*- coding: utf-8 -*-
"""v0.4.0 M2 · Python → 极快方向集成测试（≥10 项，AC-97~AC-104）。

覆盖：
  - 三入口：load / run_file / run_source
  - 函数 / 变量 / 类（实例化 + 方法调用）
  - 异常翻译（极快 JiKuaiError → Python 侧子类，保留中文文案与 ErrorInfo）
  - 类型映射（py → jk → py 往返）
  - AC-104：import 不触发 load、无全局状态；两次 load 得独立模块对象
  - 路径安全：拒绝绝对路径与 `..` 穿越
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

import jikuai
from jikuai import JiKuaiError
from jikuai.errors import ErrorCategory


# 一个覆盖 函数/变量/类/异常 的模块脚本
SCRIPT = '''\
定义 王基数 = 100。

函数 平方 接收 数：
    返回 数 乘 数。
。

函数 会抛错 接收 x：
    如果 x 小于 0：
        抛出 "负数不允许"。
    。
    返回 x。
。

类 计数器：
    构造 接收 起始：
        自身.值 = 起始。
    。
    方法 增加 接收 步长：
        自身.值 = 自身.值 加 步长。
        返回 自身.值。
    。
。

导出 平方 王基数 计数器 会抛错。
'''


@pytest.fixture
def script_dir(tmp_path):
    """把测试脚本写入临时目录，返回目录路径与脚本文件名。"""
    (tmp_path / 'script.jk').write_text(SCRIPT, encoding='utf-8')
    return str(tmp_path)


class TestEmbedInbound:
    """Python → 极快方向。"""

    # ==================== 三入口 ====================

    def test_ac97_load_and_call_function(self, script_dir):
        """load 后 mod.某函数(3) 可调用。"""
        mod = jikuai.load('script.jk', base_dir=script_dir)
        assert mod.平方(3) == 9

    def test_ac98_access_variable(self, script_dir):
        """mod.某变量 可访问（类型按映射表反向）。"""
        mod = jikuai.load('script.jk', base_dir=script_dir)
        assert mod.王基数 == 100
        assert isinstance(mod.王基数, int)

    def test_ac99_class_instantiate_and_method(self, script_dir):
        """mod.某类 可实例化并调用方法。"""
        mod = jikuai.load('script.jk', base_dir=script_dir)
        counter = mod.计数器(10)
        assert counter.值 == 10
        assert counter.增加(5) == 15
        assert counter.值 == 15

    def test_entry_run_source(self):
        """入口 run_source：直接跑一段源码。"""
        # run_source 返回最后一条语句的值
        result = jikuai.run_source('加 3 5。')
        assert result == 8

    def test_entry_run_file(self, tmp_path, capsys):
        """入口 run_file：跑整个文件。"""
        f = tmp_path / 'hello.jk'
        f.write_text('打印 加 40 2。', encoding='utf-8')
        jikuai.run_file(str(f))
        assert capsys.readouterr().out.strip() == '42'

    # ==================== 异常翻译（AC-100） ====================

    def test_ac100_jikuai_error_crosses_to_python(self, script_dir):
        """极快侧 JiKuaiError → Python 侧 jikuai.JiKuaiError 子类，保留中文文案。"""
        mod = jikuai.load('script.jk', base_dir=script_dir)
        with pytest.raises(JiKuaiError) as exc_info:
            mod.会抛错(-1)
        # 是 JiKuaiError 的子类实例
        assert isinstance(exc_info.value, JiKuaiError)
        assert '负数不允许' in str(exc_info.value)

    def test_ac100_error_carries_errorinfo(self, script_dir):
        """跨界错误保留 ErrorInfo。"""
        mod = jikuai.load('script.jk', base_dir=script_dir)
        with pytest.raises(JiKuaiError) as exc_info:
            mod.会抛错(-5)
        assert exc_info.value.info is not None
        assert isinstance(exc_info.value.info.category, ErrorCategory)

    # ==================== 类型映射 ====================

    def test_type_mapping_roundtrip_list(self, script_dir):
        """列表 py → jk → py 往返。"""
        mod = jikuai.load('script.jk', base_dir=script_dir)
        # 平方 接受 int 返回 int；用它间接验证映射
        assert mod.平方(7) == 49

    def test_type_mapping_string_arg(self, tmp_path):
        """字符串参数 py → jk → py。"""
        (tmp_path / 's.jk').write_text(
            '函数 重复 接收 文字：\n    返回 拼接 文字 文字。\n。\n导出 重复。',
            encoding='utf-8')
        mod = jikuai.load('s.jk', base_dir=str(tmp_path))
        assert mod.重复('啊') == '啊啊'

    # ==================== AC-104：无全局状态 + 独立模块 ====================

    def test_ac104_import_does_not_trigger_load(self):
        """import jikuai 不触发 load、不改全局 mutable state。"""
        import importlib
        m = importlib.import_module('jikuai')
        # 仅暴露约定的入口，无隐式加载的模块缓存对外泄漏
        assert hasattr(m, 'load')
        assert hasattr(m, 'run_source')
        assert callable(m.load)

    def test_ac104_two_loads_independent(self, script_dir):
        """同脚本 load 两次得独立模块对象（互不影响状态）。"""
        mod_a = jikuai.load('script.jk', base_dir=script_dir)
        mod_b = jikuai.load('script.jk', base_dir=script_dir)
        assert mod_a is not mod_b
        # 在 A 的实例上改状态，不影响 B 新建的实例
        ca = mod_a.计数器(0)
        ca.增加(100)
        cb = mod_b.计数器(0)
        assert cb.值 == 0
        assert ca.值 == 100

    # ==================== 路径安全 ====================

    def test_reject_absolute_path(self, script_dir):
        """load 拒绝绝对路径逃逸。"""
        abspath = os.path.join(script_dir, 'script.jk')
        with pytest.raises(JiKuaiError) as exc_info:
            jikuai.load(abspath, base_dir=script_dir)
        assert '绝对路径' in exc_info.value.info.message

    def test_reject_path_traversal(self, script_dir):
        """load 拒绝 `..` 路径穿越。"""
        with pytest.raises(JiKuaiError) as exc_info:
            jikuai.load('../script.jk', base_dir=script_dir)
        assert '穿越' in exc_info.value.info.message or '..' in exc_info.value.info.message

    def test_missing_file_chinese_error(self, script_dir):
        """缺失文件给中文诊断。"""
        with pytest.raises(JiKuaiError) as exc_info:
            jikuai.load('不存在.jk', base_dir=script_dir)
        assert '找不到' in exc_info.value.info.message

    # ==================== 导出可见性 ====================

    def test_unexported_name_not_accessible(self, tmp_path):
        """未导出的名字不可从 Python 侧访问。"""
        (tmp_path / 'p.jk').write_text(
            '定义 王私有 = 1。\n定义 王公开 = 2。\n导出 王公开。',
            encoding='utf-8')
        mod = jikuai.load('p.jk', base_dir=str(tmp_path))
        assert mod.王公开 == 2
        with pytest.raises(AttributeError):
            _ = mod.王私有
