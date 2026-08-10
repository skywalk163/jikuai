# -*- coding: utf-8 -*-
"""v0.14.0 · W1 · ADR-26 类型词表校验测试。

被测：`jikuai.pkg.blocks._validate` 经 `load_block_metadata` 触发，覆盖
`输入[].类型` 与 `输出.类型` 的类型标注校验：标量 / 容器裸串（向后兼容）/
结构化容器对象 / 嵌套 / 非法值。
"""

import json
import os
import shutil
import tempfile
import unittest

from jikuai.pkg.blocks import (
    BLOCK_METADATA_NAME, BlockError, load_block_metadata,
)


def _minimal_data(**overrides):
    data = {
        '名称': '读取文件',
        '版本': '0.1.0',
        '层级': 0,
        '领域': ['数据'],
        '描述': '读取文本文件为字符串',
    }
    data.update(overrides)
    return data


class _TmpBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _load(self, data):
        os.makedirs(self.tmp, exist_ok=True)
        path = os.path.join(self.tmp, BLOCK_METADATA_NAME)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return load_block_metadata(path)


# ---- 合法：标量 / 容器裸串（向后兼容）/ 结构化 --------------------------

class ValidTypeTest(_TmpBase):
    def test_标量类型全部合法(self):
        for t in ('数', '字符串', '布尔', '函数', '任意'):
            meta = self._load(_minimal_data(输出={'类型': t}))
            self.assertEqual(meta.output, {'类型': t})

    def test_裸容器向后兼容(self):
        """旧格式裸 `列表`/`字典`/`元组` 字符串仍合法（视为通配元素）。"""
        for t in ('列表', '字典', '元组'):
            meta = self._load(_minimal_data(输入=[{'名': 'x', '类型': t}]))
            self.assertEqual(meta.inputs[0]['类型'], t)

    def test_结构化列表(self):
        meta = self._load(_minimal_data(
            输入=[{'名': '项表', '类型': {'类型': '列表', '元素类型': '数'}}]))
        self.assertEqual(meta.inputs[0]['类型'],
                         {'类型': '列表', '元素类型': '数'})

    def test_结构化字典(self):
        meta = self._load(_minimal_data(
            输出={'类型': '字典', '键类型': '字符串', '值类型': '任意'}))
        self.assertEqual(meta.output['键类型'], '字符串')

    def test_结构化元组(self):
        meta = self._load(_minimal_data(
            输出={'类型': '元组', '元数': ['数', '数', '数', '字符串']}))
        self.assertEqual(meta.output['元数'], ['数', '数', '数', '字符串'])

    def test_嵌套容器(self):
        """列表<列表<数>> 这类嵌套合法。"""
        t = {'类型': '列表', '元素类型': {'类型': '列表', '元素类型': '数'}}
        meta = self._load(_minimal_data(输出={'类型': t}))
        self.assertEqual(meta.output['类型'], t)

    def test_联合类型(self):
        t = {'类型': '联合', '候选': ['字典', '列表']}
        meta = self._load(_minimal_data(输出={'类型': t}))
        self.assertEqual(meta.output['类型'], t)


# ---- 非法 --------------------------------------------------------------

class InvalidTypeTest(_TmpBase):
    def test_未知标量串(self):
        with self.assertRaises(BlockError) as ctx:
            self._load(_minimal_data(输出={'类型': '字典或列表'}))
        self.assertIn('类型', str(ctx.exception))

    def test_列表缺元素类型(self):
        with self.assertRaises(BlockError) as ctx:
            self._load(_minimal_data(输出={'类型': {'类型': '列表'}}))
        self.assertIn('元素类型', str(ctx.exception))

    def test_字典缺值类型(self):
        with self.assertRaises(BlockError) as ctx:
            self._load(_minimal_data(
                输出={'类型': {'类型': '字典', '键类型': '字符串'}}))
        self.assertIn('值类型', str(ctx.exception))

    def test_元组元数非数组(self):
        with self.assertRaises(BlockError) as ctx:
            self._load(_minimal_data(
                输出={'类型': {'类型': '元组', '元数': '数'}}))
        self.assertIn('元数', str(ctx.exception))

    def test_元组元数空(self):
        with self.assertRaises(BlockError):
            self._load(_minimal_data(
                输出={'类型': {'类型': '元组', '元数': []}}))

    def test_标量不能用结构化对象(self):
        """标量类型（数）用结构化对象写法应被拒。"""
        with self.assertRaises(BlockError) as ctx:
            self._load(_minimal_data(输出={'类型': {'类型': '数'}}))
        self.assertIn('容器', str(ctx.exception))

    def test_嵌套非法元素类型(self):
        with self.assertRaises(BlockError):
            self._load(_minimal_data(
                输出={'类型': {'类型': '列表', '元素类型': '未知型'}}))

    def test_联合候选少于两项(self):
        with self.assertRaises(BlockError) as ctx:
            self._load(_minimal_data(
                输出={'类型': {'类型': '联合', '候选': ['数']}}))
        self.assertIn('候选', str(ctx.exception))

    def test_裸联合非法(self):
        """裸 `联合` 字符串无候选、语义等于 `任意`，应报错。"""
        with self.assertRaises(BlockError):
            self._load(_minimal_data(输出={'类型': '联合'}))


if __name__ == '__main__':
    unittest.main()
