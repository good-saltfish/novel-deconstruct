"""章节切分器测试：中文数字、噪声行、二合一大章、误切防护、偏移正确。"""

from pathlib import Path

import pytest

from ndecon.ingest.splitter import cn_chapter_to_int, split_chapters

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "synthetic_novel.txt"


def test_cn_chapter_to_int_basic_and_large() -> None:
    """中文数字转换覆盖个位数、十百千位、'两'与'万'位权。"""
    assert cn_chapter_to_int("11") == 11
    assert cn_chapter_to_int("两") == 2
    assert cn_chapter_to_int("十一") == 11
    assert cn_chapter_to_int("二十") == 20
    assert cn_chapter_to_int("两百") == 200
    assert cn_chapter_to_int("两千零三") == 2003
    assert cn_chapter_to_int("一万三千五百") == 13500


def test_split_fixture_boundaries() -> None:
    """合成小说应被切成 4 章，章号连续，标题含中文数字与二合一大章标记。"""
    result = split_chapters(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert result.chapter_count == 4
    assert [b.order for b in result.boundaries] == [1, 2, 3, 4]
    assert result.boundaries[0].title == "雨夜归人"
    assert result.boundaries[2].title == "白极光"
    assert result.boundaries[3].title == "终局（二合一大章）"
    # fixture 首行是 CC0 声明，应只产生一条 preamble 告警，无章号类告警
    assert len(result.warnings) == 1
    assert "未归类内容" in result.warnings[0]
    assert result.preamble.startswith("本文件为")


def test_noise_lines_are_not_chapters() -> None:
    """更新时间行、插图链接行、正文长句行都不得被误切为章节。"""
    result = split_chapters(FIXTURE_PATH.read_text(encoding="utf-8"))
    titles = [b.title for b in result.boundaries]
    assert not any("插图" in title or "更新时间" in title for title in titles)
    full_text = FIXTURE_PATH.read_text(encoding="utf-8")
    # 那句以"第三章"开头但超长且含逗号的叙述句必须落在第 1 章正文里
    chapter_one = result.chapter_text(0)
    assert "只能凭着本能往前挪动脚步" in chapter_one
    assert full_text[result.boundaries[0].start :].startswith("第1章")


def test_offsets_roundtrip() -> None:
    """边界偏移切回原文必须以章题行开头、且四段拼起来等于全文。"""
    text = FIXTURE_PATH.read_text(encoding="utf-8")
    result = split_chapters(text)
    for expected, boundary in zip(("第1章", "第2章", "第3章", "第4章"), result.boundaries):
        assert text[boundary.start : boundary.end].startswith(expected)
    # preamble 与各章切片拼起来必须无损还原全文
    assert result.preamble + "".join(result.chapter_text(i) for i in range(4)) == text


def test_no_chapter_raises() -> None:
    """完全识别不到章节时必须报错，而不是静默把整本书当一章。"""
    with pytest.raises(ValueError):
        split_chapters("这是一段没有章节标记的正文。\n第二行依然没有标记。\n")


def test_duplicate_order_warns() -> None:
    """章号重复要产生告警且不丢边界。"""
    text = "第1章 甲\n正文甲。\n第1章 乙\n正文乙。\n"
    result = split_chapters(text)
    assert result.chapter_count == 2
    assert any("章号重复" in warning for warning in result.warnings)


def test_short_title_with_comma_after_separator_accepted() -> None:
    """有分隔空白的短标题即使含逗号/问号也是章题（回归：'第9章 爸，我饿' 曾被误杀）。"""
    text = "第9章 爸，我饿\n他低头吃饭。\n第10章 “观众”\n台下坐满了人。\n第11章 第二只？\n它又来了。\n"
    result = split_chapters(text)
    assert [b.title for b in result.boundaries] == ["爸，我饿", "“观众”", "第二只？"]


def test_title_with_ellipsis_and_question_accepted() -> None:
    """有分隔标题允许省略号+问号（回归：'第36章 你们……谁能惩戒我？' 曾被误杀）。"""
    text = "第36章 你们……谁能惩戒我？\n无人应答。\n"
    result = split_chapters(text)
    assert result.chapter_count == 1
    assert result.boundaries[0].title == "你们……谁能惩戒我？"


def test_unseparated_long_sentence_rejected() -> None:
    """无分隔直接接长正文（含逗号）不得误切为章节。"""
    text = "第三章他一个人在雨里走了很久，想了很多过去的事情。\n第1章 真正的开头\n正文。\n"
    result = split_chapters(text)
    assert result.chapter_count == 1
    assert result.boundaries[0].title == "真正的开头"
