# -*- coding: utf-8 -*-
"""AOT 产物权限位回归测试（CI 首跑暴露的 Errno 13）。

背景
----
`driver._atomic_copy` 原来是 `mkstemp` + `shutil.copyfile` + `os.replace`。
三个动作各自都对，合起来有坑：

- `tempfile.mkstemp` 在 POSIX 上刻意用 mode **0600** 建文件（防竞态泄露）；
- `shutil.copyfile` 只搬**内容**，不搬 mode；
- `os.replace` 把这个 0600 的临时文件顶到目标路径。

结果：gcc 明明产出了 0755 的可执行文件，落到用户指定的 `-o` 路径后变成
0600，Linux 上一执行就是 `PermissionError: [Errno 13]`。

Windows 靠扩展名判断可执行性，所以本机开发全程没暴露；CI（ubuntu）首次真跑
AOT 端到端用例时，30 条 e2e 全部死在这一行。修法是补一句 `shutil.copymode`。

本测试**不需要 C 编译器**：直接拿一个手工 chmod 过的假产物驱动 `_atomic_copy`，
所以在 Windows 与 Linux 都会实跑，不会像 e2e 那样 skip 掉。
"""

import os
import stat
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'aot'))

import pytest

from jikuai_aot.driver import _atomic_copy


_POSIX_ONLY = pytest.mark.skipif(
    os.name == 'nt', reason='Windows 不用 POSIX 权限位判断可执行性')


def _make_fake_binary(path, mode=0o755):
    with open(path, 'wb') as f:
        f.write(b'\x7fELF fake binary payload')
    os.chmod(path, mode)
    return path


class TestAtomicCopyPreservesMode:
    def test_content_is_copied(self, tmp_path):
        src = _make_fake_binary(str(tmp_path / 'src.bin'))
        dst = str(tmp_path / 'out.bin')
        _atomic_copy(src, dst)
        with open(dst, 'rb') as f:
            assert f.read() == b'\x7fELF fake binary payload'

    @_POSIX_ONLY
    def test_exec_bit_survives(self, tmp_path):
        """核心断言：源可执行 → 目标必须可执行。"""
        src = _make_fake_binary(str(tmp_path / 'src.bin'), 0o755)
        dst = str(tmp_path / 'out.bin')
        _atomic_copy(src, dst)
        assert os.access(dst, os.X_OK), (
            f'产物丢了可执行位：mode={oct(os.stat(dst).st_mode & 0o777)}')

    @_POSIX_ONLY
    def test_mode_matches_source_exactly(self, tmp_path):
        src = _make_fake_binary(str(tmp_path / 'src.bin'), 0o755)
        dst = str(tmp_path / 'out.bin')
        _atomic_copy(src, dst)
        src_mode = stat.S_IMODE(os.stat(src).st_mode)
        dst_mode = stat.S_IMODE(os.stat(dst).st_mode)
        assert dst_mode == src_mode, (
            f'mode 不一致：源 {oct(src_mode)}，目标 {oct(dst_mode)}')

    @_POSIX_ONLY
    def test_non_executable_source_stays_non_executable(self, tmp_path):
        """反向：源不可执行时不该凭空加上执行位。"""
        src = _make_fake_binary(str(tmp_path / 'src.bin'), 0o644)
        dst = str(tmp_path / 'out.bin')
        _atomic_copy(src, dst)
        assert not os.access(dst, os.X_OK)

    def test_overwrite_existing_target(self, tmp_path):
        """重复构建：已存在的旧产物应被原子替换，且权限仍正确。"""
        src = _make_fake_binary(str(tmp_path / 'src.bin'), 0o755)
        dst = str(tmp_path / 'out.bin')
        with open(dst, 'wb') as f:
            f.write(b'stale')
        _atomic_copy(src, dst)
        with open(dst, 'rb') as f:
            assert f.read() != b'stale'
        if os.name != 'nt':
            assert os.access(dst, os.X_OK)

    def test_creates_missing_parent_dir(self, tmp_path):
        src = _make_fake_binary(str(tmp_path / 'src.bin'))
        dst = str(tmp_path / '深' / '层' / 'out.bin')
        _atomic_copy(src, dst)
        assert os.path.isfile(dst)

    def test_no_temp_leftovers(self, tmp_path):
        """成功路径不应留下 .jkaot_*.tmp 残渣。"""
        src = _make_fake_binary(str(tmp_path / 'src.bin'))
        dst = str(tmp_path / 'out.bin')
        _atomic_copy(src, dst)
        leftovers = [n for n in os.listdir(str(tmp_path))
                     if n.startswith('.jkaot_')]
        assert leftovers == [], leftovers
