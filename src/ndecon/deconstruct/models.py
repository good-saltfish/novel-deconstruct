"""结构化拆书产物模型（bd-v1）。

站在"作者要写自己的书"这一侧：把他人成书拆成**可迁移的零部件**。

三条硬纪律（由本模块的结构而非约定保证）：
1. **产物不复制原文**：全模型没有任何字段存放第三人称摘录或原文段落。
   需要回看原文的地方只存坐标（见 `ConflictAnchor`），渲染时由本机 A 面再次读取用户自己的文件；
2. **测量与语义分层**：`BookStructure`/`ChapterMetrics` 是可纯规则算出的客观测量，
   `PanoramaDeconstruction`/冲突三角的角色位是语义层，规则层一律置占位文本并如实告警，
   绝不冒充"看懂了"；
3. **可重放**：每份产物带 model_id / prompt_version / schema_version 与章节 content_hash。

最省事的做法是把摘出来的原文直接塞进 KB —— 我们不那么做：
KB 版本（见 renderers）只落手法与统计，不落他人情节与成句。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = "bd-v1"
# 语义层里允许出现的连续原文长度上限（字符）：调研实测合法短引 33-67 字，这里收紧到 30
MAX_QUOTE_CHARS = 30
# 文案类字段（编号为锚点的手法提示）的硬截断长度
_NOTE_LIMIT = 32
# 规则层占位文本：渲染器与测试据此识别"这一项没有被真正分析"
PLACEHOLDER = "（待确认）"


def _clip(value: str, limit: int) -> str:
    """把字符串硬裁到 limit 字。**是兜底而非默许**：超长内容应在上游就 ASan 掉。"""
    return (value or "").strip()[:limit]


class TriangleItem(BaseModel):
    """一个卡普曼冲突三角（逐章拆解的底层零件）。"""

    kind: str = Field(
        min_length=1,
        description="三角类型：完整/不完整/潜在/抽象/复合/反转/悬念，规则层写'候选'",
    )
    name: str = Field(default="", description="该三角的一句话命名，如'规则猎杀'")
    persecutor: str = Field(min_length=1, description="迫害者；规则层写占位文本")
    victim: str = Field(min_length=1, description="受害者；规则层写占位文本")
    savior: str = Field(default="", description="拯救者；缺席时写'（缺席——原因）'")
    effect: str = Field(default="", description="这组配置产生的阅读效果（短点评）")


class ConflictAnchor(BaseModel):
    """一处"疑似冲突点"的坐标：指向用户本机原文的一个字符区间。

    **不存原文，只存坐标 + 触发规则名**，因此：
    - KB 版本天然无版权内容（连引用的机会都没有）；
    - 作者复核时由渲染层拿这本机的文件按偏移取回上下文；
    - 一旦 `content_hash` 与再次读取到的章节不符，说明源文件被改动过，坐标当场作废。
    """

    source_id: str = Field(min_length=1, description="稳定前缀，形如 bk-<hash>:CH0007")
    start: int = Field(ge=0, description="章内起始字符偏移（含）")
    end: int = Field(gt=0, description="章内结束字符偏移（不含）")
    trigger: str = Field(min_length=1, description="触发的表面规则名，如'施压动词'")
    content_hash: str = Field(min_length=1, description="所属章节内容指纹，用于检测漂移")
    note: str = Field(default="", description="面向创作者的手法提示，≤32 字")

    @field_validator("note")
    @classmethod
    def _short(cls, value: str) -> str:
        return _clip(value, _NOTE_LIMIT)

    @model_validator(mode="after")
    def _check_span(self) -> ConflictAnchor:
        if self.end <= self.start:
            raise ValueError(f"锚点坐标不合法：start={self.start} 必须小于 end={self.end}")
        return self


class ChapterMetrics(BaseModel):
    """单章客观测量（纯规则、可重算、不需要模型也不依赖语义理解）。"""

    chapter_id: str = Field(min_length=1)
    order: int = Field(ge=1)
    char_count: int = Field(ge=0, description="去空白字数")
    sentence_count: int = Field(ge=0)
    avg_sentence_chars: float = Field(ge=0.0, description="平均句长（去空白字数/句数）")
    dialogue_sentence_ratio: float = Field(ge=0.0, le=1.0, description="含引号的句子占比")
    paragraph_count: int = Field(ge=0)
    exclaim_ratio: float = Field(ge=0.0, le=1.0, description="含感叹号的句子占比")
    question_ratio: float = Field(ge=0.0, le=1.0, description="含问号的句子占比")
    no_punct_sentence_ratio: float = Field(
        ge=0.0, le=1.0,
        description="切句后不以句末标点收尾的比例；偏高通常意味着切分噪声或大量断句残片",
    )
    opening_hook_signal: bool = Field(default=False, description="开篇 200 字内出现悬念信号")
    content_hash: str = Field(min_length=1)


class ChapterDeconstruction(BaseModel):
    """单章拆解：客观测量 + 语义层（核心事件/三角/钩子/点评）。"""

    order: int = Field(ge=1)
    title: str = Field(min_length=1, description="章标题（沿用原标题）")
    core_event: str = Field(
        default="",
        description="本章发生了什么（第三人称压缩转述，不摘录）；语义层未跑时留空",
    )
    # 规则层跑不出来三角——那时就应当是空列表，而不是编一个出来充数
    triangles: list[TriangleItem] = Field(
        default_factory=list, max_length=7, description="本章冲突三角；模板口径每章 3-7 个"
    )
    end_hook: str = Field(default="", description="章末钩子（未收束的线）")
    highlights: list[str] = Field(default_factory=list, description="点评/亮点，逐条短句")
    emotion_track: str = Field(default="", description="本章情绪轨迹，如 紧→紧→压→爽")
    metrics: ChapterMetrics | None = None
    anchors: list[ConflictAnchor] = Field(
        default_factory=list, description="疑似冲突点坐标；**不含原文**"
    )
    warnings: list[str] = Field(default_factory=list, description="该章的局部告警")


class PlotNode(BaseModel):
    """剧情节点表中的一行（按节点而非按章聚合）。"""

    index: int = Field(ge=1)
    node: str = Field(min_length=1, description="节点名")
    chapters: str = Field(default="", description="覆盖章号，如 '1-5' 或 '7'")
    core_event: str = Field(min_length=1)
    emotion_peak: str = Field(default="", description="该节点的情绪峰值")


class KeySetting(BaseModel):
    """关键设定三件套：世界观 / 金手指 / 力量体系。"""

    worldview: str = Field(default="", description="世界的运行规则与特殊处")
    golden_finger: str = Field(default="", description="金手指形态/收益/**限制代价**/成长性")
    power_system: str = Field(default="", description="力量体系与等级阶梯")


class PanoramaDeconstruction(BaseModel):
    """749 式全景拆书：全书骨架（语义层，规则层不产出）。"""

    genre: str = Field(default="", description="题材/类型")
    tags: list[str] = Field(default_factory=list, description="内容标签")
    mainline_open: str = Field(default="", description="明线：读者视角可见的主目标")
    mainline_hidden: str = Field(default="", description="暗线：作者埋的世界真相/长远局")
    key_setting: KeySetting = Field(default_factory=KeySetting)
    main_characters: list[str] = Field(default_factory=list, description="主要角色：名——功能一句话")
    plot_nodes: list[PlotNode] = Field(default_factory=list, description="剧情节点表")
    core_conflict: str = Field(default="", description="主冲突及升级逻辑")
    driving_force: str = Field(default="", description="剧情驱动力：是什么让读者往下翻")
    one_line_outline: dict[str, str] = Field(
        default_factory=dict, description="章号字符串 -> 一句话细纲"
    )
    highlights: list[str] = Field(default_factory=list, description="亮点提炼")
    reusable_techniques: list[str] = Field(
        default_factory=list, description="可复用写作技巧（手法，不是情节）"
    )


class BookStructure(BaseModel):
    """全书测量层：**零语义**，规则就能算出来，也是最该沉淀进知识库的部分。"""

    total_chars: int = Field(ge=0, description="去空白总字数")
    avg_chapter_chars: float = Field(ge=0.0)
    avg_sentence_chars: float = Field(ge=0.0)
    avg_dialogue_ratio: float = Field(ge=0.0, le=1.0)
    per_thousand_conflicts: float = Field(ge=0.0, description="每千字疑似冲突点密度")
    conflict_density: dict[str, float] = Field(
        default_factory=dict, description="章号字符串 -> 每千字冲突密度"
    )
    quiet_chapters: list[int] = Field(
        default_factory=list, description="冲突密度低于全书均值一半的章（节奏空窗候选）"
    )
    hook_openings: list[int] = Field(default_factory=list, description="开篇有悬念信号的章号")


class BookDeconstruction(BaseModel):
    """一次自动拆书的完整产物。"""

    book_title: str = Field(min_length=1)
    author: str = Field(default="", description="原书作者；未知留空")
    range_label: str = Field(default="全文", description="拆解范围，如 前40章")
    total_chars: int = Field(default=0, ge=0, description="输入正文去空白字数")
    source_path: str = Field(default="", description="源文在本机的路径（仅本地使用，便于回看原文）")
    structure: BookStructure | None = None
    panorama: PanoramaDeconstruction | None = None
    chapters: list[ChapterDeconstruction] = Field(default_factory=list)
    # 连续原文超标的章号：由语义层产出后的"摘录体检"判定，KB 版本会跳过这些章的叙述字段
    flagged_chapters: list[int] = Field(default_factory=list)
    model_id: str = Field(min_length=1, description="产出方；规则产物用 rule:<name>")
    prompt_version: str = Field(min_length=1)
    schema_version: str = Field(default=SCHEMA_VERSION, min_length=1)
    warnings: list[str] = Field(default_factory=list, description="非致命问题，供使用者知悉")
