# -*- coding: utf-8 -*-
"""v0.12.0 · ADR-15 · 块元数据（`块.json`）解析与索引生成测试。

被测模块：`jikuai.pkg.blocks`。校验逻辑不对外暴露独立入口，统一走
`load_block_metadata()`（读盘 → 校验 → 返回 `BlockMetadata`），因此非法
元数据的用例都通过"写一份坏 `块.json` 再加载"来触发 `BlockError`。

覆盖：
- 合法 `块.json` 加载（3 条：最小/含全部可选字段/含依赖块）
- 缺少必填字段（3 条：缺名称、缺层级、缺领域）
- 非法字段值（3 条：层级<0、稳定性非法、领域空列表）
- scan_blocks 目录扫描（2 条：空目录、多块目录）
- generate_index 输出结构（1 条）
- 附加：非法版本 / 坏 JSON / 领域越界白名单 / 名称与路径不一致
"""

import json
import os
import shutil
import tempfile
import unittest

from jikuai.pkg import blocks
from jikuai.pkg.blocks import (
    ALLOWED_DOMAINS, BLOCK_METADATA_NAME, BlockError, BlockMetadata,
    generate_index, load_block_metadata, scan_blocks,
)


def _minimal_data(**overrides):
    """构造一份最小合法块元数据。领域取白名单内的『数据』。"""
    data = {
        '名称': '读取文件',
        '版本': '0.1.0',
        '层级': 0,
        '领域': ['数据'],
        '描述': '读取文本文件为字符串，UTF-8 编码',
    }
    data.update(overrides)
    return data


def _write_block(dir_path, data, filename=BLOCK_METADATA_NAME):
    """把 data 写成 <dir_path>/块.json，返回文件路径。"""
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, filename)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


class _TmpBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _load_bad(self, data):
        """写一份 data 到临时 块.json 并加载，供期待 BlockError 的用例复用。"""
        path = _write_block(self.tmp, data)
        return load_block_metadata(path)


# ---- 合法加载 --------------------------------------------------------

class LoadValidTest(_TmpBase):
    def test_load_minimal(self):
        """只填必填字段的块能加载成功，可选字段落到合理缺省。"""
        path = _write_block(self.tmp, _minimal_data())
        meta = load_block_metadata(path)
        self.assertIsInstance(meta, BlockMetadata)
        self.assertEqual(meta.name, '读取文件')
        self.assertEqual(meta.version, '0.1.0')
        self.assertEqual(meta.level, 0)
        self.assertEqual(meta.domains, ['数据'])
        self.assertEqual(meta.dep_blocks, [])
        self.assertEqual(meta.inputs, [])
        self.assertEqual(meta.output, {})
        self.assertEqual(meta.stability, 'experimental')
        self.assertEqual(meta.path, os.path.abspath(path))

    def test_load_full_optional_fields(self):
        """含所有可选字段的块能加载成功，字段被正确读取。"""
        data = _minimal_data(
            输入=[{'名': '路径', '类型': '字符串'}],
            输出={'类型': '字符串'},
            依赖块=[],
            极快版本='>=0.12.0',
            示例='读取文件 "data.txt"，长度。',
            稳定性='stable',
        )
        meta = load_block_metadata(_write_block(self.tmp, data))
        self.assertEqual(meta.inputs, [{'名': '路径', '类型': '字符串'}])
        self.assertEqual(meta.output, {'类型': '字符串'})
        self.assertEqual(meta.jikuai_requirement, '>=0.12.0')
        self.assertEqual(meta.stability, 'stable')
        self.assertIn('data.txt', meta.example)

    def test_load_with_deps(self):
        """含 `依赖块` 与层级 1 的组合块能加载成功；传目录也能定位块.json。"""
        data = _minimal_data(
            名称='清洗与求和',
            层级=1,
            依赖块=['读取文件', '求和'],
            稳定性='stable',
        )
        _write_block(self.tmp, data)
        meta = load_block_metadata(self.tmp)          # 传目录
        self.assertEqual(meta.dep_blocks, ['读取文件', '求和'])
        self.assertEqual(meta.level, 1)
        self.assertEqual(meta.name, '清洗与求和')


# ---- 缺失必填字段 ----------------------------------------------------

class MissingRequiredTest(_TmpBase):
    def test_missing_name(self):
        data = _minimal_data()
        del data['名称']
        with self.assertRaises(BlockError) as ctx:
            self._load_bad(data)
        self.assertIn('名称', str(ctx.exception))

    def test_missing_level(self):
        data = _minimal_data()
        del data['层级']
        with self.assertRaises(BlockError) as ctx:
            self._load_bad(data)
        self.assertIn('层级', str(ctx.exception))

    def test_missing_domains(self):
        data = _minimal_data()
        del data['领域']
        with self.assertRaises(BlockError) as ctx:
            self._load_bad(data)
        self.assertIn('领域', str(ctx.exception))


# ---- 非法字段值 ------------------------------------------------------

class InvalidFieldTest(_TmpBase):
    def test_negative_level(self):
        with self.assertRaises(BlockError) as ctx:
            self._load_bad(_minimal_data(层级=-1))
        self.assertIn('层级', str(ctx.exception))

    def test_invalid_stability(self):
        with self.assertRaises(BlockError) as ctx:
            self._load_bad(_minimal_data(稳定性='alpha'))
        self.assertIn('稳定性', str(ctx.exception))

    def test_empty_domain_list(self):
        with self.assertRaises(BlockError) as ctx:
            self._load_bad(_minimal_data(领域=[]))
        self.assertIn('领域', str(ctx.exception))

    def test_invalid_version(self):
        with self.assertRaises(BlockError):
            self._load_bad(_minimal_data(版本='not-a-version'))

    def test_domain_outside_whitelist(self):
        """领域必须落在白名单内（ADR-15 §2.2）。"""
        self.assertNotIn('金融', ALLOWED_DOMAINS)
        with self.assertRaises(BlockError) as ctx:
            self._load_bad(_minimal_data(领域=['金融']))
        self.assertIn('领域', str(ctx.exception))

    def test_level_bool_rejected(self):
        """bool 是 int 子类，`"层级": true` 必须被挡掉。"""
        with self.assertRaises(BlockError):
            self._load_bad(_minimal_data(层级=True))


# ---- 扫描目录 --------------------------------------------------------

class ScanBlocksTest(_TmpBase):
    def test_scan_empty_dir(self):
        """空目录返回空列表。"""
        self.assertEqual(scan_blocks(self.tmp), [])

    def test_scan_multiple_blocks(self):
        """递归扫出多个块，按名称排序；名称须与目录名一致。"""
        _write_block(os.path.join(self.tmp, '数据', '读取文件'),
                     _minimal_data(名称='读取文件', 稳定性='stable'))
        _write_block(os.path.join(self.tmp, '数据', '求和'),
                     _minimal_data(名称='求和', 稳定性='stable'))
        _write_block(os.path.join(self.tmp, '中文', '简繁转换'),
                     _minimal_data(名称='简繁转换', 领域=['中文'],
                                   稳定性='stable'))
        found = scan_blocks(self.tmp)
        self.assertEqual([b.name for b in found],
                         ['求和', '简繁转换', '读取文件'])
        self.assertTrue(all(isinstance(b, BlockMetadata) for b in found))

    def test_scan_name_path_mismatch_raises(self):
        """名称与所在目录名不一致时扫描报错。"""
        _write_block(os.path.join(self.tmp, '数据', '读取文件'),
                     _minimal_data(名称='另一个名字'))
        with self.assertRaises(BlockError) as ctx:
            scan_blocks(self.tmp)
        self.assertIn('不一致', str(ctx.exception))


# ---- 索引生成 --------------------------------------------------------

class GenerateIndexTest(_TmpBase):
    def test_index_structure(self):
        """generate_index 扫描目录并输出稳定的三段式字典。"""
        _write_block(os.path.join(self.tmp, '读取文件'),
                     _minimal_data(名称='读取文件', 稳定性='stable',
                                   输入=[{'名': '路径', '类型': '字符串'}],
                                   输出={'类型': '字符串'}))
        _write_block(os.path.join(self.tmp, '求和'),
                     _minimal_data(名称='求和', 稳定性='stable'))

        idx = generate_index(self.tmp, timestamp='2026-08-09T03:16:00')
        self.assertEqual(idx['版本'], blocks.BLOCK_INDEX_VERSION)
        self.assertEqual(idx['生成时间'], '2026-08-09T03:16:00')
        self.assertEqual(len(idx['块']), 2)

        names = [e['名称'] for e in idx['块']]
        self.assertEqual(names, sorted(names))          # 确定性排序
        for entry in idx['块']:
            for key in ('名称', '领域', '层级', '描述', '稳定性'):
                self.assertIn(key, entry)
            self.assertNotIn('版本', entry)             # 索引条目字段精简
            self.assertNotIn('path', entry)

        # 索引可无损 JSON 往返（UTF-8 中文键）
        parsed = json.loads(json.dumps(idx, ensure_ascii=False, indent=2))
        self.assertEqual(parsed['块'][0]['名称'], names[0])


# ---- 附加：坏 JSON / 找不到文件 --------------------------------------

class LoadErrorTest(_TmpBase):
    def test_bad_json(self):
        path = os.path.join(self.tmp, BLOCK_METADATA_NAME)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('{not valid json')
        with self.assertRaises(BlockError):
            load_block_metadata(path)

    def test_file_not_found(self):
        with self.assertRaises(BlockError):
            load_block_metadata(os.path.join(self.tmp, '缺失.json'))


# ---- G12：向量索引一致性（ADR-25 §3.3） -------------------------------

class VectorIndexConsistencyTest(_TmpBase):
    """`check_vector_index` 的四种状态。

    全部在 tmp 目录里造假索引，不碰 `stdlib/blocks/`——门禁测试自己不该
    污染真实块库。
    """

    def _写索引(self, blocks_list):
        idx = {'版本': blocks.BLOCK_INDEX_VERSION, '生成时间': 'x',
               '块': blocks_list}
        blocks.save_index(idx, blocks.index_path(self.tmp))
        return idx

    def _写元信息(self, **overrides):
        meta = {'格式版本': 1, '模型': 'm', '维度': 8, '块数': 1,
                '块哈希': 'sha256:deadbeef'}
        meta.update(overrides)
        with open(blocks.vector_index_meta_path(self.tmp), 'w',
                  encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False)

    def _写bin(self):
        with open(blocks.vector_index_bin_path(self.tmp), 'wb') as f:
            f.write(b'JKBV')

    def test_两个文件都缺算缺失不算失败(self):
        # ADR-25 §3.1：无向量索引是合法状态，运行时降级启发式，
        # 不该迫使每个贡献者装 torch 重生成索引。
        状态, _ = blocks.check_vector_index(self.tmp)
        self.assertEqual(状态, '缺失')

    def test_哈希一致(self):
        条目 = [{'名称': '甲', '领域': ['数据'], '层级': 0, '描述': 'd',
                '输入': [], '输出': {}, '稳定性': 'stable'}]
        self._写索引(条目)
        self._写元信息(块数=1, 块哈希=blocks.blocks_content_hash(条目))
        self._写bin()
        状态, 说明 = blocks.check_vector_index(self.tmp)
        self.assertEqual(状态, '一致', 说明)

    def test_块改了但没重跑生成脚本_哈希不符(self):
        条目 = [{'名称': '甲', '领域': ['数据'], '层级': 0, '描述': 'd',
                '输入': [], '输出': {}, '稳定性': 'stable'}]
        self._写元信息(块数=1, 块哈希=blocks.blocks_content_hash(条目))
        self._写bin()
        条目[0]['描述'] = '描述改过了'      # 索引更新，向量索引没跟上
        self._写索引(条目)
        状态, 说明 = blocks.check_vector_index(self.tmp)
        self.assertEqual(状态, '不一致')
        self.assertIn('哈希不符', 说明)
        self.assertIn('generate_embeddings.py', 说明)

    def test_只有bin缺元信息(self):
        self._写bin()
        状态, 说明 = blocks.check_vector_index(self.tmp)
        self.assertEqual(状态, '不一致')
        self.assertIn(blocks.VECTOR_INDEX_META_NAME, 说明)

    def test_块数不符(self):
        条目 = [{'名称': '甲', '领域': ['数据'], '层级': 0, '描述': 'd',
                '输入': [], '输出': {}, '稳定性': 'stable'}]
        self._写索引(条目)
        self._写元信息(块数=99, 块哈希=blocks.blocks_content_hash(条目))
        self._写bin()
        状态, 说明 = blocks.check_vector_index(self.tmp)
        self.assertEqual(状态, '不一致')
        self.assertIn('块数不符', 说明)


class BlocksContentHashTest(unittest.TestCase):
    def test_与条目顺序无关(self):
        """哈希前按名称排序，所以索引里条目顺序变化不该改哈希。"""
        甲 = {'名称': '甲', '描述': 'a'}
        乙 = {'名称': '乙', '描述': 'b'}
        self.assertEqual(blocks.blocks_content_hash([甲, 乙]),
                         blocks.blocks_content_hash([乙, 甲]))

    def test_内容变了哈希就变(self):
        base = [{'名称': '甲', '描述': 'a'}]
        改 = [{'名称': '甲', '描述': 'a2'}]
        self.assertNotEqual(blocks.blocks_content_hash(base),
                            blocks.blocks_content_hash(改))


# ---- G14：类型标注精度（ADR-26 §4.3） --------------------------------

class TypeAnnotationGateTest(unittest.TestCase):
    """`check_type_annotation` 拒裸容器、放行细化类型。"""

    def _entry(self, 输入=None, 输出=None):
        return {'名称': 'X', '输入': 输入 or [], '输出': 输出 or {}}

    def test_裸列表输出被拒(self):
        问题 = blocks.check_type_annotation(self._entry(输出={'类型': '列表'}))
        self.assertEqual(len(问题), 1)
        self.assertIn('裸', 问题[0])

    def test_裸字典输入被拒(self):
        问题 = blocks.check_type_annotation(
            self._entry(输入=[{'名': '表', '类型': '字典'}]))
        self.assertEqual(len(问题), 1)
        self.assertIn('字典', 问题[0])

    def test_细化列表放行(self):
        问题 = blocks.check_type_annotation(
            self._entry(输出={'类型': {'类型': '列表', '元素类型': '数'}}))
        self.assertEqual(问题, [])

    def test_元组元数里的裸容器也被抓(self):
        输出 = {'类型': {'类型': '元组', '元数': ['数', '列表']}}
        问题 = blocks.check_type_annotation(self._entry(输出=输出))
        self.assertEqual(len(问题), 1)
        self.assertIn('元数[1]', 问题[0])

    def test_标量输出放行(self):
        self.assertEqual(
            blocks.check_type_annotation(self._entry(输出={'类型': '数'})), [])

    def test_内置块库全绿(self):
        """G14 对当前 stdlib 块库应零问题（W2 回填后的硬门槛）。"""
        self.assertEqual(blocks.check_stdlib_type_annotations(), [])


if __name__ == '__main__':
    unittest.main()
