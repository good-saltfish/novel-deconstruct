"""L1 一致性检索（ADR-0004）：中文 bigram BM25 + 确定性上下文包。

公共入口：
- :func:`tokenize` / :class:`BM25Index` / :class:`IndexedDocument` —— 分词与索引；
- :func:`build_project_index` —— 只索引本书已确认部件 + 已写章节摘要；
- :func:`build_context_pack` —— 章节生成前的确定性上下文包；
- :func:`extract_foreshadows` / :func:`open_foreshadows_at` —— 伏笔台账；
- :func:`run_gold_eval` / :func:`evaluate_recall_at_k` —— Recall@k 金标评测。
"""

from __future__ import annotations

from ndecon.retrieval.bm25 import (
    BM25Index,
    IndexedDocument,
    ScoredDoc,
    controlled_terms,
)
from ndecon.retrieval.context import ContextPack, RetrievedDoc, build_context_pack
from ndecon.retrieval.eval_gold import (
    GoldCase,
    GoldCorpus,
    evaluate_recall_at_k,
    load_gold_cases,
    load_gold_corpus,
    run_gold_eval,
)
from ndecon.retrieval.foreshadow import (
    ForeshadowEntry,
    extract_foreshadows,
    open_foreshadows_at,
)
from ndecon.retrieval.indexer import (
    build_index,
    build_project_index,
    documents_from_creation,
    documents_from_summaries,
)
from ndecon.retrieval.tokenize import normalize_text, tokenize

__all__ = [
    "BM25Index",
    "ContextPack",
    "ForeshadowEntry",
    "GoldCase",
    "GoldCorpus",
    "IndexedDocument",
    "RetrievedDoc",
    "ScoredDoc",
    "build_context_pack",
    "build_index",
    "build_project_index",
    "controlled_terms",
    "documents_from_creation",
    "documents_from_summaries",
    "evaluate_recall_at_k",
    "extract_foreshadows",
    "load_gold_cases",
    "load_gold_corpus",
    "normalize_text",
    "open_foreshadows_at",
    "run_gold_eval",
    "tokenize",
]
