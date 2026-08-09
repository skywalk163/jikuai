# -*- coding: utf-8 -*-
"""极快 AOT 跨端编译试验 · 测试用例（T-M6-A05）。

覆盖：
    - subset_gate：受支持子集通过 + 每类不支持特性各一条负例
    - AC-M6-06-03：子集外源码经 driver → 退出码非 0 且不产出任何输出文件
    - --emit-c：受支持子集能产出 C 源码，包含预期的关键片段
    - 物理隔离：主包不 import jikuai_aot

注意：
    - 无 C 编译器时相关用例 pytest.skip，不 fail
    - 主包基线 533 passed 不受影响
"""

import os
import sys
import shutil
import tempfile

# 路径设置：确保 AOT 和主包都能正确导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools', 'aot'))

import pytest

from jikuai.frontend import compile_source
from jikuai.diagnostics import ListSink
from jikuai.diagnostics.codes import JK_E7001

from jikuai_aot.subset_gate import (
    SUPPORTED_VERBS,
    UNSUPPORTED_NODE_TYPES,
    check,
    unsupported_reasons,
    is_supported,
    describe_subset,
)
from jikuai_aot.codegen import generate_c, CodegenError
from jikuai_aot.driver import (
    build, BuildOptions, detect_c_compiler, EXIT_SUBSET,
)


# ===========================================================================
# 辅助
# ===========================================================================

def _check_source(src, expect_pass=True, file=None):
    """编译源码并过门禁，返回 (passed, diagnostics)。"""
    r = compile_source(src, file=file)
    sink = ListSink()
    passed = check(r.ast, sink, file=file)
    diags = sink.drain()
    if expect_pass:
        assert passed, "预期通过但门禁拒绝，诊断：{}".format(
            [d.message for d in diags])
        assert not diags
    else:
        assert not passed, "预期拒绝但门禁通过"
        assert diags
        for d in diags:
            assert d.code == JK_E7001
    return passed, diags


# ===========================================================================
# T-M6-A01 subset_gate：受支持子集通过
# ===========================================================================

class TestSubsetGatePositive:
    """受支持子集内的源码必须通过门禁。"""

    def test_hello(self):
        _check_source('打印 "你好，世界！"。\n')

    def test_define_and_print(self):
        _check_source("定义 甲 = 42。\n打印 甲。\n")

    def test_arithmetic(self):
        _check_source("打印 3 加 4。\n打印 10 减 2。\n打印 5 乘 3。\n打印 10 除 3。\n")

    def test_comparison(self):
        _check_source("打印 3 大于 2。\n打印 1 等于 1。\n打印 5 小于 3。\n")

    def test_logic(self):
        _check_source("打印 真 且 假。\n打印 真 或 假。\n打印 非 假。\n")

    def test_unary(self):
        _check_source("打印 负 5。\n打印 绝对值 负 3。\n")

    def test_money_literal(self):
        _check_source("打印 ￥99.90。\n")

    def test_chinese_number(self):
        _check_source("打印 三。\n")

    def test_nil_bool(self):
        _check_source("打印 空。\n打印 真。\n打印 假。\n")

    def test_assign(self):
        _check_source("定义 甲 = 1。\n赋值 甲 = 2。\n打印 甲。\n")

    def test_oral_aliases(self):
        _check_source("打印 3 加上 4。\n打印 10 减去 2。\n打印 3 乘以 4。\n打印 10 除以 2。\n")

    def test_intdiv_mod_pow(self):
        _check_source("打印 7 整除 2。\n打印 7 取余 2。\n打印 2 幂 10。\n")


# ===========================================================================
# T-M6-A01 subset_gate：不支持特性各一条负例
# ===========================================================================

class TestSubsetGateNegative:
    """每类不支持特性至少一条负例，断言 JK-E7001 + 消息含特性名 + 位置合法。"""

    def _check_rejected(self, src, feature_keyword):
        """验证 src 被拒绝且诊断消息包含 feature_keyword。"""
        r = compile_source(src)
        sink = ListSink()
        passed = check(r.ast, sink, file="test.jk")
        diags = sink.drain()
        assert not passed
        assert len(diags) >= 1
        d = diags[0]
        assert d.code == JK_E7001
        assert feature_keyword in d.message, \
            "诊断消息不含 {!r}：{!r}".format(feature_keyword, d.message)
        assert d.span.start.line >= 1
        assert d.span.start.column >= 1

    def test_class(self):
        self._check_rejected(
            '类 动物：\n  方法 叫声：\n    返回 "汪"。\n  。\n。\n', "类定义")

    def test_pipeline(self):
        self._check_rejected("打印 1，加 2。\n", "管道")

    def test_import(self):
        self._check_rejected("导入 工具。\n", "模块导入")

    def test_pybridge(self):
        self._check_rejected("导入 蟒:math。\n", "Python 互操作导入")

    def test_funcdef(self):
        # T2a：顶层函数已进入子集，故负例改用**嵌套函数**（闭包），它仍被拒绝。
        self._check_rejected(
            "函数 外 接收 赵x：\n  函数 内 接收 赵y：\n    返回 赵y。\n  。\n"
            "  返回 内(赵x)。\n。\n", "嵌套函数定义")

    def test_for(self):
        self._check_rejected(
            "遍历 赵i 于 列 1 2：\n  打印 赵i。\n。\n", "范围")

    def test_return_outside_function(self):
        # `返回` 仍不在子集内（AOT 尚不支持用户函数）
        self._check_rejected("返回 1。\n", "返回")

    def test_try(self):
        self._check_rejected(
            "尝试：\n  打印 1。\n捕获 赵错误：\n  打印 赵错误。\n。\n", "异常捕获")

    def test_unsupported_verb(self):
        self._check_rejected("打印 求和 列 1 2。\n", "内建动词 求和")

    def test_money_verb(self):
        self._check_rejected("打印 人民币 9.9。\n", "内建动词 人民币")


# ===========================================================================
# T-M6-A03 driver：AC-M6-06-03 子集外不产出任何文件
# ===========================================================================

class TestDriverNoOutput:
    """子集外源码经 driver → 退出码非 0 且不产出任何输出文件。

    断言强度说明（CI 首跑教训）：这里必须断言**具体**的 `EXIT_SUBSET` +
    `JK-E7001`，不能只写 `exit_code != 0`。原来只判非 0 时，无 C 编译器的开发机
    会走到 `EXIT_TOOLCHAIN`（3）也满足断言——于是 `test_if_no_output` 在
    `如果` 早已进入 AOT 子集之后，仍在 Windows 上"通过"了好几个批次，直到
    ubuntu CI 装上 gcc 才暴露。判具体码 = 让用例与编译器是否在位解耦。
    """

    def _assert_rejected_by_gate(self, r, out):
        assert r.exit_code == EXIT_SUBSET, (
            f'期望被子集门禁拒绝（EXIT_SUBSET={EXIT_SUBSET}），'
            f'实得 exit_code={r.exit_code}：{r.message}')
        assert any(d.code == JK_E7001 for d in r.diagnostics), (
            f'应含 JK-E7001 诊断，实得 {[d.code for d in r.diagnostics]}')
        assert not out.exists(), '门禁不通过却产出了文件'

    def test_class_no_output(self, tmp_path):
        src = tmp_path / "bad.jk"
        src.write_text('类 甲：\n  方法 M：\n    返回 1。\n  。\n。\n', encoding="utf-8")
        out = tmp_path / "out.exe"
        r = build(BuildOptions(source_file=str(src), output_path=str(out)))
        self._assert_rejected_by_gate(r, out)
        # 目录里除源文件外不应有任何文件
        extra = [f.name for f in tmp_path.iterdir() if f.name != "bad.jk"]
        assert extra == [], "意外产物：{}".format(extra)

    def test_try_no_output(self, tmp_path):
        # M9-3 起 `如果`/`当`/`重复` 已进入 AOT 子集，不能再拿它们当「子集外」
        # 负例——有 gcc 的环境会真编译成功。改用异常捕获 `尝试`/`捕获`：
        # 它需要栈展开，仍明确不在子集内。
        src = tmp_path / "bad2.jk"
        src.write_text("尝试：\n  打印 1。\n捕获 赵e：\n  打印 2。\n。\n",
                       encoding="utf-8")
        out = tmp_path / "prog.exe"
        r = build(BuildOptions(source_file=str(src), output_path=str(out)))
        self._assert_rejected_by_gate(r, out)

    def test_supported_control_flow_is_not_rejected(self, tmp_path):
        """正向锚点：`如果` 自 M9-3 起就该被接受。

        有了这一条，将来若有人再把控制流误挡回门禁外，会立刻红——而不是
        像上面那样，靠一条名字写着 `if_no_output` 的过期用例悄悄掩盖。
        本用例只查门禁判定，不依赖 C 编译器。
        """
        from jikuai_aot import subset_gate as _gate
        result = compile_source("定义 x=5。\n如果 x 大于 1 那么：\n  打印 1。\n。\n")
        assert _gate.is_supported(result.ast), \
            f'控制流应在子集内，实得拒绝原因：{_gate.unsupported_reasons(result.ast)}'

    def test_missing_file(self, tmp_path):
        r = build(BuildOptions(source_file=str(tmp_path / "nope.jk")))
        assert r.exit_code != 0


# ===========================================================================
# T-M6-A02/03 --emit-c 链路验证
# ===========================================================================

class TestEmitC:
    """--emit-c 模式产出 C 源码并包含预期关键片段。"""

    def test_hello_emit_c(self, tmp_path):
        src = tmp_path / "hello.jk"
        src.write_text('打印 "你好，世界！"。\n', encoding="utf-8")
        out = tmp_path / "hello.c"
        r = build(BuildOptions(source_file=str(src), output_path=str(out), emit_c=True))
        assert r.exit_code == 0
        assert out.exists()
        c = out.read_text(encoding="utf-8")
        assert "jk_print" in c
        assert "jk_str" in c
        assert "int main(void)" in c
        assert "jk_init_console" in c

    def test_arithmetic_emit_c(self, tmp_path):
        src = tmp_path / "calc.jk"
        src.write_text("定义 甲 = 3。\n定义 乙 = 甲 加 4。\n打印 乙。\n",
                       encoding="utf-8")
        out = tmp_path / "calc.c"
        r = build(BuildOptions(source_file=str(src), output_path=str(out), emit_c=True))
        assert r.exit_code == 0
        c = out.read_text(encoding="utf-8")
        assert "jk_add" in c
        assert "jk_int(3LL)" in c
        assert "jk_int(4LL)" in c

    def test_money_literal_emit_c(self, tmp_path):
        src = tmp_path / "money.jk"
        src.write_text("打印 ￥99.90。\n", encoding="utf-8")
        out = tmp_path / "money.c"
        r = build(BuildOptions(source_file=str(src), output_path=str(out), emit_c=True))
        assert r.exit_code == 0
        c = out.read_text(encoding="utf-8")
        # 99.90 元 = 9990 分
        assert "jk_rmb(9990LL)" in c


# ===========================================================================
# 二进制编译（有 C 编译器时）
# ===========================================================================

class TestBinaryCompile:
    """若本机有 C 编译器，验证编译产物运行输出与解释器一致。"""

    @pytest.fixture(autouse=True)
    def _require_cc(self):
        cc = detect_c_compiler()
        if cc is None:
            pytest.skip("未检测到 C 编译器，跳过二进制编译测试")
        self.cc = cc

    def test_hello_binary(self, tmp_path):
        import subprocess
        src = tmp_path / "hello.jk"
        src.write_text('打印 "你好，世界！"。\n', encoding="utf-8")
        out = tmp_path / ("hello.exe" if os.name == "nt" else "hello")
        r = build(BuildOptions(source_file=str(src), output_path=str(out)))
        assert r.exit_code == 0, r.message
        assert out.exists()
        assert out.stat().st_size > 0

        # 运行编译产物
        proc = subprocess.run([str(out)], capture_output=True, text=True, encoding="utf-8")
        aot_output = proc.stdout.strip()

        # 运行解释器
        from jikuai.main import run_source
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_source('打印 "你好，世界！"。\n')
        interp_output = buf.getvalue().strip()

        assert aot_output == interp_output


# ===========================================================================
# 物理隔离
# ===========================================================================

class TestIsolation:
    """主包 jikuai 不导入 jikuai_aot。"""

    def test_main_package_does_not_import_aot(self):
        import importlib
        # 先清理可能已加载的 AOT 模块
        to_del = [k for k in sys.modules if "jikuai_aot" in k]
        saved = {}
        for k in to_del:
            saved[k] = sys.modules.pop(k)

        # 强制重新导入 jikuai
        for k in list(sys.modules):
            if k.startswith("jikuai") and "aot" not in k:
                del sys.modules[k]
        import jikuai
        importlib.reload(jikuai)

        aot_in_modules = [m for m in sys.modules if "jikuai_aot" in m]
        assert aot_in_modules == [], "主包意外导入了 jikuai_aot：{}".format(aot_in_modules)

        # 恢复
        sys.modules.update(saved)


# ===========================================================================
# subset_gate 辅助 API
# ===========================================================================

class TestSubsetGateAPI:
    """describe_subset / unsupported_reasons / is_supported 的基础契约。"""

    def test_describe_subset(self):
        desc = describe_subset()
        assert "打印" in desc["supported_verbs"]
        assert "ClassDef" in desc["unsupported_node_types"]
        assert "类定义" in desc["unsupported_feature_names"].values()

    def test_unsupported_reasons(self):
        r = compile_source("导入 工具。\n")
        reasons = unsupported_reasons(r.ast)
        assert len(reasons) >= 1
        name, line, col = reasons[0]
        assert "导入" in name or "模块" in name
        assert line >= 1

    def test_is_supported_true(self):
        r = compile_source('打印 "OK"。\n')
        assert is_supported(r.ast)

    def test_is_supported_false(self):
        r = compile_source("导入 工具。\n")
        assert not is_supported(r.ast)