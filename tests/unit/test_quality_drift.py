"""跨章漂移判据的单元测试（新增文件，补上此前没有覆盖的 drift 路径）。"""

from __future__ import annotations

from ndecon.quality.drift import (
    _REFERENCE_P95,
    DRIFT_MEDIAN_RATIO,
    DRIFT_WINDOW,
    drift_verdict,
)
from ndecon.quality.gates import PLACEHOLDER_BANNER, check_chapter


def test_drift_verdict_needs_full_window() -> None:
    """样本不足一个窗口时不判定漂移——否则写前几章就报警。"""
    for count in range(DRIFT_WINDOW):
        drifted, why = drift_verdict([99.0] * count, "em_dash")
        assert drifted is False
        assert why == ""


def test_drift_verdict_flags_sustained_high() -> None:
    """连续 K 章都远高于参考线时判定漂移，并说明实测中位数。"""
    line = _REFERENCE_P95["em_dash"] * DRIFT_MEDIAN_RATIO
    drifted, why = drift_verdict([line + 1] * DRIFT_WINDOW, "em_dash")
    assert drifted is True
    assert f"{line + 1:.1f}" in why
    assert "整本正在朝同一个套路漂" in why


def test_drift_verdict_tolerates_one_spike() -> None:
    """一章偏高不会触发——人类作者也会写一章用力过猛的。"""
    line = _REFERENCE_P95["em_dash"] * DRIFT_MEDIAN_RATIO
    values = [0.0] * (DRIFT_WINDOW - 1) + [line * 3]
    drifted, _why = drift_verdict(values, "em_dash")
    assert drifted is False


def test_drift_verdict_ignores_unknown_rule() -> None:
    """没有参考线的规则不判定（避免拿没标定的数值当判据）。"""
    drifted, why = drift_verdict([100.0] * DRIFT_WINDOW, "not_a_rule")
    assert drifted is False
    assert why == ""


def test_check_chapter_exempts_placeholder_banner() -> None:
    """占位正文豁免漂移判据：模板生成器必然饱和，判它 AI 味没有意义。"""
    text = PLACEHOLDER_BANNER + "\n" + "他推开门。走了进去。看见一个人。\n" * 200
    history = [{"period_per_paragraph": 16.0} for _ in range(DRIFT_WINDOW - 1)]
    report = check_chapter(text, previous_fingerprints=history)
    assert not [f for f in report.hard_failures if f.rule.startswith("drift_")]
