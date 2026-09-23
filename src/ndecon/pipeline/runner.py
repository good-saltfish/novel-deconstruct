"""端到端 runner：split（纯本地）与 analyze（可插拔 provider）。"""

from __future__ import annotations

from ndecon.domain.models import ReportBundle
from ndecon.ingest.splitter import SplitResult, split_chapters
from ndecon.pipeline.stage0 import build_entries
from ndecon.pipeline.stage3 import aggregate
from ndecon.providers.base import DeconstructProvider

# 黄金三章固定为前 3 章（顺序章号前提下；若切分告警章号非连续，只取前三章边界）
_GOLDEN_COUNT = 3


def run_split(text: str) -> SplitResult:
    """执行章节切分；不识别章节时由 splitter 抛错。"""
    return split_chapters(text)


def run_analyze(text: str, book_title: str, provider: DeconstructProvider) -> ReportBundle:
    """跑完整 v0.1 管道：切分 → 索引 → 逐章摘要 → 黄金三章报告。"""
    split = split_chapters(text)
    entries = build_entries(split)

    summaries = []
    for boundary, entry in zip(split.boundaries, entries):
        chapter_text = text[boundary.start : boundary.end]
        summaries.append(
            provider.summarize_chapter(
                order=boundary.order,
                title=boundary.title,
                chapter_text=chapter_text,
                chapter_hash=entry.content_hash,
            )
        )

    golden = []
    for boundary, entry in list(zip(split.boundaries, entries))[:_GOLDEN_COUNT]:
        chapter_text = text[boundary.start : boundary.end]
        golden.append(
            provider.golden_report(
                order=boundary.order,
                title=boundary.title,
                chapter_text=chapter_text,
                chapter_hash=entry.content_hash,
            )
        )

    first_chapter_text = text[split.boundaries[0].start : split.boundaries[0].end]
    thin_summary = provider.thin_summary(first_chapter_text, book_title)
    aggregation = aggregate(book_title, summaries)

    return ReportBundle(
        book_title=book_title,
        thin_summary=thin_summary,
        entries=entries,
        summaries=summaries,
        golden_reports=golden,
        aggregation=aggregation,
        warnings=list(split.warnings),
    )
