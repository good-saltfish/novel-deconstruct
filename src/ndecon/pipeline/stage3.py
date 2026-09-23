"""Stage 3 跨章聚合：全部为确定性推导，不调用模型。

输入只有 ChapterSummary 列表（可来自 JSONL 真源重放）；
本模块只做计数、众数、间距这类可解释统计，不做叙事归纳——
"为什么这样安排节奏"属于学习层，仍由使用者自己写。
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict

from ndecon.domain.enums import Tone
from ndecon.domain.models import (
    Aggregation,
    ChapterRhythm,
    ChapterSummary,
    CharacterAppearance,
    PacingStats,
)


def _dominant_tone(counts: dict[str, int]) -> Tone:
    """取基调众数；并列时选计数过程中最先到达该峰值的基调（保持确定性）。"""
    winner = Tone.OTHER
    best = 0
    for tone in Tone:  # 枚举定义顺序即决胜顺序
        if counts.get(tone.value, 0) > best:
            best = counts[tone.value]
            winner = tone
    return winner


def _chapter_rhythm(summary: ChapterSummary) -> ChapterRhythm:
    """由单章情节点统计基调/主题分布与爽点位置。"""
    tone_counts: dict[str, int] = defaultdict(int)
    theme_counts: dict[str, int] = defaultdict(int)
    satisfying: list[int] = []
    for point in summary.plot_points:
        tone_counts[point.tone.value] += 1
        for tag in point.theme_tags:
            theme_counts[tag.value] += 1
        if point.tone is Tone.SATISFYING:
            satisfying.append(point.index)
    return ChapterRhythm(
        order=summary.order,
        dominant_tone=_dominant_tone(tone_counts),
        tone_counts=dict(tone_counts),
        theme_counts=dict(theme_counts),
        satisfying_point_indexes=satisfying,
    )


def aggregate(book_title: str, summaries: list[ChapterSummary]) -> Aggregation:
    """把逐章摘要聚合为全书节奏/主题/角色画像。

    空摘要列表直接抛错——没有输入却产出聚合报告属于静默造假。
    """
    if not summaries:
        raise ValueError("聚合至少需要 1 章摘要")

    rhythms = [_chapter_rhythm(s) for s in summaries]

    # 全书主题分布
    theme_dist: OrderedDict[str, int] = OrderedDict()
    for rhythm in rhythms:
        for tag, count in rhythm.theme_counts.items():
            theme_dist[tag] = theme_dist.get(tag, 0) + count

    # 角色出场：情节点提及计数 + 章级去重章号
    chapters_by_name: OrderedDict[str, list[int]] = OrderedDict()
    mentions: dict[str, int] = defaultdict(int)
    for summary in summaries:
        chapter_names: set[str] = set(summary.characters)
        for point in summary.plot_points:
            for name in point.characters:
                mentions[name] += 1
                chapter_names.add(name)
        for name in chapter_names:
            chapters_by_name.setdefault(name, []).append(summary.order)
    characters = [
        CharacterAppearance(
            name=name,
            chapter_orders=sorted(orders),
            mention_count=mentions.get(name, 0),
        )
        for name, orders in chapters_by_name.items()
    ]
    characters.sort(key=lambda c: (-c.mention_count, c.name))

    # 爽点章距
    satisfying_chapters = [r.order for r in rhythms if r.satisfying_point_indexes]
    gaps = [b - a for a, b in zip(satisfying_chapters, satisfying_chapters[1:])]
    pacing = PacingStats(
        satisfying_chapters=satisfying_chapters,
        gaps=gaps,
        average_gap=round(sum(gaps) / len(gaps), 2) if gaps else None,
        longest_gap=max(gaps) if gaps else None,
    )

    prompt_versions = sorted({s.prompt_version for s in summaries})
    total_points = sum(len(s.plot_points) for s in summaries)
    return Aggregation(
        book_title=book_title,
        source_prompt_versions=prompt_versions,
        total_plot_points=total_points,
        chapter_rhythms=rhythms,
        theme_distribution=dict(theme_dist),
        characters=characters,
        pacing=pacing,
    )
