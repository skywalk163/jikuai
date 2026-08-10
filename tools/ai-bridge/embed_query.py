# -*- coding: utf-8 -*-
"""查询向量 sidecar —— ADR-25 §3.1 神经路径的「调用方本地推理」职责（W11）。

主发布包 `src/jikuai/` 严格零运行时依赖，绝不 import torch /
sentence-transformers / numpy（ADR-25 §2 红线）。查询文本变向量这一步的推理
被拆到本脚本里，主发布包用 subprocess 拉取——这样运行时既拿得到神经检索质量，
又不把几百 MB 的依赖压到普通用户头上。

**向量口径必须与 `generate_embeddings.py` 完全一致**：同模型
（``shibing624/text2vec-base-chinese``）、同归一化（``normalize_embeddings=True``）。
否则查询向量与索引向量不同源，余弦相似度算出来是垃圾。

两种模式：

* 默认（一发一收）：从 stdin 读**一行**需求 → stdout 打**一行** JSON 数组 →
  退出。适合 CLI 一次性调用。
* ``--daemon``：stdin **逐行**读，每读到一行需求就回一行 JSON 数组；模型只
  加载一次，省掉每次 ~10s 的冷启动。读到 EOF 退出。适合 REPL / LSP 常驻。

失败契约（调用方靠 **stdout 的可解析性** 判定成败）：

* 模型加载失败 / 依赖缺失 / 编码异常 → **stderr 打人读原因 + 非零退出码**，
  且**绝不往 stdout 打半个 JSON**。调用方读不到合法 JSON 数组即判失败，
  降级到启发式。

离线用法（本机连不上 huggingface，模型已在本地缓存）::

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\
        python tools/ai-bridge/embed_query.py < 需求.txt

见 ``--help`` 尾部说明。
"""

import argparse
import json
import sys

#: 与 generate_embeddings.py 的 DEFAULT_MODEL 同源。改这里必须同步改那边，
#: 否则查询向量与索引向量不同源。
DEFAULT_MODEL = 'shibing624/text2vec-base-chinese'


def _load_model(model_name):
    """加载 sentence-transformers 模型。任何异常向上抛给 main 统一处理。"""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(model_name)


def _encode_one(model, text):
    """把一行文本编码成 float32 向量列表。

    ``normalize_embeddings=True`` 与 generate_embeddings.py 的 `_encode`
    完全一致——索引向量归一化了，查询向量也必须归一化，余弦相似度才有意义。
    """
    import numpy as np
    vec = model.encode([text], normalize_embeddings=True)[0]
    return [float(x) for x in np.asarray(vec, dtype=np.float32).tolist()]


def _emit(vec):
    """往 stdout 打一行 JSON 数组并 flush（daemon 模式下调用方按行读）。"""
    sys.stdout.write(json.dumps(vec, ensure_ascii=False))
    sys.stdout.write('\n')
    sys.stdout.flush()


def _build_parser():
    parser = argparse.ArgumentParser(
        prog='embed_query.py',
        description=(
            '极快查询向量 sidecar（ADR-25 §3.1 神经路径）。\n'
            '默认：stdin 读一行需求 → stdout 打一行 JSON 数组 → 退出。\n'
            '向量口径与 generate_embeddings.py 完全一致（同模型、同归一化）。'
        ),
        epilog=(
            '离线用法（本机连不上 huggingface，模型需预先缓存好）：\n'
            '  设环境变量 HF_HUB_OFFLINE=1 与 TRANSFORMERS_OFFLINE=1\n'
            '  PowerShell:  $env:HF_HUB_OFFLINE=1; $env:TRANSFORMERS_OFFLINE=1\n'
            '  bash:        HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python ...\n'
            '\n'
            '失败时 stderr 打原因、非零退出，stdout 保持空白——调用方靠 stdout\n'
            '能否解析成 JSON 数组来判定成败，据此决定是否降级到启发式检索。'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--model', default=DEFAULT_MODEL,
        help='embedding 模型名（默认 %(default)s，须与索引生成时一致）')
    parser.add_argument(
        '--daemon', action='store_true',
        help='逐行 daemon 模式：stdin 每行一条需求 → stdout 每行一个向量；'
             '模型只加载一次，EOF 退出')
    return parser


def _reconfigure_utf8(stream):
    """把一条文本流切到 UTF-8，尽力而为不抛异常。

    Windows 默认 stdin/stdout 是 GBK：主发布包用 ``encoding='utf-8'`` 往子进程
    stdin 写 UTF-8 字节，子进程若按 GBK 解码，中文需求会被解成乱码，喂给
    tokenizer 直接报 ``TextEncodeInput must be ...``。这里跟 blocks_cli 同一招把
    两端都钉到 UTF-8。``reconfigure`` 在 Python 3.7+ 才有，探测存在再调。
    """
    reconfigure = getattr(stream, 'reconfigure', None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding='utf-8')
    except Exception:
        pass


def main(argv=None):
    args = _build_parser().parse_args(argv)

    # stdin 收中文需求、stdout 回 JSON——两端都钉 UTF-8，避免 Windows GBK 把
    # 中文查询解成乱码后 tokenizer 崩溃。
    _reconfigure_utf8(sys.stdin)
    _reconfigure_utf8(sys.stdout)

    # 模型加载失败：打人读原因，非零退出，stdout 一个字都不吐。
    try:
        model = _load_model(args.model)
    except ImportError as e:
        print('embed_query: 缺少依赖（请 pip install -r '
              'tools/ai-bridge/requirements-ai.txt）：%s' % e, file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001  —— 模型加载可能抛各式后端异常
        print('embed_query: 模型加载失败：%s' % e, file=sys.stderr)
        return 2

    if args.daemon:
        for line in sys.stdin:
            text = line.strip()
            if not text:
                continue
            try:
                vec = _encode_one(model, text)
            except Exception as e:  # noqa: BLE001
                # daemon 下一条编码失败即整体退出——避免调用方把某一行的空白
                # 当成合法结果继续等下一行。
                print('embed_query: 编码失败：%s' % e, file=sys.stderr)
                return 3
            _emit(vec)
        return 0

    # 默认一发一收模式。
    text = sys.stdin.readline().strip()
    if not text:
        print('embed_query: stdin 无输入', file=sys.stderr)
        return 4
    try:
        vec = _encode_one(model, text)
    except Exception as e:  # noqa: BLE001
        print('embed_query: 编码失败：%s' % e, file=sys.stderr)
        return 3
    _emit(vec)
    return 0


if __name__ == '__main__':
    sys.exit(main())
