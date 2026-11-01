"""语义层：把逐章正文与"逐章简报"交给真实模型，产出可迁移的零部件。

与规则层的分工（这条边界是硬性的，不是风格偏好）：
- 规则层（pipeline.py）只量词面：句长、对话占比、疑似冲突点坐标。**它不懂语义**。
- 本层只做语义：核心事件、冲突三角、章末钩子、全书骨架、可迁移手法。

两阶段（逐章 → 全景）不是"为了省 token"这种小理由，而是两条硬约束：
1. 一次喂 40 章原文必然超出上下文预算，模型会用幻觉水词填满它没看到的部分；
2. **全景阶段的输入里没有任何第三方正文**——只喂我方自己的逐章转述简报。
   版权红线因此是物理成立的，而不是靠"提醒模型别摘录"这种君子协定。

失败策略（重要）：单章失败**记 warning 并跳过该章**，不中断全书；
因为 40 章里坏 1 章，作者仍拿到 39 章可用产物；坏 1 章就让整次跑失败，是拿洁癖换可用性。
但**全部章都失败必须抛错**——那时产物是空的，静默返回空报告才真正危险。
"""

from __future__ import annotations

from ndecon.deconstruct import prompts
from ndecon.deconstruct import quotes as quote_audit
from ndecon.deconstruct.models import (
    MAX_QUOTE_CHARS,
    ChapterDeconstruction,
    PanoramaDeconstruction,
    TriangleItem,
)
from ndecon.domain.ids import chapter_id, content_hash
from ndecon.providers.errors import ProviderError

# 单章三角数量上限：超过即截断（模型偶尔会拆出十几个碎三角，反而不可用）
MAX_TRIANGLES_PER_CHAPTER = 7


class DeconstructionError(RuntimeError):
    """语义层失败：消息面向使用者，可直接显示。"""


class OpenAICompatDeconstructor:
    """走 OpenAI 兼容接口的语义层拆解器（使用者自带 key，项目不内置凭据）。"""

    def __init__(self, provider, *, model_label: str = "") -> None:
        """provider 需具备 `_chat_json(messages)`；model_label 用于产物溯源。"""
        self._provider = provider
        self.model_id = model_label or f"openai-compat:{getattr(provider, 'model', 'unknown')}"

    # ---------- 单章 ----------

    def deconstruct_chapter(self, order: int, title: str, chapter_text: str) -> ChapterDeconstruction:
        """拆一章：核心事件 + 冲突三角 + 章末钩子 + 点评。"""
        body = chapter_text
        warnings: list[str] = []
        if len(body) > prompts.MAX_CHAPTER_CHARS:
            body = body[: prompts.MAX_CHAPTER_CHARS]
            warnings.append(
                f"第{order}章正文 {len(chapter_text)} 字超过 {prompts.MAX_CHAPTER_CHARS} 字上限，"
                "已截断后拆解（结论可能不完整，请人工复核章末）"
            )

        try:
            data = self._chat(
                prompts.SYSTEM_MESSAGE, prompts.chapter_user_message(order, title, body)
            )
        except ProviderError as exc:
            raise DeconstructionError(f"第{order}章拆解失败：{exc}") from exc

        try:
            triangles = [
                TriangleItem(
                    kind=str(item.get("kind", "")).strip() or "未分类",
                    name=str(item.get("name", "")).strip(),
                    persecutor=str(item.get("persecutor", "")).strip() or "（待确认）",
                    victim=str(item.get("victim", "")).strip() or "（待确认）",
                    savior=str(item.get("savior", "")).strip(),
                    effect=str(item.get("effect", "")).strip(),
                )
                for item in (data.get("triangles") or [])[:MAX_TRIANGLES_PER_CHAPTER]
            ]
            chapter = ChapterDeconstruction(
                order=order,
                title=title or f"第{order}章",
                core_event=self._clip_narrative(str(data.get("core_event", "")).strip()),
                triangles=triangles,
                end_hook=str(data.get("end_hook", "")).strip(),
                highlights=[str(h).strip() for h in (data.get("highlights") or []) if str(h).strip()],
                emotion_track=str(data.get("emotion_track", "")).strip(),
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise DeconstructionError(f"第{order}章模型返回字段不合法：{exc}") from exc

        self._verify_field(chapter, "core_event", body, order, warnings)
        self._verify_field(chapter, "end_hook", body, order, warnings)

        if not chapter.core_event:
            warnings.append(f"第{order}章未产出核心事件（模型返回缺 core_event）")
        if not chapter.triangles:
            warnings.append(f"第{order}章未产出任何冲突三角（模型返回缺 triangles）")
        chapter.warnings.extend(warnings)
        return chapter

    @staticmethod
    def _verify_field(
        chapter: ChapterDeconstruction,
        field: str,
        body: str,
        order: int,
        warnings: list[str],
    ) -> None:
        """就地体检一个叙述字段：过长虚构引文会被移除/替换为占位，并落成可复核锚点。"""
        value = getattr(chapter, field)
        if not value:
            return
        cleaned, anchors, local = quote_audit.audit_narrative(
            value,
            chapter_text=body,
            source_id=f"bk:{chapter_id(order)}",
            content_hash=content_hash(body),
        )
        chapter.anchors.extend(anchors)
        if local:
            setattr(chapter, field, cleaned)
            warnings.extend(f"第{order}章{field}：{item}" for item in local)

    # ---------- 全书骨架 ----------

    def deconstruct_panorama(
        self,
        book_title: str,
        range_label: str,
        chapters: list[ChapterDeconstruction],
        total_chars: int = 0,
    ) -> PanoramaDeconstruction:
        """在逐章转述的基础上产出全书骨架（输入不含任何第三方正文）。"""
        digest = [chapter.model_dump() for chapter in chapters]
        try:
            data = self._chat(
                prompts.SYSTEM_MESSAGE,
                prompts.panorama_user_message(book_title, range_label, digest, total_chars),
            )
        except ProviderError as exc:
            raise DeconstructionError(f"全景拆书失败：{exc}") from exc

        key_setting = data.get("key_setting") or {}
        nodes = data.get("plot_nodes") or []
        try:
            panorama = PanoramaDeconstruction(
                genre=str(data.get("genre", "")).strip(),
                tags=[str(t).strip() for t in (data.get("tags") or []) if str(t).strip()],
                mainline_open=str(data.get("mainline_open", "")).strip(),
                mainline_hidden=str(data.get("mainline_hidden", "")).strip(),
                key_setting={
                    "worldview": str(key_setting.get("worldview", "")).strip(),
                    "golden_finger": str(key_setting.get("golden_finger", "")).strip(),
                    "power_system": str(key_setting.get("power_system", "")).strip(),
                },
                main_characters=[
                    str(c).strip() for c in (data.get("main_characters") or []) if str(c).strip()
                ],
                plot_nodes=[
                    {
                        "index": int(node.get("index", i + 1)),
                        "node": str(node.get("node", "")).strip() or f"节点{i + 1}",
                        "chapters": str(node.get("chapters", "")).strip(),
                        "core_event": str(node.get("core_event", "")).strip() or "（未给出）",
                        "emotion_peak": str(node.get("emotion_peak", "")).strip(),
                    }
                    for i, node in enumerate(nodes)
                ],
                core_conflict=str(data.get("core_conflict", "")).strip(),
                driving_force=str(data.get("driving_force", "")).strip(),
                one_line_outline={
                    str(k): str(v)
                    for k, v in (data.get("one_line_outline") or {}).items()
                    if str(k).strip()
                },
                highlights=[str(h).strip() for h in (data.get("highlights") or []) if str(h).strip()],
                reusable_techniques=[
                    str(t).strip()
                    for t in (data.get("reusable_techniques") or [])
                    if str(t).strip()
                ],
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise DeconstructionError(f"全景拆书字段不合法：{exc}") from exc
        return panorama

    # ---------- 内部 ----------

    def _chat(self, system: str, user: str) -> dict:
        """发起一次 JSON 对话；把 provider 异常统一成可展示的 DeconstructionError。"""
        try:
            return self._provider._chat_json(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - provider 异常面很宽，兜底只为可读
            raise DeconstructionError(f"模型调用失败：{type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _clip_narrative(value: str) -> str:
        """对叙述字段做长度兜底：**截断并记录，不静默**。

        模型偶尔会把整段原文当 core_event 回吐。这里不试图"判断它是不是摘录"
        （判断不了），只保证单字段不会失控，并在调用方 warning 里留下痕迹。
        """
        limit = MAX_QUOTE_CHARS * 20
        return value[:limit]
