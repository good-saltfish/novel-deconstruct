"""Markdown + JSONL 产物渲染。

写入纪律：
- data/*.jsonl 是机读真源，Markdown 可由其与原文重建；
- 目录不存在时创建；章节文件名用零填充章号，保证文件管理器内天然有序；
- 所有写入使用 UTF-8。
"""

from __future__ import annotations

import json
from pathlib import Path

from ndecon.domain.models import (
    Aggregation,
    ChapterEntry,
    ChapterSummary,
    GoldenChapterReport,
    ReportBundle,
)

_CHAPTER_DIR = "章节"
_PLOT_DIR = "剧情"
_DATA_DIR = "data"


def _write_text(path: Path, content: str) -> None:
    """确保父目录存在后以 UTF-8 写入文本。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _render_index(
    book_title: str,
    entries: list[ChapterEntry],
    warnings: list[str],
    thin_summary: str | None,
) -> str:
    """渲染概要.md：全书概要位 + 章节索引表 + 切分告警。"""
    lines = [
        f"# 概要：{book_title}",
        "",
        f"总章数：{len(entries)} | 总字数（去空白）：{sum(e.char_count for e in entries)}",
        "",
        "## 全书概要",
        "",
        thin_summary if thin_summary else "（split 模式未生成概要；如需概要请使用 analyze）",
        "",
        "## 章节索引",
        "",
        "| 章号 | 标题 | 起始行 | 字数 |",
        "|---:|---|---:|---:|",
    ]
    for entry in entries:
        lines.append(f"| {entry.order} | {entry.title} | {entry.line_no} | {entry.char_count} |")
    if warnings:
        lines += ["", "## 切分告警", ""]
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)


def _render_summary_md(summary: ChapterSummary) -> str:
    """渲染单章摘要 Markdown。"""
    lines = [
        f"# 第{summary.order}章 {summary.title} · 摘要",
        "",
        f"**概要**：{summary.gist}",
        "",
        f"出场人物：{'、'.join(summary.characters) if summary.characters else '（未提取）'}",
        "",
        "## 情节点",
        "",
    ]
    for point in summary.plot_points:
        tag_text = "、".join(tag.value for tag in point.theme_tags)
        chars = f" | 人物：{'、'.join(point.characters)}" if point.characters else ""
        lines += [
            f"P{point.index} 基调：{point.tone.value} | 主题标签：{tag_text}{chars}",
            "",
            point.summary,
            "",
            f"> 证据：{point.source.quote}（{point.source.chapter_id} 偏移 {point.source.start}-{point.source.end}）",
            "",
        ]
    lines += [
        "---",
        f"provider: {summary.model_id} | prompt: {summary.prompt_version} | schema: {summary.schema_version}",
        "",
    ]
    return "\n".join(lines)


def _render_golden_md(report: GoldenChapterReport) -> str:
    """渲染黄金三章深度拆解 Markdown。"""
    lines = [
        f"# 第{report.order}章 {report.title} · 深度拆解",
        "",
        f"provider: {report.model_id} | prompt: {report.prompt_version} | schema: {report.schema_version}",
        "",
        "## 开篇钩子",
        "",
        f"- 原文锚点：{report.opening_hook_quote or '（无）'}",
        f"- 手法说明：{report.opening_hook_note}",
        "",
        "## 世界观铺设（本章透露）",
        "",
    ]
    if report.worldview_revealed:
        lines.extend(f"- {item}" for item in report.worldview_revealed)
    else:
        lines.append("- （无）")
    lines += ["", "## 结构拆解", "", "| 段落 | 说明 |", "|---|---|"]
    lines.extend(f"| {beat.name} | {beat.note} |" for beat in report.structure_beats)
    lines += [
        "",
        "## 章尾钩子",
        "",
        report.cliffhanger_quote or "（无）",
        "",
        "## 可借鉴要素（学习层，需作者本人填写，工具不代笔）",
        "",
    ]
    if report.takeaways:
        lines.extend(f"- {item}" for item in report.takeaways)
    else:
        lines.append("- （待填写）")
    lines.append("")
    return "\n".join(lines)


def write_outline(
    out_dir: Path,
    book_title: str,
    entries: list[ChapterEntry],
    warnings: list[str],
) -> None:
    """split 模式落盘：概要（无全书概要）+ data/entries.jsonl。"""
    _write_text(
        out_dir / "概要.md",
        _render_index(book_title, entries, warnings, thin_summary=None),
    )
    _write_jsonl(out_dir / _DATA_DIR / "entries.jsonl", [entry.model_dump() for entry in entries])


def _render_aggregation_md(agg: Aggregation) -> str:
    """渲染 Stage 3 节奏与跨章统计 Markdown（只呈现事实统计，不写叙事评论）。"""
    lines = [
        f"# 节奏与跨章统计：{agg.book_title}",
        "",
        "> 本页全部为章节摘要的确定性统计（Stage 3），可由 data/aggregation.jsonl 重算；"
        "“为什么这样安排”属于学习层，请自行填写。",
        "",
        f"情节点总数：{agg.total_plot_points} | 输入 prompt 版本：{'、'.join(agg.source_prompt_versions)}",
        "",
        "## 章节节奏",
        "",
        "| 章 | 主导基调 | 爽点情节点序号 | 基调分布 |",
        "|---:|---|---|---|",
    ]
    for rhythm in agg.chapter_rhythms:
        tone_text = "、".join(f"{tone}×{count}" for tone, count in sorted(rhythm.tone_counts.items()))
        hits = "、".join(str(i) for i in rhythm.satisfying_point_indexes) or "—"
        lines.append(
            f"| {rhythm.order} | {rhythm.dominant_tone.value} | {hits} | {tone_text} |"
        )

    pacing = agg.pacing
    lines += [
        "",
        "## 爽点章距",
        "",
        f"- 含爽点章节：{pacing.satisfying_chapters or '（无）'}",
        f"- 相邻章距：{pacing.gaps or '（不足两处）'}",
        f"- 平均章距：{pacing.average_gap if pacing.average_gap is not None else '—'}",
        f"- 最大章距（节奏空窗）：{pacing.longest_gap if pacing.longest_gap is not None else '—'}",
        "",
        "## 主题分布",
        "",
        "| 主题标签 | 次数 |",
        "|---|---:|",
    ]
    for tag, count in sorted(agg.theme_distribution.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {tag} | {count} |")

    lines += ["", "## 角色出场矩阵", "", "| 角色 | 情节点提及 | 出场章号 |", "|---|---:|---|"]
    for character in agg.characters:
        chapters = "、".join(str(n) for n in character.chapter_orders)
        lines.append(f"| {character.name} | {character.mention_count} | {chapters} |")
    lines.append("")
    return "\n".join(lines)


def write_bundle(out_dir: Path, bundle: ReportBundle) -> None:
    """analyze 模式落盘：概要、逐章摘要、黄金三章报告与 JSONL 真源。"""
    _write_text(
        out_dir / "概要.md",
        _render_index(bundle.book_title, bundle.entries, bundle.warnings, bundle.thin_summary),
    )
    chapter_dir = out_dir / _CHAPTER_DIR
    for summary in bundle.summaries:
        _write_text(chapter_dir / f"第{summary.order:04d}章_摘要.md", _render_summary_md(summary))
    for report in bundle.golden_reports:
        _write_text(chapter_dir / f"第{report.order:04d}章_深度拆解.md", _render_golden_md(report))

    _write_jsonl(out_dir / _DATA_DIR / "entries.jsonl", [e.model_dump() for e in bundle.entries])
    _write_jsonl(out_dir / _DATA_DIR / "chapters.jsonl", [s.model_dump() for s in bundle.summaries])
    _write_jsonl(out_dir / _DATA_DIR / "reports.jsonl", [g.model_dump() for g in bundle.golden_reports])
    if bundle.aggregation is not None:
        _write_text(
            out_dir / _PLOT_DIR / "节奏.md",
            _render_aggregation_md(bundle.aggregation),
        )
        _write_jsonl(
            out_dir / _DATA_DIR / "aggregation.jsonl",
            [bundle.aggregation.model_dump()],
        )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    """把字典列表以每行一个 JSON 的形式写入（ensure_ascii=False 保留中文）。"""
    _write_text(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
