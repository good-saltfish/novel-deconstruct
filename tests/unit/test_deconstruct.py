"""结构化拆书包的单元测试：覆盖规则层、层间边界与两条版权纪律。

这里测的不是"算法好不好"，而是几条**一旦坏了就静默失真**的性质：
- 规则层永远不冒充语义（忘留占位 = 作者会以为是模型看出来的）；
- 给了几章就量几章（静默截断 = 节奏结论建立在残缺数据上）；
- KB 版本不含他人成句（这是 ADR-0005 版权闸门的最后一道）。
"""

import pytest

from ndecon.deconstruct import quotes as quote_audit
from ndecon.deconstruct.models import MAX_QUOTE_CHARS, PLACEHOLDER
from ndecon.deconstruct.pipeline import (
    RULE_MODEL_ID,
    build_rule_deconstruction,
    measure_chapter,
)
from ndecon.deconstruct.renderers import render_author_md, render_kb_md
from ndecon.deconstruct.signals import has_dialogue, split_sentences

SAMPLE = (
    "第1章 深夜来电\n"
    "半夜来的单子，他盯着屏幕看了很久。"
    "客户名单里有他自己。\n"
    "“这不可能。”他低声说。\n"
    "电话那头没说话，只有风声。\n"
    "\n"
    "第2章 保单背面\n"
    "第二天天没亮他就出门了。路上没有人。"
    "他忽然意识到，保单背面印着的名字不是客户，是受益人。"
    "他一口气跑到营业厅，柜台后面的姑娘抬头看了他一眼。\n"
    "“您要退保？”\n"
    "他愣住。难道这件事从头到尾都是冲着他来的？\n"
)


# --------------------------------------------------------------------------
# 切句器：引号感知（这段如果退化，对话占比会静默归零）
# --------------------------------------------------------------------------


def test_split_sentences_keeps_quoted_sentence_intact():
    """句内含成对引号时不得在引号内的句号处切断。"""
    body = "“这不可能。”他低声说。然后他挂了电话。"
    spans = split_sentences(body)
    texts = [text for _, _, text in spans]
    # 早期版本在这里切成 '“这不可能。' + '”他低声说。'，两段都不含成对引号
    assert any(dialogue_sentence.startswith("“这不可能。”") for dialogue_sentence in texts)
    assert any(has_dialogue(text) for text in texts)


def test_split_sentences_offsets_point_back_into_source():
    """偏移必须能原样切回原文，否则anchor坐标就是伪造的。"""
    body = "甲说了一句话。乙接着说。丙没说话。"
    for start, end, text in split_sentences(body):
        assert body[start:end].strip() == text


def test_has_dialogue_needs_a_pair():
    assert has_dialogue("“出来。”")
    assert not has_dialogue("他站在门口。")


# --------------------------------------------------------------------------
# 规则层：不冒充语义
# --------------------------------------------------------------------------


def test_rule_layer_leaves_semantics_blank():
    """规则层不得产出任何'看懂了'的内容 —— 宁可空着，也不编。"""
    book = build_rule_deconstruction(SAMPLE, "样本")
    assert book.model_id == RULE_MODEL_ID
    assert len(book.chapters) == 2
    for chapter in book.chapters:
        assert chapter.triangles == []
        assert chapter.core_event == ""
    joined = " ".join(book.warnings)
    assert PLACEHOLDER in joined
    assert "语义层未产出" in joined


def test_measure_chapter_reports_real_dialogue_ratio():
    """样本里明确有对话，占比不该是 0 —— 恒为 0 说明切分器坏了而不是数据没有。"""
    _metrics, _anchors, _warnings = measure_chapter(1, "深夜来电", SAMPLE)
    assert _metrics.sentence_count > 0
    assert _metrics.dialogue_sentence_ratio > 0
    assert 0.0 <= _metrics.dialogue_sentence_ratio <= 1.0


def test_anchors_carry_trigger_and_no_source_text():
    """冲突点存的是坐标 + 规则名，**任何原文都不进产物**。"""
    _metrics, anchors, _warnings = measure_chapter(1, "深夜来电", SAMPLE)
    assert anchors, "样本里有冲突信号，应当至少产出一个锚点"
    for raw in anchors:
        assert raw["end"] > raw["start"]
        assert raw["trigger"]
        # 唯一的文本字段是手法提示，且不含原句
        assert "半夜来的单子" not in raw["note"]


def test_measure_chapter_is_deterministic():
    """同输入必须两次完全一样 —— 产物要能被别人重放核对。"""
    first = measure_chapter(1, "深夜来电", SAMPLE)
    second = measure_chapter(1, "深夜来电", SAMPLE)
    assert first[0].model_dump() == second[0].model_dump()
    assert first[1] == second[1]


def test_split_sentences_handles_unclosed_quote_without_hanging():
    """引号未闭合时不得吞掉后文 —— 整段算一句（宁可长，不可切错）。

    `has_dialogue` 在这种残缺句上是 False 属于预期：没有成对引号就没有口语信号，
    这比"为了凑指标把断句也算成对话"诚实。
    """
    body = "“没有闭合的对话。后面还有内容。"
    texts = [text for _, _, text in split_sentences(body)]
    assert texts == [body]
    assert not any(has_dialogue(text) for text in texts)

    paired = "“甲说。”乙说。丙沉默。"
    paired_texts = [text for _, _, text in split_sentences(paired)]
    assert paired_texts[0].startswith("“甲说。”")
    assert any(has_dialogue(text) for text in paired_texts)


def test_rule_layer_measures_every_chapter_given():
    """`max_chapters` 只改范围标签，不得静默少量几章。"""
    full = build_rule_deconstruction(SAMPLE, "样本")
    partial = build_rule_deconstruction(SAMPLE, "样本", max_chapters=1)
    assert len(full.chapters) == 2
    assert len(partial.chapters) == 1
    assert partial.range_label == "前1章"
    # 规则层该量几章就量几章，但我们确实只喂了一章时的总量要对得上这 1 章
    assert partial.structure.total_chars < full.structure.total_chars


# --------------------------------------------------------------------------
# 渲染：作者版与 KB 版的分工（这是版权纪律的落地点）
# --------------------------------------------------------------------------


def test_kb_version_never_contains_source_sentences():
    """KB 版不得含他人成句 —— 它会被注入下一次创作 prompt。"""
    book = build_rule_deconstruction(SAMPLE, "样本")
    kb = render_kb_md(book)
    for sentence in ("半夜来的单子，他盯着屏幕看了很久。", "客户名单里有他自己。"):
        assert sentence not in kb
    # 书名词以外的标题也不该出现在 KB 版
    assert "深夜来电" not in kb


def test_author_version_keeps_reviewable_offsets():
    """作者版必须给出可复核坐标，且坐标能对回原文。"""
    book = build_rule_deconstruction(SAMPLE, "样本")
    author = render_author_md(book)
    assert "偏移" in author
    for chapter in book.chapters:
        for anchor in chapter.anchors:
            assert isinstance(anchor.start, int) and anchor.end > anchor.start


# --------------------------------------------------------------------------
# 引文体检：语义层吐回的原文必须能被定位
# --------------------------------------------------------------------------


def test_audit_drops_quote_that_cannot_be_located():
    """定位不到的引文必须丢弃 —— 留着就是一条无法核对的假证据。"""
    cleaned, anchors, warnings = quote_audit.audit_narrative(
        "他说了一句“这句话根本不在原文里”。",
        chapter_text="原文是别的完全不同的内容。",
        source_id="bk:CH0001",
        content_hash="h",
    )
    assert anchors == []
    assert warnings and "无法在本章原文中唯一定位" in warnings[0]
    assert PLACEHOLDER in cleaned


def test_audit_removes_overlong_quote_as_excerpt():
    """超过上限的整段回吐不是证据，是摘录 —— 直接移除。"""
    long_quote = "甲" * (MAX_QUOTE_CHARS + 5)
    cleaned, anchors, warnings = quote_audit.audit_narrative(
        f"这一处写着“{long_quote}”。",
        chapter_text="原文内容",
        source_id="bk:CH0001",
        content_hash="h",
    )
    assert anchors == []
    assert long_quote not in cleaned
    assert any("超过" in warning for warning in warnings)


def test_audit_pins_locatable_short_quote():
    """能唯一定位的短引保留，并落成可复核坐标。"""
    chapter_text = "他抬起头，看见一行字：限时三日内归还。然后他就跑了。"
    quote = "限时三日内归还"
    cleaned, anchors, warnings = quote_audit.audit_narrative(
        f"关键约束是“{quote}”。",
        chapter_text=chapter_text,
        source_id="bk:CH0001",
        content_hash="h",
    )
    assert len(anchors) == 1
    assert chapter_text[anchors[0].start : anchors[0].end] == quote
    assert quote in cleaned
    assert warnings == []


def test_triangles_per_chapter_is_bounded():
    """一章最多 7 个三角：模型偶尔吐十几个碎三角，那种产物不可用。"""
    from ndecon.deconstruct.models import ChapterDeconstruction

    triangle = {"kind": "悬念", "name": "x", "persecutor": "甲", "victim": "乙"}
    ChapterDeconstruction(order=1, title="第1章", triangles=[dict(triangle) for _ in range(7)])
    with pytest.raises(ValueError):
        ChapterDeconstruction(order=1, title="第1章", triangles=[dict(triangle) for _ in range(8)])
