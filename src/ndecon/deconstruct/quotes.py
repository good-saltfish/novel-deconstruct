"""语义层引文体检：模型吐回来的每一处原文，都要能在本机原文里定位。

为什么不由模型自己保证（它已经在 SYSTEM_MESSAGE 里被要求"≤30 字且必须跟点评"）：
提示词约束是君子协定，模型漂移时会整段回吐原文。这里是**事后强制**：

- 能唯一定位的短引保留，并落成带坐标的锚点（可复核、可追溯）；
- 定位不到的删掉并告警——宁可少一条证据，也不留一条无法核对的"引文"；
- 超过 `MAX_QUOTE_CHARS` 的直接裁掉并告警：那是摘录，不是证据。

与 `providers/locate.py` 的分工：那里负责"analysis 情节点 quote"的三级定位，
这里负责"叙述字段里混杂的零星引号"；两边共用同一条纪律——**定位失败即丢弃，绝不伪造坐标**。
"""

from __future__ import annotations

import re

from ndecon.deconstruct.models import (
    MAX_QUOTE_CHARS,
    PLACEHOLDER,
    ConflictAnchor,
)
from ndecon.providers.locate import locate_quote

# 成对引号内的内容；允许 0 字以便把"空引号"也当成问题查出来
_QUOTED = re.compile(r"[“\"'‘]([^”\"'’\n]*)[”\"'’]?")


def extract_quotes(text: str) -> list[str]:
    """抽出文本里的成对引号内容（去重保序）。"""
    seen: list[str] = []
    for match in _QUOTED.finditer(text or ""):
        quote = match.group(1).strip()
        if quote and quote not in seen:
            seen.append(quote)
    return seen


def audit_narrative(
    text: str,
    *,
    chapter_text: str,
    source_id: str,
    content_hash: str,
) -> tuple[str, list[ConflictAnchor], list[str]]:
    """体检一段叙述：定位其中的每一处引文，返回 (清理后的叙述, 锚点, 告警)。

    规则（按此顺序执行，不做"看起来像不像摘录"的主观判断）：
    1. 长度 > `MAX_QUOTE_CHARS`：整处删除，告警"疑似摘录，已移除"；
    2. 在本章原文里唯一定位成功：保留原文短引，并落成带坐标的锚点；
    3. 定位失败：整处删除，告警"无法定位，已丢弃"。
    """
    if not text:
        return "", [], []

    anchors: list[ConflictAnchor] = []
    warnings: list[str] = []
    cleaned = text

    for quote in extract_quotes(text):
        if len(quote) > MAX_QUOTE_CHARS:
            warnings.append(
                f"移除一处 {len(quote)} 字引文（超过 {MAX_QUOTE_CHARS} 字上限，判定为摘录而非证据）"
            )
            cleaned = cleaned.replace(f"“{quote}”", "").replace(f'"{quote}"', "")
            continue
        span = locate_quote(chapter_text, quote)
        if span is None:
            warnings.append(
                f"丢弃一处无法在本章原文中唯一定位的引文：{quote[:12]}…"
            )
            cleaned = cleaned.replace(f"“{quote}”", PLACEHOLDER).replace(
                f'"{quote}"', PLACEHOLDER
            )
            continue
        anchors.append(
            ConflictAnchor(
                source_id=source_id,
                start=span[0],
                end=span[1],
                trigger=f"可复核短引（{len(quote)}字）",
                content_hash=content_hash,
                note="",  # 刻意留空：本锚点的作用是可复核坐标，不是手法提示
            )
        )

    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if anchors:
        cleaned = f"{cleaned} （含 {len(anchors)} 处可复核短引，坐标见本章锚点表）"
    return cleaned, anchors, warnings
