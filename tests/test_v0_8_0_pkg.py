# -*- coding: utf-8 -*-
"""M8 · 包管理工具测试。

覆盖：semver 约束匹配、清单/锁文件读写、路径依赖解析与安装、
CLI 端到端流程、以及 module_loader 从 `极快_包/` 加载模块的接线。

只用路径依赖构造场景，避开网络与 git 依赖，测试可离线跑。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from jikuai.pkg import (
    Dependency, InstallError, LOCKFILE_NAME, LOCK_VERSION,
    Manifest, ManifestError, PACKAGES_DIR, ResolveError, SourceError,
    install, load_manifest, load_lockfile, new_manifest,
    packages_dir, save_manifest, validate_package_name,
)
from jikuai.pkg import semver
from jikuai.pkg.cli import run as pkg_run


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)


def _make_pkg(root, name, version='0.1.0', deps=None, entry_body=None):
    """在 root 下生成一个最小极快包（清单 + 入口文件）。"""
    manifest = {
        '名称': name, '版本': version, '入口': 'main.jk', '依赖': deps or {},
    }
    _write(os.path.join(root, '包.json'),
           json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    # 默认入口：一个合法的极快模块（百家姓前缀标识符 + 句号收尾）
    body = entry_body if entry_body is not None else (
        '函数 赵取名：\n  返回 "{}"。\n。\n导出 赵取名。\n'.format(name))
    _write(os.path.join(root, 'main.jk'), body)


class SemverTest(unittest.TestCase):
    def test_parse_and_compare(self):
        a = semver.parse_version('1.2.3')
        b = semver.parse_version('v1.2.3-rc.1')
        self.assertTrue(b < a)                 # 预发布 < 正式版
        self.assertEqual(a, semver.parse_version('1.2.3'))
        self.assertEqual(str(a), '1.2.3')

    def test_invalid_version(self):
        with self.assertRaises(semver.InvalidVersion):
            semver.parse_version('1.2')

    def test_caret_range(self):
        # ^1.2.3: [1.2.3, 2.0.0)
        self.assertTrue(semver.matches('1.2.3', '^1.2.3'))
        self.assertTrue(semver.matches('1.9.0', '^1.2.3'))
        self.assertFalse(semver.matches('2.0.0', '^1.2.3'))
        self.assertFalse(semver.matches('1.2.2', '^1.2.3'))

    def test_caret_leading_zero(self):
        # ^0.1.2: [0.1.2, 0.2.0) —— 主版本为 0 时锁定次版本
        self.assertTrue(semver.matches('0.1.2', '^0.1.2'))
        self.assertTrue(semver.matches('0.1.9', '^0.1.2'))
        self.assertFalse(semver.matches('0.2.0', '^0.1.2'))

    def test_tilde_range(self):
        self.assertTrue(semver.matches('1.2.3', '~1.2.3'))
        self.assertTrue(semver.matches('1.2.9', '~1.2.3'))
        self.assertFalse(semver.matches('1.3.0', '~1.2.3'))

    def test_intersection(self):
        c = '>=1.0.0, <2.0.0'
        self.assertTrue(semver.matches('1.5.0', c))
        self.assertFalse(semver.matches('2.0.0', c))
        self.assertFalse(semver.matches('0.9.0', c))

    def test_prerelease_not_implicit(self):
        # 范围约束默认不吞预发布版
        self.assertFalse(semver.matches('2.0.0-rc1', '^1.0.0'))
        self.assertFalse(semver.matches('2.0.0-rc1', '*'))
        # 显式提及预发布号才命中
        self.assertTrue(
            semver.matches('2.0.0-rc1', '>=2.0.0-alpha, <3.0.0'))

    def test_max_satisfying(self):
        versions = ['1.0.0', '1.2.3', '1.4.0', '2.0.0']
        self.assertEqual(str(semver.max_satisfying(versions, '^1.0.0')),
                         '1.4.0')
        self.assertIsNone(semver.max_satisfying(versions, '^3.0.0'))


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='jk-pkg-test-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_and_save_roundtrip(self):
        m = new_manifest('测试包')
        path = save_manifest(m, os.path.join(self.tmp, '包.json'))
        m2 = load_manifest(path)
        self.assertEqual(m2.name, '测试包')
        self.assertEqual(m2.version, '0.1.0')
        self.assertEqual(m2.entry, 'main.jk')

    def test_validate_package_name(self):
        validate_package_name('中文包')
        validate_package_name('mixed_name-1')
        for bad in ('', 'a/b', '..', '包 名', 'x' * 65, '分词'):
            with self.assertRaises(ManifestError, msg=bad):
                validate_package_name(bad)

    def test_load_missing_manifest(self):
        with self.assertRaises(ManifestError):
            load_manifest(self.tmp)

    def test_load_invalid_json(self):
        _write(os.path.join(self.tmp, '包.json'), '{ 不是合法 JSON')
        with self.assertRaises(ManifestError):
            load_manifest(self.tmp)

    def test_load_missing_required_field(self):
        _write(os.path.join(self.tmp, '包.json'),
               json.dumps({'版本': '0.1.0'}, ensure_ascii=False))
        with self.assertRaises(ManifestError):
            load_manifest(self.tmp)

    def test_dependency_specs(self):
        _make_pkg(self.tmp, '主包', deps={
            '甲': '^1.0.0',
            '乙': {'路径': './乙'},
            '丙': {'仓库': 'https://example/x.git', '标签': 'v1.0.0'},
        })
        m = load_manifest(self.tmp)
        deps = m.dependencies()
        self.assertEqual(deps['甲'].kind, '注册表')
        self.assertEqual(deps['甲'].constraint, '^1.0.0')
        self.assertEqual(deps['乙'].kind, '路径')
        self.assertEqual(deps['乙'].path, './乙')
        self.assertEqual(deps['丙'].kind, '仓库')
        self.assertEqual(deps['丙'].tag, 'v1.0.0')

    def test_dependency_invalid_constraint(self):
        _make_pkg(self.tmp, '主包', deps={'甲': '不是版本'})
        m = load_manifest(self.tmp)
        with self.assertRaises(ManifestError):
            m.dependencies()

    def test_find_manifest_from_subdir(self):
        _make_pkg(self.tmp, '根包')
        sub = os.path.join(self.tmp, 'a', 'b')
        os.makedirs(sub)
        old = os.getcwd()
        try:
            os.chdir(sub)
            m = load_manifest()          # 应向上找到
            self.assertEqual(m.name, '根包')
        finally:
            os.chdir(old)


class LockfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='jk-lock-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_when_missing(self):
        lock = load_lockfile(self.tmp)
        self.assertEqual(len(lock), 0)

    def test_version_mismatch_rejected(self):
        _write(os.path.join(self.tmp, LOCKFILE_NAME),
               json.dumps({'锁版本': LOCK_VERSION + 999, '包': []}))
        with self.assertRaises(Exception) as cm:
            load_lockfile(self.tmp)
        self.assertIn('锁文件版本', str(cm.exception))


class InstallTest(unittest.TestCase):
    """路径依赖的完整安装闭环。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='jk-install-')
        # 主包依赖两个本地包，其中乙又依赖丙——覆盖传递依赖
        self.root = os.path.join(self.tmp, '主包')
        _make_pkg(self.root, '主包', deps={
            '甲': {'路径': '../甲'}, '乙': {'路径': '../乙'},
        })
        _make_pkg(os.path.join(self.tmp, '甲'), '甲', version='1.0.0')
        _make_pkg(os.path.join(self.tmp, '乙'), '乙', version='0.2.0',
                  deps={'丙': {'路径': '../丙'}})
        _make_pkg(os.path.join(self.tmp, '丙'), '丙', version='0.0.1')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_install_path_deps_transitive(self):
        m = load_manifest(self.root)
        report = install(m)
        self.assertEqual(report.total, 3)
        base = packages_dir(self.root)
        for name in ('甲', '乙', '丙'):
            self.assertTrue(os.path.isfile(
                os.path.join(base, name, 'main.jk')),
                msg=f'{name} 应已安装')

    def test_lockfile_written_and_stable(self):
        m = load_manifest(self.root)
        install(m)
        lock_path = os.path.join(self.root, LOCKFILE_NAME)
        with open(lock_path, 'r', encoding='utf-8') as f:
            first = f.read()
        install(m)                       # 二次安装
        with open(lock_path, 'r', encoding='utf-8') as f:
            second = f.read()
        self.assertEqual(first, second, '锁文件应对可重现的输入保持字节相同')

    def test_prune_removes_stale(self):
        m = load_manifest(self.root)
        install(m)
        # 移除甲后再装，甲目录应被裁掉
        m.remove_dependency('甲')
        save_manifest(m)
        report = install(m)
        self.assertIn('甲', report.removed)
        self.assertFalse(os.path.isdir(
            os.path.join(packages_dir(self.root), '甲')))

    def test_circular_detected(self):
        # 让丙反过来依赖甲，构成 甲? 乙-丙-甲 的环（走乙分支）
        丙_manifest = os.path.join(self.tmp, '丙', '包.json')
        with open(丙_manifest, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data['依赖'] = {'乙': {'路径': '../乙'}}
        _write(丙_manifest, json.dumps(data, ensure_ascii=False, indent=2))
        m = load_manifest(self.root)
        with self.assertRaises(ResolveError) as cm:
            install(m)
        self.assertIn('循环依赖', str(cm.exception))

    def test_missing_path_dep(self):
        m = load_manifest(self.root)
        m.add_dependency(Dependency('丁', path='../不存在'))
        with self.assertRaises(ResolveError):
            install(m)


class ModuleLoaderIntegrationTest(unittest.TestCase):
    """接线校验：`导入 甲` 应能从 `极快_包/甲/main.jk` 加载。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='jk-loader-')
        self.root = os.path.join(self.tmp, '应用')
        _make_pkg(self.root, '应用', deps={'甲': {'路径': '../甲'}})
        _make_pkg(os.path.join(self.tmp, '甲'), '甲',
                  entry_body='函数 平方 接收 赵甲：\n  返回 赵甲乘赵甲。\n。\n'
                             '导出 平方。\n')
        m = load_manifest(self.root)
        install(m)
        # 主程序：从依赖包里 `导入 甲` 并调用
        _write(os.path.join(self.root, 'app.jk'),
               '导入 甲。\n打印 甲.平方(5)。\n')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_import_from_installed_package(self):
        # 用子进程避免污染全局 loader 的项目根缓存
        env = os.environ.copy()
        env['PYTHONPATH'] = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')
        result = subprocess.run(
            [sys.executable, '-m', 'jikuai',
             os.path.join(self.root, 'app.jk')],
            capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0,
                         msg=f'stderr={result.stderr!r}')
        self.assertIn('25', result.stdout)


class CliTest(unittest.TestCase):
    """通过 `pkg_run` 直接跑子命令，避开子进程开销。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='jk-cli-')
        self.old_cwd = os.getcwd()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_creates_manifest(self):
        rc = pkg_run(['初始化', '我的包'])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, '包.json')))

    def test_init_refuses_overwrite(self):
        pkg_run(['初始化', '我的包'])
        rc = pkg_run(['初始化', '再来一次'])
        self.assertEqual(rc, 1)

    def test_add_path_dependency(self):
        pkg_run(['初始化', '主'])
        dep_dir = os.path.join(self.tmp, '..', '外部')
        os.makedirs(dep_dir, exist_ok=True)
        _make_pkg(dep_dir, '外部')
        rc = pkg_run(['添加', '外部', '--路径', '../外部'])
        self.assertEqual(rc, 0)
        m = load_manifest(self.tmp)
        self.assertIn('外部', m.dependencies())
        # 清理外面那个目录，避免污染其他测试
        shutil.rmtree(dep_dir, ignore_errors=True)

    def test_help_returns_zero(self):
        self.assertEqual(pkg_run(['帮助']), 0)
        self.assertEqual(pkg_run([]), 0)

    def test_unknown_command_fails(self):
        self.assertEqual(pkg_run(['不存在的命令']), 1)


if __name__ == '__main__':
    unittest.main()
