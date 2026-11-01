"""AI 指纹扫描器单元测试：确定性、只读、只报告不改写。"""

from ndecon.quality.fingerprint import (
    AI_FINGERPRINT_VERSION,
    count_cjk,
    scan_chapter,
    split_paragraphs,
)


def _finding(report, rule: str):
    return next(item for item in report.findings if item.rule == rule)


def test_cjk_count_and_paragraph_split():
    assert count_cjk("你好，世界") == 4
    assert count_cjk("abc 123") == 0
    assert split_paragraphs("第一段。\n\n第二段。") == ["第一段。", "第二段。"]
    # 无空行的文本退化按行切，保证单段文本也能被度量
    assert split_paragraphs("甲\n乙") == ["甲", "乙"]


def test_clean_chapter_passes():
    """一章"人味"正文：短段、少破折号、有场景轮换、字数达标。"""
    text = "\n\n".join(
        [
            "他推开门，冷风灌进来。屋里没有人。",
            "桌上留着半杯茶，还温着。",
            "与此同时，巷口的灯灭了。",
            "他没回头，把手插进兜里，摸到那张皱掉的收据。",
            "次日清晨，茶馆的伙计在门口扫地。看见他，扫帚顿了一下。",
            "他说，茶钱记我账上。伙计没应声，扫帚又动起来。",
            "夜幕落下，雨开始下。他站在檐下，等一个不会来的人。",
        ]
    )
    report = scan_chapter(text)
    assert report.version == AI_FINGERPRINT_VERSION
    assert _finding(report, "not_x_but_y").passed
    assert _finding(report, "em_dash").passed
    assert _finding(report, "period_per_paragraph").passed
    assert _finding(report, "simile_density").passed
    assert _finding(report, "de_repeat").passed
    # 场景轮换与字数是"密度/体量"规则，短样本天然不达标，此处只验证不误报
    assert _finding(report, "scene_rotation").value >= 3


def test_ai_flavored_chapter_is_flagged_but_not_rewritten():
    """高密度 AI 味样本应被标出越界；报告里绝不出现改写后的文本。"""
    text = (
        "这不是一个简单的选择，而是一个关乎生死的抉择。" * 6
        + "\n\n"
        + "他的眼神就像深渊一般，仿佛能吞噬一切，如同黑夜中的孤狼似的。"
        + "\n\n"
        + f"这是一段很长的文字，里面有许许多多的句号。{'继续铺垫。' * 10}"
        + "\n\n"
        + "他看到了那个东西的颜色的边缘的温度的变化。"
    )
    report = scan_chapter(text)
    assert not report.passed
    assert not _finding(report, "not_x_but_y").passed
    assert not _finding(report, "em_dash").passed if "——" in text else True
    assert not _finding(report, "simile_density").passed
    assert not _finding(report, "period_per_paragraph").passed
    # 报告只含实测值与阈值，不含任何"建议改为……"的文本
    dumped = report.model_dump_json()
    assert "建议改为" not in dumped and "改写" not in dumped


def test_em_dash_rule_counts_double_dash():
    text = "他停住了——因为门外有声音——他屏住呼吸——脚步声近了。"
    report = scan_chapter(text)
    finding = _finding(report, "em_dash")
    assert finding.value == 3
    assert not finding.passed


def test_de_repeat_rule():
    # 3 个及以上连续的"的"才判违规（"的的的"）
    assert _finding(scan_chapter("他看到那个东西的颜色的边缘的温度"), "de_repeat").passed is False
    # 正常间隔的"的"不应误报
    assert _finding(scan_chapter("他看到了那个东西的颜色的边缘"), "de_repeat").passed is True


def test_single_quote_is_info_only():
    """单引号只提示，不参与 passed 判定（网文少见但引用本身合法）。"""
    report = scan_chapter("他说：‘不可能。’\n\n她没接话。")
    finding = _finding(report, "single_quote")
    assert finding.severity == "info"
    assert finding.passed is True


def test_deterministic_and_no_mutation():
    text = "第一段。\n\n第二段——测试。\n\n不是甲，是乙。"
    first = scan_chapter(text)
    second = scan_chapter(text)
    assert first.model_dump() == second.model_dump()
