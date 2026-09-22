"""拆书产物领域模型（v0.1）。

覆盖：证据引用、章节索引、情节点、章节摘要（Stage 2）、黄金三章报告（Stage 1）、总报告包。

硬约束（由代码而非 prompt 保证）：
1. 情节点基调/主题标签只能取受控枚举值；
2. 证据 quote 必须带章内字符偏移，且能在同 hash 原文中定位；
3. 每条模型产物带 provider / prompt_version / schema_version，支持重放；
4. 黄金三章报告中的"可借鉴要素"留给使用者——Fake 不得填充评论性结论。
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


class ChapterEntry(BaseModel):
    """章节索引项（来自确定性切分，不含模型产物）。"""

    order: int = Field(ge=1)
    title: str
    line_no: int = Field(ge=1)
    char_count: int = Field(ge=0, description="去空白后的字数")
    content_hash: str


class PlotPoint(BaseModel):
    """单个情节点（Stage 2 的最小叙事单元）。"""

    index: int = Field(ge=1)
    summary: str = Field(min_length=1, description="情节点一句话概括")
    tone: Tone
    theme_tags: list[ThemeTag] = Field(min_length=1)
    characters: list[str] = Field(default_factory=list)
    source: SourceRef


class ChapterSummary(_Provenance):
    """单章结构化摘要（Stage 2 产物）。"""

    chapter_id: str
    order: int = Field(ge=1)
    title: str
    gist: str = Field(min_length=1, description="本章一句话概要")
    plot_points: list[PlotPoint] = Field(default_factory=list, max_length=40)
    characters: list[str] = Field(default_factory=list)


class StructureBeat(BaseModel):
    """黄金三章报告中的结构段落（功能段 + 说明）。"""

    name: str = Field(min_length=1)
    note: str = Field(min_length=1)


class GoldenChapterReport(_Provenance):
    """黄金三章深度拆解报告（Stage 1，仅前 3 章）。

    注意：takeaways（可借鉴要素）属于学习层评论，v0.1 的 Fake 必须留空，
    由使用者自行填写；真实模型 provider 也只给证据型候选，不替人下结论。
    """

    chapter_id: str
    order: int = Field(ge=1)
    title: str
    opening_hook_quote: str = Field(default="", description="开篇钩子的原文锚点（可空）")
    opening_hook_note: str = Field(min_length=1, description="钩子手法的事实性描述")
    worldview_revealed: list[str] = Field(default_factory=list, description="本章透露的世界观信息")
    structure_beats: list[StructureBeat] = Field(default_factory=list)
    cliffhanger_quote: str = Field(default="", description="章尾钩子原文锚点（可空）")
    takeaways: list[str] = Field(default_factory=list, description="可借鉴要素；Fake 必须留空")


class ReportBundle(BaseModel):
    """一次完整拆解的总产物包（序列化为 JSONL 真源 + Markdown 渲染源）。"""

    book_title: str = Field(min_length=1)
    thin_summary: str = Field(min_length=1)
    entries: list[ChapterEntry] = Field(min_length=1)
    summaries: list[ChapterSummary] = Field(default_factory=list)
    golden_reports: list[GoldenChapterReport] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
