"""写作质量自检（只读、纯本地、离线）。

本包只做**度量与报告**，从不产出"修改后的文本"：
改什么、怎么改永远由作者自己决定。

理由（见 docs/ai-writing-workflow.md 5.5）：语感只能自己练；
用 AI 润色来"去 AI 味"既会让语感不可逆退化，也会在平台侧自证违规。
"""

from ndecon.quality.fingerprint import (
    AI_FINGERPRINT_VERSION,
    CHAPTER_CALIBRATION_CHARS,
    FingerprintFinding,
    FingerprintReport,
    count_cjk,
    scan_chapter,
    split_paragraphs,
)

__all__ = [
    "AI_FINGERPRINT_VERSION",
    "CHAPTER_CALIBRATION_CHARS",
    "FingerprintFinding",
    "FingerprintReport",
    "count_cjk",
    "scan_chapter",
    "split_paragraphs",
]
