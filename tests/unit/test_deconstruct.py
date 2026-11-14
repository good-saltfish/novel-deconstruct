"""结构化拆书包的单元测试：覆盖规则层、层间边界与两条版权纪律。

这里测的不是"算法好不好"，而是几条**一旦坏了就静默失真**的性质：
- 规则层永远不冒充语义（忘留占位 = 作者会以为是模型看出来的）；
- 给了几章就量几章（静默截断 = 节奏结论建立在残缺数据上）；
- KB 版本不含他人成句（这是 ADR-0005 版权闸门的最后一道）。
"""

import pytest

from ndecon.deconstruct import quotes as quote_audit
from ndecon.deconstruct.pipeline import (
    RULE_MODEL_ID,
    build_rule_deconstruction,
    build_structure,
    measure_chapter,
)
from ndecon.deconstruct.renderers import render_author_md, render_kb_md
from ndecon.deconstruct.signals import has_dialogue, split_sentences
from ndecon.domain.deconstruction import MAX_QUOTE_CHARS, PLACEHOLDER

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

# 开篇平淡、章尾挂问句：用来验证 closing_hook_signal 与 opening_hook_signal 分家。
# 正文必须明显长于两个 200 字窗口之和，否则章尾那句会同时落进开篇窗口。
_CLOSING_HOOK_TEXT = (
    "第1章 收尾\n"
    "他走了一整天，路上什么也没有发生，天黑了才回到住处，洗了脸就躺下。"
    "他翻来覆去想着白天那些细碎的对话，一句一句地在心里重放，直到眼皮发沉。"
    "窗外有人推着车过去，轮子碾过石板发出很轻的响声，很快就远了。"
    "他起身把窗户关好，回来又把被角掖了两遍，还是睡不着。"
    "隔壁的灯亮了一会儿，然后灭了。楼道里有脚步声上来，又下去了。"
    "他数着自己的呼吸，数到两百多的时候终于有点迷糊。"
    "天快亮的时候，楼下早点摊的蒸汽慢慢升起来，把整条街都泡在一层白气里。"
    "他下楼买了一碗豆浆，坐在塑料凳上慢慢地喝，看街上的人越来越多。"
    "摊主问他今天怎么起这么早，他说睡不着，摊主就笑了笑没再问。"
    "他喝完把碗放回去，起身往巷子深处走，脚步很慢，像是并不急着去哪里。"
    "巷子尽头那扇门关着，门缝底下透出一点光，一闪一闪的。"
    "他站在那里看了很久，直到光灭了，才抬手去推。"
    "可是门后那个人是谁？\n"
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
    from ndecon.domain.deconstruction import ChapterDeconstruction

    triangle = {"kind": "悬念", "name": "x", "persecutor": "甲", "victim": "乙"}
    ChapterDeconstruction(order=1, title="第1章", triangles=[dict(triangle) for _ in range(7)])
    with pytest.raises(ValueError):
        ChapterDeconstruction(order=1, title="第1章", triangles=[dict(triangle) for _ in range(8)])


# --------------------------------------------------------------------------
# 句长分布（L1）：平均句长回答不了"靠长句压气还是靠短句抽打"
# --------------------------------------------------------------------------


def test_sentence_length_distribution_separates_short_and_long():
    """短句连打与长句铺陈必须落在分布的两端，而不是同一个平均值上。"""
    choppy = "第1章 打点\n他跑。她追。狗叫。灯灭。门开。"
    flowing = (
        "第1章 铺陈\n他沿着那条被雨水泡得发亮的长街一直往前走，"
        "心里反复想着营业厅柜台上那张被翻过来的保单背面究竟印了谁的名字，"
        "直到路灯一盏一盏地在他身后熄灭。"
    )
    short_metrics, _, _ = measure_chapter(1, "打点", choppy)
    long_metrics, _, _ = measure_chapter(1, "铺陈", flowing)

    assert short_metrics.median_sentence_chars < long_metrics.median_sentence_chars
    assert short_metrics.short_sentence_ratio > long_metrics.short_sentence_ratio
    assert long_metrics.long_sentence_ratio > short_metrics.long_sentence_ratio
    # 中位数与最短/最长必须自洽，否则渲染出来的分布是假的
    assert short_metrics.min_sentence_chars <= short_metrics.median_sentence_chars
    assert short_metrics.median_sentence_chars <= short_metrics.max_sentence_chars
    assert short_metrics.min_sentence_chars > 0


def test_book_structure_aggregates_sentence_length_by_sentence_count():
    """全书句长分布要按句数计权：短章不能和主线章等权，否则中位数不是这本书的。"""
    from ndecon.domain.deconstruction import ChapterMetrics

    def _metrics(order: int, sentences: int, median: float, short: float, long: float):
        return ChapterMetrics(
            chapter_id=f"bk:CH{order:04d}",
            order=order,
            char_count=sentences * 30,
            sentence_count=sentences,
            avg_sentence_chars=30.0,
            dialogue_sentence_ratio=0.0,
            paragraph_count=1,
            exclaim_ratio=0.0,
            question_ratio=0.0,
            no_punct_sentence_ratio=0.0,
            content_hash="h",
            median_sentence_chars=median,
            short_sentence_ratio=short,
            long_sentence_ratio=long,
        )

    # 一个 2 句的过场章 + 一个 200 句的主线章：中位数必须落在主线章的 30.0
    per_chapter = [
        (_metrics(1, 2, 8.0, 1.0, 0.0), []),
        (_metrics(2, 200, 30.0, 0.1, 0.4), []),
    ]
    structure = build_structure(per_chapter)

    assert structure.median_sentence_chars == 30.0
    assert structure.short_sentence_ratio == pytest.approx((1.0 * 2 + 0.1 * 200) / 202)
    assert structure.long_sentence_ratio == pytest.approx((0.0 * 2 + 0.4 * 200) / 202)


def test_book_structure_exposes_closing_hook_chapters():
    """章尾有悬念信号的章号要单独列出来，不能和开篇信号合成一个字段。"""
    book = build_rule_deconstruction(_CLOSING_HOOK_TEXT, "章尾钩样本")
    structure = book.structure

    assert structure.closing_hook_openings, "章尾问句必须被量到"
    assert structure.hook_openings == [], "这个样本开篇没有悬念信号"


def test_structure_chapter_lists_have_no_duplicates():
    """源文重号时，章号列表不许把同一章写两遍。

    真实语料里重复章号很常见（实测《夜无疆》1269 个章节只有 850 个不同章号，
    切分器会告警但保留），不去重的话报告里"哪些章有悬念信号"会同一章号连写两遍。
    """
    repeated = "第1章 甲\n他跑。她追。\n第1章 甲\n他跑。她追。\n第1章 甲\n他跑。她追。\n"
    book = build_rule_deconstruction(repeated, "重号样本")
    structure = book.structure

    assert len(book.chapters) == 3, "切分器保留重号，测量层也照量"
    assert len(structure.quiet_chapters) == len(set(structure.quiet_chapters))
    assert len(structure.hook_openings) == len(set(structure.hook_openings))
    assert len(structure.closing_hook_openings) == len(set(structure.closing_hook_openings))


def test_metrics_keep_closing_hook_separate_from_opening():
    """章尾钩与开篇钩是两个判断：只看开篇会把"开局抓人但结尾平"的书夸成好节奏。"""
    opens_strong = "第1章 开局\n难道他真的已经死了？后面全是流水账，一句接一句，平铺直叙到收尾。"
    closes_strong = "第1章 收尾\n" + "他走了一天，什么也没有发生。" * 20 + "可是门后那个人是谁？"
    a, _, _ = measure_chapter(1, "开局", opens_strong)
    b, _, _ = measure_chapter(1, "收尾", closes_strong)

    assert a.opening_hook_signal is True
    assert b.opening_hook_signal is False
    assert b.closing_hook_signal is True


# --------------------------------------------------------------------------
# 钩子台账（L3）：拆来的书"悬念一般吊几章"
# --------------------------------------------------------------------------


def _book_with_hooks(hooks: dict[int, str], cores: dict[int, str]) -> object:
    """造一本规则层产物并手填语义层字段（钩子台账只读这两处）。"""
    from ndecon.domain.deconstruction import ChapterDeconstruction

    book = build_rule_deconstruction(SAMPLE, "样本")
    chapters = [
        ChapterDeconstruction(order=order, title=f"第{order}章")
        for order in sorted(set(hooks) | set(cores))
    ]
    for chapter in chapters:
        chapter.end_hook = hooks.get(chapter.order, "")
        chapter.core_event = cores.get(chapter.order, "")
    book.chapters = chapters
    return book


def test_hook_ledger_records_span_and_open_count():
    """埋钩 → 隔几章回收 → 中位数/最长/未回收条数都要能算出来。"""
    from ndecon.deconstruct.pipeline import build_hook_ledger

    book = _book_with_hooks(
        hooks={1: "登记簿上被划掉的名字", 3: "柜台上那张保单背面的受益人"},
        cores={
            2: "他回到营业厅核对登记簿上被划掉的名字",
            5: "柜台上那张保单背面的受益人原来是他自己",
        },
    )
    build_hook_ledger(book)
    structure = book.structure

    assert len(structure.hook_ledger) == 2
    first, second = structure.hook_ledger
    assert (first.opened_order, first.resolved_order, first.span) == (1, 2, 1)
    assert (second.opened_order, second.resolved_order, second.span) == (3, 5, 2)
    assert structure.hook_recovery_median == 1.5
    assert structure.hook_recovery_longest == 2
    assert structure.hook_open_count == 0


def test_hook_ledger_marks_unresolved_and_ignores_own_hook():
    """未回收要如实计数；且一条钩子不许被它自己那一章"回收"。"""
    from ndecon.deconstruct.pipeline import build_hook_ledger

    book = _book_with_hooks(
        hooks={1: "门后那个一直在等的人"},
        cores={1: "门后那个一直在等的人", 2: "无关的一章"},
    )
    build_hook_ledger(book)
    entry = book.structure.hook_ledger[0]

    assert entry.resolved_order is None
    assert entry.span is None
    assert entry.is_open is True
    assert book.structure.hook_open_count == 1
    assert book.structure.hook_recovery_median is None


def test_rule_layer_has_no_hook_ledger():
    """规则层量不出 end_hook，台账就必须是空的 —— 没跑模型就说"没钩子"是撒谎。"""
    book = build_rule_deconstruction(SAMPLE, "样本")
    assert book.structure.hook_ledger == []
    assert book.structure.hook_open_count == 0
    assert book.structure.hook_recovery_median is None


def test_kb_version_lists_hook_spans_without_hook_text():
    """KB 版可以给章号与间隔，不能给出钩子写了什么。"""
    from ndecon.deconstruct.pipeline import build_hook_ledger
    from ndecon.deconstruct.renderers import render_kb_md

    book = _book_with_hooks(
        hooks={1: "登记簿上被划掉的名字"}, cores={2: "他去核对登记簿上被划掉的名字"}
    )
    build_hook_ledger(book)
    kb = render_kb_md(book)

    assert "钩子的回收节奏" in kb
    assert "| 1 | 2 | 1 |" in kb
    assert "登记簿上被划掉的名字" not in kb
