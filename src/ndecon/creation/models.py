"""创作侧结构化产物模型（基础版：定位/卷纲/前十章细纲/主角/金手指/章节正文）。

全部为可编辑的结构化候选；没有 SourceRef——这些是面向未来作品的生成内容，
不是对既有文本的引用。学习注入只允许来自参考书的聚合数字统计，不含原文。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 章号上限：早期实现硬限 10 章（B1），全自动长篇需要写到数百章，故放到 999。
# 节拍数是"章内顺序"，与章号无关，单独用 BEAT_ORDER_MAX。
CHAPTER_ORDER_MAX = 999
BEAT_ORDER_MAX = 20


class Beat(BaseModel):
    """单章节拍（Planner Agent 提交的候选；确认后挂到细纲章上）。

    字段与 agent/tools.py 的 DraftBeats 校验保持一致：order 1-20、必填 scene/event。
    """

    order: int = Field(ge=1, le=BEAT_ORDER_MAX, description="节拍序号（章内顺序）")
    scene: str = Field(min_length=1, description="场景/地点")
    characters: list[str] = Field(default_factory=list, description="出场角色")
    triangle: list[str] = Field(default_factory=list, description="冲突三角角色：persecutor/victim/savior")
    emotion: str = Field(default="", description="本节拍情绪走向")
    event: str = Field(min_length=1, description="核心事件")
    plant_foreshadow: str | None = Field(default=None, description="可选：在本节拍埋下的伏笔")


class SettingEntry(BaseModel):
    """一条结构化设定候选（势力/配角/地点/规则/道具/力量体系等）。

    entry_type 受控词表与 agent/tools.py 的 DraftSetting 校验保持一致。
    """

    entry_type: Literal[
        "world_rule", "faction", "character", "location", "item", "power_system"
    ] = Field(description="设定类型")
    name: str = Field(min_length=1, description="名称")
    content: str = Field(min_length=1, description="设定内容/描述")
    tags: list[str] = Field(default_factory=list, description="标签")
    related_chapters: list[int] = Field(default_factory=list, description="关联章号")


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


class ChapterOutline(BaseModel):
    """细纲中的单章条目（章号上限见 CHAPTER_ORDER_MAX）。"""

    order: int = Field(ge=1, le=CHAPTER_ORDER_MAX)
    title: str = Field(min_length=1)
    core_event: str = Field(default="", description="本章核心事件")
    opening_hook: str = Field(default="", description="本章开篇钩子")
    ending_hook: str = Field(default="", description="本章章尾钩子")
    # Planner Agent 提交的节拍候选；确认（accept）后才作为权威内容挂上。
    beats: list[Beat] = Field(default_factory=list, description="本章节拍候选（Agent 提交，未确认前为空）")


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
    chapters: list[ChapterOutline] = Field(default_factory=list, max_length=CHAPTER_ORDER_MAX)
    protagonist: ProtagonistProfile | None = None
    golden_finger: GoldenFingerSpec | None = None
    # 每部件的模型来源：part_name -> model_id，用于区分模型候选与人工内容
    provenance: dict[str, str] = Field(default_factory=dict)

    def part_names(self) -> list[str]:
        """返回可生成/编辑的部件名清单（固定顺序）。"""
        return ["positioning", "volume", "chapters", "protagonist", "golden_finger"]


class ChapterBeatPlan(BaseModel):
    """细纲阶段为一章产出的节拍序列（候选，未确认前不得合并进 ChapterOutline.beats）。"""

    order: int = Field(ge=1, le=CHAPTER_ORDER_MAX, description="章号")
    beats: list[Beat] = Field(default_factory=list, max_length=BEAT_ORDER_MAX, description="本章节拍 3-20 个")
    emotion_track: str = Field(default="", description="本章情绪走向，如 压→转→爽")


class SettingPlan(BaseModel):
    """设定阶段产物（候选）。

    **候选不代笔**：落到 `CreationProject.settings` 前必须由作者显式确认。
    """

    entries: list[SettingEntry] = Field(default_factory=list, description="设定条目候选")
    golden_finger: GoldenFingerSpec | None = Field(default=None, description="金手指候选（含必填代价）")
    notes: list[str] = Field(default_factory=list, description="产出方的自我说明/学到的方法")
    model_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)


class OutlinePlan(BaseModel):
    """细纲阶段产物（候选）。"""

    chapters: list[ChapterBeatPlan] = Field(default_factory=list, max_length=CHAPTER_ORDER_MAX)
    notes: list[str] = Field(default_factory=list, description="产出方的自我说明/学到的方法")
    model_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)


# 章节正文生命周期：模型/人工候选 -> 自动验收（全自动链路）-> 用户确认；重新生成会回到 draft
ManuscriptStatus = Literal["draft", "auto-accepted", "user-confirmed"]


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

    order: int = Field(ge=1, le=CHAPTER_ORDER_MAX)
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
