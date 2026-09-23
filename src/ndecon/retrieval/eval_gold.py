"""金标评测：在 CC0 自造语料上度量 L1 检索的 Recall@k。

这是 ADR-0004 的"引入门"产物：
- L2 向量检索（#13）唯有在本报告证明 L1 bigram BM25 召回不足时才允许解冻；
- 报告可从金标文件完全重算，CI 将重算结果与提交的基线报告逐字段比对，
  任何无意识的排序/分词漂移都会让测试变红；
- 金标语料必须是自造 CC0 文本，真实小说原文不进仓库。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from ndecon.domain.models import ChapterSummary
from ndecon.retrieval.bm25 import BM25_B, BM25_K1, CONTROLLED_TERM_BOOST
from ndecon.retrieval.indexer import SUMMARY_DOC_PREFIX, build_index, documents_from_summaries

TOKENIZER_ID = "cjk-bigram-v1"
REPORT_VERSION = 1
# 报告中浮点保留位数：足够区分差异，又保证跨平台二进制稳定
SCORE_DIGITS = 6


class GoldCase(BaseModel):
    """一条金标查询：query 文本 + 应召回的章号集合。"""

    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    relevant_orders: list[int] = Field(min_length=1)


class GoldCorpus(BaseModel):
    """金标语料文件：自造 CC0 小书的章节摘要集。"""

    book_title: str = Field(min_length=1)
    license: str = Field(min_length=1, description="必须为 CC0-1.0 等自由许可声明")
    summaries: list[ChapterSummary] = Field(min_length=1)


def load_gold_corpus(path: str | Path) -> GoldCorpus:
    """读取金标语料 JSON 并校验 schema。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return GoldCorpus(**data)


def load_gold_cases(path: str | Path) -> list[GoldCase]:
    """读取金标查询集 JSON。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [GoldCase(**item) for item in data["cases"]]


def evaluate_recall_at_k(
    summaries: list[ChapterSummary],
    cases: list[GoldCase],
    *,
    k: int = 10,
) -> dict:
    """在语料上跑全部金标查询，产出可序列化的 Recall@k 报告字典。

    只统计 chapter_summary 类文档的章号命中；宏平均为各查询 recall 的算术平均。
    """
    documents = documents_from_summaries(summaries)
    index = build_index(documents)
    order_by_doc = {doc.doc_id: doc.order for doc in documents}

    case_reports: list[dict] = []
    for case in cases:
        hits = index.search(case.query, k=k)
        retrieved_orders = [
            order_by_doc[hit.doc_id]
            for hit in hits
            if hit.doc_id.startswith(SUMMARY_DOC_PREFIX) and order_by_doc.get(hit.doc_id) is not None
        ]
        relevant = set(case.relevant_orders)
        hit_orders = sorted(set(retrieved_orders) & relevant)
        recall = len(hit_orders) / len(relevant)
        case_reports.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "relevant_orders": sorted(relevant),
                "retrieved_orders": retrieved_orders,
                "hit_orders": hit_orders,
                "recall": round(recall, SCORE_DIGITS),
            }
        )

    macro_recall = sum(item["recall"] for item in case_reports) / len(case_reports) if case_reports else 0.0
    return {
        "version": REPORT_VERSION,
        "eval": "recall_at_k",
        "k": k,
        "case_count": len(case_reports),
        "macro_recall": round(macro_recall, SCORE_DIGITS),
        "params": {
            "tokenizer": TOKENIZER_ID,
            "k1": BM25_K1,
            "b": BM25_B,
            "controlled_term_boost": CONTROLLED_TERM_BOOST,
        },
        "cases": case_reports,
    }


def run_gold_eval(corpus_path: str | Path, cases_path: str | Path, *, k: int = 10) -> dict:
    """便捷入口：从两个金标文件直接产出报告。"""
    corpus = load_gold_corpus(corpus_path)
    cases = load_gold_cases(cases_path)
    return evaluate_recall_at_k(corpus.summaries, cases, k=k)
