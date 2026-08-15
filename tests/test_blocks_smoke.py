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
def test_块测试跑通(test_path, capsys, tmp_path, monkeypatch):
    """每个块目录下的 `测试.jk` 必须能跑完不抛异常。

    `run_source` 底层会 tokenize + parse + evaluate 完整流程，所以既覆盖了
    块自身的 .jk 语法正确性，也覆盖了从 `blocks.<领域>.<块名>` 导入的路径解析、
    白名单反哺、依赖块加载。

    **W114（v0.24.0）：先 chdir 到 `tmp_path`。** `数据.存文` / `数据.载入` /
    `数据.序出` 三个块的自测要真写文件，此前它们用写死的
    `stdlib/blocks/数据/存文/临时_测试*.txt`（相对 cwd），于是每跑一次就往
    源码树里拉一堆产物——9 个这样的产物当年被提交进了库，还跟着进了 wheel。
    chdir 到临时目录一次性根治：产物随 pytest 自动回收，且这条保护对**所有**
    块自测生效，不只这三个。
    模块解析不受影响——`run_source(..., file=test_path)` 让 `module_loader`
    从 `test_path` 所在目录与包内 stdlib 找块，不依赖 cwd。
    """
    monkeypatch.chdir(tmp_path)
    with open(test_path, 'r', encoding='utf-8') as f:
        源码 = f.read()
    try:
        run_source(源码, file=test_path)
    except SystemExit:
        # 正常退出可以接受
        pass
