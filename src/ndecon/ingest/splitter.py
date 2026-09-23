"""章节智能切分（纯确定性，不允许 LLM 参与）。

设计原则：
1. 章节边界是全书一切下游产物的坐标原点，必须可复现，因此只用正则与规则；
2. 支持阿拉伯数字与中文数字（含"两""千/万"），单位支持 章/回/节；
3. 对"更新时间/插图链接"等噪声行零误切；对章号重复/不连续给告警不静默；
4. 输出字符偏移与行号，下游证据 quote 靠偏移回原文定位。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 章节标题行：行首"第" + 数字（阿拉伯或中文）+ 单位（章/回/节）+ 分隔空白 + 标题
# 第 3 组捕获单位与标题之间的空白：有分隔（如"第9章 爸，我饿"）可信度高，
# 无分隔直接接长句（如"第三章他一个人在雨里走了很久，想了…"）按正文处理
_CHAPTER_LINE = re.compile(
    r"^[ \t　]*第[ \t　]*([0-9零一二两三四五六七八九十百千]+)[ \t　]*([章回节])([ \t　]*)(.*?)[ \t　]*$"
)
_MAX_TITLE_LEN = 30
_MAX_UNSEPARATED_TITLE_LEN = 12
_SENTENCE_PUNCT = "。！？!?…"


def _looks_like_prose(title: str, separated: bool) -> bool:
    """启发式判断标题部分是否其实是正文句。

    - 有分隔：标题允许问号/感叹号/省略号（如"你们……谁能惩戒我？"）；
      仅拒绝含句号，或超过 12 字且带逗号（典型正文长句）；
    - 无分隔：只接受极短短语，任何逗号/句末标点都判为正文。
    残余误切风险由章号连续性告警兜底。
    """
    if not separated:
        return len(title) > _MAX_UNSEPARATED_TITLE_LEN or any(
            ch in title for ch in "，," + _SENTENCE_PUNCT
        )
    if "。" in title:
        return True
    return len(title) > 12 and ("，" in title or "," in title)

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


@dataclass(frozen=True)
class RawBoundary:
    """单个章节边界（切分器的原始输出，不含 hash 等领域字段）。"""

    order: int
    title: str
    line_no: int  # 1-based 行号
    start: int  # 标题行在全文中的起始字符偏移
    end: int  # 下一章标题行偏移（末章为全文长度）


@dataclass(frozen=True)
class SplitResult:
    """整本书的切分结果。"""

    text: str
    boundaries: tuple[RawBoundary, ...]
    warnings: tuple[str, ...]
    preamble: str = ""

    @property
    def chapter_count(self) -> int:
        """返回识别到的章节数。"""
        return len(self.boundaries)

    def chapter_text(self, index: int) -> str:
        """按 0-based 下标取回该章原文切片（含标题行）。"""
        b = self.boundaries[index]
        return self.text[b.start : b.end]


def cn_chapter_to_int(token: str) -> int:
    """把章节号 token 转成 int；纯阿拉伯数字直接转，中文数字按位权累加。

    支持：'11'、'十一'、'二十'、'两百'、'两千零三'、'一万三千'。
    """
    if token.isdigit():
        return int(token)

    total = 0
    section = 0
    digit = 0
    for ch in token:
        if ch == "零":
            digit = 0
            continue
        if ch in _CN_DIGITS:
            digit = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            section += (digit or 1) * _CN_UNITS[ch]
            digit = 0
        elif ch == "万":
            section = (section + digit) * 10000
            total += section
            section = 0
            digit = 0
        else:
            raise ValueError(f"无法识别的中文数字字符: {ch}")
    return total + section + digit


def _is_title_line(line: str) -> tuple[int, str] | None:
    """判断一行是否为章节标题；是则返回 (章号, 标题)，否则返回 None。"""
    match = _CHAPTER_LINE.match(line)
    if not match:
        return None
    number_token, _unit, separator, title = match.groups()
    if len(title) > _MAX_TITLE_LEN or _looks_like_prose(title, separated=bool(separator)):
        return None
    return cn_chapter_to_int(number_token), title


def split_chapters(text: str) -> SplitResult:
    """扫描全文，按行识别章节标题并产出边界表与告警。

    无法识别任何章节时抛 ValueError（调用方应提示用户检查文本格式，
    而不是把整本书静默当成一章）。
    """
    lines = text.splitlines(keepends=True)
    heads: list[tuple[int, int, str]] = []  # (行号, 行起始偏移, 章号, 标题)
    offset = 0
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        parsed = _is_title_line(stripped)
        if parsed is not None:
            order, title = parsed
            heads.append((idx, offset, order, title))
        offset += len(line)

    if not heads:
        raise ValueError("未识别到任何章节标题（期望形如 '第1章 标题' / '第一章 标题'）")

    warnings: list[str] = []
    seen: dict[int, int] = {}
    for line_no, _start, order, _title in heads:
        if order in seen:
            warnings.append(f"章号重复：第{order}章 同时出现在第 {seen[order]} 行与第 {line_no} 行")
        seen[order] = line_no
    orders = [h[2] for h in heads]
    if orders != list(range(1, len(orders) + 1)):
        warnings.append(f"章号非从 1 连续：实际序列为 {orders}")

    # 第一章标题之前的内容（版权声明/推荐语等）显式保留为 preamble，不静默丢字
    preamble = text[: heads[0][1]]
    if preamble.strip():
        warnings.append(f"第 1 章之前存在未归类内容（{len(preamble.strip())} 字），已保留为 preamble，不参与逐章拆解")

    boundaries: list[RawBoundary] = []
    for i, (line_no, start, order, title) in enumerate(heads):
        end = heads[i + 1][1] if i + 1 < len(heads) else len(text)
        boundaries.append(
            RawBoundary(order=order, title=title, line_no=line_no, start=start, end=end)
        )

    return SplitResult(
        text=text,
        boundaries=tuple(boundaries),
        warnings=tuple(warnings),
        preamble=preamble,
    )
