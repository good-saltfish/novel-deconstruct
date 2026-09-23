"""伏笔台账测试：只登记显式钩子、阈值回收、取最早章、不臆造、开放集合正确。"""

from ndecon.creation.models import ChapterOutline
from ndecon.retrieval.foreshadow import (
    RESOLVE_THRESHOLD,
    extract_foreshadows,
    open_foreshadows_at,
)


def _outline(order: int, *, ending: str = "", opening: str = "", event: str = "") -> ChapterOutline:
    """构造最小细纲条目。"""
    return ChapterOutline(
        order=order,
        title=f"第{order}章",
        core_event=event,
        opening_hook=opening,
        ending_hook=ending,
    )


def test_empty_ending_hook_produces_no_entry() -> None:
    """没有显式章尾钩时不登记任何伏笔（不从正文猜测）。"""
    outlines = [_outline(1), _outline(2)]
    assert extract_foreshadows(outlines) == []


def test_hook_stays_open_without_lexical_recovery() -> None:
    """后续章与钩子字面不重合时，钩子保持开放。"""
    outlines = [
        _outline(1, ending="铜灯在雨夜里熄灭"),
        _outline(2, opening="天亮了", event="主角去学校上课"),
    ]
    entries = extract_foreshadows(outlines)
    assert len(entries) == 1
    assert entries[0].is_open and entries[0].resolved_order is None
    assert entries[0].key == "hook-ch1"


def test_hook_resolved_when_threshold_met() -> None:
    """后续章文本与钩子的 token 重合达到阈值时，记为回收并保留证据。"""
    hook = "铜灯在雨夜里熄灭"
    # 近乎重述钩子：7 个 bigram 中 6 个重合（0.857）
    resolving_event = "铜灯在雨夜里终于熄灭"
    outlines = [
        _outline(1, ending=hook),
        _outline(2, event=resolving_event),
    ]
    entries = extract_foreshadows(outlines)
    assert entries[0].resolved_order == 2
    assert entries[0].match_ratio is not None
    assert entries[0].match_ratio >= RESOLVE_THRESHOLD
    assert "铜灯" in entries[0].resolution_evidence


def test_earliest_resolving_chapter_wins() -> None:
    """多章都满足时，取最早的一章作为回收章。"""
    hook = "铜灯在雨夜里熄灭"
    event = "铜灯在雨夜里终于熄灭"
    outlines = [
        _outline(1, ending=hook),
        _outline(2, event=event),
        _outline(3, event=event),
    ]
    entries = extract_foreshadows(outlines)
    assert entries[0].resolved_order == 2


def test_ending_hooks_of_later_chapters_not_self_resolving() -> None:
    """后续章自己的章尾钩不作为回收证据，避免同类钩子连锁误判。"""
    hook = "新的异常信号出现"
    outlines = [
        _outline(1, ending=hook),
        _outline(2, event="主角吃饭散步", ending=hook),
        _outline(3, event="主角回家睡觉", ending=hook),
    ]
    entries = extract_foreshadows(outlines)
    ch1_entry = next(entry for entry in entries if entry.opened_order == 1)
    assert ch1_entry.is_open


def test_open_foreshadows_at_scope() -> None:
    """open_foreshadows_at：只含本章之前开启、且未在本章之前回收的伏笔。"""
    outlines = [
        _outline(1, ending="铜灯在雨夜里熄灭"),
        _outline(2, event="铜灯在雨夜里终于熄灭"),
        _outline(3, ending="井底传来敲门声"),
    ]
    ledger = extract_foreshadows(outlines)
    # 写第 2 章前：ch1 的钩子开放
    assert [entry.key for entry in open_foreshadows_at(ledger, 2)] == ["hook-ch1"]
    # 写第 3 章前：ch1 已在第 2 章回收；ch3 自己的钩子尚未开启
    assert open_foreshadows_at(ledger, 3) == []
    # 写第 4 章前：ch3 的钩子开放
    assert [entry.key for entry in open_foreshadows_at(ledger, 4)] == ["hook-ch3"]
