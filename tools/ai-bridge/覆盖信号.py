# -*- coding: utf-8 -*-
"""覆盖判定原型 —— 两阶段拒答的第一版实现（方向 1）。

## 问题

`bench_retrieval_reject.py` 已证伪标量阈值路线：三个基于**相关度分数**的置信信号
（绝对分 / 分差 / 突出度）在近边缘负例档上留出 AUC 只有 0.52–0.56，等于随机。

根因是任务错配：TF-IDF 分数衡量的是「查询词和块描述的词面重合度」，里头**没有
「覆盖」这个概念**。「给这列数算累计求和」和「求和」块重合度当然极高——高到 4.29，
比任何真命中都自信——但库里没有块会做累计。

所以不能继续在相关度分数上找第四个信号，得换一个**方向**的量：不是「块和查询有多像」，
而是「查询里有多少东西是块解释不了的」。这是残余（residual）视角。

## 本原型量什么

统一形式：把查询切成 token，按 IDF 加权，算「被某个词表覆盖的 IDF 质量占比」。
全部落在 [0, 1]，**越大越可能有覆盖**（与 bench 的信号约定一致，可直接并列比较）。

- ``库覆盖``：查询 IDF 质量里，落在**全库词表**内的占比。
  管的是远离档——「K8s 滚动更新」大部分 token 全库没见过。
- ``域覆盖``：对 6 个域各算一次覆盖，取最大。管的也是远离档，但比库覆盖更严：
  要求命中集中在**同一个域**，而不是散落在各域。
- ``顶块覆盖``：查询 IDF 质量里，落在 **top-1 块自身词表**内的占比。
  这是冲近边缘档的主力：「累计求和」里的「累计」在 `求和` 块的文本里找不到。
- ``候选并集覆盖``：换成 top-K 所有候选块词表的并集。比顶块宽松，用来看
  「答案是不是拆在几个块里」。
- ``两阶段``：``min(域覆盖, 顶块覆盖)``——两道门都得过，即方向 1 的直接实现。

各变体都有一个 `_多字` 版本，只算长度 ≥2 的 token（丢掉单字 unigram）。中文单字
噪声大（「的」「个」「数」几乎处处都在），多字 token 更接近词。

## 词表怎么来

**全部从块索引现算，不写任何人工关键词表。** `retrieval.py` 里的 `_DOMAIN_KEYWORDS`
和 `_SYNONYMS` 是人工表，而记忆里的既有结论是「靠人工同义词表刷指标已到收益上限、
且是按 miss 明细补出来的过拟合」（v0.13.0 起的既定路线）。这里不重犯：域词表 =
该域下所有块的 名称+描述+领域+导出名+输入名 的 token 并集。

复用 `retrieval._tokenize_chinese` 保证与检索侧切词一致（私有函数，本文件是
`tools/ai-bridge/` 下的实验旁路，可以接受这层耦合；一旦某个变体被证明有效再谈
搬进 `src/jikuai/ai/` 的正式接口）。

## 用法

作为库被 `bench_retrieval_reject.py` 引用；也可直接跑看单条需求的分解：

    python tools/ai-bridge/覆盖信号.py "给这列数算累计求和"

零第三方依赖。
"""

import math
import os
import sys
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set

_HERE = os.path.abspath(os.path.dirname(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p) != _HERE]
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from jikuai.ai.retrieval import _tokenize_chinese  # noqa: E402


def _块文本(block: dict) -> str:
    """一个块的全部可见文本：名称 + 领域 + 描述 + 导出名 + 输入形参名。

    比 `_TFIDFIndex` 的语料多了导出名和输入形参名——判覆盖时它们有用
    （「按账龄计提」里的「账龄」如果只在某块形参名里出现，也算库里认识）。
    """
    parts = [block.get('名称', ''), block.get('描述', '')]
    parts.extend(block.get('领域') or [])
    parts.extend(block.get('导出') or [])
    for 入 in block.get('输入') or []:
        名 = 入.get('名') if isinstance(入, dict) else None
        if 名:
            parts.append(名)
    return ' '.join(p for p in parts if p)


class 覆盖模型:
    """从块索引现算的词表 + IDF。构造一次可重复查询。"""

    def __init__(self, blocks: List[dict]):
        self._blocks = blocks
        self._N = len(blocks) or 1

        self._块词表: List[FrozenSet[str]] = []
        df: Dict[str, int] = {}
        域累积: Dict[str, Set[str]] = {}

        for block in blocks:
            toks = frozenset(_tokenize_chinese(_块文本(block)))
            self._块词表.append(toks)
            for t in toks:
                df[t] = df.get(t, 0) + 1
            for 域 in block.get('领域') or []:
                域累积.setdefault(域, set()).update(toks)

        self._idf: Dict[str, float] = {
            t: math.log((self._N + 1) / (f + 1)) + 1.0 for t, f in df.items()
        }
        #: 未登录 token 的 IDF：按 df=0 算，取得到的最大值
        self._未登录idf = math.log((self._N + 1) / 1.0) + 1.0
        self._全库词表: FrozenSet[str] = frozenset(df)
        self._域词表: Dict[str, FrozenSet[str]] = {
            域: frozenset(ts) for 域, ts in 域累积.items()
        }
        self._名到序: Dict[str, int] = {
            b.get('名称', ''): i for i, b in enumerate(blocks)
        }
        #: 查询侧虚词，由 `装载查询语料` 从调优集统计得到；默认空。
        self._虚词: FrozenSet[str] = frozenset()

    # -- 基础件 ---------------------------------------------------------

    def idf(self, token: str) -> float:
        return self._idf.get(token, self._未登录idf)

    def 查询词(self, query: str, 多字: bool = False,
             限内: bool = False, 去虚: bool = False) -> List[str]:
        """切词并按开关过滤。

        - ``多字``：丢掉单字 unigram（中文单字噪声大）
        - ``限内``：只留**块库词表里出现过**的 token（df ≥ 1）。这一刀是第三轮
          实测定下来的关键修正：`帮我`/`把这堆`/`K8s` 这类 df=0 的 token 在
          原版里拿满额 IDF 进分母且永远算「未覆盖」，把正例的顶块覆盖也一起
          压到中位 0.10，信号被自己的分母淹掉。剔掉后「库里根本没这个词」和
          「库里有这个词但这个块没有」变成两件独立的事，各由一个信号管。
        - ``去虚``：按**查询侧 df** 剔高频措辞词（见 `装载查询语料`）。与 `限内`
          正交：`限内` 管 df=0 的库外词，`去虚` 管在库内但满查询都出现的水词。
        """
        toks = _tokenize_chinese(query)
        if 多字:
            toks = [t for t in toks if len(t) >= 2]
        if 限内:
            toks = [t for t in toks if t in self._全库词表]
        if 去虚 and self._虚词:
            toks = [t for t in toks if t not in self._虚词]
        # 去重：覆盖率算的是「有没有被解释」，重复出现不该加权
        return sorted(set(toks))

    def 覆盖率(self, query: str, 词表: Iterable[str], 多字: bool = False,
             限内: bool = False, 去虚: bool = False) -> float:
        """查询 IDF 质量里被 `词表` 覆盖的占比。无有效 token 时返回 0.0。"""
        toks = self.查询词(query, 多字, 限内, 去虚)
        if not toks:
            return 0.0
        词表 = 词表 if isinstance(词表, (set, frozenset)) else frozenset(词表)
        总 = 0.0
        中 = 0.0
        for t in toks:
            w = self.idf(t)
            总 += w
            if t in 词表:
                中 += w
        return (中 / 总) if 总 > 0 else 0.0

    # -- 查询侧虚词（统计式，非人工表）-----------------------------------

    def 装载查询语料(self, queries: Sequence[str], 占比阈: float = 0.15) -> int:
        """从**调优集**查询统计措辞词，出现在 ≥`占比阈` 比例查询里的 token 判虚词。

        为什么不手搓停用词表：记忆里的既定结论是 `_SYNONYMS` 这类人工表已到收益
        上限、且是按 miss 明细补出来的过拟合（v0.13.0 起）。所以这里让虚词也从
        语料现算。**只能喂调优集查询**——喂留出集就等于看裁判的答案调参。

        返回判出的虚词个数。
        """
        n = len(queries)
        if n == 0:
            self._虚词 = frozenset()
            return 0
        计: Dict[str, int] = {}
        for q in queries:
            for t in set(_tokenize_chinese(q)):
                计[t] = 计.get(t, 0) + 1
        门 = max(2, int(占比阈 * n))
        self._虚词 = frozenset(t for t, c in 计.items() if c >= 门)
        return len(self._虚词)

    @property
    def 虚词(self) -> FrozenSet[str]:
        return self._虚词

    # -- 各信号 ---------------------------------------------------------

    def 库覆盖(self, query: str, 多字: bool = False,
             去虚: bool = False) -> float:
        """查询里有多少 IDF 质量是块库词表见过的。管远离档，**不能加 `限内`**
        （限内会把 df=0 的词从分母里剔掉，这个信号的全部信息就在那些词上）。"""
        return self.覆盖率(query, self._全库词表, 多字, 限内=False, 去虚=去虚)

    def 域覆盖(self, query: str, 多字: bool = False, 限内: bool = False,
             去虚: bool = False) -> float:
        if not self._域词表:
            return 0.0
        return max(self.覆盖率(query, ts, 多字, 限内, 去虚)
                   for ts in self._域词表.values())

    def 最佳域(self, query: str, 多字: bool = False, 限内: bool = False,
             去虚: bool = False) -> Optional[str]:
        if not self._域词表:
            return None
        return max(self._域词表, key=lambda 域: self.覆盖率(
            query, self._域词表[域], 多字, 限内, 去虚))

    def 块词表(self, 块名: str) -> FrozenSet[str]:
        i = self._名到序.get(块名)
        return self._块词表[i] if i is not None else frozenset()

    def 顶块覆盖(self, query: str, 候选名: Sequence[str], 多字: bool = False,
              限内: bool = False, 去虚: bool = False) -> float:
        if not 候选名:
            return 0.0
        return self.覆盖率(query, self.块词表(候选名[0]), 多字, 限内, 去虚)

    def 候选并集覆盖(self, query: str, 候选名: Sequence[str],
                多字: bool = False, 限内: bool = False,
                去虚: bool = False) -> float:
        if not 候选名:
            return 0.0
        并: Set[str] = set()
        for 名 in 候选名:
            并 |= self.块词表(名)
        return self.覆盖率(query, 并, 多字, 限内, 去虚)

    # -- 诊断 -----------------------------------------------------------

    def 未解释词(self, query: str, 候选名: Sequence[str], 多字: bool = True,
             限内: bool = True, 去虚: bool = False) -> List[str]:
        """top-1 块解释不了的查询 token，按 IDF 降序。给拒答理由用。

        默认 `限内=True`：只报「库里认识但这个块没有」的词——那才是可解释的
        拒答依据。库外词（`K8s`）归 `库覆盖` 那条线去说。
        """
        词表 = self.块词表(候选名[0]) if 候选名 else frozenset()
        剩 = [t for t in self.查询词(query, 多字, 限内, 去虚) if t not in 词表]
        return sorted(剩, key=lambda t: -self.idf(t))


_缓存: Optional[覆盖模型] = None


def 取模型(blocks: Optional[List[dict]] = None) -> 覆盖模型:
    """进程级缓存。传 blocks 则强制重建。"""
    global _缓存
    if blocks is not None:
        _缓存 = 覆盖模型(blocks)
    elif _缓存 is None:
        from jikuai.pkg.blocks import load_index
        index = load_index() or {}
        _缓存 = 覆盖模型(index.get('块') or [])
    return _缓存


def _main(argv: List[str]) -> int:
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                      errors='replace', line_buffering=True)
    except Exception:
        pass
    if not argv:
        print('用法：python tools/ai-bridge/覆盖信号.py "<需求>"')
        return 2
    query = argv[0]

    from jikuai.ai import Retriever
    from jikuai.pkg.blocks import load_index
    blocks = (load_index() or {}).get('块') or []
    模型 = 取模型(blocks)
    hits = Retriever(blocks, vector_index=None).retrieve(query, top=5)
    名单 = [h.name for h in hits]

    print('需求：%s' % query)
    print('top-5：%s' % '、'.join('%s(%.3f)' % (h.name, h.score) for h in hits))
    print('最佳域：%s' % 模型.最佳域(query, 多字=True, 限内=True))
    print('库覆盖=%.3f（全 token，管远离档）' % 模型.库覆盖(query))
    for 限内 in (False, True):
        标 = '限内' if 限内 else '原版'
        print('[%s] 域覆盖=%.3f 顶块覆盖=%.3f 并集覆盖=%.3f'
              % (标, 模型.域覆盖(query, 限内=限内),
                 模型.顶块覆盖(query, 名单, 限内=限内),
                 模型.候选并集覆盖(query, 名单, 限内=限内)))
    print('top-1 未解释词（限内）：%s'
          % '、'.join(模型.未解释词(query, 名单)[:12]))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main(sys.argv[1:]))
