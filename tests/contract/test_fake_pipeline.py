"""Fake 端到端契约测试：证据可回原文、确定性可重放、不代笔学习层。"""

from pathlib import Path

from ndecon.ingest.splitter import split_chapters
from ndecon.pipeline.runner import run_analyze
from ndecon.providers.fake import FakeProvider

FIXTURE = Path(__file__).parent.parent / "fixtures" / "synthetic_novel.txt"


def _bundle():
    """对 CC0 合成小说跑完整 fake 管道。"""
    text = FIXTURE.read_text(encoding="utf-8")
    return run_analyze(text, "合成测试书", FakeProvider()), text


def test_bundle_shape_and_counts() -> None:
    """4 章摘要 + 3 份黄金三章报告；每章情节点不少于硬下限 10。"""
    bundle, _ = _bundle()
    assert len(bundle.entries) == 4
    assert len(bundle.summaries) == 4
    assert len(bundle.golden_reports) == 3
    assert all(len(s.plot_points) >= 10 for s in bundle.summaries)
    assert bundle.thin_summary


def test_every_quote_locates_in_chapter_text() -> None:
    """每条情节点证据必须按偏移在对应章节原文中定位（可追溯率 100%）。"""
    bundle, text = _bundle()
    boundaries = split_chapters(text).boundaries
    for entry, summary in zip(bundle.entries, bundle.summaries):
        boundary = next(b for b in boundaries if b.order == summary.order)
        chapter_text = text[boundary.start : boundary.end]
        assert entry.content_hash
        for point in summary.plot_points:
            assert point.source.is_located_in(chapter_text)


def test_fake_never_writes_takeaways() -> None:
    """学习层"可借鉴要素"必须留空——fake 红线：不代笔评论。"""
    bundle, _ = _bundle()
    assert all(report.takeaways == [] for report in bundle.golden_reports)


def test_fake_is_deterministic() -> None:
    """相同输入两次运行产物逐字节一致（可重放、可回归对比）。"""
    text = FIXTURE.read_text(encoding="utf-8")
    first = run_analyze(text, "合成测试书", FakeProvider()).model_dump_json()
    second = run_analyze(text, "合成测试书", FakeProvider()).model_dump_json()
    assert first == second


def test_provenance_marks_rule_fake() -> None:
    """所有模型产物必须带 rule:fake 来源与版本信息。"""
    bundle, _ = _bundle()
    assert all(s.model_id == "rule:fake" for s in bundle.summaries)
    assert all(g.model_id == "rule:fake" for g in bundle.golden_reports)
    assert all(s.prompt_version == "fake-v0" for s in bundle.summaries)
