"""金标评测测试：基线可重算、与提交的基线报告一致、宏 Recall 冻结。"""

import json
from pathlib import Path

from ndecon.retrieval.eval_gold import (
    evaluate_recall_at_k,
    load_gold_cases,
    load_gold_corpus,
    run_gold_eval,
)

GOLD_DIR = Path(__file__).parent.parent / "fixtures" / "gold"
CORPUS_PATH = GOLD_DIR / "corpus.json"
CASES_PATH = GOLD_DIR / "queries.json"
BASELINE_PATH = GOLD_DIR / "baseline_report.json"

# 冻结基线：bigram BM25 在当前 CC0 金标上的 Macro Recall@10（12 章 16 问，含 2 条语义改写难例）
FROZEN_MACRO_RECALL = 0.875
EXPECTED_FAILED_CASES = {"g15-hard-responsibility", "g16-hard-protect-supply"}


def test_gold_files_are_cc0() -> None:
    """金标语料必须显式声明 CC0，防止真实原文混入仓库。"""
    corpus = load_gold_corpus(CORPUS_PATH)
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    assert corpus.license == "CC0-1.0"
    assert cases["license"] == "CC0-1.0"
    assert len(corpus.summaries) == 12


def test_report_matches_committed_baseline() -> None:
    """重算报告必须与提交的基线报告逐字段一致（任何分词/排序漂移都变红）。"""
    regenerated = run_gold_eval(CORPUS_PATH, CASES_PATH)
    committed = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert regenerated == committed


def test_frozen_macro_recall() -> None:
    """宏 Recall@10 锁定在冻结基线；下降即回归。"""
    corpus = load_gold_corpus(CORPUS_PATH)
    cases = load_gold_cases(CASES_PATH)
    report = evaluate_recall_at_k(corpus.summaries, cases, k=10)
    assert report["macro_recall"] == FROZEN_MACRO_RECALL
    assert report["case_count"] == 16


def test_hard_paraphrase_cases_are_the_known_weakness() -> None:
    """两条零字面重合的语义改写查询落空——这是 bigram 的已知短板，也是 #13 的解冻依据。"""
    corpus = load_gold_corpus(CORPUS_PATH)
    cases = load_gold_cases(CASES_PATH)
    report = evaluate_recall_at_k(corpus.summaries, cases, k=10)
    failed = {case["case_id"] for case in report["cases"] if case["recall"] < 1.0}
    assert failed == EXPECTED_FAILED_CASES
    literal = [case for case in report["cases"] if case["case_id"] not in EXPECTED_FAILED_CASES]
    assert all(case["recall"] == 1.0 for case in literal)
