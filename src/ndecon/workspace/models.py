"""工作区数据模型：参考书项目与新长篇创作项目。

两类项目共存于同一工作区：
- reference：一次拆书分析的入库结果（只存源路径与聚合 JSON，绝不复制原文）；
- creation：新长篇创作工作台，各结构部件区分 draft（模型候选）与 confirmed（用户确认）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ndecon.creation.models import CreationDraft, ManuscriptRecord, SettingEntry
from ndecon.domain.models import Aggregation

ProjectKind = Literal["reference", "creation"]


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


class CreationProject(BaseModel):
    """新长篇项目：设定输入、参考拆书与各结构部件的候选/确认状态。"""

    meta: ProjectMeta
    genre: str = ""
    premise: str = Field(default="", description="一句话设定/核心创意")
    reference_ids: list[str] = Field(default_factory=list)
    draft: CreationDraft = Field(default_factory=CreationDraft)
    # 部件确认状态：part_name -> True 表示用户已确认当前内容
    confirmed_parts: dict[str, bool] = Field(default_factory=dict)
    # 已确认的结构化设定库（#25 Planner Agent 候选经确认后进入）
    settings: list[SettingEntry] = Field(default_factory=list)
    # 已写章节：键为 "ch<order>"，正文 Markdown 另存 manuscripts/chNNN.md
    manuscripts: dict[str, ManuscriptRecord] = Field(default_factory=dict)
