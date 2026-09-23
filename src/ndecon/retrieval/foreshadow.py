"""伏笔台账（叙事债）：哪些钩子开了、在后面哪一章被回收。

"绝不臆造"在本模块的具体含义：
1. **开启**只认细纲里显式写出的章尾钩（``ChapterOutline.ending_hook``），
   不从正文/摘要里猜测"这里可能埋伏笔"；
2. **回收**用固定阈值的字面 token 重合判定：后续章节的开篇钩/核心事件
   （或已写章节的摘要）与钩子文本共享足够比例的 bigram，才算回收，
   并记录命中的证据文本；达不到阈值就保持开放，宁缺毋滥；
3. 阈值写死、扫描顺序固定，同一输入永远得到同一张台账。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ndecon.creation.models import ChapterOutline
from ndecon.domain.models import ChapterSummary
from ndecon.retrieval.tokenize import tokenize

# 钩子 token 至少有该比例在后续章节文本中出现，才判定为回收（保守值，偏向"不回收"）
RESOLVE_THRESHOLD = 0.6
# 证据文本保留长度上限，避免上下文包被长摘要撑爆
EVIDENCE_LIMIT = 120


class ForeshadowEntry(BaseModel):
    """一条伏笔的完整生命周期记录。"""

    key: str = Field(description="稳定标识：hook-ch<开启章号>")
    description: str = Field(min_length=1, description="钩子原文（来自细纲章尾钩）")
    opened_order: int = Field(ge=1, description="钩子开启章号")
    resolved_order: int | None = Field(default=None, description="回收章号；未回收为 None")
    match_ratio: float | None = Field(default=None, description="回收时的 token 重合比例")
    resolution_evidence: str = Field(default="", description="回收章中触发判定的文本片段")

    @property
    def is_open(self) -> bool:
        """便捷判断：该伏笔是否尚未回收。"""
        return self.resolved_order is None


def _overlap_ratio(hook_tokens: set[str], candidate_tokens: set[str]) -> float:
    """计算钩子 token 在候选文本中的覆盖比例；钩子无有效 token 时记 0。"""
    if not hook_tokens:
        return 0.0
    return len(hook_tokens & candidate_tokens) / len(hook_tokens)


def _candidate_text(
    order: int,
    outlines_by_order: dict[int, ChapterOutline],
    summaries_by_order: dict[int, ChapterSummary],
) -> str:
    """组装某一章可作为"回收证据"的文本：开篇钩+核心事件（细纲）与摘要 gist/情节点。

    刻意不包含该章自己的章尾钩，避免"每章结尾都有同类钩子"造成连锁误回收。
    """
    parts: list[str] = []
    outline = outlines_by_order.get(order)
    if outline is not None:
        parts.extend([outline.opening_hook, outline.core_event])
    summary = summaries_by_order.get(order)
    if summary is not None:
        parts.append(summary.gist)
        parts.extend(point.summary for point in summary.plot_points)
    return "\n".join(part for part in parts if part and part.strip())


def extract_foreshadows(
    outlines: list[ChapterOutline],
    summaries: list[ChapterSummary] | None = None,
) -> list[ForeshadowEntry]:
    """从细纲章尾钩确定性提取伏笔台账，并扫描后续章判定回收。

    扫描按章号升序，每个钩子取**最早**达到阈值的章作为回收章；
    summaries 为本书已写章节的结构化摘要（#16 起产生），可选。
    """
    ordered = sorted(outlines, key=lambda item: item.order)
    outlines_by_order = {item.order: item for item in ordered}
    summaries_by_order = {item.order: item for item in summaries or []}
    later_orders = sorted(set(outlines_by_order) | set(summaries_by_order))

    entries: list[ForeshadowEntry] = []
    for outline in ordered:
        hook = outline.ending_hook.strip()
        if not hook:
            # 没有显式章尾钩 = 没有可登记的伏笔，跳过而非造一条
            continue
        hook_tokens = set(tokenize(hook))
        entry = ForeshadowEntry(
            key=f"hook-ch{outline.order}",
            description=hook,
            opened_order=outline.order,
        )
        for later_order in later_orders:
            if later_order <= outline.order:
                continue
            evidence = _candidate_text(later_order, outlines_by_order, summaries_by_order)
            ratio = _overlap_ratio(hook_tokens, set(tokenize(evidence)))
            if ratio >= RESOLVE_THRESHOLD:
                entry.resolved_order = later_order
                entry.match_ratio = round(ratio, 4)
                entry.resolution_evidence = evidence[:EVIDENCE_LIMIT]
                break
        entries.append(entry)
    return entries


def open_foreshadows_at(entries: list[ForeshadowEntry], chapter_order: int) -> list[ForeshadowEntry]:
    """返回写到第 chapter_order 章之前仍需照顾的伏笔：已开启且未在本章之前回收。"""
    result = [
        entry
        for entry in entries
        if entry.opened_order < chapter_order
        and (entry.resolved_order is None or entry.resolved_order >= chapter_order)
    ]
    return sorted(result, key=lambda entry: entry.opened_order)
