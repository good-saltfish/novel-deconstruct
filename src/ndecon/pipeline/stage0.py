"""Stage 0：由确定性切分结果构建章节索引（字数与内容指纹）。"""

from __future__ import annotations

import re

from ndecon.domain.ids import content_hash
from ndecon.domain.models import ChapterEntry
from ndecon.ingest.splitter import SplitResult


def _char_count(text: str) -> int:
    """统计去空白后的字符数（与人工拆书字数口径一致）。"""
    return len(re.sub(r"\s", "", text))


def _strip_title_line(chapter_text: str) -> str:
    """去掉章节切片的标题行，返回正文（字数统计不含标题，内容指纹仍含整章）。"""
    newline = chapter_text.find("\n")
    return chapter_text[newline + 1 :] if newline >= 0 else ""


def build_entries(split: SplitResult) -> list[ChapterEntry]:
    """遍历边界表，为每章计算正文字数与覆盖整章的 SHA-256 指纹，生成索引项。"""
    entries: list[ChapterEntry] = []
    for boundary in split.boundaries:
        chapter_text = split.text[boundary.start : boundary.end]
        entries.append(
            ChapterEntry(
                order=boundary.order,
                title=boundary.title,
                line_no=boundary.line_no,
                char_count=_char_count(_strip_title_line(chapter_text)),
                content_hash=content_hash(chapter_text),
            )
        )
    return entries
