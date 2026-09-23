"""Prompt 模板（版本化）。

纪律：
- prompt 是产物的一部分（影响质量），修改措辞必须 bump PROMPT_VERSION；
- 模型只返回引用文本 quote，字符偏移由本地代码定位，模型不提供偏移；
- 强制 JSON 输出与受控词表，词表与 ndecon.domain.enums 保持一致（测试守护一致性）。
"""

from __future__ import annotations

import json

from ndecon.domain.enums import ThemeTag, Tone

PROMPT_VERSION = "oc-v0"

_TONES = "、".join(t.value for t in Tone)
_THEMES = "、".join(t.value for t in ThemeTag)

_SYSTEM = f"""你是一名严谨的中文网络小说结构分析编辑。
你的任务是把指定章节拆解为结构化数据，只记录文本中实际发生的事实，
不脑补、不评价好坏、不写写作建议。

硬性要求：
1. 只输出一个 JSON 对象，不要输出 JSON 以外的任何文字，
   也不要使用 markdown 代码块。
2. 所有枚举字段只能取以下受控值：
   - tone（基调）只能是：{_TONES}
   - theme_tags（主题标签）只能是：{_THEMES}
3. 每个情节点必须给出 quote：从本章原文中**逐字摘录**的短句（10-25 字），用于证据定位；不得改写、拼接或缩写原文。
4. 情节点数量 10-40 个，按原文顺序排列。
5. 人物名使用章节中出现的全名或稳定称呼，不要提取路人泛称。
"""


def chapter_user_message(order: int, title: str, chapter_text: str) -> str:
    """组装逐章摘要的用户消息。"""
    return (
        f"请拆解第{order}章《{title}》。\n"
        "输出 JSON 结构：\n"
        + json.dumps(
            {
                "gist": "本章一句话概要（不超过40字）",
                "characters": ["本章出场人物"],
                "plot_points": [
                    {
                        "summary": "情节点概括（不超过40字）",
                        "tone": f"取值之一：{_TONES}",
                        "theme_tags": [f"取值之一：{_THEMES}"],
                        "characters": ["该情节点涉及人物（可空）"],
                        "quote": "从原文逐字摘录的10-25字短句",
                    }
                ],
            },
            ensure_ascii=False,
        )
        + f"\n\n章节原文如下：\n{chapter_text}"
    )


def golden_user_message(order: int, title: str, chapter_text: str) -> str:
    """组装黄金三章报告的用户消息（记录层事实，不产出学习层结论）。"""
    return (
        f"请对第{order}章《{title}》做开篇级结构拆解。\n"
        "输出 JSON 结构：\n"
        + json.dumps(
            {
                "opening_hook_note": "本章开篇钩子手法的事实性描述（不超过60字，只描述手法本身）",
                "opening_quote": "开篇钩子对应的原文逐字摘录（10-25字）",
                "worldview_revealed": ["本章明确透露的世界观信息，每条一句话"],
                "structure_beats": [
                    {"name": "段落名（4-10字）", "note": "该段叙事功能与内容（不超过40字）"}
                ],
                "cliffhanger_quote": "章尾钩子对应的原文逐字摘录（10-25字，无则留空字符串）",
            },
            ensure_ascii=False,
        )
        + "\n禁止输出'可借鉴要素''为什么好'等评价性内容。\n\n"
        f"章节原文如下：\n{chapter_text}"
    )


def thin_summary_user_message(book_title: str, first_chapter_text: str) -> str:
    """组装全书 thin 概要消息（基于首章，100-200 字）。"""
    return (
        f"基于以下第 1 章原文，为《{book_title}》写一段 100-200 字的全书概要候选，"
        "只使用首章明确给出的信息与可直接预期的类型框架，不剧透未发生情节。"
        "只输出 JSON：{\"thin_summary\": \"...\"}\n\n"
        f"第 1 章原文：\n{first_chapter_text}"
    )


SYSTEM_MESSAGE = _SYSTEM
