"""领域模型纪律测试：受控词表、证据定位、情节点上限。"""

import pytest
from pydantic import ValidationError

from ndecon.domain.enums import ThemeTag, Tone
from ndecon.domain.models import PlotPoint, SourceRef


def make_source(quote: str = "短刀", start: int = 0) -> SourceRef:
    """构造测试用证据引用。"""
    return SourceRef(
        chapter_id="CH0001",
        start=start,
        end=start + len(quote),
        quote=quote,
        content_hash="h" * 8,
    )


def test_source_ref_locates_in_text() -> None:
    """quote 必须按偏移在章节原文中定位，错位即判定证据无效。"""
    chapter = "陈默在旧箱子里翻出一把短刀。"
    ref = make_source(quote="短刀", start=chapter.index("短刀"))
    assert ref.is_located_in(chapter)
    bad = make_source(quote="短刀", start=0)
    assert not bad.is_located_in(chapter)


def test_source_ref_rejects_bad_span() -> None:
    """start >= end 的区间在构造时即被拒绝。"""
    with pytest.raises(ValidationError):
        SourceRef(chapter_id="CH0001", start=3, end=3, quote="x", content_hash="h")


def test_plot_point_rejects_unknown_enum() -> None:
    """基调与主题标签只能取受控枚举值。"""
    with pytest.raises(ValidationError):
        PlotPoint(
            index=1,
            summary="测试情节点。",
            tone="狂喜",  # 非法基调
            theme_tags=[ThemeTag.OTHER],
            source=make_source(),
        )
    with pytest.raises(ValidationError):
        PlotPoint(
            index=1,
            summary="测试情节点。",
            tone=Tone.OTHER,
            theme_tags=["创业"],  # 非法主题标签
            source=make_source(),
        )


def test_plot_point_requires_theme_tag() -> None:
    """主题标签至少一个，防止聚合阶段出现无标签情节点。"""
    with pytest.raises(ValidationError):
        PlotPoint(
            index=1,
            summary="测试情节点。",
            tone=Tone.OTHER,
            theme_tags=[],
            source=make_source(),
        )
