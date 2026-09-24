"""章节正文生成器（#16）：Fake 占位 + OpenAI 兼容真实生成。

上下文纪律（ADR-0004 / #17）：
- 生成器唯一输入是 :class:`ContextPack`——本书定位/人设/金手指、本章细纲、
  前章摘要、BM25 相关回顾、开放伏笔；
- ContextPack 在结构上不含参考书 ID、源路径与任何原文 quote，
  因此生成 prompt 不可能携带版权内容；
- 正文产出与结构化自评（钩子/信息密度/细纲偏差）同时返回，自评只报告不改写。
"""

from __future__ import annotations

import json
from typing import Protocol

from ndecon.creation.models import ChapterSelfReview, ChapterWriting
from ndecon.providers.errors import ProviderResponseError
from ndecon.providers.openai_compat import OpenAICompatProvider
from ndecon.retrieval.context import ContextPack

CHAPTER_PROMPT_VERSION = "cw-v0"

CHAPTER_SYSTEM_MESSAGE = """你是一名资深中文网络小说写手，为主笔作者撰写章节正文草稿。

硬性要求：
1. 只输出一个 JSON 对象，不输出任何额外文字或 markdown 代码块。
2. 严格按"本章细纲"推进：核心事件必须发生，开篇钩与章尾钩必须落到正文里；
   若为了叙事合理需要偏离细纲，必须在 review.deviations 中逐条说明。
3. 正文使用中文网文白话：场景具体、对话推动、钩子前置；不写大纲、不写写作说明。
4. 上下文只包含作者本书的资料，严禁虚构与设定冲突的能力/角色/规则。
5. 同时输出结构化自评：钩子强度、信息密度（1-5）、是否贴合细纲与偏差点。
"""


def context_brief(pack: ContextPack) -> str:
    """把 ContextPack 渲染为只含本书信息的 prompt 文本（无参考书、无源路径）。"""
    lines = ["【本书已确认设定】"]
    if pack.positioning is not None:
        p = pack.positioning
        lines.append(
            f"题材：{p.genre}；读者：{p.audience}；卖点：{p.core_premise}；"
            f"目标基调：{p.tone_target}"
        )
        if p.selling_points:
            lines.append("差异化卖点：" + "；".join(p.selling_points))
    if pack.protagonist is not None:
        hero = pack.protagonist
        lines.append(
            f"主角：{hero.name}（{hero.personality}）；欲望：{hero.desire}；"
            f"缺陷：{hero.flaw}；手段：{hero.signature_ability}；出身：{hero.background}"
        )
    if pack.golden_finger is not None:
        finger = pack.golden_finger
        lines.append(
            f"金手指：{finger.name}（{finger.form}）——{finger.ability}；"
            f"限制代价：{finger.cost_limitation}；成长：{finger.growth_path}"
        )

    target = pack.chapter_outline
    lines.append(
        f"\n【本章细纲·第{target.order}章《{target.title}》】"
        f"\n核心事件：{target.core_event}"
        f"\n开篇钩：{target.opening_hook or '（细纲未指定）'}"
        f"\n章尾钩：{target.ending_hook or '（细纲未指定）'}"
    )

    if pack.previous_summary:
        lines.append(f"\n【前一章摘要】（来源：{pack.previous_summary_source}）\n{pack.previous_summary}")
    if pack.retrieved:
        lines.append("\n【本书相关回顾】")
        for item in pack.retrieved:
            lines.append(f"- [{item.doc_type}] {item.title}：{item.snippet}".rstrip("："))
    if pack.open_foreshadows:
        lines.append("\n【尚未回收的伏笔（写作时保持一致，勿遗忘）】")
        for debt in pack.open_foreshadows:
            lines.append(f"- 第{debt.opened_order}章埋下：{debt.description}")
    return "\n".join(lines)


def _chapter_output_spec() -> dict:
    """返回章节生成 JSON 的结构说明（消息的一部分，不参与类型校验）。"""
    return {
        "content": "本章正文，多段中文，段落间用 \\n\\n 分隔；不要输出章题行",
        "review": {
            "hook_strength": "1-5 整数",
            "info_density": "1-5 整数",
            "outline_followed": "布尔值",
            "deviations": ["与细纲不符之处；完全贴合时为空数组"],
            "notes": "一句评语",
        },
    }


def chapter_user_message(pack: ContextPack) -> str:
    """组装章节生成的用户消息。"""
    return (
        f"{context_brief(pack)}\n\n"
        f"请为第 {pack.chapter_order} 章撰写正文草稿并输出 JSON：\n"
        + json.dumps(_chapter_output_spec(), ensure_ascii=False)
    )


class ChapterWriter(Protocol):
    """章节正文生成器协议。"""

    model_id: str
    prompt_version: str

    def write_chapter(self, pack: ContextPack) -> ChapterWriting:
        """基于上下文包产出一章正文候选与结构化自评。"""
        ...


class FakeChapterWriter:
    """离线确定性章节占位器：把细纲要素拼成示意正文，明确标记不可直接使用。"""

    model_id = "rule:fake-chapter"
    prompt_version = CHAPTER_PROMPT_VERSION

    PLACEHOLDER_BANNER = "【Fake 占位正文：由离线规则生成，仅用于跑通流程，不是可用的小说内容，请替换】"

    def write_chapter(self, pack: ContextPack) -> ChapterWriting:
        """按开篇钩→核心事件→章尾钩的固定结构生成确定性占位正文。"""
        target = pack.chapter_outline
        paragraphs = [
            self.PLACEHOLDER_BANNER,
        ]
        if pack.previous_summary:
            paragraphs.append(f"（前情承接）上一章：{pack.previous_summary}。本章紧接其后展开。")
        paragraphs.extend(
            [
                f"（开篇钩）{target.opening_hook or '本章以一个反常场景开场。'}"
                "主角在熟悉的日常里被猛地推了一把，局势不容他慢慢理解。",
                f"（核心事件）{target.core_event}。"
                "他按照自己一贯的方式先算代价再动手，局面在试探与反击之间来回翻转，"
                "本章必须交代的推进在这一段落内完成。",
            ]
        )
        if pack.open_foreshadows:
            debt = "；".join(item.description for item in pack.open_foreshadows)
            paragraphs.append(f"（伏笔照应）行文没有丢下旧线：{debt}。")
        paragraphs.append(
            f"（章尾钩）{target.ending_hook or '新的异常在结尾处露头。'}"
            "问题只解决了一半，更大的压力已经到了门口。"
        )
        content = "\n\n".join(paragraphs)
        review = ChapterSelfReview(
            hook_strength=3,
            info_density=3,
            outline_followed=True,
            deviations=[],
            notes="Fake 占位文本：结构对齐细纲，语言与细节需作者重写。",
            model_id=self.model_id,
            prompt_version=self.prompt_version,
        )
        return ChapterWriting(content=content, review=review)


class OpenAICompatChapterWriter:
    """真实章节生成器：复用 OpenAICompatProvider 的 JSON 通道、重试与错误分类。"""

    model_id: str
    prompt_version = CHAPTER_PROMPT_VERSION

    def __init__(self, provider: OpenAICompatProvider) -> None:
        """包装已配置 key 的拆书 provider（与骨架生成共用鉴权/传输栈）。"""
        self._provider = provider
        self.model_id = f"openai-compat:{provider.model}"

    def write_chapter(self, pack: ContextPack) -> ChapterWriting:
        """请求章节 JSON 并严格校验正文与自评；非法结构按响应错误处理。"""
        messages = [
            {"role": "system", "content": CHAPTER_SYSTEM_MESSAGE},
            {"role": "user", "content": chapter_user_message(pack)},
        ]
        # 同包复用内部 JSON 通道，避免重复造传输栈
        data = self._provider._chat_json(messages)  # noqa: SLF001
        try:
            content = str(data["content"]).strip()
            if not content:
                raise ValueError("content 为空")
            review_data = dict(data["review"])
            # 来源字段以本地 writer 为准，丢弃模型可能回传的同名字段防冲突
            review_data.pop("model_id", None)
            review_data.pop("prompt_version", None)
            review = ChapterSelfReview(
                **review_data,
                model_id=self.model_id,
                prompt_version=self.prompt_version,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderResponseError(f"章节生成 JSON 不合法：{exc}") from exc
        return ChapterWriting(content=content, review=review)
