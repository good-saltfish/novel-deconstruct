"""确定性 Fake Provider（离线规则演示，不是 NLP）。

边界声明：
- 它只做"句子切分 + 关键词映射 + 原文锚点"，绝不冒充真实语义抽取；
- 产物一律标记 model_id='rule:fake'，禁止用于任何真实拆书效果宣称；
- 用途仅限于：无 key 跑通摘要链路、契约测试、输出格式演示。

2026-11-14 起只剩 `summarize_chapter`：旧拆书管道的 `thin_summary` 与
`golden_report` 随管道一起退役（这两个方法原先只被 `pipeline/runner.py`
与 `cli.py analyze` 调用，两者都已删除）。
"""

from __future__ import annotations

import re

from ndecon.domain.enums import ThemeTag, Tone
from ndecon.domain.ids import chapter_id
from ndecon.domain.models import ChapterSummary, PlotPoint, SourceRef

# 句末标点切句（保留省略号与换行作为句子边界）
_SENTENCE = re.compile(r"[^。！？!?…\n]+(?:[。！？!?…]+|$)")
# 单条 quote 上限，与项目"quote 克制"纪律一致
_QUOTE_LIMIT = 20
_SUMMARY_LIMIT = 60
_MAX_PLOT_POINTS = 40

_TONE_HORROR_WORDS = ("鬼", "尸", "血", "死", "悚", "尸", "魂")
_THEME_KEYWORDS = (
    (("父", "母", "家", "兄", "妹"), ThemeTag.KINSHIP),
    (("仇", "杀"), ThemeTag.REVENGE),
    (("笑", "逗"), ThemeTag.COMEDY),
    (("钱", "币", "财"), ThemeTag.MONEY),
    (("修", "级", "练"), ThemeTag.GROWTH),
    (("谁", "谜", "为何"), ThemeTag.SUSPENSE),
)


def _strip_title_line(chapter_text: str) -> tuple[str, int]:
    """去掉章节文本的标题行，返回 (正文, 正文起始偏移)。"""
    newline = chapter_text.find("\n")
    if newline < 0:
        return chapter_text, 0
    return chapter_text[newline + 1 :], newline + 1


def _iter_sentences(body: str, base_offset: int) -> list[tuple[int, int, str]]:
    """切句并返回 (章内绝对起始偏移, 结束偏移, 去空白句子) 列表，过滤空句。"""
    sentences: list[tuple[int, int, str]] = []
    for match in _SENTENCE.finditer(body):
        raw = match.group(0)
        lead = len(raw) - len(raw.lstrip())
        start = base_offset + match.start() + lead
        text = raw.strip()
        if text:
            sentences.append((start, start + len(text), text))
    return sentences


def _map_tone(sentence: str) -> Tone:
    """按关键词与标点确定性映射基调（恐怖词优先，其次问号/叹号）。"""
    if any(word in sentence for word in _TONE_HORROR_WORDS):
        return Tone.HORROR
    if "？" in sentence or "?" in sentence:
        return Tone.TENSE
    if "！" in sentence or "!" in sentence:
        return Tone.HOT_BLOODED
    return Tone.OTHER


def _map_themes(sentence: str) -> list[ThemeTag]:
    """按关键词表确定性映射主题标签；无命中给"其他"，保证非空。"""
    tags = [tag for words, tag in _THEME_KEYWORDS if any(w in sentence for w in words)]
    return tags or [ThemeTag.OTHER]


def _make_source(
    order: int,
    sentence_start: int,
    sentence_end: int,
    chapter_text: str,
    h: str,
) -> SourceRef:
    """由句子偏移构造受 quote 长度上限约束的证据引用。"""
    end = min(sentence_end, sentence_start + _QUOTE_LIMIT)
    quote = chapter_text[sentence_start:end]
    return SourceRef(
        chapter_id=chapter_id(order),
        start=sentence_start,
        end=end,
        quote=quote,
        content_hash=h,
    )


class FakeProvider:
    """离线确定性拆书 Provider。"""

    name = "rule:fake"
    prompt_version = "fake-v0"

    def summarize_chapter(
        self, order: int, title: str, chapter_text: str, chapter_hash: str
    ) -> ChapterSummary:
        """切句后均匀采样至情节点上限，逐句生成带证据的情节点。"""
        body, base = _strip_title_line(chapter_text)
        sentences = _iter_sentences(body, base)
        if len(sentences) > _MAX_PLOT_POINTS:
            step = len(sentences) / _MAX_PLOT_POINTS
            sentences = [sentences[int(i * step)] for i in range(_MAX_PLOT_POINTS)]

        plot_points: list[PlotPoint] = []
        for idx, (start, end, text) in enumerate(sentences, start=1):
            plot_points.append(
                PlotPoint(
                    index=idx,
                    summary=text[:_SUMMARY_LIMIT],
                    tone=_map_tone(text),
                    theme_tags=_map_themes(text),
                    characters=[],  # fake 不做命名实体识别
                    source=_make_source(order, start, end, chapter_text, chapter_hash),
                )
            )
        gist = sentences[0][2][:_SUMMARY_LIMIT] if sentences else "（fake：本章无可用句子）"
        return ChapterSummary(
            model_id=self.name,
            prompt_version=self.prompt_version,
            chapter_id=chapter_id(order),
            order=order,
            title=title,
            gist=gist,
            plot_points=plot_points,
            characters=[],
        )
