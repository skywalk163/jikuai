# -*- coding: utf-8 -*-
"""M11-1 · 本地注册表与发布（借鉴 duanpub 双层索引设计）。

覆盖：
- 双层索引结构（主索引路由 + 分类分片）
- 发布默认演练、显式确认才落盘
- 拒绝静默覆盖已发布版本
- 发布前体检（入口缺失、路径依赖阻断）
- 版本选择（满足约束的最高版本）
- 搜索、列表、撤回
- 注册表来源安装端到端
- 安全：包名/分类白名单挡住路径穿越
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from jikuai.pkg import registry
from jikuai.pkg.manifest import (
    MANIFEST_NAME, Manifest, ManifestError, load_manifest, save_manifest,
)


# ---------------------------------------------------------------------------
# 夹具：临时注册表 + 造包助手
# ---------------------------------------------------------------------------

@pytest.fixture
def reg_root(tmp_path, monkeypatch):
    """把注册表根指向临时目录，避免污染开发者的 ~/.jikuai。"""
    root = tmp_path / '注册表'
    monkeypatch.setenv('JIKUAI_REGISTRY', str(root))
    return str(root)


def make_pkg(base, name, version='1.0.0', description='测试包',
             deps=None, entry='main.jk', with_readme=True,
             write_entry=True, category=None):
    """在 base 下造一个可发布的包目录，返回它的 Manifest。"""
    root = os.path.join(str(base), name)
    os.makedirs(root, exist_ok=True)
    data = {
        '名称': name,
        '版本': version,
        '描述': description,
        '入口': entry,
        '依赖': deps or {},
    }
    if category:
        data['分类'] = category
    manifest = Manifest(data, path=os.path.join(root, MANIFEST_NAME))
    save_manifest(manifest)
    if write_entry:
        with open(os.path.join(root, entry), 'w', encoding='utf-8') as f:
            f.write('打印 "你好"。\n')
    if with_readme:
        with open(os.path.join(root, 'README.md'), 'w', encoding='utf-8') as f:
            f.write(f'# {name}\n')
    return load_manifest(root)


# ---------------------------------------------------------------------------
# 演练与确认
# ---------------------------------------------------------------------------

class TestPublishDryRun:
    def test_dry_run_is_default(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包')
        report = registry.publish(m)
        assert report.dry_run is True
        assert report.checksum
        assert report.file_count >= 2          # main.jk + 包.json + README
        # 演练不落盘：注册表目录不该出现
        assert not os.path.exists(os.path.join(reg_root, registry.INDEX_NAME))

    def test_confirm_writes_index_and_snapshot(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包', version='1.2.3')
        report = registry.publish(m, dry_run=False)
        assert report.dry_run is False
        index_path = os.path.join(reg_root, registry.INDEX_NAME)
        assert os.path.isfile(index_path)
        with open(index_path, encoding='utf-8') as f:
            index = json.load(f)
        assert index['索引']['甲包']['最新版本'] == '1.2.3'
        assert index['统计'] == {'总包数': 1, '总版本数': 1}
        # 快照目录里必须有清单
        assert os.path.isfile(os.path.join(report.target, MANIFEST_NAME))

    def test_dry_run_leaves_no_staging_dir(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包')
        report = registry.publish(m)
        assert not os.path.isdir(report.target + '.staging')


# ---------------------------------------------------------------------------
# 双层索引结构
# ---------------------------------------------------------------------------

class TestDualLayerIndex:
    def test_main_index_only_holds_routing(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包', description='一句话说明',
                     category='工具')
        registry.publish(m, dry_run=False)
        index = registry.load_index()
        entry = index['索引']['甲包']
        # 主索引只有路由字段，描述这类详情必须在分片里
        assert entry['文件'] == f'{registry.CATEGORY_DIR}/工具.json'
        assert '描述' not in entry
        assert entry['分类'] == '工具'

    def test_shard_holds_detail(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包', description='一句话说明',
                     category='工具')
        registry.publish(m, dry_run=False)
        shard_path = os.path.join(reg_root, registry.CATEGORY_DIR, '工具.json')
        with open(shard_path, encoding='utf-8') as f:
            shard = json.load(f)
        detail = shard['甲包']['1.0.0']
        assert detail['描述'] == '一句话说明'
        assert detail['校验和']
        assert detail['快照'].startswith(f'{registry.PACKAGE_DIR}/甲包/')

    def test_multiple_versions_accumulate(self, tmp_path, reg_root):
        registry.publish(make_pkg(tmp_path / 'a', '甲包', version='1.0.0'),
                         dry_run=False)
        registry.publish(make_pkg(tmp_path / 'b', '甲包', version='1.2.0'),
                         dry_run=False)
        registry.publish(make_pkg(tmp_path / 'c', '甲包', version='1.1.0'),
                         dry_run=False)
        entry = registry.load_index()['索引']['甲包']
        assert entry['版本'] == ['1.0.0', '1.1.0', '1.2.0']   # semver 排序
        assert entry['最新版本'] == '1.2.0'
        assert registry.load_index()['统计']['总版本数'] == 3


# ---------------------------------------------------------------------------
# 覆盖保护
# ---------------------------------------------------------------------------

class TestOverwriteProtection:
    def test_republish_same_version_rejected(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包', version='1.0.0')
        registry.publish(m, dry_run=False)
        with pytest.raises(registry.RegistryError) as e:
            registry.publish(m, dry_run=False)
        assert '不允许静默覆盖' in str(e.value)

    def test_explicit_overwrite_allowed(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包', version='1.0.0')
        registry.publish(m, dry_run=False)
        report = registry.publish(m, dry_run=False, allow_overwrite=True)
        assert report.overwritten is True
        # 覆盖后版本列表不该出现重复
        assert registry.load_index()['索引']['甲包']['版本'] == ['1.0.0']


# ---------------------------------------------------------------------------
# 发布前体检
# ---------------------------------------------------------------------------

class TestPublishChecklist:
    def test_missing_entry_file_is_hard_error(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包', write_entry=False)
        with pytest.raises(registry.RegistryError) as e:
            registry.publish(m)
        assert '入口' in str(e.value)

    def test_path_dependency_blocks_publish(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包', deps={'乙包': {'路径': '../乙包'}})
        with pytest.raises(registry.RegistryError) as e:
            registry.publish(m)
        assert '本地路径来源' in str(e.value)

    def test_missing_description_warns_only(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包', description='')
        report = registry.publish(m)
        assert any('描述' in w for w in report.warnings)

    def test_missing_readme_warns_only(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包', with_readme=False)
        report = registry.publish(m)
        assert any('README' in w for w in report.warnings)


# ---------------------------------------------------------------------------
# 选版
# ---------------------------------------------------------------------------

class TestLookup:
    def test_picks_highest_satisfying_version(self, tmp_path, reg_root):
        for v in ('1.0.0', '1.1.0', '1.2.0', '2.0.0'):
            registry.publish(make_pkg(tmp_path / v, '甲包', version=v),
                             dry_run=False)
        version, snapshot = registry.lookup('甲包', '^1.0.0')
        assert version == '1.2.0'          # ^1 不跨大版本
        assert os.path.isdir(snapshot)

    def test_star_constraint_picks_latest(self, tmp_path, reg_root):
        for v in ('1.0.0', '2.3.4'):
            registry.publish(make_pkg(tmp_path / v, '甲包', version=v),
                             dry_run=False)
        assert registry.lookup('甲包', '*')[0] == '2.3.4'
        assert registry.lookup('甲包')[0] == '2.3.4'

    def test_unknown_package_error_mentions_root(self, tmp_path, reg_root):
        with pytest.raises(registry.RegistryError) as e:
            registry.lookup('不存在的包')
        assert '注册表里没有包' in str(e.value)

    def test_unsatisfiable_constraint_lists_available(self, tmp_path, reg_root):
        registry.publish(make_pkg(tmp_path, '甲包', version='1.0.0'),
                         dry_run=False)
        with pytest.raises(registry.RegistryError) as e:
            registry.lookup('甲包', '^9.0.0')
        msg = str(e.value)
        assert '没有满足' in msg and '1.0.0' in msg   # 报出实际可用版本

    def test_corrupt_registry_missing_snapshot(self, tmp_path, reg_root):
        import shutil
        registry.publish(make_pkg(tmp_path, '甲包', version='1.0.0'),
                         dry_run=False)
        shutil.rmtree(os.path.join(reg_root, registry.PACKAGE_DIR, '甲包'))
        with pytest.raises(registry.RegistryError) as e:
            registry.lookup('甲包')
        assert '快照目录缺失' in str(e.value)


# ---------------------------------------------------------------------------
# 搜索 / 列表 / 撤回
# ---------------------------------------------------------------------------

class TestSearchAndList:
    def test_search_by_name_and_description(self, tmp_path, reg_root):
        registry.publish(make_pkg(tmp_path / 'a', '分词助手',
                                  description='中文分词工具'), dry_run=False)
        registry.publish(make_pkg(tmp_path / 'b', '历法转换',
                                  description='农历公历互转'), dry_run=False)
        assert [r['名称'] for r in registry.search('分词')] == ['分词助手']
        assert [r['名称'] for r in registry.search('农历')] == ['历法转换']
        assert len(registry.search('')) == 2

    def test_search_empty_registry(self, tmp_path, reg_root):
        assert registry.search('任意') == []

    def test_list_packages(self, tmp_path, reg_root):
        registry.publish(make_pkg(tmp_path / 'a', '甲包'), dry_run=False)
        registry.publish(make_pkg(tmp_path / 'b', '乙包'), dry_run=False)
        assert sorted(registry.list_packages()) == ['乙包', '甲包']

    def test_unpublish_one_version(self, tmp_path, reg_root):
        for v in ('1.0.0', '1.1.0'):
            registry.publish(make_pkg(tmp_path / v, '甲包', version=v),
                             dry_run=False)
        assert registry.unpublish('甲包', '1.0.0') == ['1.0.0']
        entry = registry.load_index()['索引']['甲包']
        assert entry['版本'] == ['1.1.0']
        assert entry['最新版本'] == '1.1.0'

    def test_unpublish_all_versions_drops_entry(self, tmp_path, reg_root):
        registry.publish(make_pkg(tmp_path, '甲包'), dry_run=False)
        registry.unpublish('甲包')
        assert '甲包' not in registry.load_index()['索引']

    def test_unpublish_unknown_raises(self, tmp_path, reg_root):
        with pytest.raises(registry.RegistryError):
            registry.unpublish('不存在')


# ---------------------------------------------------------------------------
# 注册表来源安装（端到端）
# ---------------------------------------------------------------------------

class TestInstallFromRegistry:
    def test_install_registry_dependency(self, tmp_path, reg_root):
        from jikuai.pkg import install, load_manifest as lm
        registry.publish(make_pkg(tmp_path / 'src', '分词助手',
                                  version='1.0.0'), dry_run=False)
        app = make_pkg(tmp_path / 'app', '我的应用',
                       deps={'分词助手': '^1.0.0'})
        report = install(app)
        assert ('分词助手', '1.0.0') in report.installed
        installed_entry = os.path.join(
            app.root, '极快_包', '分词助手', MANIFEST_NAME)
        assert os.path.isfile(installed_entry)

    def test_install_unsatisfiable_gives_chinese_error(self, tmp_path, reg_root):
        from jikuai.pkg import install, ResolveError
        registry.publish(make_pkg(tmp_path / 'src', '分词助手',
                                  version='1.0.0'), dry_run=False)
        app = make_pkg(tmp_path / 'app', '我的应用',
                       deps={'分词助手': '^2.0.0'})
        with pytest.raises(ResolveError) as e:
            install(app)
        assert '没有满足' in str(e.value)


# ---------------------------------------------------------------------------
# 安全：包名 / 分类白名单挡住路径穿越
# ---------------------------------------------------------------------------

class TestSecurity:
    @pytest.mark.parametrize('bad', [
        '../逃逸', '甲/乙', '甲\\乙', '甲.乙', '', 'a' * 65,
    ])
    def test_bad_package_name_rejected_in_lookup(self, bad, reg_root):
        with pytest.raises((registry.RegistryError, ManifestError)):
            registry.lookup(bad)

    def test_category_whitelist(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包', category='../逃逸')
        with pytest.raises(registry.RegistryError) as e:
            registry.publish(m)
        assert '不在白名单内' in str(e.value)

    def test_unknown_category_rejected(self, tmp_path, reg_root):
        m = make_pkg(tmp_path, '甲包', category='我瞎编的分类')
        with pytest.raises(registry.RegistryError):
            registry.publish(m)

    def test_installed_deps_not_included_in_snapshot(self, tmp_path, reg_root):
        """极快_包/ 不该进快照，否则依赖套娃。"""
        m = make_pkg(tmp_path, '甲包')
        nested = os.path.join(m.root, '极快_包', '别的包')
        os.makedirs(nested)
        with open(os.path.join(nested, '混入.jk'), 'w', encoding='utf-8') as f:
            f.write('打印 "不该出现"。\n')
        registry.publish(m, dry_run=False)
        _v, snapshot = registry.lookup('甲包')
        assert not os.path.exists(os.path.join(snapshot, '极快_包'))


# ---------------------------------------------------------------------------
# 索引格式版本
# ---------------------------------------------------------------------------

class TestIndexFormat:
    def test_future_format_version_rejected(self, tmp_path, reg_root):
        os.makedirs(reg_root, exist_ok=True)
        with open(os.path.join(reg_root, registry.INDEX_NAME), 'w',
                  encoding='utf-8') as f:
            json.dump({'格式版本': 999, '索引': {}}, f, ensure_ascii=False)
        with pytest.raises(registry.RegistryError) as e:
            registry.load_index()
        assert '高于本工具支持' in str(e.value)

    def test_corrupt_json_gives_chinese_error(self, tmp_path, reg_root):
        os.makedirs(reg_root, exist_ok=True)
        with open(os.path.join(reg_root, registry.INDEX_NAME), 'w',
                  encoding='utf-8') as f:
            f.write('{ 这不是 JSON')
        with pytest.raises(registry.RegistryError) as e:
            registry.load_index()
        assert '不是合法 JSON' in str(e.value)

    def test_empty_registry_returns_empty_index(self, reg_root):
        index = registry.load_index()
        assert index['索引'] == {}
        assert index['统计']['总包数'] == 0
