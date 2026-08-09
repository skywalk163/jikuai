# -*- coding: utf-8 -*-
"""M9-2 · VS Code 调试集成 — 契约测试。

不启动 VS Code（那要图形界面），只做**契约级校验**：
`editors/vscode/package.json` 与 `extension.ts` 必须与 `jikuai_dap` 的
launch 协议保持一致，否则用户按 F5 会立刻失败。

Fixture 依赖：无网络、无子进程，只做静态文件读取与结构断言。
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
_VSCODE_DIR = os.path.join(_ROOT, 'editors', 'vscode')
_PKG_JSON = os.path.join(_VSCODE_DIR, 'package.json')
_EXTENSION_TS = os.path.join(_VSCODE_DIR, 'src', 'extension.ts')


@pytest.fixture(scope='module')
def package_json():
    with open(_PKG_JSON, 'r', encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture(scope='module')
def extension_ts():
    with open(_EXTENSION_TS, 'r', encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------------------
# package.json 契约
# ---------------------------------------------------------------------------

class TestPackageJsonDebuggerContribution:
    def test_declares_debuggers(self, package_json):
        contributes = package_json.get('contributes', {})
        debuggers = contributes.get('debuggers')
        assert debuggers, 'contributes.debuggers 必须存在'
        assert len(debuggers) == 1
        assert debuggers[0]['type'] == 'jikuai'

    def test_declares_breakpoints_for_language(self, package_json):
        # 没有这条 VS Code 不允许在 .jk 文件上打断点
        breakpoints = package_json.get('contributes', {}).get('breakpoints')
        assert breakpoints, 'contributes.breakpoints 必须存在'
        languages = {b.get('language') for b in breakpoints}
        assert 'jikuai' in languages

    def test_activation_event_debug_resolve(self, package_json):
        events = package_json.get('activationEvents', [])
        assert 'onDebugResolve:jikuai' in events, (
            'F5 首次调试时需要 onDebugResolve:jikuai 触发扩展激活')

    def test_debugger_declares_launch_program_required(self, package_json):
        d = package_json['contributes']['debuggers'][0]
        launch = d['configurationAttributes']['launch']
        assert 'program' in launch['required'], (
            'launch 必须要求 program 字段，否则 DAP adapter 会以 '
            '「launch 缺少 program 或 code」拒绝启动')

    def test_debugger_initial_configuration_uses_current_file(self, package_json):
        d = package_json['contributes']['debuggers'][0]
        inits = d.get('initialConfigurations', [])
        assert inits, 'initialConfigurations 至少给一个默认项'
        first = inits[0]
        assert first['type'] == 'jikuai'
        assert first['request'] == 'launch'
        assert first['program'] == '${file}'

    def test_debugger_category(self, package_json):
        # VS Code 应用商店按此分类展示
        assert 'Debuggers' in package_json.get('categories', [])


# ---------------------------------------------------------------------------
# extension.ts 契约（用正则检查关键调用，避免真的编译 TS）
# ---------------------------------------------------------------------------

class TestExtensionTs:
    def test_registers_debug_adapter_factory(self, extension_ts):
        assert 'registerDebugAdapterDescriptorFactory' in extension_ts, (
            '扩展必须注册 DebugAdapterDescriptorFactory，'
            '否则 launch 请求会被 VS Code 报「找不到调试适配器」')

    def test_registers_debug_configuration_provider(self, extension_ts):
        assert 'registerDebugConfigurationProvider' in extension_ts, (
            '缺少 ConfigurationProvider，用户直接 F5 会弹「未找到配置」')

    def test_spawns_jikuai_dap_module(self, extension_ts):
        # 命令必须走 -m jikuai_dap，不能改成 shell 字符串
        assert re.search(r"['\"]-m['\"],\s*['\"]jikuai_dap['\"]", extension_ts), (
            '必须以 `-m jikuai_dap` 数组形式启动，避免命令注入')

    def test_does_not_use_shell_true(self, extension_ts):
        # 保险检查：不允许出现 shell:true 之类的字样
        assert 'shell: true' not in extension_ts
        assert "shell: 'true'" not in extension_ts

    def test_debug_type_matches_package_json(self, extension_ts, package_json):
        d = package_json['contributes']['debuggers'][0]
        expected = d['type']
        # 找 const DEBUG_TYPE = '...'
        m = re.search(r"DEBUG_TYPE\s*=\s*['\"]([^'\"]+)['\"]", extension_ts)
        assert m and m.group(1) == expected


# ---------------------------------------------------------------------------
# DAP 后端可达性（不启动子进程，只 import 验证包结构完整）
# ---------------------------------------------------------------------------

class TestDapPackageReachable:
    def test_dap_package_layout(self):
        dap_dir = os.path.join(_ROOT, 'dap', 'jikuai_dap')
        assert os.path.isfile(os.path.join(dap_dir, '__init__.py'))
        assert os.path.isfile(os.path.join(dap_dir, '__main__.py'))
        assert os.path.isfile(os.path.join(dap_dir, 'adapter.py'))

    def test_dap_main_launches_adapter(self):
        # `python -m jikuai_dap` 必须走 adapter.main
        main_py = os.path.join(_ROOT, 'dap', 'jikuai_dap', '__main__.py')
        with open(main_py, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'from .adapter import main' in content
        assert 'sys.exit(main())' in content


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
