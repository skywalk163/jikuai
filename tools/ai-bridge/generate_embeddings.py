# -*- coding: utf-8 -*-
"""向量索引生成脚本 —— ADR-25 §3.2 索引生成层。

本脚本的依赖在 `requirements-ai.txt`——只有维护者/CI 打标节点需要安装。
日常 `pip install jikuai` 不需要这些依赖。

用法::

    pip install -r tools/ai-bridge/requirements-ai.txt
    python tools/ai-bridge/generate_embeddings.py

功能：
1. 读取 `stdlib/blocks/索引.json` 拿全部块
2. 对每个块的 `名称 + 领域 + 描述` 用 text2vec-base-chinese 生成 embedding
3. 量化为 int16 落盘 `stdlib/blocks/向量索引.bin`
4. 生成 sidecar `stdlib/blocks/向量索引.元信息.json`

索引格式见 ADR-25 §4 数据格式契约。
"""

import argparse
import json
import os
import struct
import sys
from datetime import datetime
from typing import List, Optional, Tuple

_HERE = os.path.abspath(os.path.dirname(__file__))
# Python 启动脚本时会把脚本目录塞进 sys.path[0]。本目录下有 select.py（关键词
# 匹配器），会遮蔽标准库 select 模块——httpx/httpcore 下载模型时 import select
# 就炸了。启动瞬间就剔掉，任何后续 import 都不再看得见本目录。
sys.path[:] = [p for p in sys.path if os.path.abspath(p) != _HERE]
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.pkg.blocks import (  # noqa: E402
    BLOCK_INDEX_NAME,
    blocks_content_hash,
    blocks_root,
)

# ---------------------------------------------------------------------------
# 常量（与 src/jikuai/ai/retrieval.py 保持一致）
# ---------------------------------------------------------------------------

MAGIC = b'JKBV'
FORMAT_VERSION = 1
DEFAULT_MODEL = 'shibing624/text2vec-base-chinese'
DEFAULT_DIM = 768  # text2vec-base-chinese 实际输出维；仅作参考，落盘维度取 encode 结果


# ---------------------------------------------------------------------------
# Embedding 生成
# ---------------------------------------------------------------------------


def _load_model(model_name: str):
    """加载 sentence-transformers 模型。"""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print('错误：请先安装依赖：pip install -r tools/ai-bridge/requirements-ai.txt',
              file=sys.stderr)
        sys.exit(1)
    print(f'加载模型：{model_name} ...')
    return SentenceTransformer(model_name)


def _build_texts(blocks: List[dict]) -> List[str]:
    """构造嵌入文本：`名称，领域，描述`。

    留出集横评（v0.13.0 P2）测过四种组合（描述+示例 / 名+域+描述 /
    +导出名 / +全示例），只有本方案在留出集上把 Recall@3 从 72% 抬到
    84%。加示例反而掉 4pp——`从 blocks.X.Y 导入` 是每块一样的样板，
    对比学习出的中文语义模型把它当噪声；拟古导出名（缴税/圆分/聚簇）
    也不在模型训练分布里，一起进语料只会拉低召回。
    """
    texts = []
    for block in blocks:
        parts = [
            block.get('名称', ''),
            '/'.join(block.get('领域', [])),
            block.get('描述', ''),
        ]
        texts.append('，'.join(p for p in parts if p))
    return texts


def _encode(model, texts: List[str]) -> 'numpy.ndarray':
    """批量编码文本为向量。"""
    import numpy as np
    print(f'编码 {len(texts)} 条文本 ...')
    embeddings = model.encode(texts, show_progress_bar=True, normalize_embeddings=True)
    return np.array(embeddings, dtype=np.float32)


# ---------------------------------------------------------------------------
# 量化 & 序列化
# ---------------------------------------------------------------------------


def _quantize_symmetric(vectors: 'numpy.ndarray') -> Tuple['numpy.ndarray', float, float]:
    """对称量化 float32 → int16。

    映射：[global_min, global_max] → [-32768, 32767]
    """
    import numpy as np
    vmin = float(vectors.min())
    vmax = float(vectors.max())
    # 避免零除
    if vmax - vmin < 1e-10:
        vmax = vmin + 1e-10
    scale = 65535.0 / (vmax - vmin)
    quantized = np.clip(
        np.round((vectors - vmin) * scale - 32768),
        -32768, 32767
    ).astype(np.int16)
    return quantized, vmin, vmax


def _write_index(path: str, names: List[str], quantized: 'numpy.ndarray',
                 dim: int, qmin: float, qmax: float) -> None:
    """写 `向量索引.bin`（ADR-25 §4 格式）。"""
    count = len(names)
    with open(path, 'wb') as f:
        f.write(MAGIC)
        f.write(struct.pack('<HH', FORMAT_VERSION, dim))
        f.write(struct.pack('<I', count))
        f.write(struct.pack('<ff', qmin, qmax))
        for i, name in enumerate(names):
            name_bytes = name.encode('utf-8')
            f.write(struct.pack('<H', len(name_bytes)))
            f.write(name_bytes)
            f.write(quantized[i].tobytes())
    size_kb = os.path.getsize(path) / 1024
    print(f'写入 {path} ({size_kb:.1f} KB, {count} 块, {dim} 维)')


def _write_meta(path: str, model_name: str, model_version: str,
                dim: int, count: int, blocks_hash: str) -> None:
    """写 sidecar 元信息 JSON。"""
    meta = {
        '格式版本': FORMAT_VERSION,
        '模型': model_name,
        '模型版本': model_version,
        '维度': dim,
        '量化': 'int16-symmetric',
        '块数': count,
        '块哈希': blocks_hash,
        '生成时间': datetime.now().replace(microsecond=0).isoformat(),
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f'写入 {path}')


def _compute_blocks_hash(blocks: List[dict]) -> str:
    """G12 用的块内容哈希。委托给 `jikuai.pkg.blocks.blocks_content_hash`——
    生成端与校验端（`scripts/check_stdlib_contract.py`）共享同一实现，避免哈希
    算法两处漂移导致 G12 假报警。"""
    return blocks_content_hash(blocks)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='极快向量索引生成器（ADR-25 §3.2）')
    parser.add_argument('--model', default=DEFAULT_MODEL,
                        help=f'embedding 模型名（默认 {DEFAULT_MODEL}）')
    parser.add_argument('--model-version', default='1.0.0',
                        help='模型版本标签')
    parser.add_argument('--output-dir', default=None,
                        help='输出目录（默认 stdlib/blocks/）')
    parser.add_argument('--dry-run', action='store_true',
                        help='只加载和编码，不写文件')
    args = parser.parse_args(argv)

    # 1. 读索引
    root = blocks_root()
    idx_path = os.path.join(root, BLOCK_INDEX_NAME)
    if not os.path.isfile(idx_path):
        print(f'错误：索引文件不存在：{idx_path}', file=sys.stderr)
        return 1
    with open(idx_path, 'r', encoding='utf-8') as f:
        index = json.load(f)
    blocks = index.get('块', [])
    if not blocks:
        print('错误：索引中无块', file=sys.stderr)
        return 1
    print(f'读取 {len(blocks)} 个块')

    # 2. 构建文本 & 编码
    texts = _build_texts(blocks)
    model = _load_model(args.model)
    vectors = _encode(model, texts)
    dim = vectors.shape[1]
    print(f'向量维度：{dim}')

    if args.dry_run:
        print('--dry-run：跳过写盘')
        return 0

    # 3. 量化
    quantized, qmin, qmax = _quantize_symmetric(vectors)
    print(f'量化范围：[{qmin:.6f}, {qmax:.6f}]')

    # 4. 写文件
    output_dir = args.output_dir or root
    names = [b.get('名称', '') for b in blocks]
    bin_path = os.path.join(output_dir, '向量索引.bin')
    meta_path = os.path.join(output_dir, '向量索引.元信息.json')

    _write_index(bin_path, names, quantized, dim, qmin, qmax)
    blocks_hash = _compute_blocks_hash(blocks)
    _write_meta(meta_path, args.model, args.model_version, dim, len(blocks), blocks_hash)

    print('完成。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
