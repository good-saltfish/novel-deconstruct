"""Planner Agent 的工具协议与白名单工具集（ADR-0006）。

协议参考 MIT 项目 ai-novelist 的 ToolDef（id/description/pydantic 参数/execute），
实现为 clean-room 自有代码。工具分两类：只读检索与只写候选；
不存在删除/确认/改正文/外网工具——agent 在物理上无法越权。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ndecon.agent.models import AgentDrafts, Beat, SettingEntry, TaskType
from ndecon.kb.corpus import search_knowledge
from ndecon.retrieval.context import build_context_pack
from ndecon.retrieval.foreshadow import (
    ForeshadowEntry,
    extract_foreshadows,
    open_foreshadows_at,
)
from ndecon.workspace.models import CreationProject

# 错误回喂模型时的截断长度（防上游错误长文本成为注入载体）
ERROR_FEEDBACK_CHARS = 200
# 各检索工具返回条数
METHODOLOGY_TOP_K = 3
BOOK_TOP_K = 5


@dataclass
class ToolContext:
    """工具执行所需的运行上下文（不含任何写入句柄之外的越权能力）。"""

    project: CreationProject
    task: TaskType
    chapter_order: int | None
    workspace: Path
    drafts: AgentDrafts


class ToolDef(ABC):
    """工具基类：子类声明 id/description/参数模型并实现 run。"""

    id: str
    description: str
    read_only: bool = True

    class Params(BaseModel):
        """默认空参数；子类用自己的嵌套 Params 覆盖。"""

    def json_schema(self) -> dict:
        """返回给模型看的参数 JSON Schema。"""
        return self.Params.model_json_schema()

    @abstractmethod
    def run(self, params: BaseModel, ctx: ToolContext) -> str:
        """执行工具，返回模型可读文本；参数已由注册表校验。"""
        raise NotImplementedError


# ---------- 只读工具 ----------


class SearchMethodology(ToolDef):
    """检索作者本地知识库中的写作方法论（L0.5，不含小说原文）。"""

    id = "search_methodology"
    description = (
        "在作者本地写作知识库（拆书方法论、写作心得、设定素材、扫榜分析）中检索可借鉴的手法。"
        "查询用具体问题，如'冲突三角怎么设计''伏笔如何回收'。返回带来源的片段。"
    )

    class Params(BaseModel):
        query: str

    def run(self, params: BaseModel, ctx: ToolContext) -> str:
        """检索方法论并渲染为带来源的文本。"""
        snippets = search_knowledge(ctx.workspace, params.query, k=METHODOLOGY_TOP_K)  # type: ignore[attr-defined]
        if not snippets:
            return "知识库无命中（可能尚未建库，或没有相关资料）。"
        return "\n".join(
            f"- 来源 {s.source} {('《' + s.heading + '》') if s.heading else ''}：{s.text[:300]}"
            for s in snippets
        )


class GetOutline(ToolDef):
    """读取指定章节的已确认细纲。"""

    id = "get_outline"
    description = "读取某一章的细纲（标题/核心事件/开篇钩/章尾钩）。写第 N 章前应查看相邻章节。"

    class Params(BaseModel):
        order: int

    def run(self, params: BaseModel, ctx: ToolContext) -> str:
        """定位并返回目标章细纲；越界/缺失给出可读提示。"""
        chapter = next((c for c in ctx.project.draft.chapters if c.order == params.order), None)  # type: ignore[attr-defined]
        if chapter is None:
            return f"没有第 {params.order} 章细纲（当前仅 1-10 章）。"
        return (
            f"第{chapter.order}章《{chapter.title}》｜核心事件：{chapter.core_event}｜"
            f"开篇钩：{chapter.opening_hook or '无'}｜章尾钩：{chapter.ending_hook or '无'}"
        )


class GetConfirmedSetting(ToolDef):
    """读取已确认的骨架设定部件。"""

    id = "get_confirmed_setting"
    description = "读取已确认的题材定位、主角人设、金手指或卷纲（写新设定前先查，防止冲突）。"

    class Params(BaseModel):
        part: str

    def run(self, params: BaseModel, ctx: ToolContext) -> str:
        """按部件名返回已确认内容摘要。"""
        part = params.part.strip()  # type: ignore[attr-defined]
        d = ctx.project.draft
        selling = "；".join(d.positioning.selling_points)
        mapping = {
            "positioning": lambda: (
                f"题材：{d.positioning.genre}｜卖点：{selling}｜{d.positioning.core_premise}"
            ),
            "protagonist": lambda: (
                f"主角：{d.protagonist.name}｜欲望：{d.protagonist.desire}｜"
                f"缺陷：{d.protagonist.flaw}｜能力：{d.protagonist.signature_ability}｜"
                f"背景：{d.protagonist.background}"
            ),
            "golden_finger": lambda: (
                f"金手指：{d.golden_finger.name}（{d.golden_finger.form}）｜"
                f"能力：{d.golden_finger.ability}｜限制：{d.golden_finger.cost_limitation}"
            ),
            "volume": lambda: (
                f"卷纲：{d.volume.volume_title}｜目标：{d.volume.volume_goal}｜"
                f"冲突：{d.volume.main_conflict}｜卷尾：{d.volume.ending_hook}"
            ),
        }
        if part not in mapping:
            return f"未知部件 {part}，可选：{', '.join(mapping)}"
        return mapping[part]()


class SearchOwnBook(ToolDef):
    """在本书已有细纲中做 bigram 检索（L1 一致性检索）。"""

    id = "search_own_book"
    description = "检索本书已确认的细纲/设定，查找相关元素（如某个意象、角色、规则），保证新增内容与全书一致。"

    class Params(BaseModel):
        query: str

    def run(self, params: BaseModel, ctx: ToolContext) -> str:
        """复用 ContextPack 同款索引检索本书语料。"""
        order = ctx.chapter_order or 1
        pack = build_context_pack(ctx.project, order)
        hits = [d for d in pack.retrieved if d.doc_type != "summary_chapter"][:BOOK_TOP_K]
        if not hits:
            return "本书暂无相关内容。"
        return "\n".join(f"- [{h.doc_type}] {h.title}：{h.snippet}" for h in hits)


class ListOpenForeshadows(ToolDef):
    """列出当前写作时点之前未回收的伏笔。"""

    id = "list_open_foreshadows"
    description = "列出截至目标章仍未回收的伏笔（含开启章）。扩写节拍时据此安排埋伏/回收。"

    class Params(BaseModel):
        pass

    def run(self, params: BaseModel, ctx: ToolContext) -> str:
        """从伏笔台账取开放伏笔并格式化。"""
        order = ctx.chapter_order or 1
        ledger = extract_foreshadows(ctx.project.draft.chapters)
        entries: list[ForeshadowEntry] = open_foreshadows_at(ledger, order)
        if not entries:
            return "当前没有待回收伏笔。"
        return "\n".join(f"- 第{e.opened_order}章埋：{e.description}" for e in entries)


# ---------- 写候选工具 ----------


class DraftBeats(ToolDef):
    """提交一组节拍候选（可多次调用追加）。"""

    id = "draft_beats"
    read_only = False
    description = (
        "提交目标章的 3-6 个节拍候选。可多次调用（会追加并按 order 去重重排）。"
        "节拍必须忠于细纲的核心事件，冲突三角与情绪要明确。提交后调用 finish。"
    )

    class Params(BaseModel):
        beats: list[Beat]

    def run(self, params: BaseModel, ctx: ToolContext) -> str:
        """校验并把节拍合并进候选（按 order 后者覆盖前者）。"""
        incoming = params.beats  # type: ignore[attr-defined]
        if not incoming:
            return "未提交任何节拍。"
        merged = {b.order: b for b in ctx.drafts.beats}
        for beat in incoming:
            merged[beat.order] = beat
        ctx.drafts.beats = sorted(merged.values(), key=lambda b: b.order)
        return f"已累积 {len(ctx.drafts.beats)} 个节拍候选。确认无误后请调用 finish。"


class DraftSetting(ToolDef):
    """提交一条设定候选。"""

    id = "draft_setting"
    read_only = False
    description = (
        "提交一条设定候选，entry_type 取 world_rule/faction/character/location/item/power_system。"
        "可多次调用提交多条。必须与已确认金手指/主角不矛盾；发现矛盾请在 finish 中报告。"
    )

    class Params(BaseModel):
        entry: SettingEntry

    def run(self, params: BaseModel, ctx: ToolContext) -> str:
        """追加设定候选（同名后者覆盖）。"""
        entry = params.entry  # type: ignore[attr-defined]
        same_type = [
            i
            for i, s in enumerate(ctx.drafts.settings)
            if s.entry_type == entry.entry_type and s.name == entry.name
        ]
        for index in reversed(same_type):
            ctx.drafts.settings.pop(index)
        ctx.drafts.settings.append(entry)
        return f"已累积 {len(ctx.drafts.settings)} 条设定候选。完成后请调用 finish。"


# ---------- 注册 ----------

_TOOL_CLASSES: list[type[ToolDef]] = [
    SearchMethodology,
    GetOutline,
    GetConfirmedSetting,
    SearchOwnBook,
    ListOpenForeshadows,
    DraftBeats,
    DraftSetting,
]
TOOL_REGISTRY: dict[str, ToolDef] = {cls.id: cls() for cls in _TOOL_CLASSES}
READ_ONLY_TOOLS = {tool_id for tool_id, tool in TOOL_REGISTRY.items() if tool.read_only}


def tool_catalog_text() -> str:
    """渲染工具目录供 system prompt 使用。"""
    import json

    lines = ["你可以调用以下工具，每轮只调用一个，用 JSON 动作回复："]
    for tool_id, tool in TOOL_REGISTRY.items():
        lines.append(f"- {tool_id}：{tool.description}")
        lines.append(f"  参数 schema：{json.dumps(tool.json_schema(), ensure_ascii=False)}")
    return "\n".join(lines)


def execute_tool(tool_id: str, raw_args: dict[str, Any], ctx: ToolContext) -> tuple[bool, str]:
    """按名称执行工具；未知工具/参数非法/执行异常统一返回 (False, 错误文本)。

    错误文本截断到 ERROR_FEEDBACK_CHARS，作为 observation 回喂模型，绝不抛出中断循环。
    """
    tool = TOOL_REGISTRY.get(tool_id)
    if tool is None:
        return False, f"未知工具 {tool_id}，可选：{', '.join(TOOL_REGISTRY)}"
    try:
        params = tool.Params.model_validate(raw_args or {})
    except ValidationError as exc:
        return False, f"参数不合法：{str(exc)[:ERROR_FEEDBACK_CHARS]}"
    try:
        return True, tool.run(params, ctx)
    except Exception as exc:  # noqa: BLE001 - 工具错误必须回喂而非中断 agent
        return False, f"工具执行失败：{type(exc).__name__}: {str(exc)[:ERROR_FEEDBACK_CHARS]}"
