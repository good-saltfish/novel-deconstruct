"""上下文包测试：字段门控、前章摘要来源、本章排除、伏笔注入、非法输入。"""

from ndecon.creation.models import (
    ChapterOutline,
    CreationDraft,
    GoldenFingerSpec,
    Positioning,
    ProtagonistProfile,
)
from ndecon.domain.enums import ThemeTag, Tone
from ndecon.domain.models import ChapterSummary, PlotPoint, SourceRef
from ndecon.retrieval.context import build_context_pack
from ndecon.workspace.models import CreationProject, ProjectMeta


def _outline(order: int, title: str, event: str, ending: str) -> ChapterOutline:
    """构造最小细纲条目。"""
    return ChapterOutline(order=order, title=title, core_event=event, opening_hook="", ending_hook=ending)


def _project() -> CreationProject:
    """构造三章细纲、设定已确认的创作项目。"""
    draft = CreationDraft(
        positioning=Positioning(genre="都市异能", core_premise="路灯封着夜色"),
        protagonist=ProtagonistProfile(name="林盏", desire="守灯"),
        golden_finger=GoldenFingerSpec(name="铜灯", cost_limitation="一日三燃"),
        chapters=[
            _outline(1, "停电夜", "林盏发现路灯随心跳明灭", "修灯老人周伯出现"),
            _outline(2, "新守灯人", "林盏被登记成守灯人", "废巷水井里有东西在抓挠"),
            _outline(3, "井里的东西", "林盏封住影兽", "陈主任远远看着这一切"),
        ],
    )
    return CreationProject(
        meta=ProjectMeta(id="creation-book-9", kind="creation", title="灯火守门人", created_at="2026-09-23T00:00:00"),
        draft=draft,
        confirmed_parts={
            "positioning": True,
            "protagonist": True,
            "golden_finger": True,
            "chapters": True,
        },
    )


def test_missing_chapter_raises() -> None:
    """为细纲中不存在的章节组装上下文必须报错。"""
    try:
        build_context_pack(_project(), 99)
    except ValueError:
        return
    raise AssertionError("不存在的章号应当报错")


def test_pack_carries_confirmed_profile_fields() -> None:
    """已确认的定位/人设/金手指直接进包；本章细纲就位。"""
    pack = build_context_pack(_project(), 2)
    assert pack.positioning is not None and pack.positioning.genre == "都市异能"
    assert pack.protagonist is not None and pack.protagonist.name == "林盏"
    assert pack.golden_finger is not None
    assert pack.chapter_outline.order == 2


def test_unconfirmed_profile_fields_absent() -> None:
    """未确认部件不直接进包（与索引的确认门控保持一致）。"""
    project = _project()
    project.confirmed_parts = {"chapters": True}
    pack = build_context_pack(project, 2)
    assert pack.positioning is None and pack.protagonist is None and pack.golden_finger is None


def test_previous_summary_prefers_written_then_outline() -> None:
    """前章摘要：有已写摘要用 gist；否则退化为上一章细纲；第一章为 none。"""
    project = _project()

    first = build_context_pack(project, 1)
    assert first.previous_summary_source == "none" and first.previous_summary == ""

    second = build_context_pack(project, 2)
    assert second.previous_summary_source == "outline"
    assert "林盏发现路灯随心跳明灭" in second.previous_summary

    written_ch1 = ChapterSummary(
        model_id="rule:fake",
        prompt_version="oc-v0",
        chapter_id="ch1",
        order=1,
        title="停电夜",
        gist="已写正文摘要：周伯把铜灯交给林盏",
        plot_points=[],
        characters=[],
    )
    second_written = build_context_pack(project, 2, summaries=[written_ch1])
    assert second_written.previous_summary_source == "written"
    assert "周伯把铜灯交给林盏" in second_written.previous_summary


def test_current_chapter_excluded_from_retrieval() -> None:
    """召回结果不得包含本章自己的细纲与摘要。"""
    written_ch2 = ChapterSummary(
        model_id="rule:fake",
        prompt_version="oc-v0",
        chapter_id="ch2",
        order=2,
        title="新守灯人",
        gist="登记守灯人",
        plot_points=[
            PlotPoint(
                index=1,
                summary="林盏被登记",
                tone=Tone.OTHER,
                theme_tags=[ThemeTag.OTHER],
                characters=["林盏"],
                source=SourceRef(chapter_id="ch2", start=0, end=1, quote="x", content_hash="h"),
            )
        ],
        characters=["林盏"],
    )
    pack = build_context_pack(_project(), 2, summaries=[written_ch2])
    ids = {doc.doc_id for doc in pack.retrieved}
    assert "outline-chapter-2" not in ids
    assert "summary-chapter-2" not in ids
    assert "summary-chapter-1" in ids or "outline-chapter-1" in ids


def test_open_foreshadows_injected() -> None:
    """本章之前开启且未回收的钩子必须出现在上下文包里。"""
    pack = build_context_pack(_project(), 3)
    keys = {entry.key for entry in pack.open_foreshadows}
    assert "hook-ch1" in keys and "hook-ch2" in keys


def test_pack_is_deterministic() -> None:
    """同输入两次组装，序列化结果逐字段一致。"""
    first = build_context_pack(_project(), 2).model_dump_json()
    second = build_context_pack(_project(), 2).model_dump_json()
    assert first == second
