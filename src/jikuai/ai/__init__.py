# -*- coding: utf-8 -*-
"""极快 AI 桥接的**运行时**部分（ADR-25 §3.1）。

这个包的边界是一条硬约束：**只用 Python 标准库**。torch / onnxruntime /
numpy / scikit-learn 一个都不许进来——它们属于 `tools/ai-bridge/`（离线索引
生成侧，装 `requirements-ai.txt` 才跑）。理由见 ADR-25 §2：`pip install
jikuai` 之后什么都不用装，是极快的核心卖点，优先于检索质量。

对外只暴露检索层：

    from jikuai.ai import retrieve, Retriever, 检索路径

`retrieve()` 是便捷入口（进程级缓存索引），`Retriever` 给需要自带块列表或
自带向量索引的调用方（测试、LSP、桥接工具）。
"""

from .retrieval import (  # noqa: F401
    MODE_AUTO,
    MODE_ENV,
    MODE_HEURISTIC,
    MODE_NEURAL,
    PATH_HEURISTIC,
    PATH_NEURAL,
    PATH_SEMANTIC,
    Hit,
    RetrievalError,
    Retriever,
    VectorIndex,
    describe,
    load_vector_index,
    reset_cache,
    retrieve,
    vector_index_path,
)

__all__ = [
    'MODE_AUTO', 'MODE_ENV', 'MODE_HEURISTIC', 'MODE_NEURAL',
    'PATH_HEURISTIC', 'PATH_NEURAL', 'PATH_SEMANTIC',
    'Hit', 'RetrievalError', 'Retriever', 'VectorIndex',
    'describe', 'load_vector_index', 'reset_cache', 'retrieve',
    'vector_index_path',
]
