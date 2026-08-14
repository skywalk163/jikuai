# -*- coding: utf-8 -*-
"""W104 · AOT 容器第一切片（ADR-37）：列表 / 字典 / 下标读。

覆盖三条链路：

1. **门禁层**（`subset_gate`）：`ListLit` / `DictLit` / `Index` 放行；ADR-37 明确
   顺延的四项继续报 `JK-E7001`，且 notes 必须指到 ADR-37 的对应小节 —— 让用户
   一眼看出「这是划出去的边界，不是 bug」。
2. **codegen 层**：生成的 C 里有 `jk_list_of` / `jk_dict_of` / `jk_index`；空容器、
   嵌套容器都能编；绕过门禁直接喂下标写入时兜底报错。
3. **双路一致性**（有 C 编译器时）：同一份 `.jk` 解释器跑一遍、AOT 二进制跑一遍，
   stdout 逐字一致；报错用例的 stderr 文案逐字一致。

为什么容器内外是两套渲染（这条最容易写错，先记住再读代码）
--------------------------------------------------------
解释器打印走 `_format_value` → `str(v)`，而 Python 的 `str(list)` 对**元素**用
`repr()`。所以本机实测（v0.22.0 主干）：

    打印 真。              → 真
    打印 【真，假，空】。  → [True, False, None]      ← 容器内是 Python repr！
    打印 "甲"。            → 甲
    打印 【"甲"】。        → ['甲']                   ← 单引号是 repr 给的

AOT 的 `jk_write` / `jk_buf_repr` 就是照这个分工写的。本文件的 e2e 用例把它钉住：
不硬编码期望值，而是拿解释器的真实输出当基准 —— 基准永远不会写歪。

stderr 的比较口径
----------------
解释器的 stderr 是**带位置信息的诊断信封**（`第 2 行，第 6 列：运行错误：<文案>`
＋源码行 ＋ 插入符），AOT 二进制只有 `jk_fatal` 打的一行裸文案。ADR-37 §3 验收
第 2 条要求的是「**报错文案**逐字一致」，不是信封逐字一致（生成的 C 不带 `#line`，
拿不到行号，见 docs/AOT.md 局限）。所以这里的基准取解释器 `ErrorInfo.message`
—— 文案的唯一定义处，与 `tests/test_index_diagnostics.py` 钉的是同一个字符串。
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools', 'aot'))

import pytest

from jikuai.frontend import compile_source
from jikuai.diagnostics.sink import ListSink
from jikuai.diagnostics.codes import JK_E7001
# AST 节点类必须在**模块导入期**取到（沿用 v0_7 / v0_9 测试的做法：
# TestIsolation 会重载 jikuai.*，函数体里再 import 会拿到不同的类对象）。
from jikuai.ast_nodes import (
    Assign as AstAssign,
    Call as AstCall,
    DictLit as AstDictLit,
    Ident as AstIdent,
    Index as AstIndex,
    ListLit as AstListLit,
    NumberLit as AstNumberLit,
    Program as AstProgram,
    StringLit as AstStringLit,
)
from jikuai.evaluator import JiKuaiError
from jikuai.main import run_source
from jikuai_aot.subset_gate import (
    DICT_KEY_FEATURE,
    LOOP_CONTAINER_FEATURE,
    UNSUPPORTED_NODE_TYPES,
    check,
    describe_subset,
    is_supported,
)
from jikuai_aot.codegen import CodegenError, generate_c
from jikuai_aot.driver import BuildOptions, build, detect_c_compiler


# ===========================================================================
# 工具
# ===========================================================================

def _gate(src):
    """跑门禁，返回 (是否通过, 诊断列表)。"""
    r = compile_source(src)
    sink = ListSink()
    ok = check(r.ast, sink, file='t.jk')
    return ok, sink.drain()


def _gen(src):
    """编译到 C 源码（要求先过门禁）。"""
    r = compile_source(src)
    ok, diags = _gate(src)
    assert ok, '门禁拒绝了本应支持的源码：{}'.format([d.message for d in diags])
    return generate_c(r.ast)


def _interpret(src):
    """用解释器跑一遍，返回 stdout（用于与 AOT 产物比对）。

    走子进程而不是 in-process，是为了拿到真正经过 `print` + 编码层的字节，
    与二进制的 stdout 处在同一比较基准上。
    """
    proc = subprocess.run(
        [sys.executable, '-c',
         'import sys; from jikuai.main import run_source; '
         'run_source(sys.stdin.read())'],
        input=src, capture_output=True, text=True, encoding='utf-8',
        env={**os.environ,
             'PYTHONPATH': os.path.join(
                 os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'src'),
             'JIKUAI_DIAGNOSTICS': 'off',
             'PYTHONUTF8': '1'},
    )
    assert proc.returncode == 0, f'解释器执行失败：{proc.stderr}'
    return proc.stdout


def _interpreter_message(src):
    """跑一段**必然出错**的源码，返回解释器诊断的文案（`ErrorInfo.message`）。

    这是文案的唯一权威来源。断言 `info is not None`：没有 info 意味着走了兜底
    包装（Python 原文直通），那种情况下 AOT 去对齐它毫无意义。
    """
    import contextlib
    import io
    with pytest.raises(JiKuaiError) as e:
        with contextlib.redirect_stdout(io.StringIO()):
            run_source(src)
    info = getattr(e.value, 'info', None)
    assert info is not None, f'解释器诊断没带 ErrorInfo：{e.value!r}'
    return info.message


def _have_c_compiler():
    """有没有可用的 C 编译器 —— **直接问驱动**，不在测试里另抄一份候选清单。

    抄一份就会漂移：W104 给 `detect_c_compiler` 加了 `zig`（走 `zig cc`）之后，
    这里原来那份硬编码的 `('gcc','clang','cc','cl')` 仍然找不到 zig，也不认
    `CC` 环境变量，结果驱动明明能编、这批测试却全报「环境无 C 编译器」而 skip。
    CI 的「AOT e2e 零 skip」守卫也就守了个空。
    """
    return detect_c_compiler() is not None



requires_cc = pytest.mark.skipif(
    not _have_c_compiler(), reason='环境无 C 编译器，跳过编译-运行比对')


def _build_exe(src, name, tmp_path):
    """把源码 AOT 编译成二进制，返回可执行文件路径。"""
    src_file = tmp_path / f'{name}.jk'
    src_file.write_text(src, encoding='utf-8')
    exe = tmp_path / (f'{name}.exe' if os.name == 'nt' else name)
    result = build(BuildOptions(source_file=str(src_file),
                               output_path=str(exe)))
    assert result.ok, f'AOT 构建失败：{result.message}'
    assert exe.exists(), '构建声称成功但产物不存在'
    return exe


# ===========================================================================
# 门禁：容器与下标读现在被接受
# ===========================================================================

class TestGateAcceptsContainers:
    def test_list_literal(self):
        ok, diags = _gate('打印 【1，2，3】。\n')
        assert ok, [d.message for d in diags]

    def test_dict_literal(self):
        ok, diags = _gate('打印 {"甲"：1}。\n')
        assert ok, [d.message for d in diags]

    def test_empty_list_literal(self):
        ok, diags = _gate('打印 【】。\n')
        assert ok, [d.message for d in diags]

    def test_empty_dict_literal(self):
        ok, diags = _gate('打印 {}。\n')
        assert ok, [d.message for d in diags]

    def test_nested_containers(self):
        ok, diags = _gate('打印 【1，【2，3】，{"甲"：【4】}】。\n')
        assert ok, [d.message for d in diags]

    def test_list_index_read(self):
        ok, diags = _gate('定义 赵表 = 【10，20，30】。\n打印 赵表[1]。\n')
        assert ok, [d.message for d in diags]

    def test_dict_index_read(self):
        ok, diags = _gate('定义 赵d = {"甲"：1}。\n打印 赵d["甲"]。\n')
        assert ok, [d.message for d in diags]

    def test_string_index_read(self):
        ok, diags = _gate('定义 赵s = "你好"。\n打印 赵s[1]。\n')
        assert ok, [d.message for d in diags]

    def test_index_with_expression_subscript(self):
        src = ('定义 赵表 = 【10，20，30】。\n'
               '定义 赵i = 1。\n'
               '打印 赵表[加 赵i 1]。\n')
        ok, diags = _gate(src)
        assert ok, [d.message for d in diags]

    def test_container_inside_function_body(self):
        """函数体不是循环体：里面造容器是允许的（in_loop 归零）。"""
        src = ('函数 造表：\n'
               '  返回 【1，2，3】。\n。\n'
               '打印 造表()。\n')
        ok, diags = _gate(src)
        assert ok, [d.message for d in diags]

    def test_index_inside_loop_body_is_fine(self):
        """循环体里**读**下标不受限 —— 受限的只有「构造」容器。"""
        src = ('定义 赵表 = 【10，20，30】。\n'
               '遍历 赵i 于 范围 0 3：\n'
               '  打印 赵表[赵i]。\n。\n')
        ok, diags = _gate(src)
        assert ok, [d.message for d in diags]

    def test_repeat_count_is_not_loop_body(self):
        """`重复 N 次` 的 N 在进入循环前只求值一次，不算循环体内。"""
        src = ('重复 长度 【1，2】 次：\n  打印 "x"。\n。\n')
        # `长度` 不在动词白名单里，所以这条整体仍会被拒 —— 但**不该**是
        # 「循环体内构造容器」。这里只断言拒绝原因不是那一条。
        ok, diags = _gate(src)
        assert not ok
        assert all(LOOP_CONTAINER_FEATURE not in d.message for d in diags), \
            [d.message for d in diags]


# ===========================================================================
# 门禁：ADR-37 明确顺延的四项仍须拒绝，且 notes 指向 ADR-37
# ===========================================================================

class TestGateRejectsDeferredItems:
    """四条反例。除了「报错」，还必须**说清是顺延项**（ADR-37 §3 验收第 3 条）。"""

    def _rejected(self, src):
        ok, diags = _gate(src)
        assert not ok, '预期拒绝但门禁通过'
        assert diags, '预期至少一条诊断'
        assert diags[0].code == JK_E7001, diags[0].code
        return diags

    def _assert_points_to_adr37(self, diags, section):
        blob = '\n'.join(n for d in diags for n in d.notes)
        assert 'ADR-37' in blob, f'notes 未提到 ADR-37：{blob!r}'
        assert section in blob, f'notes 未指向 {section}：{blob!r}'

    # ---- 顺延项 1：`.成员`（ADR-37 §2.1） ----

    def test_member_access_rejected(self):
        diags = self._rejected('定义 赵d = {"甲"：1}。\n打印 赵d.甲。\n')
        assert any('成员访问' in d.message for d in diags), \
            [d.message for d in diags]
        self._assert_points_to_adr37(diags, '§2.1')

    def test_member_access_note_points_to_index_as_workaround(self):
        """拒绝之余要给出路：字典取值请写 `字典[键]`。"""
        diags = self._rejected('定义 赵d = {"甲"：1}。\n打印 赵d.甲。\n')
        blob = '\n'.join(n for d in diags for n in d.notes)
        assert '字典[键]' in blob, blob

    # ---- 顺延项 2：下标写（ADR-37 §2.3） ----

    def test_index_write_rejected(self):
        diags = self._rejected('定义 赵表 = 【1，2】。\n赵表[0] = 9。\n')
        assert any('赋值目标' in d.message and '索引访问' in d.message
                   for d in diags), [d.message for d in diags]
        self._assert_points_to_adr37(diags, '§2.3')

    def test_dict_index_write_rejected(self):
        diags = self._rejected('定义 赵d = {"甲"：1}。\n赵d["乙"] = 2。\n')
        self._assert_points_to_adr37(diags, '§2.3')

    def test_index_read_still_allowed_after_write_rejected(self):
        """拒绝写不能顺手把读也拒了 —— 这是最容易写坏的一处。"""
        ok, _ = _gate('定义 赵表 = 【1，2】。\n打印 赵表[0]。\n')
        assert ok

    # ---- 顺延项 3：容器作字典键（ADR-37 §2.4） ----

    def test_list_as_dict_key_rejected(self):
        diags = self._rejected('打印 {【1】：2}。\n')
        assert any(DICT_KEY_FEATURE in d.message for d in diags), \
            [d.message for d in diags]
        self._assert_points_to_adr37(diags, '§2.4')

    def test_dict_as_dict_key_rejected(self):
        diags = self._rejected('打印 {{"甲"：1}：2}。\n')
        assert any(DICT_KEY_FEATURE in d.message for d in diags), \
            [d.message for d in diags]

    def test_container_as_dict_value_is_allowed(self):
        """限的是**键**，值可以是任意容器（ADR-37 §2.4：嵌套只是指针）。"""
        ok, diags = _gate('打印 {"甲"：【1，2】}。\n')
        assert ok, [d.message for d in diags]

    def test_scalar_dict_keys_all_allowed(self):
        for key in ('"甲"', '1', '1.5', '真', '空', '￥9.90'):
            ok, diags = _gate('打印 {%s：1}。\n' % key)
            assert ok, (key, [d.message for d in diags])

    # ---- 顺延项 4：循环体内构造容器（ADR-37 §2.2） ----

    def test_list_in_for_body_rejected(self):
        diags = self._rejected(
            '遍历 赵i 于 范围 1 3：\n  定义 赵x = 【1，2】。\n。\n')
        assert any(LOOP_CONTAINER_FEATURE in d.message for d in diags), \
            [d.message for d in diags]
        self._assert_points_to_adr37(diags, '§2.2')

    def test_dict_in_for_body_rejected(self):
        diags = self._rejected(
            '遍历 赵i 于 范围 1 3：\n  定义 赵x = {"甲"：赵i}。\n。\n')
        assert any(LOOP_CONTAINER_FEATURE in d.message for d in diags), \
            [d.message for d in diags]

    def test_list_in_while_body_rejected(self):
        diags = self._rejected(
            '定义 赵n = 0。\n当 赵n 小于 3：\n'
            '  定义 赵x = 【1】。\n  赵n = 加 赵n 1。\n。\n')
        assert any(LOOP_CONTAINER_FEATURE in d.message for d in diags), \
            [d.message for d in diags]

    def test_list_in_repeat_body_rejected(self):
        diags = self._rejected('重复 3 次：\n  定义 赵x = 【1】。\n。\n')
        assert any(LOOP_CONTAINER_FEATURE in d.message for d in diags), \
            [d.message for d in diags]

    def test_list_nested_deep_in_loop_body_rejected(self):
        """藏在循环体里的 `如果` 分支中，一样要抓住。"""
        diags = self._rejected(
            '遍历 赵i 于 范围 1 3：\n'
            '  如果 赵i 大于 1 那么：\n    定义 赵x = 【1】。\n  。\n。\n')
        assert any(LOOP_CONTAINER_FEATURE in d.message for d in diags), \
            [d.message for d in diags]

    def test_for_iterable_listlit_is_not_rejected(self):
        """**关键回归**：`遍历 ... 于 【字面量列表】` 是既有合法特例。

        那个列表会被 `_emit_for` 就地展开成栈上数组，压根不落堆，所以不能被
        「循环体内构造容器」连坐。写坏这一条会把 T2b 的既有能力打掉。
        """
        ok, diags = _gate('遍历 赵项 于 【10，20，30】：\n  打印 赵项。\n。\n')
        assert ok, [d.message for d in diags]

    def test_nested_for_iterable_listlit_still_ok(self):
        """外层是循环，内层遍历源仍是就地展开，不该被拒。"""
        src = ('遍历 赵i 于 范围 1 3：\n'
               '  遍历 赵项 于 【10，20】：\n'
               '    打印 赵i 赵项。\n  。\n。\n')
        ok, diags = _gate(src)
        assert ok, [d.message for d in diags]


# ===========================================================================
# 门禁：子集描述与文档核对
# ===========================================================================

class TestDescribeSubsetAfterW104:
    def test_containers_moved_out_of_unsupported(self):
        d = describe_subset()
        unsupported = set(d['unsupported_node_types'])
        for node in ('ListLit', 'DictLit', 'Index'):
            assert node not in unsupported, \
                f'{node} 已进子集（ADR-37 §2.1），应移出 UNSUPPORTED_NODE_TYPES'

    def test_member_access_stays_unsupported(self):
        assert 'MemberAccess' in UNSUPPORTED_NODE_TYPES
        assert 'MemberAccess' in set(
            describe_subset()['unsupported_node_types'])

    def test_feature_names_do_not_advertise_supported_features(self):
        """`unsupported_feature_names` 不能把已支持的特性列成不支持。

        `_NODE_FEATURE_NAMES` 里仍留着 `Index`（`Assign` 分支要用它取中文名），
        所以 `describe_subset()` 必须按 `UNSUPPORTED_NODE_TYPES` 过滤。
        """
        names = describe_subset()['unsupported_feature_names']
        assert set(names) <= set(UNSUPPORTED_NODE_TYPES), \
            f'多出来的键：{set(names) - set(UNSUPPORTED_NODE_TYPES)}'
        assert '索引访问' not in names.values()
        assert '列表字面量' not in names.values()

    def test_new_contextual_rules_exported(self):
        ctx = describe_subset()['unsupported_contextual_features']
        assert DICT_KEY_FEATURE in ctx
        assert LOOP_CONTAINER_FEATURE in ctx
        assert 'ADR-37' in ctx[DICT_KEY_FEATURE]
        assert 'ADR-37' in ctx[LOOP_CONTAINER_FEATURE]

    def test_is_supported_agrees_with_check(self):
        assert is_supported(compile_source('打印 【1，2】。\n').ast)
        assert not is_supported(compile_source('打印 {【1】：2}。\n').ast)


# ===========================================================================
# C 运行时源码自检（不需要编译器）
# ===========================================================================

class TestCRuntimeShape:
    def test_runtime_declares_container_tags_and_structs(self):
        c = _gen('打印 1。\n')      # prelude 与程序内容无关
        for token in ('JK_LIST', 'JK_DICT', 'void *obj',
                      'typedef struct {\n    JKValue *items;',
                      'JKDict', 'jk_index', 'jk_list_of', 'jk_dict_of'):
            assert token in c, token

    def test_no_unexpanded_placeholder_left(self):
        """`$中文$` 占位符必须全部展开 —— 漏一个就编译不过。"""
        c = _gen('打印 【1】。\n')
        assert '$' not in c

    def test_arena_decision_is_documented_in_generated_c(self):
        """ADR-37 §2.2 的「不回收」必须写在生成的 C 里，别让读者当成漏了 free。"""
        c = _gen('打印 【1】。\n')
        assert 'ADR-37' in c
        assert 'free' in c          # 注释里解释了为什么不 free

    def test_truthiness_covers_containers(self):
        c = _gen('打印 1。\n')
        assert 'case JK_LIST:  return ((JKList *)a.v.obj)->len != 0;' in c

    def test_repr_uses_python_shape_for_container_elements(self):
        """容器内元素走 Python repr：None / True / False 这三个字面串必须在。"""
        c = _gen('打印 1。\n')
        assert '"None"' in c
        assert '"True" : "False"' in c


# ===========================================================================
# codegen：生成的 C 结构正确
# ===========================================================================

class TestCodegenContainers:
    def test_list_literal_emits_jk_list_of(self):
        c = _gen('打印 【1，2，3】。\n')
        assert 'jk_list_of(3, jk_int(1LL), jk_int(2LL), jk_int(3LL))' in c

    def test_empty_list_literal_compiles(self):
        """空列表：`jk_list_of(0)`。

        这里正是 `_emit_for` 当年踩过的坑（C 不允许零长度数组初始化），
        varargs 形态天然没这个问题，但仍要有测试守着。
        """
        c = _gen('打印 【】。\n')
        assert 'jk_list_of(0)' in c

    def test_dict_literal_emits_flat_key_value_pairs(self):
        c = _gen('打印 {"甲"：1，"乙"：2}。\n')
        assert 'jk_dict_of(2, ' in c
        # 键值交替，且顺序与源码一致（打印顺序靠它）
        i_jia = c.index('jk_dict_of(2, ')
        segment = c[i_jia:i_jia + 200]
        assert segment.index('jk_int(1LL)') < segment.index('jk_int(2LL)')

    def test_empty_dict_literal_compiles(self):
        c = _gen('打印 {}。\n')
        assert 'jk_dict_of(0)' in c

    def test_nested_containers_nest_the_calls(self):
        c = _gen('打印 【1，【2，3】】。\n')
        assert 'jk_list_of(2, jk_int(1LL), jk_list_of(2, ' in c

    def test_dict_value_can_be_container(self):
        c = _gen('打印 {"甲"：【1，2】}。\n')
        assert 'jk_dict_of(1, ' in c
        assert 'jk_list_of(2, ' in c

    def test_index_emits_jk_index(self):
        c = _gen('定义 赵表 = 【10，20】。\n打印 赵表[0]。\n')
        assert 'jk_index(jk_var1, jk_int(0LL))' in c

    def test_index_subscript_can_be_expression(self):
        c = _gen('定义 赵表 = 【10，20】。\n定义 赵i = 0。\n'
                 '打印 赵表[加 赵i 1]。\n')
        assert 'jk_index(jk_var1, jk_add(' in c

    def test_chained_index_nests(self):
        c = _gen('定义 赵表 = 【【1，2】，【3，4】】。\n打印 赵表[0][1]。\n')
        assert 'jk_index(jk_index(' in c

    def test_container_in_function_return(self):
        c = _gen('函数 造表：\n  返回 【1，2】。\n。\n打印 造表()。\n')
        assert 'static JKValue jk_fn1(void)' in c
        assert 'return jk_list_of(2, ' in c

    def test_bare_index_statement_is_evaluated_not_dropped(self):
        """单独一行的 `赵表[9]。` 也要求值 —— 越界照样得停机。"""
        c = _gen('定义 赵表 = 【1】。\n赵表[0]。\n')
        assert '(void)(jk_index(' in c

    def test_container_expression_is_pure(self):
        """容器字面量必须是纯表达式：能直接塞进条件表达式里。

        若改成「临时变量 + 逐元素赋值」，这行就编不出来 —— `if (...)` 的头部
        插不进语句。
        （注意不能拿 `当` 的条件来测：容器写在循环条件里每轮都要重建，会被
        门禁的「循环体内构造容器」规则拒掉，见 TestGateRejectsDeferredItems。）
        """
        c = _gen('如果 非 【】 那么：\n  打印 "空表"。\n。\n')
        assert 'if (jk_truthy(jk_not(jk_list_of(0))))' in c



# ===========================================================================
# codegen：绕过门禁时的兜底
# ===========================================================================

class TestCodegenGuards:
    def test_index_assign_target_rejected_with_adr_reference(self):
        """直接构造 AST 绕过门禁：codegen 兜底，且消息里写明是 ADR-37 §2.3 顺延。"""
        prog = AstProgram(body=[
            AstAssign(target=AstIndex(obj=AstIdent(name='赵表'),
                                      index=AstNumberLit(value=0)),
                      value=AstNumberLit(value=9)),
        ])
        with pytest.raises(CodegenError) as e:
            generate_c(prog)
        assert 'ADR-37' in str(e.value)
        assert '2.3' in str(e.value)

    def test_empty_ast_containers_compile(self):
        """直接构造空容器 AST 也要能编（防止只在 parser 路径上测到）。"""
        prog = AstProgram(body=[
            AstCall(verb='打印', args=[AstListLit(items=[])]),
            AstCall(verb='打印', args=[AstDictLit(items=[])]),
        ])
        c = generate_c(prog)
        assert 'jk_list_of(0)' in c
        assert 'jk_dict_of(0)' in c

    def test_dict_lit_ast_pairs_are_flattened_in_order(self):
        prog = AstProgram(body=[
            AstCall(verb='打印', args=[AstDictLit(items=[
                (AstStringLit(value='甲'), AstNumberLit(value=1)),
                (AstStringLit(value='乙'), AstNumberLit(value=2)),
            ])]),
        ])
        c = generate_c(prog)
        assert 'jk_dict_of(2, ' in c


# ===========================================================================
# 双路一致性 · stdout（ADR-37 §3 验收第 1 条）
# ===========================================================================

_E2E_STDOUT_CASES = [
    ('list_ints', '打印 【1，2，3】。\n'),
    ('list_empty', '打印 【】。\n'),
    ('list_strings', '打印 【"甲"，"乙"】。\n'),
    ('list_mixed_scalars', '打印 【1，1.5，"甲"，真，假，空】。\n'),
    ('list_money', '打印 【￥9.90，￥0.05】。\n'),
    ('list_nested', '打印 【1，【2，【3】】】。\n'),
    ('dict_one', '打印 {"甲"：1}。\n'),
    ('dict_empty', '打印 {}。\n'),
    ('dict_many', '打印 {"甲"：1，"乙"：2，"丙"：3}。\n'),
    ('dict_int_keys', '打印 {1："壹"，2："贰"}。\n'),
    ('dict_mixed_keys', '打印 {1：真，"甲"：空，真：1.5}。\n'),
    ('dict_container_value', '打印 {"甲"：【1，2】，"乙"：{"丙"：3}}。\n'),
    ('dict_dup_key', '打印 {"甲"：1，"甲"：2}。\n'),
    ('index_list', '定义 赵表 = 【10，20，30】。\n打印 赵表[0] 赵表[1] 赵表[2]。\n'),
    ('index_negative', '定义 赵表 = 【10，20，30】。\n打印 赵表[-1] 赵表[-3]。\n'),
    ('index_float_truncates',
     '定义 赵表 = 【10，20，30】。\n打印 赵表[1.7]。\n'),
    ('index_bool_subscript', '定义 赵表 = 【10，20】。\n打印 赵表[真] 赵表[假]。\n'),
    ('index_dict', '定义 赵d = {"甲"：1，"乙"：2}。\n打印 赵d["甲"] 赵d["乙"]。\n'),
    ('index_dict_int_key', '定义 赵d = {1："壹"}。\n打印 赵d[1]。\n'),
    ('index_string', '定义 赵s = "你好世界"。\n打印 赵s[0] 赵s[1] 赵s[-1]。\n'),
    ('index_nested', '定义 赵表 = 【【1，2】，【3，4】】。\n打印 赵表[1][0]。\n'),
    ('index_expression_subscript',
     '定义 赵表 = 【10，20，30】。\n定义 赵i = 1。\n打印 赵表[加 赵i 1]。\n'),
    ('index_in_loop',
     '定义 赵表 = 【10，20，30】。\n'
     '遍历 赵i 于 范围 0 3：\n  打印 赵表[赵i]。\n。\n'),
    ('container_truthiness',
     '如果 【】 那么：\n  打印 "非空"。\n否则：\n  打印 "空"。\n。\n'
     '如果 【1】 那么：\n  打印 "非空"。\n否则：\n  打印 "空"。\n。\n'
     '如果 {} 那么：\n  打印 "非空"。\n否则：\n  打印 "空"。\n。\n'),
    ('container_equality',
     '打印 等于 【1，2】 【1，2】。\n'
     '打印 等于 【1，2】 【2，1】。\n'
     '打印 等于 【1，2】 1。\n'
     '打印 等于 {"甲"：1} {"甲"：1}。\n'),
    ('container_ordering',
     '打印 小于 【1，2】 【1，3】。\n'
     '打印 大于 【1，2，3】 【1，2】。\n'),
    ('container_from_function',
     '函数 造表：\n  返回 【1，2，3】。\n。\n'
     '打印 造表()。\n打印 造表()[1]。\n'),
    ('container_as_arg',
     '函数 首项 接收 赵表：\n  返回 赵表[0]。\n。\n'
     '打印 首项(【7，8，9】)。\n'),
    ('sum_over_indexed_list',
     '定义 赵表 = 【1，2，3，4，5】。\n定义 赵和 = 0。\n'
     '遍历 赵i 于 范围 0 5：\n  赵和 = 加 赵和 赵表[赵i]。\n。\n打印 赵和。\n'),
    ('list_with_quote_in_string', '打印 【"含\'单引号\'"】。\n'),

    # -----------------------------------------------------------------------
    # W106-b：容器 × 控制流的组合。
    #
    # W104 收口时的 e2e 只覆盖了「容器 + `遍历 范围`」与「容器 + 函数传参/返回」，
    # `当` / `重复` / `跳出` / `跳过` / 嵌套二维下标 / 字典进循环 都还没有一条真
    # 编译真运行的用例。下面这批把它们补齐。
    #
    # **写这批用例的硬约束**：容器一律在循环**外**构造，循环体内只做下标**读**。
    # 循环体内构造容器会被门禁按「循环体内构造容器」拒掉（ADR-37 §2.2 升级触发线
    # (a)，反例见 TestGateRejectsDeferredItems），那样测的就是门禁而不是 e2e 了。
    # -----------------------------------------------------------------------

    ('container_with_while',
     # 堆列表 + `当`：计数器变量当下标，条件每轮重新求值。
     '定义 赵表 = 【10，20，30】。\n定义 赵i = 0。\n'
     '当 赵i 小于 3：\n'
     '  打印 赵表[赵i]。\n'
     '  赵i = 加 赵i 1。\n。\n'),
    ('container_with_while_accum',
     '定义 赵表 = 【1，2，3，4】。\n定义 赵i = 0。\n定义 赵和 = 0。\n'
     '当 赵i 小于 4：\n'
     '  赵和 = 加 赵和 赵表[赵i]。\n'
     '  赵i = 加 赵i 1。\n。\n打印 赵和。\n'),
    ('container_with_repeat',
     # `重复 N 次` 的 N 只在进入前求值一次（_emit_repeat），下标靠体内自增的变量。
     '定义 赵表 = 【100，200，300】。\n定义 赵i = 0。\n'
     '重复 3 次：\n'
     '  打印 赵表[赵i]。\n'
     '  赵i = 加 赵i 1。\n。\n'),
    ('container_with_break',
     # 读到不满足条件的元素就提前停：`跳出` 之后的元素不能被打印。
     '定义 赵表 = 【10，20，30，40，50】。\n'
     '遍历 赵i 于 范围 0 5：\n'
     '  如果 赵表[赵i] 大于 30 那么：\n    跳出。\n  。\n'
     '  打印 赵表[赵i]。\n。\n'),
    ('container_with_continue',
     '定义 赵表 = 【1，2，3，4，5】。\n'
     '遍历 赵i 于 范围 0 5：\n'
     '  如果 赵表[赵i] 取余 2 等于 0 那么：\n    跳过。\n  。\n'
     '  打印 赵表[赵i]。\n。\n'),
    ('nested_loop_2d_index',
     # 嵌套列表在循环外构造，内外层各出一个下标：`赵表[i][j]` 走 jk_index 套 jk_index。
     '定义 赵表 = 【【1，2，3】，【4，5，6】】。\n'
     '遍历 赵i 于 范围 0 2：\n'
     '  遍历 赵j 于 范围 0 3：\n'
     '    打印 赵表[赵i][赵j]。\n  。\n。\n'),
    ('dict_lookup_in_loop_int_key',
     # 字典是线性查找（jk_dict_get 顺序扫 keys），循环里按变量键查值得单独钉。
     '定义 赵d = {1："壹"，2："贰"，3："叁"}。\n'
     '遍历 赵i 于 范围 1 4：\n  打印 赵d[赵i]。\n。\n'),
    ('dict_lookup_in_loop_var_key',
     # 键本身来自另一个容器的下标读 —— 变量键的最一般形态。
     '定义 赵d = {"甲"：1，"乙"：2，"丙"：3}。\n'
     '定义 赵键表 = 【"甲"，"乙"，"丙"】。\n'
     '遍历 赵i 于 范围 0 3：\n  打印 赵d[赵键表[赵i]]。\n。\n'),
    ('stack_iter_source_reads_heap_list',
     # 两条降级路径首次相遇：遍历源 `【0，2】` 被 _emit_for 就地展开成栈上数组、
     # 不落堆；循环体里读的 赵表 是真的堆容器（jk_list_of）。
     '定义 赵表 = 【10，20，30】。\n'
     '遍历 赵项 于 【0，2】：\n  打印 赵项 赵表[赵项]。\n。\n'),
    ('rmb_element_arithmetic',
     # 人民币经容器往返：分为单位的整数不能变成浮点，格式也不能变。
     '定义 赵表 = 【￥19.99，￥0.01，￥5.50】。\n'
     '定义 赵总 = ￥0.00。\n'
     '遍历 赵i 于 范围 0 3：\n  赵总 = 加 赵总 赵表[赵i]。\n。\n'
     '打印 赵总。\n打印 赵表[0]。\n打印 减 赵表[0] 赵表[1]。\n'),
    ('two_list_params_function',
     '函数 逐位比对 接收 赵甲 赵乙：\n'
     '  遍历 赵i 于 范围 0 3：\n'
     '    打印 加 赵甲[赵i] 赵乙[赵i]。\n'
     '    打印 等于 赵甲[赵i] 赵乙[赵i]。\n  。\n。\n'
     '逐位比对(【1，2，3】, 【10，2，30】)。\n'),
    ('recursive_func_with_container',
     # 容器在递归外构造一次，每层递归只是把同一个指针传下去 + 读下标。
     '函数 倒序求和 接收 赵表 赵i：\n'
     '  如果 赵i 小于 0 那么：\n    返回 0。\n  。\n'
     '  打印 赵表[赵i]。\n'
     '  返回 加 赵表[赵i] 倒序求和(赵表, 赵i 减 1)。\n。\n'
     '打印 倒序求和(【1，2，3，4】, 3)。\n'),
]


@requires_cc
@pytest.mark.parametrize('name,src', _E2E_STDOUT_CASES,
                         ids=[c[0] for c in _E2E_STDOUT_CASES])
def test_aot_container_stdout_matches_interpreter(name, src, tmp_path):
    """AOT 原生产物的 stdout 必须与解释器逐字一致（ADR-37 §3 验收第 1 条）。"""
    expected = _interpret(src)
    exe = _build_exe(src, name, tmp_path)
    proc = subprocess.run([str(exe)], capture_output=True, text=True,
                          encoding='utf-8')
    assert proc.returncode == 0, f'产物运行失败：{proc.stderr}'
    assert proc.stdout == expected, (
        f'AOT 与解释器输出不一致\n'
        f'AOT      ={proc.stdout!r}\n解释器={expected!r}')


# ===========================================================================
# 双路一致性 · stderr 文案（ADR-37 §3 验收第 2 条）
# ===========================================================================

#: 四条错误路径，逐条对应 ADR-37 §2.5 实测记录里那四种。
_E2E_STDERR_CASES = [
    ('err_index_out_of_range',
     '定义 赵表 = 【10，20，30】。\n打印 赵表[9]。\n'),
    ('err_index_out_of_range_negative',
     '定义 赵表 = 【10，20，30】。\n打印 赵表[-4]。\n'),
    ('err_empty_list_index',
     '定义 赵表 = 【】。\n打印 赵表[0]。\n'),
    ('err_dict_key_missing',
     '定义 赵d = {"甲"：1}。\n打印 赵d["乙"]。\n'),
    ('err_dict_key_missing_int',
     '定义 赵d = {1："壹"}。\n打印 赵d[2]。\n'),
    ('err_not_subscriptable',
     '定义 赵n = 5。\n打印 赵n[0]。\n'),
    ('err_nil_not_subscriptable',
     '定义 赵x = 空。\n打印 赵x[0]。\n'),
    ('err_subscript_not_int',
     '定义 赵表 = 【10，20，30】。\n打印 赵表["甲"]。\n'),
    ('err_subscript_is_nil',
     '定义 赵表 = 【10，20，30】。\n打印 赵表[空]。\n'),
    ('err_string_index_out_of_range',
     '定义 赵s = "你好"。\n打印 赵s[5]。\n'),
]


@pytest.mark.parametrize('name,src', _E2E_STDERR_CASES,
                         ids=[c[0] for c in _E2E_STDERR_CASES])
def test_interpreter_gives_chinese_message(name, src):
    """前置条件：解释器这条路径必须已有中文诊断（ADR-37 §2.5 W104 第一步）。

    这条不需要 C 编译器。它守的是「AOT 去对齐的那个基准本身是合规的」——
    基准若退回 Python 英文原文，下面的 stderr 比对就毫无意义。
    """
    msg = _interpreter_message(src)
    for leak in ('index out of range', 'subscriptable', 'invalid literal',
                 'Traceback', 'KeyError', 'IndexError'):
        assert leak not in msg, (leak, msg)


@requires_cc
@pytest.mark.parametrize('name,src', _E2E_STDERR_CASES,
                         ids=[c[0] for c in _E2E_STDERR_CASES])
def test_aot_container_stderr_matches_interpreter(name, src, tmp_path):
    """AOT 二进制的报错**文案**与解释器逐字一致，退出码同为非 0。

    比较口径见模块 docstring：基准是解释器的 `ErrorInfo.message`，AOT 侧是
    `jk_fatal` 打的那一行（`"%s\\n"`）。
    """
    expected_message = _interpreter_message(src)
    exe = _build_exe(src, name, tmp_path)
    proc = subprocess.run([str(exe)], capture_output=True, text=True,
                          encoding='utf-8')
    assert proc.returncode != 0, '预期停机但产物正常退出'
    assert proc.stderr == expected_message + '\n', (
        f'AOT 与解释器报错文案不一致\n'
        f'AOT      ={proc.stderr!r}\n解释器={expected_message + chr(10)!r}')


@requires_cc
def test_aot_flushes_stdout_before_fatal(tmp_path):
    """停机前已打印的内容不能丢 —— `jk_fatal` 里的 fflush 守的就是这个。"""
    src = '打印 "前"。\n定义 赵表 = 【1】。\n打印 赵表[9]。\n'
    exe = _build_exe(src, 'flush_before_fatal', tmp_path)
    proc = subprocess.run([str(exe)], capture_output=True, text=True,
                          encoding='utf-8')
    assert proc.returncode != 0
    assert proc.stdout == '前\n', proc.stdout


@requires_cc
def test_runtime_rejects_container_dict_key_from_variable(tmp_path):
    """门禁只看字面量；变量里装着容器要靠运行时兜（文案对齐解释器）。"""
    src = ('定义 赵键 = 【1】。\n定义 赵d = {赵键：1}。\n打印 赵d。\n')
    ok, _ = _gate(src)
    assert ok, '这条形态门禁看不出来，应当放行到运行期'
    expected_message = _interpreter_message(src)
    exe = _build_exe(src, 'runtime_bad_key', tmp_path)
    proc = subprocess.run([str(exe)], capture_output=True, text=True,
                          encoding='utf-8')
    assert proc.returncode != 0
    assert proc.stderr == expected_message + '\n', (
        f'AOT ={proc.stderr!r}\n解释器={expected_message!r}')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
