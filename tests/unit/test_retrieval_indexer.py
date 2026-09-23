"""索引器测试：确认态门控、参考书类型闸门、原文 quote 永不入索引。"""

from ndecon.creation.models import (
    ChapterOutline,
    CreationDraft,
    GoldenFingerSpec,
    Positioning,
    ProtagonistProfile,
    VolumeOutline,
)
from ndecon.domain.enums import ThemeTag, Tone
from ndecon.domain.models import ChapterSummary, PlotPoint, SourceRef
from ndecon.retrieval.bm25 import BM25Index
from ndecon.retrieval.indexer import (
    build_project_index,
    documents_from_creation,
    documents_from_summaries,
)
from ndecon.workspace.models import CreationProject, ProjectMeta, ReferenceProject


def _creation(confirmed: dict[str, bool] | None = None) -> CreationProject:
    """构造含全部五部件的创作项目；部件确认态由参数控制。"""
    draft = CreationDraft(
        positioning=Positioning(genre="都市异能", core_premise="路灯下封着夜色"),
        volume=VolumeOutline(volume_title="第一卷 守灯"),
        chapters=[
            ChapterOutline(order=1, title="停电夜", core_event="发现路灯随心跳明灭", ending_hook="周伯出现")
        ],
        protagonist=ProtagonistProfile(name="林盏", desire="查清真相"),
        golden_finger=GoldenFingerSpec(name="铜灯", cost_limitation="一日三燃"),
    )
    return CreationProject(
        meta=ProjectMeta(id="creation-book-1", kind="creation", title="灯火守门人", created_at="2026-09-23T00:00:00"),
        draft=draft,
        confirmed_parts=confirmed or {},
    )


def test_only_confirmed_parts_indexed() -> None:
    """未确认部件不产生文档；全部确认后五类骨架文档齐全。"""
    assert documents_from_creation(_creation({})) == []

    confirmed = {part: True for part in ("positioning", "volume", "chapters", "protagonist", "golden_finger")}
    docs = documents_from_creation(_creation(confirmed))
    assert {doc.doc_type for doc in docs} == {
        "positioning",
        "volume",
        "chapter_outline",
        "protagonist",
        "golden_finger",
    }
    chapter_doc = next(doc for doc in docs if doc.doc_type == "chapter_outline")
    assert chapter_doc.order == 1 and chapter_doc.doc_id == "outline-chapter-1"


def test_reference_project_rejected_by_type() -> None:
    """参考书项目在类型层面无法进入索引（版权结构闸门）。"""
    reference = ReferenceProject(
        meta=ProjectMeta(id="reference-x", kind="reference", title="某参考书", created_at="2026-09-23T00:00:00"),
        source_path="D:/secret/novel.txt",
        chapter_count=40,
        total_plot_points=100,
    )
    try:
        build_project_index(reference)  # type: ignore[arg-type]
    except TypeError:
        return
    raise AssertionError("参考书项目必须被类型闸门拒绝")


def test_source_quotes_never_indexed() -> None:
    """章节摘要入索引时，SourceRef.quote 的任何字符都不得出现在索引文本里。"""
    secret = "版权原文密语玖壹捌贰柒叁"
    summary = ChapterSummary(
        model_id="rule:fake",
        prompt_version="oc-v0",
        chapter_id="ch1",
        order=1,
        title="第一章",
        gist="林盏点亮铜灯",
        plot_points=[
            PlotPoint(
                index=1,
                summary="影兽被封回井底",
                tone=Tone.HOT_BLOODED,
                theme_tags=[ThemeTag.GROWTH],
                characters=["林盏"],
                source=SourceRef(
                    chapter_id="ch1",
                    start=0,
                    end=len(secret),
                    quote=secret,
                    content_hash="hash-x",
                ),
            )
        ],
        characters=["林盏"],
    )
    docs = documents_from_summaries([summary])
    assert len(docs) == 1
    assert secret not in docs[0].text
    assert "影兽被封回井底" in docs[0].text

    serialized = BM25Index(docs).to_dict()
    assert secret not in str(serialized)


def test_project_index_combines_outlines_and_summaries() -> None:
    """创作索引同时包含已确认细纲与本书已写章节摘要，doc_id 稳定。"""
    project = _creation({"chapters": True})
    summary = ChapterSummary(
        model_id="rule:fake",
        prompt_version="oc-v0",
        chapter_id="ch1",
        order=1,
        title="停电夜",
        gist="巡夜的第一晚",
        plot_points=[],
        characters=[],
    )
    index = build_project_index(project, [summary])
    ids = {doc.doc_id for doc in index.documents}
    assert ids == {"outline-chapter-1", "summary-chapter-1"}
