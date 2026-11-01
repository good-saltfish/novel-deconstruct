"""RAG 第一轮落地（t2）的 P0 修复单测：断言真实效果，不玩"只改开关"的把戏。

覆盖：
- P0③ KB 渲染三合一：render_kb_block 为唯一实现；prompts/plans 委托；400 截断一致；
- P0② SearchOwnBook 用真实 doc_type="chapter_summary" 过滤 + 注入已写章节摘要；
- P0④⑤ CreationProject.summaries 落盘可回读；store.load_project_index 真正消费
  reindex 的 index.json（消除死写）；build_project_index 消费 summaries；
- P0① 面板章节生成写后闭环：生成一章后 project.summaries 真的多了一条结构摘要。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndecon.agent.models import AgentDrafts
from ndecon.agent.tools import SearchOwnBook, ToolContext
from ndecon.creation import plans as creation_plans
from ndecon.creation import prompts as creation_prompts
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
from ndecon.kb.corpus import render_kb_block
from ndecon.retrieval.context import build_context_pack
from ndecon.retrieval.indexer import build_project_index
from ndecon.workspace.models import CreationProject, ProjectMeta
from ndecon.workspace.store import WorkspaceStore

# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------


def _chapter_summary(order: int, gist: str, *, plot_text: str = "林盏点亮铜灯") -> ChapterSummary:
    """构造一条结构章节摘要（不含任何版权原文 quote）。"""
    return ChapterSummary(
        model_id="rule:fake",
        prompt_version="oc-v0",
        chapter_id=f"ch{order}",
        order=order,
        title=f"第{order}章",
        gist=gist,
        plot_points=[
            PlotPoint(
                index=1,
                summary=plot_text,
                tone=Tone.HOT_BLOODED,
                theme_tags=[ThemeTag.GROWTH],
                characters=["林盏"],
                source=SourceRef(chapter_id=f"ch{order}", start=0, end=5, quote="xxxxx", content_hash="x"),
            )
        ],
        characters=["林盏"],
    )


def _chapter_outline(order: int, title: str, core_event: str, hook: str) -> ChapterOutline:
    """构造一个真实 ChapterOutline（build_context_pack / indexer 需要的字段）。"""
    return ChapterOutline(
        order=order, title=title, core_event=core_event, opening_hook=hook, ending_hook=hook
    )


def _creation_project(tmp_path: Path, *, with_draft: bool = True) -> CreationProject:
    """在临时工作区落一个创作项目（含完整骨架，可被 reindex/context 消费）。"""
    draft = None
    if with_draft:
        draft = CreationDraft(
            positioning=Positioning(genre="都市异能", core_premise="路灯封印"),
            volume=VolumeOutline(volume_title="第一卷", volume_goal="活下来", main_conflict="规则冲突", ending_hook="卷尾"),
            chapters=[_chapter_outline(1, "停电夜", "发现路灯明灭", "异常开场")],
            protagonist=ProtagonistProfile(name="林盏", desire="查清真相"),
            golden_finger=GoldenFingerSpec(name="铜灯", cost_limitation="一日三燃"),
        )
    return CreationProject(
        meta=ProjectMeta(id="creation-rag-1", kind="creation", title="灯火", created_at="2026-09-23T00:00:00"),
        draft=draft,
        confirmed_parts={} if with_draft else {},
    )


# ---------------------------------------------------------------------------
# P0③ KB 渲染三合一
# ---------------------------------------------------------------------------


def test_render_kb_block_is_single_source() -> None:
    """空片段返回空串；带 heading 时套用同一句"严禁照抄"口径；统一 400 截断。"""
    assert render_kb_block([]) == ""
    snippets = [{"source": "kb/x.md", "heading": "可迁移做法", "text": "每 2-3 章一次爽点" * 20}]
    block = render_kb_block(snippets)
    assert "严禁照抄具体作品情节" in block
    assert "来源 kb/x.md 《可迁移做法》" in block
    # 单条文本不得超过 KB_SNIPPET_CHARS(400) + 省略号
    body = next(line for line in block.splitlines() if line.startswith("1. 来源 "))
    assert len(body) - len("1. 来源 ") <= 401


def test_render_kb_block_with_heading_keeps_consistent_wording() -> None:
    """plans 侧的带标题渲染必须和正文侧共用同一句"严禁照抄"，不再写分号变体。"""
    heading = "【设定写法的方法论参考】"
    block = render_kb_block([{"source": "kb/y.md", "heading": "代价设计", "text": "代价前置"}], heading=heading)
    assert heading in block
    assert "严禁照抄具体作品情节" in block


def test_prompts_and_plans_delegate_to_render_kb_block() -> None:
    """prompts/plans 不再各自实现渲染逻辑，而是复用统一口径。"""
    snippets = [{"source": "kb/z.md", "heading": "节奏", "text": "章尾留钩"}]
    prompt_block = creation_prompts.full_draft_user_message("书", "都市", "设定", [], snippets)
    assert "严禁照抄具体作品情节" in prompt_block
    plans_block = creation_plans._kb_block(snippets, "【设定写法的方法论参考】")
    assert "严禁照抄具体作品情节" in plans_block
    assert "禁止照抄" in plans_block
    # 两者正文行一致（统一来源前缀）
    assert "来源 kb/z.md 《节奏》" in prompt_block
    assert "来源 kb/z.md 《节奏》" in plans_block


# ---------------------------------------------------------------------------
# P0② SearchOwnBook 用真实 doc_type 过滤 + 注入已写章节摘要
# ---------------------------------------------------------------------------


def test_search_own_book_excludes_summary_documents(tmp_path: Path) -> None:
    """SearchOwnBook 必须用真实 doc_type="chapter_summary" 过滤掉摘要类文档。

    t3 缺陷 #1：旧代码写 `!="summary_chapter"`（永远为真，等于没过滤）。
    这里验证：注入已写章节摘要后，本章小结不会被当成"搜索结果"回灌给模型；
    同时索引里确实含有摘要（排除是"过滤"而非"未索引"，P0① 生效）。
    """
    project = _creation_project(tmp_path)
    project.summaries["ch2"] = _chapter_summary(2, "巡夜第二晚")
    ctx = ToolContext(
        project=project,
        task="expand_chapter",
        chapter_order=1,
        workspace=tmp_path,
        drafts=AgentDrafts(),
    )

    result = SearchOwnBook().run(SearchOwnBook.Params(query="林盏"), ctx)
    # 本章小结不得作为搜索结果出现（正确 doc_type 过滤）
    assert "[chapter_summary]" not in result

    # 索引确实含有已写章节摘要（证明注入生效，而非被静默丢弃）
    index = build_project_index(project, list(project.summaries.values()))
    assert any(d.doc_type == "chapter_summary" for d in index.documents)
    # 且检索能召回它（与 SearchOwnBook 的"过滤"形成对照）
    pack = build_context_pack(project, 1, summaries=list(project.summaries.values()))
    assert any(d.doc_type == "chapter_summary" for d in pack.retrieved)


# ---------------------------------------------------------------------------
# P0④⑤ summaries 落盘 + load_project_index 消费 index.json
# ---------------------------------------------------------------------------


def test_summaries_field_round_trips(tmp_path: Path) -> None:
    """CreationProject.summaries 落盘后回读仍是 ChapterSummary，且不含 quote 原文。"""
    store = WorkspaceStore(tmp_path)
    project = _creation_project(tmp_path)
    secret = "版权原文密语玖捌柒"
    summary = _chapter_summary(1, "巡夜首晚")
    # 把密语挂到 SourceRef.quote（参考原文证据位）；indexer 永不读取 quote
    summary.plot_points[0].source.quote = secret
    project.summaries["ch1"] = summary
    store.save_creation(project)

    reloaded = store.get(project.meta.id)
    assert "ch1" in reloaded.summaries
    assert reloaded.summaries["ch1"].gist == "巡夜首晚"
    # 摘要即便携带 quote 也不进入索引文本（与 indexer 纪律一致：_plot_point_text 不读 quote）
    index = build_project_index(reloaded, list(reloaded.summaries.values()))
    assert secret not in str(index.to_dict())


def test_reindex_writes_index_then_load_project_index_reads_it(tmp_path: Path) -> None:
    """reindex 的 index.json 必须可被 load_project_index 真正消费（消除 t3 死写）。"""
    store = WorkspaceStore(tmp_path)
    project = _creation_project(tmp_path)
    project.summaries["ch1"] = _chapter_summary(1, "巡夜首晚")
    store.save_creation(project)

    index = build_project_index(project, list(project.summaries.values()))
    store.write_project_artifact(project.meta.id, "index.json", index.to_dict())

    # 之前没有读取方 → dead write；现在 load_project_index 必须能读回
    loaded = store.load_project_index(project.meta.id)
    assert loaded is not None
    assert len(loaded.documents) == len(index.documents)
    ids = {doc.doc_id for doc in loaded.documents}
    # reindex 已注入已写章节摘要（P0①），且未确认骨架按 P0④ 确认门控不入索引
    assert "summary-chapter-1" in ids

    # 不存在时返回 None（不抛、不假造）
    assert store.load_project_index("nonexistent-project") is None


# ---------------------------------------------------------------------------
# P0① 面板章节生成写后闭环（契约层验证真实效果）
# ---------------------------------------------------------------------------


@pytest.fixture()
def server(tmp_path):
    """在随机端口启动面板服务器，测试结束后关闭。"""
    from ndecon.panel.server import serve_in_thread

    srv, _thread = serve_in_thread(tmp_path, port=0)
    yield srv
    srv.shutdown()
    srv.server_close()


def test_panel_generate_chapter_writes_summary(server) -> None:
    """#16 扩展：Fake 生成一章后，project.summaries 真的多出一条结构摘要（写后闭环）。"""
    from ndecon.panel.server import api_request

    _, cre = api_request(
        server, "POST", "/api/projects/create", {"title": "闭环书", "genre": "都市", "premise": ""}
    )
    cre_id = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{cre_id}/generate")

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/generate", {"provider": "fake"}
    )
    assert status == 200, body

    # 重新读项目，确认 summaries 真的落盘了一条 ch1 摘要
    _, full = api_request(server, "GET", f"/api/projects/{cre_id}")
    assert "ch1" in full["summaries"]
    summary = full["summaries"]["ch1"]
    assert summary["order"] == 1
    assert summary["chapter_id"]
    assert isinstance(summary["plot_points"], list) and summary["plot_points"]
    # 本章小结确实是结构化产物（来源锚点存在，且为作者自家稿件而非版权原文）
    assert summary["plot_points"][0]["source"]["chapter_id"]
