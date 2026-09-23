"""证据 quote 锚定：把模型给出的自由文本 quote 确定性地定位回章节原文。

安全纪律：
- 定位成功才产出 SourceRef；定位失败一律返回 None，由调用方丢弃该情节点并计数，
  绝不允许用章首偏移等方式伪造证据；
- 三级匹配：精确 → NFKC+空白折叠归一化后滑窗 → 去标点滑窗（应对模型漏字/加标点）；
- 匹配必须唯一；多处命中时视为歧义，返回 None（宁可漏，不可锚错位置）。
"""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
# 注意：调用本正则前文本已经过 NFKC，全角逗号/句号会被归一成半角，因此半角全角都要列
_PUNCT = re.compile(r"[，。！？!?…、：；“”\"'‘’（）()《》〈〉,\.\-—·]+")


def _norm(text: str) -> str:
    """NFKC 归一化并折叠所有空白为无，用于宽松匹配。"""
    return _WS.sub("", unicodedata.normalize("NFKC", text))


def _strip_punct(text: str) -> str:
    """在归一化基础上再去标点，用于容忍模型 quote 的标点偏差。"""
    return _PUNCT.sub("", _norm(text))


def locate_quote(chapter_text: str, quote: str) -> tuple[int, int] | None:
    """在章节原文中定位 quote，返回 [start, end) 偏移；失败/歧义返回 None。"""
    quote = quote.strip()
    if not quote:
        return None

    # 第一级：精确唯一匹配
    hits = [m.start() for m in re.finditer(re.escape(quote), chapter_text)]
    if len(hits) == 1:
        return hits[0], hits[0] + len(quote)

    # 第二级：空白/NFKC 归一化滑窗（同归一化长度对齐）
    norm_chapter = _norm(chapter_text)
    norm_quote = _norm(quote)
    hit = _unique_window(norm_chapter, norm_quote, chapter_text)
    if hit is not None:
        return hit

    # 第三级：连标点也忽略的滑窗
    loose_chapter = _strip_punct(chapter_text)
    loose_quote = _strip_punct(quote)
    if len(loose_quote) >= 4:  # 过短的去标点串误命中风险高
        return _loose_window(loose_chapter, loose_quote, chapter_text)
    return None


def _unique_window(haystack_norm: str, needle_norm: str, original: str) -> tuple[int, int] | None:
    """在归一化文本中找唯一窗口，并映射回原文字符偏移。"""
    positions = [m.start() for m in re.finditer(re.escape(needle_norm), haystack_norm)]
    if len(positions) != 1:
        return None
    return _map_norm_span(original, positions[0], positions[0] + len(needle_norm))


def _loose_window(loose: str, needle: str, original: str) -> tuple[int, int] | None:
    """去标点文本中的唯一窗口映射；实现复用归一化映射（loose 是 norm 的进一步剔除）。

    为避免建立双重映射表，这里在"去标点后的原文序列"上直接重建字符映射。
    """
    mapping: list[int] = []  # loose 中每个字符在 original 中的下标
    norm_original = _norm(original)
    for idx, ch in enumerate(norm_original):
        if not _PUNCT.match(ch):
            mapping.append(idx)
    positions = [m.start() for m in re.finditer(re.escape(needle), loose)]
    if len(positions) != 1:
        return None
    start_norm = mapping[positions[0]]
    end_norm = mapping[positions[0] + len(needle) - 1] + 1
    return _map_norm_span(original, start_norm, end_norm)


def _map_norm_span(original: str, norm_start: int, norm_end: int) -> tuple[int, int]:
    """把归一化文本的偏移区间映射回原文偏移。"""
    norm = _norm(original)
    # 建立 norm 下标 -> original 下标 的映射（去空白导致多对一关系）
    pos_map: list[int] = []
    norm_idx = 0
    for orig_idx, ch in enumerate(unicodedata.normalize("NFKC", original).replace("\r\n", "\n").replace("\r", "\n")):
        if _WS.fullmatch(ch):
            continue
        pos_map.append(orig_idx)
        norm_idx += 1
    start = pos_map[norm_start]
    end = pos_map[min(norm_end - 1, len(pos_map) - 1)] + 1
    _ = norm
    return start, end
