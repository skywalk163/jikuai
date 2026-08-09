# -*- coding: utf-8 -*-
"""v0.13.0 M2 B0 · stdlib/blocks/**/测试.jk 冒烟测试。

背景：`validate_block` 只检查 `测试.jk` 存在（缺失仅警告），既没有 test runner
主动去执行这些测试文件，也没有 CI 门禁保证它们能跑通。M2 要把块数从 52 翻到
102，`.jk` 里稍不留神埋个运行期错误 CI 全绿——所以这个 runner 是 M2 的正确性
防线，必须先补上。

只做冒烟：每个 `测试.jk` 通过 `run_source` 跑完不抛异常即算过。深层断言由
测试文件自己用 `断言` / `打印` 写。
"""

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jikuai.main import run_source  # noqa: E402
from jikuai.pkg.blocks import blocks_root  # noqa: E402


def _iter_测试文件():
    root = blocks_root()
    if not os.path.isdir(root):
        return
    for domain in sorted(os.listdir(root)):
        domain_dir = os.path.join(root, domain)
        if not os.path.isdir(domain_dir) or domain.startswith('.'):
            continue
        for block in sorted(os.listdir(domain_dir)):
            block_dir = os.path.join(domain_dir, block)
            if not os.path.isdir(block_dir):
                continue
            test_jk = os.path.join(block_dir, '测试.jk')
            if os.path.isfile(test_jk):
                yield pytest.param(test_jk, id=f'{domain}/{block}')


@pytest.mark.parametrize('test_path', list(_iter_测试文件()))
def test_块测试跑通(test_path, capsys):
    """每个块目录下的 `测试.jk` 必须能跑完不抛异常。

    `run_source` 底层会 tokenize + parse + evaluate 完整流程，所以既覆盖了
    块自身的 .jk 语法正确性，也覆盖了从 `blocks.<领域>.<块名>` 导入的路径解析、
    白名单反哺、依赖块加载。
    """
    with open(test_path, 'r', encoding='utf-8') as f:
        源码 = f.read()
    try:
        run_source(源码, file=test_path)
    except SystemExit:
        # 正常退出可以接受
        pass
