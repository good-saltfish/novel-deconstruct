"""拆书 Provider 协议。

v0.1 有两个实现位：
- FakeProvider：确定性规则，离线、可重放，用于测试与无 key 演示；
- OpenAI 兼容实现（后续版本）：使用者自带 key，本项目不内置任何凭据。
"""

from __future__ import annotations

from typing import Protocol

from ndecon.domain.models import ChapterSummary, GoldenChapterReport


class DeconstructProvider(Protocol):
    """拆书语义产出的最小契约。切章不经过本协议（必须纯确定性）。"""

    name: str
    prompt_version: str

    def thin_summary(self, first_chapter_text: str, book_title: str) -> str:
        """依据首章原文产出全书 thin 概要（约 100-200 字）。"""
        ...

    def summarize_chapter(
        self, order: int, title: str, chapter_text: str, chapter_hash: str
    ) -> ChapterSummary:
        """产出单章结构化摘要；每个情节点必须携带可定位的 SourceRef。"""
        ...

    def golden_report(
        self, order: int, title: str, chapter_text: str, chapter_hash: str
    ) -> GoldenChapterReport:
        """产出黄金三章（前 3 章）深度拆解报告；学习层 takeaways 必须留空。"""
        ...
