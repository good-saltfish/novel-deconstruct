"""创作生成的版本化 prompt（cr-v0）。

学习注入纪律：参考拆书只允许提供聚合数字（主题分布/爽点章距等），
不允许把原文、quote、情节点文本送入创作 prompt——既防版权串入，也防模型照抄。
"""

from __future__ import annotations

import json
from collections import Counter

PROMPT_VERSION = "cr-v0"

SYSTEM_MESSAGE = """你是一名资深中文网络小说主编。
你的任务是基于作者给出的题材与一句话设定，产出结构化的长篇小说写作骨架，
供作者修改确认。

硬性要求：
1. 只输出一个 JSON 对象，不输出任何额外文字或 markdown 代码块。
2. 内容面向商业网文：钩子前置、目标明确、矛盾具体；金手指必须带明确限制与代价。
3. 不写章节正文，只写骨架字段；所有字段使用中文，简明具体，不要空话套话。
4. 提供的"参考拆书统计"只是数字层面的市场手感参考，严禁照抄任何具体作品的情节与表达。
"""


def learning_brief(reference_stats: list[dict]) -> str:
    """把参考书聚合结果压缩成只有数字/标签的学习简报文本。"""
    if not reference_stats:
        return "（无参考拆书）"
    lines = []
    for stat in reference_stats:
        themes = Counter(stat.get("themes", {}))
        top_themes = "、".join(f"{name}×{count}" for name, count in themes.most_common(5)) or "无"
        pacing = stat.get("pacing", {})
        avg_gap = pacing.get("average_gap")
        lines.append(
            f"- 《{stat.get('title', '参考书')}》（{stat.get('chapters', 0)} 章）："
            f"高频主题 {top_themes}；爽点平均章距 {avg_gap if avg_gap is not None else '—'}"
        )
    return "\n".join(lines)


def _outline_spec() -> dict:
    """返回输出 JSON 结构说明（作为消息的一部分，不参与类型校验）。"""
    return {
        "positioning": {
            "genre": "主类型",
            "audience": "目标读者",
            "core_premise": "一句话卖点",
            "selling_points": ["差异化卖点1", "差异化卖点2"],
            "tone_target": "目标基调",
        },
        "volume": {
            "volume_title": "首卷标题",
            "volume_goal": "本卷目标",
            "main_conflict": "核心矛盾",
            "ending_hook": "卷尾钩子",
        },
        "chapters": [
            {
                "order": "1-10 的整数",
                "title": "章题",
                "core_event": "核心事件",
                "opening_hook": "开篇钩子",
                "ending_hook": "章尾钩子",
            }
        ],
        "protagonist": {
            "name": "主角名",
            "background": "出身/初始处境",
            "personality": "性格",
            "desire": "核心欲望",
            "flaw": "缺陷/成长课题",
            "signature_ability": "标志能力/手段",
        },
        "golden_finger": {
            "name": "金手指名称",
            "form": "形态",
            "ability": "能力",
            "cost_limitation": "限制与代价（必填且具体）",
            "growth_path": "成长路径",
        },
    }


def knowledge_brief(snippets: list[dict]) -> str:
    """把本地知识库命中片段渲染为带来源的方法论参考区块；空列表返回空串。

    片段由 ndecon.kb 检索产生（只含方法论/教学/素材，版权小说原文在入库时已排除）。
    """
    if not snippets:
        return ""
    lines = ["【写作方法论参考】（来自作者本地知识库；借鉴手法与结构，严禁照抄具体作品情节）："]
    for i, snippet in enumerate(snippets, start=1):
        heading = snippet.get("heading") or ""
        title_part = f"《{heading}》 " if heading else ""
        lines.append(f"{i}. 来源 {snippet.get('source', '?')} {title_part}\n   {snippet.get('text', '')}")
    return "\n".join(lines)


def full_draft_user_message(
    title: str,
    genre: str,
    premise: str,
    reference_stats: list[dict],
    knowledge_snippets: list[dict] | None = None,
) -> str:
    """组装一次性生成全部骨架部件的用户消息。"""
    sections = [
        f"新长篇标题：{title}\n题材：{genre or '由你根据设定判断'}\n"
        f"一句话设定：{premise}\n",
        f"参考拆书统计（仅数字手感，禁止照抄情节）：\n{learning_brief(reference_stats)}",
    ]
    knowledge = knowledge_brief(knowledge_snippets or [])
    if knowledge:
        sections.append(knowledge)
    sections.append(
        "请输出完整骨架 JSON（chapters 恰好 10 条，order 为 1-10）：\n"
        + json.dumps(_outline_spec(), ensure_ascii=False)
    )
    return "\n\n".join(sections)
