"""工作区数据模型：参考书项目与新长篇创作项目。

两类项目共存于同一工作区：
- reference：一次拆书分析的入库结果（只存源路径与聚合 JSON，绝不复制原文）；
- creation：新长篇创作工作台，各结构部件区分 draft（模型候选）与可用的已验收状态。

部件状态三态（B2 修复）：早期只有 bool（True=人工确认、False=未确认），
全自动链路下模型自检通过的候选必须能入库，否则 `context` 永远为空。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ndecon.domain.creation import CreationDraft, ManuscriptRecord, SettingEntry
from ndecon.domain.deconstruction import BookDeconstruction
from ndecon.domain.models import Aggregation

ProjectKind = Literal["reference", "creation"]

# 部件生命周期：模型候选 -> 自动验收 -> 人工确认。
# auto-accepted 与 human-confirmed 都算"可用"，draft 不算（不进上下文包）。
PartState = Literal["draft", "auto-accepted", "human-confirmed"]

USABLE_PART_STATES: frozenset[str] = frozenset({"auto-accepted", "human-confirmed"})


def is_usable(state: str | None) -> bool:
    """部件状态是否可用于注入上下文（None/未知值一律视为不可用）。"""
    return state in USABLE_PART_STATES


class ProjectMeta(BaseModel):
    """项目公共元数据。"""

    id: str = Field(min_length=1)
    kind: ProjectKind
    title: str = Field(min_length=1)
    created_at: str = Field(min_length=1, description="ISO 8601 本地时间戳")


class ReferenceProject(BaseModel):
    """参考书项目：源文件路径 + 章节索引 + 聚合统计（无原文正文）。"""

    meta: ProjectMeta
    source_path: str = Field(min_length=1, description="原文在本机的绝对路径；仅本地使用")
    chapter_count: int = Field(ge=0)
    total_plot_points: int = Field(ge=0)
    aggregation: Aggregation | None = None
    # 结构化拆书产物（规则层，若跑过语义层则含 panorama）。
    # 与 aggregation 并存不是冗余：aggregation 是"章节摘要聚合"（旧分析管道），
    # 这里存的是逐章测量 + 全书结构剖面 + 钩子台账，创作侧要的正是后者。
    deconstruction: BookDeconstruction | None = None


class CreationProject(BaseModel):
    """新长篇项目：设定输入、参考拆书与各结构部件的候选/确认状态。"""

    meta: ProjectMeta
    genre: str = ""
    premise: str = Field(default="", description="一句话设定/核心创意")
    reference_ids: list[str] = Field(default_factory=list)
    draft: CreationDraft = Field(default_factory=CreationDraft)
    # 部件状态：part_name -> PartState（draft / auto-accepted / human-confirmed）。
    # 兼容旧数据：mode="before" 迁移器把 True -> human-confirmed、False -> 删键。
    confirmed_parts: dict[str, PartState] = Field(default_factory=dict)
    # 已写章节：键为 "ch<order>"，正文 Markdown 另存 manuscripts/chNNN.md
    manuscripts: dict[str, ManuscriptRecord] = Field(default_factory=dict)
    # 章节结构化摘要在早期版本里是这里的 summaries 字段，现在**已外置**到
    # summaries/chNNN.json（store.read_summary / list_summaries 读）。
    # 注意：字段本身已删除——它在生成链路里从来没被写过，唯一的效果是让读代码的人
    # 以为"摘要在这里"（agent/tools.py 曾据此读它，结果恒为空）。旧工作区的数据由
    # WorkspaceStore.get() 在反序列化前搬盘，不会丢。
    # 连写游标：已推进到第几章（resume 从 cursor+1 继续；全自动连跑的唯一进度真相）
    cursor: int = Field(default=0, ge=0, description="已提交的最大章号；0 表示尚未开始")
    # Planner Agent 提交的设定候选；经 accept 接口确认后落库（候选不代笔）
    settings: list[SettingEntry] = Field(default_factory=list, description="Agent 提交的设定候选")

    @field_validator("confirmed_parts", mode="before")
    @classmethod
    def _migrate_confirmed_parts(cls, value: object) -> object:
        """旧数据兼容：bool -> 三态字符串。

        历史工作区里存的是 {"positioning": True, "volume": False}；
        True 曾是"人工确认"，迁移为 human-confirmed；False 本就不进上下文，直接删键。
        无法识别的值一并丢弃——宁可让部件回到 draft（用户看得见），
        也不要因为一个脏值让整个项目读不出来。
        """
        if not isinstance(value, dict):
            return value
        migrated: dict[str, str] = {}
        for key, raw in value.items():
            if isinstance(raw, bool):
                if raw:
                    migrated[key] = "human-confirmed"
                continue
            if raw in USABLE_PART_STATES or raw == "draft":
                migrated[key] = str(raw)
            continue
        return migrated
