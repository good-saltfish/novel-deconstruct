"""确定性建索引：把"本书"已确认的结构数据转成 BM25 语料。

版权红线（ADR-0004，由本模块的代码结构强制，而非靠自觉）：
1. 入口只接受 :class:`CreationProject`——参考书项目（ReferenceProject）类型上无法进入，
   参考书的源路径、聚合统计在结构上就不可能进入索引；
2. 只有用户已确认（confirmed_parts）的骨架部件才入索引，模型草稿不进；
3. 章节摘要只取 gist、情节点一句话概括、角色名、主题标签；
   ``PlotPoint.source.quote``（原文引用）在本模块中根本没有被读取，任何原文片段
   都不可能出现在索引文本里；
4. 本章对应的"已写章节摘要"由 #16 后续注入，本函数签名已预留 summaries 入口。
"""

from __future__ import annotations

from ndecon.domain.models import ChapterSummary, PlotPoint
from ndecon.retrieval.bm25 import BM25Index, IndexedDocument
from ndecon.workspace.models import CreationProject

# 文档 ID 前缀在模块间共享，context.py 组装上下文时据此排除"本章自己"
OUTLINE_DOC_PREFIX = "outline-chapter-"
SUMMARY_DOC_PREFIX = "summary-chapter-"


def _non_empty(parts: list[str]) -> list[str]:
    """过滤空字符串，避免空白行污染切词统计。"""
    return [part for part in parts if part and part.strip()]


def _join(parts: list[str]) -> str:
    """用换行拼接非空文本片段。"""
    return "\n".join(_non_empty(parts))


def documents_from_creation(project: CreationProject) -> list[IndexedDocument]:
    """把创作项目中**已用户确认**的骨架部件转为索引文档；未确认部件一律跳过。"""
    draft = project.draft
    confirmed = project.confirmed_parts
    docs: list[IndexedDocument] = []

    if confirmed.get("positioning") and draft.positioning is not None:
        p = draft.positioning
        docs.append(
            IndexedDocument(
                doc_id="positioning",
                doc_type="positioning",
                title="题材定位",
                text=_join(
                    [
                        p.genre,
                        p.audience,
                        p.core_premise,
                        " ".join(p.selling_points),
                        p.tone_target,
                    ]
                ),
                meta={"genre": p.genre},
            )
        )

    if confirmed.get("volume") and draft.volume is not None:
        v = draft.volume
        docs.append(
            IndexedDocument(
                doc_id="volume",
                doc_type="volume",
                title=v.volume_title,
                text=_join([v.volume_title, v.volume_goal, v.main_conflict, v.ending_hook]),
            )
        )

    if confirmed.get("chapters"):
        for chapter in draft.chapters:
            docs.append(
                IndexedDocument(
                    doc_id=f"{OUTLINE_DOC_PREFIX}{chapter.order}",
                    doc_type="chapter_outline",
                    order=chapter.order,
                    title=chapter.title,
                    text=_join([chapter.title, chapter.core_event, chapter.opening_hook, chapter.ending_hook]),
                    meta={"opening_hook": chapter.opening_hook, "ending_hook": chapter.ending_hook},
                )
            )

    if confirmed.get("protagonist") and draft.protagonist is not None:
        hero = draft.protagonist
        docs.append(
            IndexedDocument(
                doc_id="protagonist",
                doc_type="protagonist",
                title=hero.name,
                text=_join(
                    [
                        hero.name,
                        hero.background,
                        hero.personality,
                        hero.desire,
                        hero.flaw,
                        hero.signature_ability,
                    ]
                ),
                meta={"name": hero.name},
            )
        )

    if confirmed.get("golden_finger") and draft.golden_finger is not None:
        finger = draft.golden_finger
        docs.append(
            IndexedDocument(
                doc_id="golden-finger",
                doc_type="golden_finger",
                title=finger.name,
                text=_join([finger.name, finger.form, finger.ability, finger.cost_limitation, finger.growth_path]),
            )
        )

    return docs


def _plot_point_text(point: PlotPoint) -> str:
    """把单个情节点转为可索引文本；刻意不读 ``point.source.quote``。"""
    return _join(
        [
            point.summary,
            " ".join(point.characters),
            " ".join(tag.value for tag in point.theme_tags),
            point.tone.value,
        ]
    )


def documents_from_summaries(summaries: list[ChapterSummary]) -> list[IndexedDocument]:
    """把本书已写章节的 Stage 2 结构化摘要转为索引文档（不触碰原文 quote）。"""
    docs: list[IndexedDocument] = []
    for summary in sorted(summaries, key=lambda s: s.order):
        plot_texts = [_plot_point_text(point) for point in summary.plot_points]
        theme_values = sorted({tag.value for point in summary.plot_points for tag in point.theme_tags})
        tone_values = sorted({point.tone.value for point in summary.plot_points})
        docs.append(
            IndexedDocument(
                doc_id=f"{SUMMARY_DOC_PREFIX}{summary.order}",
                doc_type="chapter_summary",
                order=summary.order,
                title=summary.title,
                text=_join([summary.title, summary.gist, *plot_texts, " ".join(summary.characters)]),
                meta={
                    "chapter_id": summary.chapter_id,
                    "order": summary.order,
                    "characters": sorted(set(summary.characters)),
                    "themes": theme_values,
                    "tones": tone_values,
                    "model_id": summary.model_id,
                },
            )
        )
    return docs


def build_index(documents: list[IndexedDocument]) -> BM25Index:
    """用默认参数构造 BM25 索引（参数集中在 bm25 模块，此处不放行自定义）。"""
    return BM25Index(documents)


def build_project_index(
    project: CreationProject,
    summaries: list[ChapterSummary] | None = None,
) -> BM25Index:
    """为创作项目构建完整 L1 索引：已确认骨架 + 本书已写章节摘要。

    传入非 CreationProject（例如 ReferenceProject）直接 TypeError——
    这是参考书永不入索引的类型级闸门。
    """
    if not isinstance(project, CreationProject):
        raise TypeError("L1 索引只接受创作项目（CreationProject），参考书数据永不入索引")
    documents = documents_from_creation(project)
    if summaries:
        documents.extend(documents_from_summaries(summaries))
    return build_index(documents)
