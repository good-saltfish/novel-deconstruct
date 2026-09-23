"""创作侧结构化产物模型（基础版：定位/卷纲/前十章细纲/主角/金手指）。

全部为可编辑的结构化候选；没有 SourceRef——这些是面向未来作品的生成内容，
不是对既有文本的引用。学习注入只允许来自参考书的聚合数字统计，不含原文。
"""

from __future__ import annotations

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


class ChapterOutline(BaseModel):
    """前 10 章细纲中的单章条目。"""

    order: int = Field(ge=1, le=10)
    title: str = Field(min_length=1)
    core_event: str = Field(default="", description="本章核心事件")
    opening_hook: str = Field(default="", description="本章开篇钩子")
    ending_hook: str = Field(default="", description="本章章尾钩子")


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
