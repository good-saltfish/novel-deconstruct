"""跨章 AI 味判据：单章不阻断，连续多章持续偏高才阻断。

为什么不能做单章硬门（2026-11-04 实测，99 本真实网文 3956 章 vs 10 章真模型正文）：

    规则                    人类p50  人类p95  人类max   AI p50  AI max
    em_dash                    0        5        30      10      17
    period_per_paragraph       2        4       329      4       5
    simile_density             3       10        39     7.5      10
    scene_rotation             0        2         6      0       2

单章上全部重叠。人类小说里 em_dash=30、段落句号=329 的章节真实存在（作者个人
风格），任何「单章超阈值即拒绝」的硬门都会误杀人类写的好书。这正是本项目
红线说的「判据写不出来，那一步就不能自动化」。

但中位数差异是稳定的（em_dash 0 vs 10、simile 3 vs 7.5）。差异不在单章，
而在**一本书整体的倾向**。所以可成立的判据是：

    连续 K 章的章级实测值中位数，持续高于人类 p95 才算「整本在漂」。

这样：
- 偶尔一章 em_dash 多（人类也这样）→ 中位数不抬高 → 不阻断；
- 整本都靠破折号造停顿（AI 的典型习惯）→ 中位数持续抬高 → 阻断。

参数取 K=5、参考线取人类 p95（见 _REFERENCE_P95）。参考线是实测值不是拍的，
换语料要重跑 `_scratch/compare_fingerprint_ai_vs_human.py` 重新标定。
"""

from __future__ import annotations

from statistics import median

# 参考线 = 人类真实网文的 p95（2026-11-04 实测，99 本 / 3956 章）。
# 超过这条线的章在人类作品里只有 5%，所以「连续多章中位数都过线」不是风格问题。
_REFERENCE_P95: dict[str, float] = {
    "em_dash": 5.0,
    "period_per_paragraph": 4.0,
    "simile_density": 10.0,
}

# 需要连续多少章的中位数过线才判定「整本在漂」。
# 5 章是权衡：3 章太容易被连续两个高强度章节带偏；10 章则要写十章才发现跑偏。
DRIFT_WINDOW = 5

# 判定阈值：窗口内中位数超过参考线的这个倍数才算漂移。
#
# 2.0 是实测标定出来的折中点（2026-11-04，10686 个人类窗口 + 10 章真模型正文）：
#   ratio  人类误杀率   真模型是否命中
#   1.0      2.12%      em_dash 第 5 章
#   1.5      0.83%      em_dash 第 5 章
#   2.0      0.19%      em_dash 第 5 章   <- 取这个
#   2.5      0.02%      em_dash 第 8 章（更晚才发现）
#   3.0      0.00%      不命中（判据失效）
# 再严就没判别力，再松就开始误杀人类作品。
DRIFT_MEDIAN_RATIO = 2.0


def drift_verdict(rule_values: list[float], rule: str) -> tuple[bool, str]:
    """判断某条规则在最近若干章是否持续偏高。

    返回 ``(是否漂移, 说明)``。样本不足 ``DRIFT_WINDOW`` 章时返回 ``(False, "")``
    —— 写前几章就报警等于让作者无法开书。
    """
    reference = _REFERENCE_P95.get(rule)
    if reference is None or len(rule_values) < DRIFT_WINDOW:
        return False, ""
    window = rule_values[-DRIFT_WINDOW:]
    middle = median(window)
    line = reference * DRIFT_MEDIAN_RATIO
    if middle <= line:
        return False, ""
    return True, (
        f"最近 {DRIFT_WINDOW} 章「{rule}」中位数 {middle:.1f}，"
        f"超过人类作品 p95（{reference:.0f}）的 {DRIFT_MEDIAN_RATIO:g} 倍（{line:.1f}）"
        f"——整本正在朝同一个套路漂"
    )
