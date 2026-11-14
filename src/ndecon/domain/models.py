"""拆书产物领域模型（v0.1）。

覆盖：证据引用、情节点、章节摘要。

硬约束（由代码而非 prompt 保证）：
1. 情节点基调/主题标签只能取受控枚举值；
2. 证据 quote 必须带章内字符偏移，且能在同 hash 原文中定位；
3. 每条模型产物带 provider / prompt_version / schema_version，支持重放。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from ndecon import SCHEMA_VERSION
from ndecon.domain.enums import ThemeTag, Tone


class SourceRef(BaseModel):
    """一条指向章节原文的证据引用。"""

    chapter_id: str
    start: int = Field(ge=0, description="章内起始字符偏移")
    end: int = Field(ge=0, description="章内结束字符偏移（不含）")
    quote: str = Field(min_length=1, description="原文摘录，必须能按偏移在章节文本中定位")
    content_hash: str = Field(min_length=1, description="所属章节内容指纹")

    @model_validator(mode="after")
    def _check_span_order(self) -> SourceRef:
        """校验引用区间非负且 start < end。"""
        if self.end <= self.start:
            raise ValueError("引用区间必须满足 start < end")
        return self

    def is_located_in(self, chapter_text: str) -> bool:
        """判断 quote 是否能按偏移在给定章节原文中双重定位（区间 + 文本一致）。"""
        if not (0 <= self.start < self.end <= len(chapter_text)):
            return False
        return chapter_text[self.start : self.end] == self.quote


class _Provenance(BaseModel):
    """模型产物公共字段：来源标记与版本，支持缓存失效与重放对比。"""

    model_id: str = Field(min_length=1, description="产出方标识；规则产物用 rule:<name>")
    prompt_version: str = Field(min_length=1)
    schema_version: str = SCHEMA_VERSION


class PlotPoint(BaseModel):
    """单个情节点（旧分析管道的最小叙事单元）。

    旧拆书管道已退役（2026-11-14），现在只有 `ChapterSummary.plot_points` 还带着
    这个类型的容器，实际写入方只有 `providers/fake.py` 的确定性摘要兜底。
    保留它是因为 `ChapterSummary` 仍被创作侧摘要复用，砍掉会让旧工作区读不出来。
    """

    index: int = Field(ge=1)
    summary: str = Field(min_length=1, description="情节点一句话概括")
    tone: Tone
    theme_tags: list[ThemeTag] = Field(min_length=1)
    characters: list[str] = Field(default_factory=list)
    source: SourceRef


class ChapterSummary(_Provenance):
    """单章结构化摘要（创作侧与旧分析管道共用）。"""

    chapter_id: str
    order: int = Field(ge=1)
    title: str
    gist: str = Field(min_length=1, description="本章一句话概要")
    plot_points: list[PlotPoint] = Field(default_factory=list, max_length=40)
    characters: list[str] = Field(default_factory=list)
