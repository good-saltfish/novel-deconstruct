"""跨章 AI 味判据：单章不阻断，连续多章持续偏高才阻断。

为什么不能做单章硬门（2026-11-12 实测重标定，dev 20 本 899 章 / hold 19 本 854 章，
单章 CJK 2000-5000 字，段落口径 = ``style.paragraphs_of``）：

    规则                      人类p50  人类p95  人类max
    em_dash                      0        9       75
    period_per_paragraph(*)    0.000    0.143  0.465   <- 已改为「超长段占比」
    simile_density               4       12       37
    scene_rotation(*)            0        3        5   <- 只计数，不判罚

    (*) fp-v2 改了这两条的口径：``period_per_paragraph`` 的 value 不再是「段内
    句号最大值」而是「超长段占比」；``scene_rotation`` 方向反了（人类中位 0），
    已降级为 info。

单章上全部重叠。人类小说里 em_dash=75、段落句号占比 0.465 的章节真实存在（作者个人
风格），任何「单章超阈值即拒绝」的硬门都会误杀人类写的好书。这正是本项目
红线说的「判据写不出来，那一步就不能自动化」。

但中位数差异是稳定的（em_dash 0 vs 10、simile 4 vs 7.5）。差异不在单章，
而在**一本书整体的倾向**。所以可成立的判据是：

    连续 K 章的章级实测值中位数，持续高于人类 p95 才算「整本在漂」。

这样：
- 偶尔一章 em_dash 多（人类也这样）→ 中位数不抬高 → 不阻断；
- 整本都靠破折号造停顿（AI 的典型习惯）→ 中位数持续抬高 → 阻断。

参数取 K=5、参考线取人类 p95（见 _REFERENCE_P95）。参考线是实测值不是拍的，
换语料要重跑标定脚本（``_scratch/fp_calibrate.py``）重新取分位。
"""

from __future__ import annotations

from statistics import median

# 参考线 = 人类真实网文的 p95（2026-11-12 重标定，dev 899 章 / hold 854 章）。
# 超过这条线的章在人类作品里只有 5%，所以「连续多章中位数都过线」不是风格问题。
# 注意：这里的 key 必须与 fingerprint.py 的 rule id 一致，且 value 的**单位**
# 也要一致——fp-v2 把 period_per_paragraph 换成了「超长段占比」，所以参考线
# 从 4.0 改成 0.143（人类 p95 占比），否则该规则永远不会触发。
_REFERENCE_P95: dict[str, float] = {
    "em_dash": 9.0,
    "period_per_paragraph": 0.143,
    "simile_density": 12.0,
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
    # 占比类规则（如 period_per_paragraph）参考线是 0-1 的小数，用整数格式化会显示成 0
    fmt = (lambda v: f"{v:.3f}") if reference < 1 else (lambda v: f"{v:.1f}")
    return True, (
        f"最近 {DRIFT_WINDOW} 章「{rule}」中位数 {fmt(middle)}，"
        f"超过人类作品 p95（{fmt(reference)}）的 {DRIFT_MEDIAN_RATIO:g} 倍（{fmt(line)}）"
        f"——整本正在朝同一个套路漂"
    )
