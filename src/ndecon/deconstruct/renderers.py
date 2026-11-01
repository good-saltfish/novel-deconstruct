"""把 BookDeconstruction 渲染成 markdown —— **两个用途、两份产物**。

为什么要分成两份，而不是"一份更全的"：
- `render_author_md`：给作者看。可以列疑似冲突点的坐标（仍不含原文），让作者据此回本机原文复核；
- `render_kb_md`：给知识库。**不得含有他人情节或成句**——只留可迁移的创作手法与客观数值。

这不是"谨慎过度"：KB 会被 `kb index` 检索并注入到下一次创作 prompt 里
（`creation/prompts.py: knowledge_brief`），一旦 KB 中混进他人成句，
下游生成无法区分"借鉴"与"复制"，版权风险会在人看不见的地方被放大。
"""

from __future__ import annotations

from ndecon.deconstruct.models import BookDeconstruction, BookStructure, ChapterMetrics


def _fmt(value: float, digits: int = 2) -> str:
    """统一数值格式，避免 3.0000000000000004 这种浮点尾数出现在报告里。"""
    return f"{value:.{digits}f}"


def _render_structure_table(structure: BookStructure | None) -> list[str]:
    """渲染"客观数值"块（KB 版本中唯一允许保留的事实性内容）。"""
    if structure is None:
        return ["_（无客观测量数据）_"]
    lines = [
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 总字数（去空白） | {structure.total_chars} |",
        f"| 章均字数 | {_fmt(structure.avg_chapter_chars, 0)} |",
        f"| 平均句长 | {_fmt(structure.avg_sentence_chars)} |",
        f"| 对话句占比 | {_fmt(structure.avg_dialogue_ratio * 100)}% |",
        f"| 疑似冲突点密度 | 每千字 {_fmt(structure.per_thousand_conflicts)} 处 |",
    ]
    if structure.quiet_chapters:
        lines.append(f"| 节奏空窗候选章 | {'、'.join(str(n) for n in structure.quiet_chapters)} |")
    if structure.hook_openings:
        lines.append(f"| 开篇有悬念信号 | {'、'.join(str(n) for n in structure.hook_openings)} |")
    return lines


def _render_metrics_block(metrics: ChapterMetrics) -> list[str]:
    """渲染单章客观指标片段。"""
    return [
        f"- 字数 {metrics.char_count} ｜ 句数 {metrics.sentence_count} ｜ "
        f"平均句长 {_fmt(metrics.avg_sentence_chars)} ｜ 段落 {metrics.paragraph_count}",
        f"- 对话句占比 {_fmt(metrics.dialogue_sentence_ratio * 100)}% ｜ "
        f"感叹号句占比 {_fmt(metrics.exclaim_ratio * 100)}% ｜ "
        f"问号句占比 {_fmt(metrics.question_ratio * 100)}% ｜ "
        f"无句末标点句占比 {_fmt(metrics.no_punct_sentence_ratio * 100)}%",
        f"- 开篇悬念信号：{'有' if metrics.opening_hook_signal else '无'}",
    ]


def render_author_md(book: BookDeconstruction) -> str:
    """给作者的拆解稿：含疑似冲突点坐标（不含原文），供回看原文时对照。"""
    lines = [
        f"# 拆书测量稿：{book.book_title}",
        "",
        f"> 范围 {book.range_label} ｜ {book.model_id} ｜ {book.prompt_version} ｜ {book.schema_version}",
        "",
        "**这份稿子不替你理解这本书**：它只把词面上的东西量出来、标好位置，判断归你。",
        "",
    ]
    if book.source_path:
        lines += [f"源文件（本机回看原文用）：`{book.source_path}`", ""]

    lines += ["## 客观测量", ""] + _render_structure_table(book.structure) + [""]

    for chapter in book.chapters:
        lines += [f"## 第{chapter.order}章 {chapter.title}", ""]
        if chapter.metrics:
            lines += _render_metrics_block(chapter.metrics) + [""]
        if chapter.core_event:
            lines += ["**发生了什么**（转述）：", "", chapter.core_event, ""]
        if chapter.triangles:
            lines += ["| 三角 | 类型 | 迫害者 | 受害者 | 拯救者 | 效果 |", "|---|---|---|---|---|---|"]
            for tri in chapter.triangles:
                lines.append(
                    f"| {tri.name or '—'} | {tri.kind} | {tri.persecutor} | {tri.victim} | "
                    f"{tri.savior or '—'} | {tri.effect or '—'} |"
                )
            lines.append("")
        if chapter.end_hook:
            lines += [f"**章末钩子**：{chapter.end_hook}", ""]
        if chapter.anchors:
            lines += [f"**疑似冲突点 {len(chapter.anchors)} 处**（坐标为章内偏移）：", ""]
            for anchor in chapter.anchors:
                suffix = f" ｜ {anchor.note}" if anchor.note else ""
                lines.append(f"- 偏移 {anchor.start}-{anchor.end} ｜ 触发规则：{anchor.trigger}{suffix}")
            lines.append("")
        if chapter.highlights:
            lines += ["**点评**：", ""] + [f"- {h}" for h in chapter.highlights] + [""]

    if book.warnings:
        lines += ["## 告警", ""] + [f"- {w}" for w in book.warnings] + [""]
    return "\n".join(lines)


def render_kb_md(book: BookDeconstruction) -> str:
    """给知识库的摘录稿：**只留手法与客观数值，不留他人情节与成句**。"""
    lines = [
        f"# 测量型写作手法备忘：{book.book_title}",
        "",
        "> 本页由 ndecon 自动拆书产生，**已刻意剔除**：具体情节、人名关系、原文句子。",
        "> 保留的只有：客观统计量、能从统计本身直接读出的节奏做法，以及给你的自检项。",
        "> 它是数据的事实说明，不是这本书的情节摘要——想看情节请回头看你的本机源文件。",
        "",
        f"来源区间：{book.range_label} ｜ {book.model_id} ｜ {book.prompt_version}",
        "",
        "## 可直接对标的客观数值",
        "",
    ]
    lines += _render_structure_table(book.structure) + [""]

    if book.structure and book.structure.conflict_density:
        lines += ["## 冲突密度的章级分布", "", "| 章 | 每千字疑似冲突点 |", "|---:|---:|"]
        for order, density in sorted(book.structure.conflict_density.items(), key=lambda kv: int(kv[0])):
            lines.append(f"| {order} | {_fmt(density)} |")
        lines.append("")

    lines += ["## 迁移到自己稿子上的做法", "", "> 下面每条只讲做法，不含这本书写了什么。", ""]
    techniques = list((book.panorama.reusable_techniques if book.panorama else None) or [])
    if techniques:
        lines += [f"- {t}" for t in techniques] + [""]
    else:
        lines += ["- （语义层未接入：需先用真实模型跑 analyze，或你自己在这里补）", ""]

    lines += [
        "## 用法提醒",
        "",
        "- 这些是**数值参照**，不是配方：拿它对标自己的节奏可以，照着数字改情节没有意义；",
        "- 想看某一章究竟怎么写的，请用作者版拆解稿里的偏移坐标回原文复核。",
        "",
    ]
    return "\n".join(lines)
