# -*- coding: utf-8 -*-
"""v0.12.0 · ADR-15 · 块 CLI 与词法原子性校验测试。

被测模块：
- `jikuai.pkg.blocks`（`check_export_atomicity` / `extract_exports` / `validate_block`）
- `jikuai.pkg.blocks_cli`（`列表` / `查找` / `详情` / `校验` / `索引`）

覆盖（共 23 条）：
- check_export_atomicity 原子名通过（3 条：汇总/合计/聚合）
- check_export_atomicity 非原子名被拒（4 条：块求和/累加/赵列表/数列总和）
- check_module_segment_atomicity 目录名判定（4 条：求和/汇总 通过，配置加载/累加 失败）
- extract_exports 正确提取（2 条）
- validate_block 对合法块通过（1 条，用真实的 stdlib/blocks/数据/求和）
- validate_block 对非原子导出名报错（1 条，tmp_path 构造）
- validate_block 对非原子块目录名报错（2 条，tmp_path 构造 + 真实块回归）
- validate_block 对非原子依赖块名只给警告（1 条）
- validate_block 对依赖块不一致报错（1 条）
- CLI 列表/查找/详情/校验/索引 各 1 条（capsys 捕获输出，断言返回码）
"""

import json
import os
import shutil
import tempfile
import unittest

from jikuai.pkg import blocks
from jikuai.pkg.blocks import (
    BLOCK_METADATA_NAME,
    check_export_atomicity, check_module_segment_atomicity,
    extract_exports, validate_block,
)
from jikuai.pkg import blocks_cli


def _blocks_root():
    """内置 stdlib/blocks/ 的绝对路径。"""
    return blocks.blocks_root()


def _write_block(dir_path, data, jk_content='', test_jk=True):
    """在 dir_path 下写完整块：块.json + <块名>.jk [+ 测试.jk]。"""
    os.makedirs(dir_path, exist_ok=True)
    meta_path = os.path.join(dir_path, BLOCK_METADATA_NAME)
    with open(meta_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    name = data.get('名称', 'main')
    jk_path = os.path.join(dir_path, name + '.jk')
    with open(jk_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(jk_content)
    if test_jk:
        test_path = os.path.join(dir_path, '测试.jk')
        with open(test_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('-- 测试\n')
    return dir_path


def _minimal_data(**overrides):
    data = {
        '名称': '测试块',
        '版本': '0.1.0',
        '层级': 0,
        '领域': ['数据'],
        '描述': '测试用块',
    }
    data.update(overrides)
    return data


class _TmpBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))


# ---------------------------------------------------------------------------
# check_export_atomicity 原子名通过（3 条）
# ---------------------------------------------------------------------------

class ExportAtomicityPassTest(unittest.TestCase):
    def test_atomic_huizong(self):
        """「汇总」是词法原子——整体被识别为单个 IDENT。"""
        atomic, pieces = check_export_atomicity('汇总')
        self.assertTrue(atomic)
        self.assertEqual(len(pieces), 1)
        self.assertEqual(pieces[0], ('IDENT', '汇总'))

    def test_atomic_heji(self):
        """「合计」是词法原子。"""
        atomic, _ = check_export_atomicity('合计')
        self.assertTrue(atomic)

    def test_atomic_juhe(self):
        """「聚合」是词法原子。"""
        atomic, _ = check_export_atomicity('聚合')
        self.assertTrue(atomic)


# ---------------------------------------------------------------------------
# check_export_atomicity 非原子名被拒（4 条）
# ---------------------------------------------------------------------------

class ExportAtomicityRejectTest(unittest.TestCase):
    def test_non_atomic_kuaiqiuhe(self):
        """「块求和」切成 块(IDENT)+求和(VERB)——非原子。"""
        atomic, pieces = check_export_atomicity('块求和')
        self.assertFalse(atomic)
        self.assertGreater(len(pieces), 1)

    def test_non_atomic_leijia(self):
        """「累加」切成 累(IDENT)+加(VERB)——非原子。"""
        atomic, pieces = check_export_atomicity('累加')
        self.assertFalse(atomic)
        self.assertGreater(len(pieces), 1)

    def test_non_atomic_zhaoliebiao(self):
        """「赵列表」被切碎——非原子。"""
        atomic, pieces = check_export_atomicity('赵列表')
        self.assertFalse(atomic)
        self.assertGreater(len(pieces), 1)

    def test_non_atomic_shuliezonghe(self):
        """「数列总和」被切碎——非原子。"""
        atomic, pieces = check_export_atomicity('数列总和')
        self.assertFalse(atomic)
        self.assertGreater(len(pieces), 1)


# ---------------------------------------------------------------------------
# check_module_segment_atomicity 目录名/路径段判定（4 条）
# ---------------------------------------------------------------------------

class ModuleSegmentAtomicityPassTest(unittest.TestCase):
    def test_segment_qiuhe(self):
        """「求和」是单个 VERB token——作为目录名合法。"""
        atomic, pieces = check_module_segment_atomicity('求和')
        self.assertTrue(atomic)
        self.assertEqual(len(pieces), 1)
        self.assertEqual(pieces[0][0], 'VERB')

    def test_segment_huizong(self):
        """「汇总」是单个 IDENT token——作为目录名合法。"""
        atomic, pieces = check_module_segment_atomicity('汇总')
        self.assertTrue(atomic)
        self.assertEqual(len(pieces), 1)

class ModuleSegmentAtomicityRejectTest(unittest.TestCase):
    def test_segment_peizhijiazai(self):
        """「配置加载」切成多个 token——不合法，是死块。"""
        atomic, pieces = check_module_segment_atomicity('配置加载')
        self.assertFalse(atomic)
        self.assertGreater(len(pieces), 1)

    def test_segment_leijia(self):
        """「累加」切成 累(IDENT)+加(VERB)——不合法。"""
        atomic, pieces = check_module_segment_atomicity('累加')
        self.assertFalse(atomic)
        self.assertGreater(len(pieces), 1)


# ---------------------------------------------------------------------------
# extract_exports 正确提取（2 条）
# ---------------------------------------------------------------------------

class ExtractExportsTest(_TmpBase):
    def test_single_export(self):
        """单行 `导出 汇总。` 应只提取出「汇总」。"""
        path = os.path.join(self.tmp, 'test.jk')
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('函数 汇总 接收 赵数值：\n  返回 求和 赵数值。\n。\n\n导出 汇总。\n')
        names = extract_exports(path)
        self.assertEqual(names, {'汇总'})

    def test_multiple_exports(self):
        """多个导出：`导出 甲，乙。` 应提取出两个名字。"""
        path = os.path.join(self.tmp, 'test.jk')
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('导出 甲，乙。\n')
        names = extract_exports(path)
        self.assertEqual(names, {'甲', '乙'})


# ---------------------------------------------------------------------------
# validate_block 对合法块通过（1 条，用真实的 stdlib/blocks/数据/求和）
# ---------------------------------------------------------------------------

class ValidateBlockRealTest(unittest.TestCase):
    def test_real_qiuhe_block(self):
        """真实的 `stdlib/blocks/数据/求和` 应通过全面校验（零错误）。"""
        block_dir = os.path.join(_blocks_root(), '数据', '求和')
        if not os.path.isdir(block_dir):
            self.skipTest('stdlib/blocks/数据/求和 不存在')
        errors, warnings = validate_block(block_dir)
        self.assertEqual(errors, [], f'不应有错误，得到：{errors}')


# ---------------------------------------------------------------------------
# validate_block 对非原子导出名报错（1 条）
# ---------------------------------------------------------------------------

class ValidateBlockAtomicityErrorTest(_TmpBase):
    def test_non_atomic_export_error(self):
        """导出名为「块求和」（非原子）时 validate_block 应报错。"""
        data = _minimal_data(名称='坏块')
        jk = '函数 块求和 接收 赵数值：\n  返回 求和 赵数值。\n。\n\n导出 块求和。\n'
        block_dir = _write_block(os.path.join(self.tmp, '坏块'), data, jk)
        errors, _ = validate_block(block_dir)
        self.assertTrue(any('非词法原子' in e for e in errors),
                        f'应检测到非原子导出名，得到：{errors}')


# ---------------------------------------------------------------------------
# validate_block 对非原子块目录名报错（2 条：tmp 构造 + 真实块回归）
# ---------------------------------------------------------------------------

class ValidateBlockDirNameAtomicityTest(_TmpBase):
    def test_non_atomic_dir_name_error(self):
        """块目录名（名称字段）为「配置加载」（切成多 token）应报错。

        导出名刻意用原子的「汇总」，把错误来源隔离到目录名这一项。
        """
        data = _minimal_data(名称='配置加载', 领域=['工具'])
        jk = '函数 汇总 接收 赵值：\n  返回 赵值。\n。\n\n导出 汇总。\n'
        block_dir = _write_block(os.path.join(self.tmp, '配置加载'), data, jk)
        errors, _ = validate_block(block_dir)
        self.assertTrue(
            any('块目录名' in e and '非词法原子' in e for e in errors),
            f'应检测到非原子块目录名，得到：{errors}')
        # 报错信息应含切分详情与「无法作为点分路径段被导入」
        hit = next(e for e in errors if '块目录名' in e)
        self.assertIn('配置加载', hit)
        self.assertIn('无法作为点分路径段被导入', hit)

    def test_real_qiuhe_dir_name_ok(self):
        """真实块 `stdlib/blocks/数据/求和` 目录名（单 VERB）应无目录名错误。"""
        block_dir = os.path.join(_blocks_root(), '数据', '求和')
        if not os.path.isdir(block_dir):
            self.skipTest('stdlib/blocks/数据/求和 不存在')
        errors, _ = validate_block(block_dir)
        self.assertFalse(any('块目录名' in e for e in errors),
                         f'求和 目录名应合法，得到：{errors}')


# ---------------------------------------------------------------------------
# validate_block 对非原子依赖块名只给警告（1 条）
# ---------------------------------------------------------------------------

class ValidateBlockDepAtomicityWarningTest(_TmpBase):
    def test_non_atomic_dep_name_warning(self):
        """依赖块名非原子只应产生警告，不进错误列表。

        为了让依赖块名进入 meta 且不触发「依赖块与导入不一致」错误，
        代码里同时导入 `blocks.工具.配置加载`，与声明保持一致。
        """
        data = _minimal_data(名称='聚合', 层级=1, 依赖块=['配置加载'])
        jk = ('从 blocks.工具.配置加载 导入 甲。\n'
              '函数 聚合 接收 赵值：\n  返回 甲 赵值。\n。\n\n导出 聚合。\n')
        block_dir = _write_block(os.path.join(self.tmp, '聚合'), data, jk)
        errors, warnings = validate_block(block_dir)
        self.assertFalse(any('依赖块名' in e for e in errors),
                         f'依赖块名非原子不应是错误，得到：{errors}')
        self.assertTrue(
            any('依赖块名' in w and '配置加载' in w for w in warnings),
            f'应产生依赖块名非原子警告，得到：{warnings}')


# ---------------------------------------------------------------------------
# validate_block 对依赖块不一致报错（1 条）
# ---------------------------------------------------------------------------

class ValidateBlockDepMismatchTest(_TmpBase):
    def test_dep_mismatch_error(self):
        """声明 `依赖块: ["读取文件"]` 但代码里没导入，应报依赖不一致。"""
        data = _minimal_data(名称='坏依赖', 依赖块=['读取文件'])
        jk = '函数 汇总 接收 赵数值：\n  返回 求和 赵数值。\n。\n\n导出 汇总。\n'
        block_dir = _write_block(os.path.join(self.tmp, '坏依赖'), data, jk)
        errors, _ = validate_block(block_dir)
        self.assertTrue(any('不一致' in e for e in errors),
                        f'应检测到依赖不一致，得到：{errors}')


# ---------------------------------------------------------------------------
# CLI 测试（5 条：列表/查找/详情/校验/索引）
# ---------------------------------------------------------------------------
# 这一段刻意用 pytest 函数式 + capsys，而非 unittest：CLI 的断言主体是
# **标准输出内容 + 返回码**，capsys 比手工 redirect_stdout 更直观。

def test_cli_list(capsys):
    """CLI `列表` 正常返回 0，并输出块清单。"""
    rc = blocks_cli.run(['列表'])
    out = capsys.readouterr().out
    assert rc == 0
    assert '求和' in out
    assert '[L0]' in out


def test_cli_search(capsys):
    """CLI `查找 求和` 应命中内置的求和块（名称/描述/领域 三字段子串匹配）。"""
    rc = blocks_cli.run(['查找', '求和'])
    out = capsys.readouterr().out
    assert rc == 0
    assert '求和' in out


def test_cli_select_口语需求命中(capsys):
    """CLI `选 <需求>` 走 retrieval 启发式，能召回描述里没有原字的块。

    「加起来」既不在任何块名也不在任何描述里——靠 `_SYNONYMS` 映射到 `求和`。
    子串匹配（`查找`）对这条必然空手而归；这条断言等价于「`选` 确实接了
    retrieval，而不是又跑了一次子串匹配」。
    """
    rc = blocks_cli.run(['选', '把一串数字加起来', '--top', '3'])
    out = capsys.readouterr().out
    assert rc == 0, f'选 命令返回 {rc}，输出：{out}'
    assert '求和' in out
    assert '[启发式]' in out or '[神经]' in out    # 路径标签必须落在输出里


def test_cli_select_json输出(capsys):
    """`--json` 出结构化候选，含 名称/领域/描述/分数/路径 五字段。"""
    rc = blocks_cli.run(['选', '求和', '--top', '2', '--json'])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data['需求'] == '求和'
    assert data['候选']
    assert set(data['候选'][0]) >= {'名称', '领域', '描述', '分数', '路径'}


def test_cli_select_缺需求返1(capsys):
    rc = blocks_cli.run(['选'])
    err = capsys.readouterr().err
    assert rc == 1
    assert '缺少需求文本' in err


def test_cli_select_坏向量文件返1(tmp_path, capsys):
    """`--向量` 指向非数组 JSON 时报错退出，不静默降级——用错了要看得见。"""
    bad = tmp_path / 'v.json'
    bad.write_text('{"不是": "数组"}', encoding='utf-8')
    rc = blocks_cli.run(['选', '求和', '--向量', str(bad)])
    err = capsys.readouterr().err
    assert rc == 1
    assert '非空 JSON 数组' in err



def test_cli_show(capsys):
    """CLI `详情 求和` 输出完整元数据 + 示例代码。"""
    rc = blocks_cli.run(['详情', '求和'])
    out = capsys.readouterr().out
    assert rc == 0
    assert '求和' in out
    assert '描述：' in out
    assert '示例：' in out


def test_cli_check(capsys):
    """CLI `校验`（省略目录 → 全量）对内置块应全绿，返回 0。"""
    rc = blocks_cli.run(['校验'])
    out = capsys.readouterr().out
    assert rc == 0, f'校验未通过，输出：{out}'
    assert '✓ 求和' in out


def test_cli_index(tmp_path, capsys):
    """CLI `索引 <目录>` 在指定块根目录生成 索引.json。

    刻意传 tmp_path 而不是缺省的内置块根——测试不该改动 stdlib/blocks/。
    """
    data = _minimal_data(名称='索引测试')
    jk = '函数 汇总 接收 赵数值：\n  返回 求和 赵数值。\n。\n\n导出 汇总。\n'
    _write_block(os.path.join(str(tmp_path), '索引测试'), data, jk)

    rc = blocks_cli.run(['索引', str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert '已生成索引' in out

    idx_path = os.path.join(str(tmp_path), '索引.json')
    assert os.path.isfile(idx_path)
    with open(idx_path, 'r', encoding='utf-8') as f:
        idx = json.load(f)
    assert len(idx['块']) == 1
    assert idx['块'][0]['名称'] == '索引测试'


if __name__ == '__main__':
    unittest.main()


# ---------------------------------------------------------------------------
# CLI 三段式：组 / 跑（v0.14.0 W9）
# ---------------------------------------------------------------------------

def test_cli_组_合法方案出源码(tmp_path, capsys):
    """合法方案 JSON → 组 → stdout 有极快源码，rc=0。"""
    plan = {
        '需求': '求和',
        '共享': [{'名': '赵料', '值': '列 1 2 3'}],
        '步骤': [{'块': '求和', '领域': '数据', '导出名': '汇总', '参数': ['赵料']}],
    }
    p = tmp_path / '方案.json'
    p.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    rc = blocks_cli.run(['组', str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert '从 blocks.数据.求和 导入 汇总' in out
    assert '汇总(赵料)' in out


def test_cli_组_坏JSON报错非0(tmp_path, capsys):
    """不合法 JSON 输入 → rc=1 + stderr 含提示。"""
    p = tmp_path / 'bad.json'
    p.write_text('{这不是合法JSON', encoding='utf-8')
    rc = blocks_cli.run(['组', str(p)])
    err = capsys.readouterr().err
    assert rc == 1
    assert '不是合法 JSON' in err


def test_cli_组_缺步骤报错非0(tmp_path, capsys):
    """有 JSON 对象但缺 `步骤` 字段 → rc=1。"""
    p = tmp_path / 'no_steps.json'
    p.write_text('{"需求":"空"}', encoding='utf-8')
    rc = blocks_cli.run(['组', str(p)])
    err = capsys.readouterr().err
    assert rc == 1
    assert '步骤' in err


def test_cli_跑_合法方案端到端出结果(tmp_path, capsys):
    """合法方案 → 跑 → stdout 有计算结果，rc=0。"""
    plan = {
        '需求': '求和',
        '共享': [{'名': '赵料', '值': '列 10 20 30'}],
        '步骤': [{'块': '求和', '领域': '数据', '导出名': '汇总', '参数': ['赵料']}],
        '打印': ['赵果1'],
    }
    p = tmp_path / '方案.json'
    p.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    rc = blocks_cli.run(['跑', str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert '60' in out


def test_cli_跑_块不存在报错非0(tmp_path, capsys):
    """方案里写了不存在的块名 → rc=1 + 人读提示。"""
    plan = {
        '步骤': [{'块': '不存在的块XYZ', '领域': '数据', '导出名': 'x'}],
    }
    p = tmp_path / '方案.json'
    p.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    rc = blocks_cli.run(['跑', str(p)])
    err = capsys.readouterr().err
    assert rc == 1
    assert '不存在' in err


def test_cli_组_stdin路径(monkeypatch, capsys):
    """stdin `-` 输入路径：从 stdin 读方案 JSON。"""
    import io
    plan = json.dumps({
        '需求': '求和',
        '共享': [{'名': '赵料', '值': '列 5 5'}],
        '步骤': [{'块': '求和', '领域': '数据', '导出名': '汇总', '参数': ['赵料']}],
    }, ensure_ascii=False)
    monkeypatch.setattr('sys.stdin', io.StringIO(plan))
    rc = blocks_cli.run(['组', '-'])
    out = capsys.readouterr().out
    assert rc == 0
    assert '汇总(赵料)' in out


def test_cli_跑_多步链式端到端(tmp_path, capsys):
    """两步方案（求和+均值）→ 跑 → 两行结果。"""
    plan = {
        '需求': '求和再算平均',
        '共享': [{'名': '赵料', '值': '列 100 200 300'}],
        '步骤': [
            {'块': '求和', '领域': '数据', '导出名': '汇总', '参数': ['赵料']},
            {'块': '均值', '领域': '数据', '导出名': '中位', '参数': ['赵料']},
        ],
        '打印': ['赵果1', '赵果2'],
    }
    p = tmp_path / '方案.json'
    p.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    rc = blocks_cli.run(['跑', str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert '600' in out
    assert '200' in out


# ---------------------------------------------------------------------------
# W11：`--神经` 神经检索的降级链路（sidecar 缺失/坏输出/维度不符）
# ---------------------------------------------------------------------------
# 关键约束（v0.14.0 WBS）：这一段所有测试都 mock subprocess，**绝不真跑模型**。
# 否则 CI 常规 job（无 torch）会挂——W11 sidecar 的整个卖点就是「神经能力可选，
# 不装 torch 也能测」。

def _fake_run_factory(returncode=0, stdout='', stderr='', exc=None):
    """造一个假的 subprocess.run。走 monkeypatch 打进 embed_client 模块。"""
    def _fake(cmd, **kwargs):
        if exc is not None:
            raise exc
        import types
        return types.SimpleNamespace(
            args=cmd, returncode=returncode, stdout=stdout, stderr=stderr,
        )
    return _fake


def test_cli_select_神经_sidecar缺失时降级到启发式(monkeypatch, capsys):
    """`--神经` 但 sidecar 命令返回非零 → 降级到启发式 + stderr 提示，rc=0。

    模拟场景：sidecar 依赖缺失（比如 torch 没装）返回退出码 2。CLI 应该：
    1. 不报错（rc=0 —— 降级不是失败）
    2. stderr 打一行「神经检索不可用，降级到启发式」提示
    3. 输出里带 `[启发式]` 标签，且真实召回相关块
    """
    from jikuai.ai import embed_client
    monkeypatch.setattr(
        embed_client.subprocess, 'run',
        _fake_run_factory(returncode=2, stderr='embed_query: 缺少依赖\n'),
    )
    rc = blocks_cli.run(['选', '把一串数字加起来', '--top', '3', '--神经'])
    captured = capsys.readouterr()
    assert rc == 0
    assert '[启发式]' in captured.out
    assert '求和' in captured.out
    assert '神经检索不可用，降级到启发式' in captured.err


def test_cli_select_神经_sidecar坏JSON时降级(monkeypatch, capsys):
    """sidecar 退出码 0 但 stdout 不是合法 JSON 数组 → 降级到启发式。"""
    from jikuai.ai import embed_client
    monkeypatch.setattr(
        embed_client.subprocess, 'run',
        _fake_run_factory(returncode=0, stdout='not a json list\n'),
    )
    rc = blocks_cli.run(['选', '求和', '--top', '2', '--神经'])
    captured = capsys.readouterr()
    assert rc == 0
    assert '[启发式]' in captured.out
    assert '神经检索不可用' in captured.err
    assert '不是合法 JSON' in captured.err


def test_cli_select_神经_sidecar输出非数组时降级(monkeypatch, capsys):
    """sidecar 吐 JSON 对象（不是数组）时降级。"""
    from jikuai.ai import embed_client
    monkeypatch.setattr(
        embed_client.subprocess, 'run',
        _fake_run_factory(returncode=0, stdout='{"vec": [1, 2, 3]}\n'),
    )
    rc = blocks_cli.run(['选', '求和', '--神经'])
    captured = capsys.readouterr()
    assert rc == 0
    assert '[启发式]' in captured.out
    assert '不是非空 JSON 数组' in captured.err


def test_cli_select_神经_命令找不到时降级(monkeypatch, capsys):
    """subprocess.run 抛 FileNotFoundError → 降级到启发式。

    JIKUAI_AI_EMBED_CMD 指了个不存在的程序时会走这条。
    """
    from jikuai.ai import embed_client
    monkeypatch.setenv(embed_client.ENV_CMD, 'definitely-not-a-real-cmd-xyz')
    monkeypatch.setattr(
        embed_client.subprocess, 'run',
        _fake_run_factory(exc=FileNotFoundError('no such file')),
    )
    rc = blocks_cli.run(['选', '求和', '--神经'])
    captured = capsys.readouterr()
    assert rc == 0
    assert '[启发式]' in captured.out
    assert '找不到 sidecar' in captured.err


def test_cli_select_神经_维度不符时降级(monkeypatch, capsys):
    """sidecar 吐的向量维度与索引不符（模型换了没重生成）→ 降级。

    stdlib/blocks 的实际维度是 768（见 向量索引.元信息.json）。这里假装
    sidecar 用了 384 维模型，client 应该在维度校验阶段就把它挡下来，
    避免抛给 retrieval._retrieve_neural 变成 RetrievalError。
    """
    from jikuai.ai import embed_client
    fake_vec = [0.1] * 384
    monkeypatch.setattr(
        embed_client.subprocess, 'run',
        _fake_run_factory(returncode=0, stdout=json.dumps(fake_vec) + '\n'),
    )
    rc = blocks_cli.run(['选', '求和', '--神经'])
    captured = capsys.readouterr()
    assert rc == 0
    assert '[启发式]' in captured.out
    # 只有索引真的存在（能读到 dim）才会走维度校验分支；stdlib 有索引所以必然
    if '神经检索不可用' in captured.err:
        assert '维度' in captured.err or '不符' in captured.err


def test_cli_select_神经加向量_向量优先(monkeypatch, tmp_path, capsys):
    """`--神经` 与 `--向量` 同时给：`--向量` 优先，`--神经` 让位并提示。

    `subprocess.run` 挂一个「一调用就爆炸」的桩——真的走进 sidecar 分支就会
    抛异常测试失败。这样断言「向量优先」路径完全绕过 subprocess。
    """
    from jikuai.ai import embed_client

    def _boom(*args, **kwargs):
        raise AssertionError('向量优先路径下不该跑 sidecar')
    monkeypatch.setattr(embed_client.subprocess, 'run', _boom)

    # 用一个合法维度的假向量（会被 retrieval 试着走神经，可能因为向量不真实
    # 命中不到东西；但我们只关心 rc==0 且 stderr 里有优先提示）
    dim = embed_client.index_dim() or 768
    vec_file = tmp_path / 'q.json'
    vec_file.write_text(json.dumps([0.0] * dim), encoding='utf-8')

    rc = blocks_cli.run(['选', '求和', '--top', '2',
                         '--神经', '--向量', str(vec_file)])
    captured = capsys.readouterr()
    assert rc == 0
    assert '`--向量` 与 `--神经` 同时给出' in captured.err
    assert '采用 `--向量`' in captured.err


def test_cli_select_神经_sidecar超时时降级(monkeypatch, capsys):
    """subprocess.TimeoutExpired → 降级到启发式（不是把用户挂到超时上）。"""
    import subprocess as _sp
    from jikuai.ai import embed_client
    monkeypatch.setattr(
        embed_client.subprocess, 'run',
        _fake_run_factory(exc=_sp.TimeoutExpired(cmd='fake', timeout=1.0)),
    )
    rc = blocks_cli.run(['选', '求和', '--神经'])
    captured = capsys.readouterr()
    assert rc == 0
    assert '[启发式]' in captured.out
    assert '超时' in captured.err



