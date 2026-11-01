"""结构化拆书的编排层：把切分、规则测量、语义层、渲染、入库串成一次可重放的运行。

分层（这条边界是硬性的）：
- `signals.py` / `measure_*`：规则层，只量词面，不懂语义；
- `semantics.py`：语义层，需要真实模型，产出转述与手法；
- 本模块：编排 + 落盘 + 入库，不含任何"理解"。

两条不妥协：
1. **给了几章就量几章**：`max_chapters` 只影响范围标签与"送模型多少章"，
   规则层永远量全部切出来的章——静默截断会让"节奏空窗"这类结论建立在残缺数据上；
2. **语义层失败不静默降级**：坏章记 warning 跳过，全坏则抛错（空报告比报错危险）。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ndecon.deconstruct.models import (
    BookDeconstruction,
    BookStructure,
    ChapterDeconstruction,
    ChapterMetrics,
    ConflictAnchor,
)
from ndecon.deconstruct.renderers import render_author_md, render_kb_md

RULE_MODEL_ID = "rule:deconstruct"
RULE_PROMPT_VERSION = "rule-v0"
# 单章锚点上限：防长章把报告撑爆；达上限即告警，不静默丢弃后续点
MAX_ANCHORS_PER_CHAPTER = 50
# 开篇悬念检测窗口（字符）
OPENING_WINDOW_CHARS = 200
# 默认只把前多少章送语义层（与调研口径"拆前 40 章"一致；0 表示不限）
DEFAULT_SEMANTIC_CHAPTERS = 40

_WS = re.compile(r"\s")
_SENTENCE_TAIL = "。！？!?…"
_SUSPENSE_NAMES = {"悬念提问", "质询逼问"}
# 落盘文件名：只保留文件系统安全字符，中文书名也能用
_UNSAFE_FILENAME = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


# --------------------------------------------------------------------------
# 规则层（纯确定性，无模型）
# --------------------------------------------------------------------------


def _char_count(text: str) -> int:
    """去空白字数（与 pipeline.stage0 同一口径）。"""
    return len(_WS.sub("", text))


def _strip_title_line(chapter_text: str) -> tuple[str, int]:
    """去掉章节文本的标题行，返回 (正文, 正文起始偏移)。"""
    newline = chapter_text.find("\n")
    if newline < 0:
        return chapter_text, 0
    return chapter_text[newline + 1 :], newline + 1


def measure_chapter(
    order: int, title: str, chapter_text: str
) -> tuple[ChapterMetrics, list[dict], list[str]]:
    """量一章：返回 (客观测量, 疑似冲突点原始 dict 列表, 局部告警)。

    anchors 的 start/end 是**章切片内的绝对偏移**（含标题行），
    作者回看时拿同一个源切片按偏移取即可定位，不需要二级 index。
    """
    from ndecon.deconstruct.signals import CLASH_LEXICON, has_dialogue, split_sentences

    del title  # 客观层不依赖标题语义
    body, base = _strip_title_line(chapter_text)

    sentences = split_sentences(body, base)
    from ndecon.domain.ids import chapter_id, content_hash

    hash_value = content_hash(chapter_text)

    sentence_chars = 0
    dialogue = 0
    exclaim = 0
    question = 0
    unpunct = 0
    anchors: list[dict] = []
    overflow = False

    for start, end, text in sentences:
        sentence_chars += len(_WS.sub("", text))
        if has_dialogue(text):
            dialogue += 1
        if "！" in text or "!" in text:
            exclaim += 1
        if "？" in text or "?" in text:
            question += 1
        if not text.rstrip().endswith(tuple(_SENTENCE_TAIL)):
            unpunct += 1

        hits = [rule for rule in CLASH_LEXICON if rule.pattern.search(text)]
        if hits:
            if len(anchors) < MAX_ANCHORS_PER_CHAPTER:
                anchors.append(
                    {
                        "source_id": f"bk:{chapter_id(order)}",
                        "start": start,
                        "end": end,
                        "trigger": "+".join(rule.name for rule in hits),
                        "content_hash": hash_value,
                        "note": hits[0].note,
                    }
                )
            else:
                overflow = True

    count = len(sentences)
    opening = body[:OPENING_WINDOW_CHARS]
    metrics = ChapterMetrics(
        chapter_id=chapter_id(order),
        order=order,
        char_count=_char_count(chapter_text),
        sentence_count=count,
        avg_sentence_chars=(sentence_chars / count) if count else 0.0,
        dialogue_sentence_ratio=(dialogue / count) if count else 0.0,
        paragraph_count=len([p for p in body.split("\n") if p.strip()]),
        exclaim_ratio=(exclaim / count) if count else 0.0,
        question_ratio=(question / count) if count else 0.0,
        no_punct_sentence_ratio=(unpunct / count) if count else 0.0,
        opening_hook_signal=any(
            rule.name in _SUSPENSE_NAMES
            for rule in CLASH_LEXICON
            if rule.pattern.search(opening)
        ),
        content_hash=hash_value,
    )
    warnings = (
        [f"第{order}章疑似冲突点达上限 {MAX_ANCHORS_PER_CHAPTER}，其余未收录（建议人工确认该章密度）"]
        if overflow
        else []
    )
    return metrics, anchors, warnings


def build_structure(
    per_chapter: list[tuple[ChapterMetrics, list[dict]]],
) -> BookStructure:
    """把逐章结果聚合成全书剖面（依然零语义，只是算术）。"""
    if not per_chapter:
        return BookStructure(total_chars=0)

    total_chars = sum(m.char_count for m, _ in per_chapter)
    total_sentences = sum(m.sentence_count for m, _ in per_chapter)
    weighted_sentence = sum(m.avg_sentence_chars * m.sentence_count for m, _ in per_chapter)
    weighted_dialogue = sum(m.dialogue_sentence_ratio * m.sentence_count for m, _ in per_chapter)
    total_anchors = sum(len(anchors) for _, anchors in per_chapter)

    per_thousand = (total_anchors / total_chars * 1000) if total_chars else 0.0
    return BookStructure(
        total_chars=total_chars,
        avg_chapter_chars=total_chars / len(per_chapter),
        avg_sentence_chars=(weighted_sentence / total_sentences) if total_sentences else 0.0,
        avg_dialogue_ratio=(weighted_dialogue / total_sentences) if total_sentences else 0.0,
        per_thousand_conflicts=per_thousand,
        conflict_density={
            str(m.order): (len(anchors) / m.char_count * 1000 if m.char_count else 0.0)
            for m, anchors in per_chapter
        },
        quiet_chapters=[
            m.order
            for m, anchors in per_chapter
            if m.char_count and (len(anchors) / m.char_count * 1000) < per_thousand / 2
        ],
        hook_openings=[m.order for m, _ in per_chapter if m.opening_hook_signal],
    )


def _to_anchors(raws: list[dict]) -> list[ConflictAnchor]:
    """把规则层产出的 anchor dict 校验成模型对象（顺手挡掉 end<=start 的脏坐标）。"""
    return [ConflictAnchor(**raw) for raw in raws]


def build_rule_deconstruction(
    text: str,
    title: str,
    source_path: str = "",
    max_chapters: int = 0,
) -> BookDeconstruction:
    """规则层入口：切分 → 逐章测量 → 聚合。**给了几章就量几章，不静默截断。**"""
    from ndecon.deconstruct.models import PLACEHOLDER
    from ndecon.ingest.splitter import split_chapters

    split = split_chapters(text)
    boundaries = list(split.boundaries)
    if max_chapters and len(boundaries) > max_chapters:
        boundaries = boundaries[:max_chapters]
        range_label = f"前{max_chapters}章"
    else:
        range_label = "全文"

    warnings = list(split.warnings)
    per_chapter: list[tuple[ChapterMetrics, list[dict]]] = []
    chapters: list[ChapterDeconstruction] = []
    for index, boundary in enumerate(boundaries):
        raw_text = split.chapter_text(index)
        metrics, anchors, local_warnings = measure_chapter(boundary.order, boundary.title, raw_text)
        per_chapter.append((metrics, anchors))
        warnings.extend(local_warnings)
        chapters.append(
            ChapterDeconstruction(
                order=boundary.order,
                title=boundary.title or f"第{boundary.order}章",
                metrics=metrics,
            )
        )

    structure = build_structure(per_chapter)
    for index, chapter in enumerate(chapters):
        chapter.anchors = _to_anchors(per_chapter[index][1])

    return BookDeconstruction(
        book_title=title,
        range_label=range_label,
        total_chars=structure.total_chars,
        source_path=source_path,
        structure=structure,
        chapters=chapters,
        model_id=RULE_MODEL_ID,
        prompt_version=RULE_PROMPT_VERSION,
        warnings=warnings
        + [
            "语义层未产出：冲突三角的角色位需要真实模型或作者本人填写，"
            f"规则层一律留 {PLACEHOLDER}"
        ],
    )


# --------------------------------------------------------------------------
# 语义层编排
# --------------------------------------------------------------------------


def attach_semantics(
    book: BookDeconstruction,
    deconstructor,
    chapter_texts: dict[int, str],
    *,
    semantic_chapters: int = DEFAULT_SEMANTIC_CHAPTERS,
) -> BookDeconstruction:
    """在规则产物上补语义层；坏章记 warning 跳过，全坏则抛错。

    chapter_texts 由调用方按章号给出原文切片（本模块不重新持有全本正文）。
    """
    from ndecon.deconstruct.models import PLACEHOLDER
    from ndecon.deconstruct.semantics import DeconstructionError

    targets = book.chapters
    if semantic_chapters and len(targets) > semantic_chapters:
        targets = targets[:semantic_chapters]

    failed = 0
    for chapter in targets:
        raw = chapter_texts.get(chapter.order)
        if raw is None:
            failed += 1
            book.warnings.append(f"第{chapter.order}章缺少原文切片，语义层跳过")
            continue
        try:
            semantic = deconstructor.deconstruct_chapter(chapter.order, chapter.title, raw)
        except DeconstructionError as exc:
            failed += 1
            book.warnings.append(str(exc))
            continue
        book.warnings.extend(semantic.warnings)
        # 语义层发现的"可复核短引"锚点要并入规则层锚点表，否则刚定位好的证据会被丢掉
        chapter.anchors.extend(semantic.anchors)
        chapter.anchors.sort(key=lambda anchor: anchor.start)
        chapter.warnings.extend(semantic.warnings)
        chapter.core_event = semantic.core_event
        chapter.triangles = semantic.triangles
        chapter.end_hook = semantic.end_hook
        chapter.highlights = semantic.highlights
        chapter.emotion_track = semantic.emotion_track

    if targets and failed == len(targets):
        raise DeconstructionError(
            f"语义层全章失败（{failed}/{len(targets)}）：产物为空，拒绝返回一份看起来正常的空报告"
        )

    try:
        book.panorama = deconstructor.deconstruct_panorama(
            book.book_title, book.range_label, targets, book.total_chars
        )
    except DeconstructionError as exc:
        book.warnings.append(f"{exc}（逐章产物仍可用）")

    # 语义层跑完后，规则层那条"一律留占位"的告警不再成立
    book.warnings = [w for w in book.warnings if not w.startswith("语义层未产出")]
    if failed:
        book.warnings.append(f"语义层跳过 {failed} 章（详见上列告警），这些章只有客观测量")
    if book.panorama is None:
        book.warnings.append(f"全景骨架未产出，相关字段留 {PLACEHOLDER}")
    book.model_id = getattr(deconstructor, "model_id", book.model_id)
    from ndecon.deconstruct.prompts import PROMPT_VERSION

    book.prompt_version = PROMPT_VERSION
    return book


def build_full_deconstruction(
    text: str,
    title: str,
    deconstructor,
    *,
    source_path: str = "",
    max_chapters: int = 0,
    semantic_chapters: int = DEFAULT_SEMANTIC_CHAPTERS,
) -> BookDeconstruction:
    """一次完整拆书：规则层打底 + 语义层补齐（无 deconstructor 时返回规则层产物）。"""
    book = build_rule_deconstruction(text, title, source_path=source_path, max_chapters=max_chapters)
    if deconstructor is None:
        return book
    from ndecon.ingest.splitter import split_chapters

    split = split_chapters(text)
    texts = {
        boundary.order: split.chapter_text(index)
        for index, boundary in enumerate(split.boundaries)
    }
    targets = book.chapters[:semantic_chapters] if semantic_chapters else book.chapters
    texts = {order: texts[order] for order in (c.order for c in targets) if order in texts}
    return attach_semantics(book, deconstructor, texts, semantic_chapters=semantic_chapters)


# --------------------------------------------------------------------------
# 落盘与入库
# --------------------------------------------------------------------------


def _safe_stem(title: str) -> str:
    """把书名收敛成文件系统安全的文件名主干。"""
    stem = _UNSAFE_FILENAME.sub("-", title.strip()).strip("-")[:40]
    return stem or "book"


def write_deconstruction(out_dir: str | Path, book: BookDeconstruction) -> tuple[Path, Path]:
    """落盘两份 markdown 与一份 JSON 真源，返回 (作者版路径, KB 版路径)。

    KB 版刻意与作者版分开落盘：它是唯一会被 `kb index` 收录的文件，
    两份混在一起就等于把"可复核坐标"和"可注入 prompt 的内容"混在一起。
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(book.book_title)

    author_path = directory / f"{stem}_拆解稿.md"
    kb_path = directory / f"{stem}_KB.md"
    data_path = directory / f"{stem}_data.json"

    _write_text(author_path, render_author_md(book))
    _write_text(kb_path, render_kb_md(book))
    _write_text(data_path, json.dumps(book.model_dump(), ensure_ascii=False, indent=2))
    return author_path, kb_path


def _write_text(path: Path, content: str) -> None:
    """UTF-8 写入（父目录已在上一步创建）。"""
    path.write_text(content, encoding="utf-8")


def export_kb_markdown(out_dir: str | Path, book: BookDeconstruction) -> Path:
    """只落 KB 版（供"拆完直接进知识库"的用法），返回文件路径。"""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_safe_stem(book.book_title)}_KB.md"
    _write_text(path, render_kb_md(book))
    return path


def _atomic_write(path: Path, content: str) -> None:
    """临时文件 + 替换，避免写一半的知识文件被建库扫到。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def ingest_into_kb(workspace: str | Path, book: BookDeconstruction) -> Path:
    """把 KB 版抄进工作区 kb/ 目录，供下一次 `kb index` 收录。

    只抄 KB 版这一条纪律由本函数结构保证：作者版根本不在参数里。
    """
    from ndecon.kb.corpus import kb_dir

    directory = kb_dir(workspace) / "deconstructed"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_safe_stem(book.book_title)}_KB.md"
    _atomic_write(path, render_kb_md(book))
    return path


def load_deconstruction(path: str | Path) -> BookDeconstruction:
    """从 JSON 真源还原产物（用于复核与重放）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return BookDeconstruction(**data)
