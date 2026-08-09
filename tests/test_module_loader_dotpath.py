# -*- coding: utf-8 -*-
"""点分路径模块解析测试（ADR-15 块生态 M1 W1）。

覆盖：
- 扁平模块名回归（4 条）
- 点分路径解析扁平文件（3 条）
- 点分路径解析目录+同名主文件（3 条）
- 点分路径解析 main.jk 兜底（2 条）
- 安全检查：`.a`、`a..b`、`a/b`、`a\\b` 都被拒（4 条）
- 循环导入在点分路径下仍能检测（1 条）
"""

import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.evaluator import Evaluator, JiKuaiError
from jikuai.module_loader import ModuleLoader


# ---------- 辅助函数 ----------

def _write(path, content=''):
    """在指定路径创建文件（自动创建父目录）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)


def _make_loader(search_dir):
    """创建一个 Evaluator 并把 _current_file 指向 search_dir 下的虚拟脚本。"""
    ev = Evaluator()
    # 在 search_dir 下放一个虚拟当前文件，使搜索路径包含 search_dir
    dummy = os.path.join(search_dir, '__dummy__.jk')
    _write(dummy, '')
    ev._current_file = dummy
    return ev.module_loader, dummy


# ---------- 扁平模块名回归（4 条）----------

def test_flat_resolve_existing():
    """扁平模块名解析已有 .jk 文件——回归基线。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        _write(os.path.join(tmp, '工具.jk'), '定义赵甲=1。\n导出 赵甲。\n')
        loader, dummy = _make_loader(tmp)
        result = loader.resolve('工具', dummy)
        assert result == os.path.abspath(os.path.join(tmp, '工具.jk'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_flat_resolve_not_found():
    """扁平模块名找不到时抛 JK-E5001。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        loader, dummy = _make_loader(tmp)
        try:
            loader.resolve('不存在', dummy)
            assert False, "应抛出 JiKuaiError"
        except JiKuaiError as e:
            assert 'JK-E5001' in str(e)
            assert '不存在' in str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_flat_resolve_priority():
    """扁平模块名搜索路径优先级：脚本目录优先于 stdlib。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        # 在脚本目录放一个与 stdlib 同名的模块
        _write(os.path.join(tmp, '工具.jk'), '定义赵甲=99。\n导出 赵甲。\n')
        loader, dummy = _make_loader(tmp)
        result = loader.resolve('工具', dummy)
        # 应当解析到 tmp 下的版本，而非 stdlib/工具.jk
        assert os.path.dirname(result) == os.path.abspath(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_flat_resolve_no_dot_in_name():
    """不含点的模块名不触发点分路径逻辑。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        _write(os.path.join(tmp, '简单模块.jk'), '定义赵甲=1。\n导出 赵甲。\n')
        loader, dummy = _make_loader(tmp)
        result = loader.resolve('简单模块', dummy)
        assert result.endswith('简单模块.jk')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 点分路径解析扁平文件（3 条）----------

def test_dotpath_flat_file_two_segments():
    """两段点分路径：`blocks.读取文件` → blocks/读取文件.jk。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        target = os.path.join(tmp, 'blocks', '读取文件.jk')
        _write(target, '定义赵甲=1。\n导出 赵甲。\n')
        loader, dummy = _make_loader(tmp)
        result = loader.resolve('blocks.读取文件', dummy)
        assert result == os.path.abspath(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dotpath_flat_file_three_segments():
    """三段点分路径：`blocks.数据.读取文件` → blocks/数据/读取文件.jk。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        target = os.path.join(tmp, 'blocks', '数据', '读取文件.jk')
        _write(target, '定义赵甲=1。\n导出 赵甲。\n')
        loader, dummy = _make_loader(tmp)
        result = loader.resolve('blocks.数据.读取文件', dummy)
        assert result == os.path.abspath(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dotpath_flat_file_four_segments():
    """四段点分路径：`a.b.c.d` → a/b/c/d.jk。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        target = os.path.join(tmp, 'a', 'b', 'c', 'd.jk')
        _write(target, '定义赵甲=1。\n导出 赵甲。\n')
        loader, dummy = _make_loader(tmp)
        result = loader.resolve('a.b.c.d', dummy)
        assert result == os.path.abspath(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 点分路径解析目录+同名主文件（3 条）----------

def test_dotpath_dir_samename_two_segments():
    """两段点分路径目录形式：`blocks.读取文件` → blocks/读取文件/读取文件.jk。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        target = os.path.join(tmp, 'blocks', '读取文件', '读取文件.jk')
        _write(target, '定义赵甲=1。\n导出 赵甲。\n')
        loader, dummy = _make_loader(tmp)
        result = loader.resolve('blocks.读取文件', dummy)
        assert result == os.path.abspath(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dotpath_dir_samename_three_segments():
    """三段点分路径目录形式：`blocks.数据.读取文件` → blocks/数据/读取文件/读取文件.jk。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        target = os.path.join(tmp, 'blocks', '数据', '读取文件', '读取文件.jk')
        _write(target, '定义赵甲=1。\n导出 赵甲。\n')
        loader, dummy = _make_loader(tmp)
        result = loader.resolve('blocks.数据.读取文件', dummy)
        assert result == os.path.abspath(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dotpath_dir_samename_priority_over_main():
    """同名主文件优先于 main.jk。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        # 同时放同名主文件和 main.jk，同名主文件应优先
        samename = os.path.join(tmp, 'blocks', '读取文件', '读取文件.jk')
        mainjk = os.path.join(tmp, 'blocks', '读取文件', 'main.jk')
        _write(samename, '定义赵甲=1。\n导出 赵甲。\n')
        _write(mainjk, '定义赵甲=2。\n导出 赵甲。\n')
        loader, dummy = _make_loader(tmp)
        result = loader.resolve('blocks.读取文件', dummy)
        assert result == os.path.abspath(samename)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 点分路径解析 main.jk 兜底（2 条）----------

def test_dotpath_main_fallback_two_segments():
    """两段点分路径 main.jk 兜底：`blocks.读取文件` → blocks/读取文件/main.jk。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        target = os.path.join(tmp, 'blocks', '读取文件', 'main.jk')
        _write(target, '定义赵甲=1。\n导出 赵甲。\n')
        loader, dummy = _make_loader(tmp)
        result = loader.resolve('blocks.读取文件', dummy)
        assert result == os.path.abspath(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dotpath_main_fallback_three_segments():
    """三段点分路径 main.jk 兜底：`blocks.数据.读取文件` → blocks/数据/读取文件/main.jk。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        target = os.path.join(tmp, 'blocks', '数据', '读取文件', 'main.jk')
        _write(target, '定义赵甲=1。\n导出 赵甲。\n')
        loader, dummy = _make_loader(tmp)
        result = loader.resolve('blocks.数据.读取文件', dummy)
        assert result == os.path.abspath(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 安全检查（4 条）----------

def test_security_reject_leading_dot():
    """以 `.` 开头的模块名被拒绝。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        loader, dummy = _make_loader(tmp)
        try:
            loader.resolve('.hidden', dummy)
            assert False, "应抛出 JiKuaiError"
        except JiKuaiError as e:
            assert '非法模块名' in str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_security_reject_double_dot():
    """含 `..` 的模块名被拒绝（如 `a..b`）。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        loader, dummy = _make_loader(tmp)
        try:
            loader.resolve('a..b', dummy)
            assert False, "应抛出 JiKuaiError"
        except JiKuaiError as e:
            assert '非法模块名' in str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_security_reject_slash():
    """含 `/` 的模块名被拒绝。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        loader, dummy = _make_loader(tmp)
        try:
            loader.resolve('a/b', dummy)
            assert False, "应抛出 JiKuaiError"
        except JiKuaiError as e:
            assert '非法模块名' in str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_security_reject_backslash():
    """含 `\\` 的模块名被拒绝。"""
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        loader, dummy = _make_loader(tmp)
        try:
            loader.resolve('a\\b', dummy)
            assert False, "应抛出 JiKuaiError"
        except JiKuaiError as e:
            assert '非法模块名' in str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 循环导入在点分路径下仍能检测（1 条）----------

def test_circular_import_dotpath():
    """点分路径下循环导入仍能检测到。

    验证方式：用两个点分路径指向同一文件，第二次 load 时应命中
    加载栈中的相同绝对路径而抛出循环导入错误。
    注：当前 parser 尚不支持词法级点分导入语法，故此处直接调用
    loader.load() 来模拟 evaluator 在加载过程中再次触发点分导入的场景。
    """
    tmp = tempfile.mkdtemp(prefix='jk-dotpath-')
    try:
        # 构造 pkg/甲.jk（内容无 import，但我们手动模拟循环）
        pkg_dir = os.path.join(tmp, 'pkg')
        os.makedirs(pkg_dir)
        _write(os.path.join(pkg_dir, '甲.jk'), '定义赵甲=1。\n')

        trigger = os.path.join(tmp, 'trigger.jk')
        _write(trigger, '')

        ev = Evaluator()
        ev._current_file = trigger
        loader = ev.module_loader

        # 手动把 pkg/甲.jk 的绝对路径塞入加载栈，模拟正在加载该模块
        target_path = loader.resolve('pkg.甲', trigger)
        loader._loading.append(target_path)
        try:
            # 再次 load 同一点分路径，应检测到循环
            loader.load('pkg.甲', trigger)
            assert False, "应抛出循环导入错误"
        except JiKuaiError as e:
            assert '循环导入' in str(e)
        finally:
            loader._loading.pop()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
