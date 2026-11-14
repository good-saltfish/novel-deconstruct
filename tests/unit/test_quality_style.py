"""笔法体检（style）单测：口径、判据、门槛与「只提示不硬拦」的纪律。

这些测试守的不是"数字对不对"，而是三件容易在后续改动中被悄悄破坏的事：

1. **口径与建基线脚本一致**。阈值是从 67 本 5,829 章里按同一套正则量出来的；
   只要 `paragraphs_of` / `measure` 的口径变了，阈值立刻失效，而界面上看不出任何异常。
2. **判据方向不能反**。`单句段占比` 是「低于 p5 才越界」，`段均句数` 是「高于 p95」——
   反了之后人类小说会成片报警，AI 章反而不报。
3. **它永远只是 advisory**。这里的判据在留出集上单条误杀 2.2%–5.1%，
   够「建议改」，不够「判定不能用」。谁把它接成硬门，人类真书就会被拦在门外。
"""

from __future__ import annotations

import pytest

from ndecon.quality.style import (
    MIN_STYLE_CHARS,
    baseline_meta,
    cjk_count,
    dialogue_rule_limits,
    inspect_style,
    measure,
    paragraphs_of,
    rule_limits,
)


def _humanish(chars: int = 1200) -> str:
    """造一段节奏接近人类网文的正文：短句成段为主，但长短交替、句内逗号够长、无破折号。

    每句都必须以句末标点收尾、每行必须独立成段——这两条不是排版讲究，
    而是 ``paragraphs_of`` 的口径：没有句末标点的行会被并进下一行。

    为什么不能全篇一行一句：那样段p90字只有 25，落在人类 p10 线（35）以下。
    人类作者即使 96% 的段落只有一句，段长 90 分位的中位仍有 45 字、最长段
    能到 120 字（32575 章实测）。「全部压成一句一段」是模型为了满足分段要求
    走的退化解，不是人类形态，拿它当人类范本等于用假阴性养着假指标。
    """
    sentences = [
        "他把那份合同翻到第二页看了一遍，月薪四千，试用期三个月",
        "王振没有抬头，手指在桌面上敲了两下又停住，像是在算什么",
        "窗外有人在喊口号，声音隔着玻璃传进来，变得又闷又远",
        "陈默把笔放回笔筒里，问了一句，这个事情现在还能改吗",
        "苏晚推门进来，手里抱着一叠打印纸，边走边数还剩下多少",
        "走廊尽头的灯忽明忽暗，把两个人的影子拉得很长很长",
    ]
    # 每 8 段一个循环：六个单句段、一个两句段、一个四句段。长段占 12.5%，
    # 刚好压过 p90 那个位置——剩下的仍是最常见的「一句话一段」。
    pattern = (1, 1, 1, 2, 1, 1, 1, 4)
    out: list[str] = []
    index = 0
    while cjk_count("\n".join(out)) < chars:
        size = pattern[len(out) % len(pattern)]
        chunk = [sentences[(index + i) % len(sentences)] for i in range(size)]
        index += size
        out.append("".join(s + "。" for s in chunk))
    return "\n".join(out)


def _aiish(chars: int = 1200) -> str:
    """造一段节奏像 AI 的正文：多句挤一段、逗号断碎、破折号与身体反应密集。"""
    block = (
        "他低头。看着合同。月薪四千——试用期三个月——违约金三十万。"
        "胸口发紧，喉咙发干，指尖发凉。他站起来，走到窗边，看着楼下的车流，"
        "心里忽然明白了什么——这座城市从来不缺努力的人。"
    )
    out: list[str] = []
    while cjk_count("\n".join(out)) < chars:
        out.append(block)
    return "\n".join(out)


def test_paragraphs_split_on_single_newline_not_blank_line() -> None:
    """单换行就是分段：大量网文 txt 是这样存的，按空行切会把整章切成一坨。"""
    text = "他推开门。\n屋里没人。\n桌上有盏灯。"
    assert paragraphs_of(text) == ["他推开门。", "屋里没人。", "桌上有盏灯。"]

    # 空行与分隔线不贡献段落
    assert paragraphs_of("他推开门。\n\n=====\n屋里没人。") == ["他推开门。", "屋里没人。"]


def test_unterminated_line_is_merged_with_next() -> None:
    """被硬折行的长句必须并回一段，否则段均句数会被折行数虚高。"""
    text = "他推开门，看见屋里\n空无一人，只有一盏灯还亮着。"
    assert paragraphs_of(text) == ["他推开门，看见屋里空无一人，只有一盏灯还亮着。"]


def test_measure_returns_empty_for_too_short_text() -> None:
    """短文本量不出稳定分布：一段 200 字对话谈不上一章节奏。"""
    assert measure("他推开门。" * 5) == {}
    long_enough = _humanish(MIN_STYLE_CHARS + 100)
    assert measure(long_enough)


def test_metrics_are_on_the_baseline_scale() -> None:
    """量出来的值必须落在基线的同一坐标系里，否则阈值全错位。"""
    metrics = measure(_humanish(1200))
    # 分句均字：人类中位 8.8，造出来的句子逗号前都有 8 字以上
    assert metrics["分句均字"] >= 7.5
    # 短句成段为主，但留了长段：单句段占比高、又不至于 1.0（人类 p50=0.91）
    assert 0.6 <= metrics["单句段占比"] <= 0.95
    # 段均句数随之略高于 1，仍在人类分布内（人类 p95=1.62）
    assert 1.0 <= metrics["段均句数"] <= 1.62
    # 段长 90 分位要越过人类 p10 线（35），否则就是「长段被删光」的退化解
    assert metrics["段p90字"] >= 35
    # 没有破折号
    assert metrics["破折号密度"] == 0.0


def test_finding_direction_is_not_reversed() -> None:
    """方向反了会让人类书成片报警、AI 章反而不报——这条专门守它。"""
    report = inspect_style(_aiish(1200))
    by_metric = {f.metric: f for f in report.findings}

    # 「段均句数」高才越界
    assert "段均句数" in by_metric
    assert by_metric["段均句数"].direction == "hi"
    # 「单句段占比」低才越界
    assert "单句段占比" in by_metric
    assert by_metric["单句段占比"].direction == "lo"
    # 破折号密度高才越界
    assert "破折号密度" in by_metric
    assert by_metric["破折号密度"].direction == "hi"


def test_human_like_text_passes_while_ai_like_text_trips() -> None:
    """判据必须能分开这两类文本，否则整套路是摆设。"""
    human = inspect_style(_humanish(1200))
    machine = inspect_style(_aiish(1200))
    assert human.passed, [f.as_detail() for f in human.findings]
    assert len(machine.findings) > 1
    assert machine.score > human.score


def test_report_is_advisory_not_a_gate() -> None:
    """越界只产出文案，不带任何「拒绝」语义：调用方据此重写，不据此丢章。"""
    report = inspect_style(_aiish(1200))
    assert report.passed is False
    assert report.feedback() == [f.as_detail() for f in report.findings]
    for line in report.feedback():
        # 每条都必须带实测值与人类分位，否则模型不知道往哪改
        assert "人类" in line
        assert any(ch.isdigit() for ch in line)


def test_evidence_points_at_the_offending_text() -> None:
    """证据要指向原文，用户才能一眼看到问题在哪。"""
    report = inspect_style(_aiish(1200))
    by_metric = {f.metric: f for f in report.findings}
    assert "——" in by_metric["破折号密度"].evidence
    assert by_metric["单句段占比"].evidence  # 这一项没有具体句子，给的是说明文字


def test_rule_limits_has_two_scales() -> None:
    """prompt 瞄准人类典型值，判罚留在外侧分位——两套数字同源，不能混。"""
    judge = rule_limits("judge")
    prompt = rule_limits("prompt")
    assert judge.keys() == prompt.keys()
    # 越高越差的两条：prompt 目标必须比判罚线更严（更小）
    for metric in ("破折号密度", "身体外化密度", "段均句数"):
        assert prompt[metric] < judge[metric], metric
    # 越低越差的两条：prompt 目标必须比判罚线更高
    for metric in ("分句均字", "单句段占比"):
        assert prompt[metric] > judge[metric], metric


def test_baseline_meta_declares_holdout() -> None:
    """基线必须自报家门：多少本书、多少章、留出集多大。缺了它无法判断可信度。"""
    meta = baseline_meta()
    assert meta["version"] == "style-v1"
    assert meta["books"] >= 50
    assert meta["chapters"] >= 4000
    # 留出集必须存在且不参与定阈值，否则误杀率是自己评自己
    assert meta["holdout_books"] >= 10
    assert meta["holdout_chapters"] >= 1000
    assert len(meta["rules"]) == 6


def test_prompt_rules_match_baseline_numbers() -> None:
    """prompt 里的数字必须来自基线，不能是手写的「大概这样」。"""
    prompt = rule_limits("prompt")
    assert pytest.approx(prompt["破折号密度"], abs=1e-6) == 0.6223
    assert pytest.approx(prompt["段均句数"], abs=1e-6) == 1.2131


def _dialogueish(chars: int = 1200) -> str:
    """造一段「AI 味台词」：条条完整、句句在解释、满嘴「你……」、破折号焊解释。"""
    block = (
        "「你听我说，这地方不能待了。」\n"
        "「为什么不能待？」\n"
        "「因为你已经被人盯上了——从你进门的第一分钟起。」\n"
        "「那你又是谁？」\n"
        "「我是来救你的人。」\n"
        "「你凭什么救我？」\n"
        "「因为你不该死在这里。」\n"
    )
    out: list[str] = []
    while cjk_count("\n".join(out)) < chars:
        out.append(block)
    return "\n".join(out)


def test_dialogue_metrics_are_skipped_when_chapter_has_no_quotes() -> None:
    """没写对白的章不进对白分布，也**不能**因此算作「对白判据全过」。

    这条守的是最容易被"优化"掉的语义：如果 ``measure_dialogue`` 对无对白章返回
    全零指标，那些指标全都会落在人类分布内，于是「一句对话都不写」变成满分策略。
    """
    report = inspect_style(_humanish(1200))
    assert report.dialogue_checked is False
    assert report.dialogue_metrics == {}
    assert report.rule_count == 6  # 只有节奏六条参与比对


def test_dialogue_metrics_join_the_comparison_when_quotes_exist() -> None:
    """有对白时判据数从 6 变 10，且对白指标落进 metrics。"""
    report = inspect_style(_dialogueish(1200))
    assert report.dialogue_checked is True
    assert report.rule_count == 10
    for metric in ("对白条数每千字", "对白破折号占比", "对白第二人称密度"):
        assert metric in report.metrics
        assert metric in report.dialogue_metrics


def test_dialogue_findings_point_at_the_offending_line() -> None:
    """对白越界也要给原文证据，否则作者不知道改哪句。"""
    report = inspect_style(_dialogueish(1200))
    by_metric = {f.metric: f for f in report.findings}
    assert "对白第二人称密度" in by_metric
    assert "你" in by_metric["对白第二人称密度"].evidence
    assert by_metric["对白第二人称密度"].direction == "hi"


def test_dialogue_rule_limits_have_two_scales() -> None:
    """对白判据同样是「瞄准紧、判罚松」，但方向不全是「越高越差」。

    ``对白句均字`` 是 lo 向（管「台词被切碎」）：人类典型值 17.7 字，
    判罚线在 p1=6.5。所以「prompt 一定小于 limit」只对 hi 向成立，
    这里按方向分别验，避免把方向写死。
    """
    judge = dialogue_rule_limits("judge")
    prompt = dialogue_rule_limits("prompt")
    assert judge.keys() == prompt.keys()
    assert set(judge) == {
        "对白破折号占比",
        "对白条数每千字",
        "对白第二人称密度",
        "对白句均字",
    }
    for metric, limit in judge.items():
        if metric == "对白句均字":
            assert prompt[metric] > limit, metric
        else:
            assert prompt[metric] < limit, metric


def test_dialogue_baseline_is_declared_separately() -> None:
    """对白基线要单独报家门：它的语料口径与节奏不同（只统计写了对白的章）。"""
    meta = baseline_meta()
    dialogue = meta["dialogue"]
    assert dialogue is not None
    assert dialogue["version"] == "style-dlg-v1"
    assert dialogue["books"] >= 50
    assert dialogue["holdout_chapters"] >= 1000
    assert len(dialogue["rules"]) == 4
    # 主表是六条节奏判据——混表会让「本基线来自 67 本 5829 章」变成假话
    assert len(meta["rules"]) == 6
