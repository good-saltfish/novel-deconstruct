"""创作侧结构化产物模型（基础版：定位/卷纲/前十章细纲/主角/金手指/章节正文）。

全部为可编辑的结构化候选；没有 SourceRef——这些是面向未来作品的生成内容，
不是对既有文本的引用。学习注入只允许来自参考书的聚合数字统计，不含原文。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Positioning(BaseModel):
    """题材定位。"""

    genre: str = Field(min_length=1, description="主类型，如末世/系统流")
    audience: str = Field(default="", description="目标读者画像")
    core_premise: str = Field(default="", description="一句话卖点/核心创意")
    selling_points: list[str] = Field(default_factory=list, description="差异化卖点")
    tone_target: str = Field(default="", description="目标基调")


class VolumeOutline(BaseModel):
    """首卷纲要。"""

    volume_title: str = Field(min_length=1)
    volume_goal: str = Field(default="", description="本卷主角要达成的目标")
    main_conflict: str = Field(default="", description="本卷核心矛盾")
    ending_hook: str = Field(default="", description="卷尾钩子/升级方向")


class Beat(BaseModel):
    """章节节拍（#25 Planner Agent 产出并经用户确认后挂到细纲章上）。"""

    order: int = Field(ge=1, le=12)
    scene: str = ""
    characters: list[str] = Field(default_factory=list)
    triangle: list[str] = Field(default_factory=list)
    emotion: str = ""
    event: str = ""
    plant_foreshadow: str = ""
    resolve_foreshadow: str = ""


class SettingEntry(BaseModel):
    """结构化设定库条目（#25）。"""

    entry_type: str = Field(
        min_length=1, description="world_rule/faction/character/location/item/power_system"
    )
    name: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    related_chapters: list[int] = Field(default_factory=list)


class ChapterOutline(BaseModel):
    """前 10 章细纲中的单章条目。"""

    order: int = Field(ge=1, le=10)
    title: str = Field(min_length=1)
    core_event: str = Field(default="", description="本章核心事件")
    opening_hook: str = Field(default="", description="本章开篇钩子")
    ending_hook: str = Field(default="", description="本章章尾钩子")
    beats: list[Beat] = Field(default_factory=list, description="已确认节拍（#25）")


class ProtagonistProfile(BaseModel):
    """主角人设（拆书方法论中的高价值字段：功能/缺陷/欲望）。"""

    name: str = Field(min_length=1)
    background: str = Field(default="", description="出身/初始处境")
    personality: str = Field(default="", description="性格特征")
    desire: str = Field(default="", description="核心欲望/目标")
    flaw: str = Field(default="", description="性格缺陷或成长课题（避免完美主角）")
    signature_ability: str = Field(default="", description="标志性能力/行事手段")


class GoldenFingerSpec(BaseModel):
    """金手指；'限制/代价' 是拆书 schema 逆向出的必填高价值字段。"""

    name: str = Field(min_length=1)
    form: str = Field(default="", description="形态（系统/血脉/物品/天赋等）")
    ability: str = Field(default="", description="能做什么")
    cost_limitation: str = Field(min_length=1, description="限制与代价（必填，防止无敌化）")
    growth_path: str = Field(default="", description="成长/升级路径")


class CreationDraft(BaseModel):
    """一次创作工程的全部结构部件；任一部件可为空表示尚未生成。"""

    positioning: Positioning | None = None
    volume: VolumeOutline | None = None
    chapters: list[ChapterOutline] = Field(default_factory=list, max_length=10)
    protagonist: ProtagonistProfile | None = None
    golden_finger: GoldenFingerSpec | None = None
    # 每部件的模型来源：part_name -> model_id，用于区分模型候选与人工内容
    provenance: dict[str, str] = Field(default_factory=dict)

    def part_names(self) -> list[str]:
        """返回可生成/编辑的部件名清单（固定顺序）。"""
        return ["positioning", "volume", "chapters", "protagonist", "golden_finger"]


# 章节正文生命周期：模型/人工候选 -> 用户确认；重新生成会回到 draft
ManuscriptStatus = Literal["draft", "user-confirmed"]


class ChapterSelfReview(BaseModel):
    """章节正文的结构化自评（#16）；只报告不自动改写。"""

    hook_strength: int = Field(ge=1, le=5, description="钩子强度（开篇钩/章尾钩）1-5")
    info_density: int = Field(ge=1, le=5, description="信息密度 1-5：有效推进/水字数")
    outline_followed: bool = Field(description="是否基本按本章细纲推进")
    deviations: list[str] = Field(default_factory=list, description="与细纲的具体偏差点")
    notes: str = Field(default="", description="其他评语（一句）")
    model_id: str = Field(min_length=1, description="自评产出方；规则产物用 rule:<name>")
    prompt_version: str = Field(min_length=1)


class ManuscriptRecord(BaseModel):
    """一章正文的元数据与状态；正文本身落盘 manuscripts/chNNN.md，不塞 JSON。"""

    order: int = Field(ge=1, le=10)
    title: str = Field(min_length=1)
    status: ManuscriptStatus = "draft"
    word_count: int = Field(ge=0, description="去空白后的正文字数")
    model_id: str = Field(min_length=1, description="正文产出方；人工编辑保存为 user-edit")
    prompt_version: str = Field(min_length=1)
    updated_at: str = Field(min_length=1, description="ISO 8601 本地时间戳")
    review: ChapterSelfReview | None = None


class ChapterWriting(BaseModel):
    """章节生成器的一次产出：正文 Markdown + 结构化自评。"""

    content: str = Field(min_length=1, description="章节正文（Markdown 纯文本，不含章题行）")
    review: ChapterSelfReview
