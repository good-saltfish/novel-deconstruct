"""Stage 3 确定性聚合测试：基调众数、爽点章距、主题分布、角色矩阵。"""

import pytest

from ndecon.domain.enums import ThemeTag, Tone
from ndecon.domain.models import ChapterSummary, PlotPoint, SourceRef
from ndecon.pipeline.stage3 import aggregate


def _ref(order: int, quote: str = "证据原文短句") -> SourceRef:
    """构造指定章节的证据引用（偏移经测试 quote 固定为前 8 字）。"""
    return SourceRef(
        chapter_id=f"CH{order:04d}",
        start=0,
        end=len(quote),
        quote=quote,
        content_hash="h" * 8,
    )


def _summary(order: int, tones: list[Tone], *, characters: list[str] | None = None) -> ChapterSummary:
    """按给定基调序列构造一章摘要；每个情节点带固定主题与人物，便于断言。"""
    points = [
        PlotPoint(
            index=i + 1,
            summary=f"情节点{i + 1}",
            tone=tone,
            theme_tags=[ThemeTag.GROWTH if tone is Tone.SATISFYING else ThemeTag.SUSPENSE],
            characters=(["阿伏"] if tone is Tone.SATISFYING else []),
            source=_ref(order),
        )
        for i, tone in enumerate(tones)
    ]
    return ChapterSummary(
        model_id="rule:test",
        prompt_version="test-v0",
        chapter_id=f"CH{order:04d}",
        order=order,
        title=f"第{order}章",
        gist="概要",
        plot_points=points,
        characters=characters or [],
    )


def test_aggregate_rejects_empty() -> None:
    """没有摘要时必须抛错，不得产出空聚合报告。"""
    with pytest.raises(ValueError):
        aggregate("空书", [])


def test_rhythm_dominant_tone_and_satisfying_positions() -> None:
    """主导基调取众数；爽点情节点序号被正确收集。"""
    agg = aggregate("测试书", [_summary(1, [Tone.TENSE, Tone.TENSE, Tone.SATISFYING])])
    rhythm = agg.chapter_rhythms[0]
    assert rhythm.dominant_tone is Tone.TENSE
    assert rhythm.satisfying_point_indexes == [3]
    assert rhythm.tone_counts[Tone.TENSE.value] == 2
    assert rhythm.tone_counts[Tone.SATISFYING.value] == 1


def test_pacing_gaps_between_satisfying_chapters() -> None:
    """爽点章距只统计相邻含爽点章节：第2与第4章 → 间距 2。"""
    summaries = [
        _summary(1, [Tone.OPPRESSIVE]),
        _summary(2, [Tone.SATISFYING]),
        _summary(3, [Tone.TENSE]),
        _summary(4, [Tone.SATISFYING, Tone.SATISFYING]),
        _summary(5, [Tone.TENSE]),
    ]
    agg = aggregate("测试书", summaries)
    assert agg.pacing.satisfying_chapters == [2, 4]
    assert agg.pacing.gaps == [2]
    assert agg.pacing.average_gap == 2
    assert agg.pacing.longest_gap == 2


def test_theme_distribution_and_prompt_versions() -> None:
    """主题分布跨章累加；prompt 版本集合用于缓存失效判断。"""
    agg = aggregate(
        "测试书",
        [
            _summary(1, [Tone.SATISFYING, Tone.TENSE]),
            _summary(2, [Tone.SATISFYING]),
        ],
    )
    assert agg.theme_distribution[ThemeTag.GROWTH.value] == 2
    assert agg.theme_distribution[ThemeTag.SUSPENSE.value] == 1
    assert agg.source_prompt_versions == ["test-v0"]
    assert agg.total_plot_points == 3


def test_character_matrix_dedup_and_sort() -> None:
    """角色章号去重排序；情节点提及单独计数；按提及数降序排列。"""
    first = _summary(1, [Tone.SATISFYING, Tone.SATISFYING], characters=["阿伏", "陆尘"])
    second = _summary(2, [Tone.TENSE], characters=["陆尘"])
    agg = aggregate("测试书", [first, second])
    by_name = {c.name: c for c in agg.characters}
    assert by_name["阿伏"].chapter_orders == [1]
    assert by_name["阿伏"].mention_count == 2
    assert by_name["陆尘"].chapter_orders == [1, 2]
    assert by_name["陆尘"].mention_count == 0
    # 阿伏提及 2 次排在陆尘（0 次）之前
    assert agg.characters[0].name == "阿伏"
