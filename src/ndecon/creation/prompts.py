"""创作生成的版本化 prompt（cr-v0）。

学习注入纪律：参考拆书只允许提供聚合数字（主题分布/爽点章距等），
不允许把原文、quote、情节点文本送入创作 prompt——既防版权串入，也防模型照抄。
"""

from __future__ import annotations

import json
from collections import Counter

from ndecon.kb.corpus import render_kb_block

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
        structure = stat.get("structure") or {}
        facts = _structure_facts(structure)
        if facts:
            lines.append(f"  - 节奏实测：{facts}")
        hooks = _hook_facts(structure)
        if hooks:
            lines.append(f"  - 钩子节拍：{hooks}")
    return "\n".join(lines)


def _structure_facts(structure: dict) -> str:
    """从拆书结构里取可直接对标的手感数字（全是数值，不含任何情节文本）。"""
    parts: list[str] = []
    if structure.get("avg_sentence_chars") is not None:
        parts.append(f"平均句长 {float(structure['avg_sentence_chars']):.1f} 字")
    if structure.get("avg_dialogue_ratio") is not None:
        parts.append(f"对话句占比 {float(structure['avg_dialogue_ratio']) * 100:.0f}%")
    if structure.get("per_thousand_conflicts") is not None:
        parts.append(f"每千字 {float(structure['per_thousand_conflicts']):.1f} 处冲突点")
    if structure.get("median_sentence_chars") is not None:
        parts.append(f"句长中位 {float(structure['median_sentence_chars']):.1f} 字")
    if structure.get("short_sentence_ratio") is not None:
        parts.append(f"短句占比 {float(structure['short_sentence_ratio']) * 100:.0f}%")
    return "；".join(parts)


def _hook_facts(structure: dict) -> str:
    """钩子的回收节奏：这本书的悬念一般吊几章。"""
    parts: list[str] = []
    median = structure.get("hook_recovery_median")
    if median is not None:
        parts.append(f"已回收钩子的间隔中位数 {float(median):.1f} 章")
    longest = structure.get("hook_recovery_longest")
    if longest is not None:
        parts.append(f"最长吊钩 {int(longest)} 章")
    open_count = structure.get("hook_open_count")
    if open_count:
        parts.append(f"读到末章仍开放 {int(open_count)} 条")
    return "；".join(parts)


def _outline_spec(chapter_count: int = 10) -> dict:
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
                "order": f"1-{chapter_count} 的整数",
                "title": "章题",
                "core_event": "核心事件",
                "opening_hook": "开篇钩子",
                "ending_hook": "章尾钩子",
                # 节拍写进一次性骨架的输出结构里：正文生成时 `context_brief` 会逐拍
                # 渲染，模型才有"这一章按什么顺序铺开"的依据。不给节拍的话，细纲只有
                # 一句 core_event（实测 26-60 字），模型只能自己编节奏——编出来就是
                # 一句一段的流水账。缺省值仍是空列表，模型不给也不报错。
                "beats": [
                    {
                        "order": "节拍序号（章内 1 起，每章 3-6 个）",
                        "scene": "场景/地点（具体到能拍）",
                        "characters": ["出场角色"],
                        "emotion": "本节拍情绪",
                        "event": "发生了什么（一句，必须是动作不是状态）",
                    }
                ],
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


def full_draft_user_message(
    title: str,
    genre: str,
    premise: str,
    reference_stats: list[dict],
    knowledge_snippets: list[dict] | None = None,
    *,
    chapter_count: int = 10,
) -> str:
    """组装一次性生成全部骨架部件的用户消息。"""
    sections = [
        f"新长篇标题：{title}\n题材：{genre or '由你根据设定判断'}\n"
        f"一句话设定：{premise}\n",
        f"参考拆书统计（仅数字手感，禁止照抄情节）：\n{learning_brief(reference_stats)}",
    ]
    knowledge = render_kb_block(knowledge_snippets or [])
    if knowledge:
        sections.append(knowledge)
    sections.append(
        f"请输出完整骨架 JSON（chapters 恰好 {chapter_count} 条，order 为 1-{chapter_count}）：\n"
        + json.dumps(_outline_spec(chapter_count), ensure_ascii=False)
    )
    return "\n\n".join(sections)
