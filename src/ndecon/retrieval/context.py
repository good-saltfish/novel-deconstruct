"""生成前的确定性上下文包（#16 正文生成唯一允许携带的上下文结构）。

组装规则全部确定、可单测：
- 直接字段：本书定位/人设/金手指（仅已确认部件）+ 本章细纲；
- 紧邻前一章摘要：优先用已写章节的 Stage 2 摘要，否则退化为上一章细纲要点；
- 相关回顾：BM25 对本书语料取 top-k，排除"本章自己"；
- 叙事债：本章之前仍开放的伏笔列表。
包里不存在任何参考书原文/quote——参考书只以 L0 聚合数字形式存在（#15 路径）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ndecon.creation.models import (
    ChapterOutline,
    GoldenFingerSpec,
    Positioning,
    ProtagonistProfile,
)
from ndecon.domain.models import ChapterSummary
from ndecon.retrieval.bm25 import BM25Index, IndexedDocument
from ndecon.retrieval.foreshadow import (
    ForeshadowEntry,
    extract_foreshadows,
    open_foreshadows_at,
)
from ndecon.retrieval.indexer import (
    OUTLINE_DOC_PREFIX,
    SUMMARY_DOC_PREFIX,
    build_project_index,
)
from ndecon.workspace.models import CreationProject

# 上下文片段长度上限：防止长摘要把 prompt 撑爆，确定性截断
SNIPPET_LIMIT = 120
DEFAULT_TOP_K = 10


class RetrievedDoc(BaseModel):
    """一条被召回的本书语料（供生成时参考与事后审计）。"""

    doc_id: str
    doc_type: str
    order: int | None = None
    title: str = ""
    snippet: str = ""
    score: float


class ContextPack(BaseModel):
    """第 N 章正文生成所需的全部上下文；#16 只能序列化本对象喂给模型。"""

    project_id: str
    chapter_order: int
    positioning: Positioning | None = None
    protagonist: ProtagonistProfile | None = None
    golden_finger: GoldenFingerSpec | None = None
    chapter_outline: ChapterOutline
    previous_summary: str = ""
    previous_summary_source: str = Field(
        description="written=已写章节摘要；outline=上一章细纲退化；none=首章无前文"
    )
    retrieved: list[RetrievedDoc] = Field(default_factory=list)
    open_foreshadows: list[ForeshadowEntry] = Field(default_factory=list)
    index_doc_count: int = Field(ge=0)


def _snippet(doc: IndexedDocument, score: float) -> RetrievedDoc:
    """把索引文档转成上下文用的轻量命中（正文确定性截断到 SNIPPET_LIMIT）。"""
    return RetrievedDoc(
        doc_id=doc.doc_id,
        doc_type=doc.doc_type,
        order=doc.order,
        title=doc.title,
        snippet=doc.text[:SNIPPET_LIMIT],
        score=score,
    )


def _previous_summary(
    chapter_order: int,
    outlines_by_order: dict[int, ChapterOutline],
    summaries_by_order: dict[int, ChapterSummary],
) -> tuple[str, str]:
    """确定前一章摘要文本与来源标记（written 优先，outline 退化，首章为空）。"""
    prev_order = chapter_order - 1
    if prev_order in summaries_by_order:
        return summaries_by_order[prev_order].gist, "written"
    previous_outline = outlines_by_order.get(prev_order)
    if previous_outline is not None:
        parts = [
            part
            for part in (previous_outline.core_event, previous_outline.ending_hook)
            if part and part.strip()
        ]
        return ("\n".join(parts), "outline") if parts else ("", "none")
    return "", "none"


def build_context_pack(
    project: CreationProject,
    chapter_order: int,
    *,
    summaries: list[ChapterSummary] | None = None,
    index: BM25Index | None = None,
    k: int = DEFAULT_TOP_K,
) -> ContextPack:
    """为创作项目的第 chapter_order 章组装确定性上下文包。

    - 细纲中找不到该章时抛 ValueError（不能为不存在的章节生成正文）；
    - index 可注入（评测/复用同一份索引），否则现场构建；
    - summaries 是本书已写章节摘要，不含参考书任何数据。
    """
    if not isinstance(project, CreationProject):
        raise TypeError("上下文包只能为创作项目（CreationProject）构建")
    outlines_by_order = {chapter.order: chapter for chapter in project.draft.chapters}
    target = outlines_by_order.get(chapter_order)
    if target is None:
        raise ValueError(f"细纲中不存在第 {chapter_order} 章，无法组装上下文包")

    summaries = summaries or []
    summaries_by_order = {item.order: item for item in summaries}
    active_index = index or build_project_index(project, summaries)

    query_parts = [target.title, target.core_event, target.opening_hook, target.ending_hook]
    if project.draft.protagonist is not None:
        query_parts.append(project.draft.protagonist.name)
    query = "\n".join(part for part in query_parts if part and part.strip())

    # 本章自己的细纲与本章已写摘要不参与"相关回顾"（直接字段里已有）
    exclude_ids = {
        f"{OUTLINE_DOC_PREFIX}{chapter_order}",
        f"{SUMMARY_DOC_PREFIX}{chapter_order}",
    }
    retrieved: list[RetrievedDoc] = []
    for hit in active_index.search(query, k=k, exclude_ids=exclude_ids):
        doc = active_index.get(hit.doc_id)
        if doc is not None:
            retrieved.append(_snippet(doc, hit.score))

    previous_summary, previous_source = _previous_summary(chapter_order, outlines_by_order, summaries_by_order)
    ledger = extract_foreshadows(project.draft.chapters, summaries)
    open_debts = open_foreshadows_at(ledger, chapter_order)

    confirmed = project.confirmed_parts
    return ContextPack(
        project_id=project.meta.id,
        chapter_order=chapter_order,
        positioning=project.draft.positioning if confirmed.get("positioning") else None,
        protagonist=project.draft.protagonist if confirmed.get("protagonist") else None,
        golden_finger=project.draft.golden_finger if confirmed.get("golden_finger") else None,
        chapter_outline=target,
        previous_summary=previous_summary,
        previous_summary_source=previous_source,
        retrieved=retrieved,
        open_foreshadows=open_debts,
        index_doc_count=len(active_index.documents),
    )
