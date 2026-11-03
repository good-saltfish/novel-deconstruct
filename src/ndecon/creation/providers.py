"""创作 Provider：FakeCreator（离线确定性）与 OpenAICompatCreator。

两者产出同一套 CreationDraft；Fake 是规则模板演示（标记 rule:fake），
真实生成走 OpenAI 兼容接口，复用拆书侧的传输/重试/错误分类，不另造 HTTP 栈。
"""

from __future__ import annotations

from typing import Protocol

from ndecon.creation import prompts
from ndecon.creation.models import (
    ChapterOutline,
    CreationDraft,
    GoldenFingerSpec,
    Positioning,
    ProtagonistProfile,
    VolumeOutline,
)
from ndecon.providers.errors import ProviderResponseError
from ndecon.providers.openai_compat import OpenAICompatProvider


class Creator(Protocol):
    """创作生成器协议。"""

    model_id: str
    prompt_version: str

    def generate_draft(
        self,
        title: str,
        genre: str,
        premise: str,
        reference_stats: list[dict],
        knowledge_snippets: list[dict] | None = None,
    ) -> CreationDraft:
        """一次产出完整骨架（五个部件）；knowledge_snippets 为本地知识库方法论片段。"""
        ...


PART_LABELS = {
    "positioning": "定位",
    "volume": "卷纲",
    "protagonist": "主角",
    "golden_finger": "金手指",
}


def _draft_from_dict(data: dict, model_id: str, prompt_version: str) -> CreationDraft:
    """把模型 JSON 严格转换为 CreationDraft；章节必须从 1 起连续递增。

    任一部件字段不合法直接抛错（pydantic ValidationError 上抛），
    防止半成品骨架写入工作区。

    缺部件必须报「缺哪一块」，不能把 `KeyError: 'protagonist'` 直接抛给用户：
    那句话只说明字典里没这个键，既不告诉人缺的是什么，也看不出该重试还是换模型。
    """
    if not isinstance(data, dict):
        raise ValueError(f"模型返回的不是 JSON 对象，而是 {type(data).__name__}")
    chapters = [ChapterOutline(**item) for item in data.get("chapters", [])]
    orders = [c.order for c in chapters]
    if not chapters:
        raise ValueError("细纲至少需要 1 章")
    if orders != list(range(1, len(chapters) + 1)):
        raise ValueError(f"细纲章号必须从 1 起连续递增，实际：{orders}")
    missing = [
        f"{part}（{PART_LABELS[part]}）"
        for part in ("positioning", "volume", "protagonist", "golden_finger")
        if not isinstance(data.get(part), dict)
    ]
    if missing:
        raise ValueError(f"缺少部件：{'、'.join(missing)}")
    draft = CreationDraft(
        positioning=Positioning(**data["positioning"]),
        volume=VolumeOutline(**data["volume"]),
        chapters=chapters,
        protagonist=ProtagonistProfile(**data["protagonist"]),
        golden_finger=GoldenFingerSpec(**data["golden_finger"]),
        provenance={
            part: model_id
            for part in ("positioning", "volume", "chapters", "protagonist", "golden_finger")
        },
    )
    _ = prompt_version
    return draft


class FakeCreator:
    """离线确定性创作器：由设定与参考统计拼装模板骨架，可重放、无网络。"""

    model_id = "rule:fake-creator"
    prompt_version = prompts.PROMPT_VERSION

    def __init__(self, chapter_count: int = 10) -> None:
        """chapter_count：生成多少章细纲；默认 10 与旧行为一致，可调大做长跑演练。"""
        if chapter_count < 1:
            raise ValueError(f"chapter_count 必须 ≥1，实际：{chapter_count}")
        self.chapter_count = chapter_count

    def generate_draft(
        self,
        title: str,
        genre: str,
        premise: str,
        reference_stats: list[dict],
        knowledge_snippets: list[dict] | None = None,
    ) -> CreationDraft:
        """用确定性模板生成五部件；金手指代价为强制字段，模板也必须给出。

        知识库片段（方法论，已过滤版权原文）确定性地追加一条带来源的卖点提示。
        """
        genre_name = genre or "都市异能"
        top_themes: list[str] = []
        if reference_stats:
            stats = reference_stats[0].get("themes", {})
            top_themes = sorted(stats, key=stats.get, reverse=True)[:2]
        theme_hint = "、".join(top_themes) if top_themes else "成长"

        selling_points = [
            f"开篇三章即抛出核心冲突（参考高频主题：{theme_hint}）",
            "金手指带硬代价，强弱节奏可控",
        ]
        knowledge_snippets = knowledge_snippets or []
        if knowledge_snippets:
            top = knowledge_snippets[0]
            source = top.get("source", "本地知识库")
            heading = top.get("heading") or ""
            method_hint = (top.get("text", "") or "")[:60]
            label = f"《{heading}》" if heading else method_hint
            selling_points.append(f"方法论参考（{source}）：{label}——开篇按该手法落地钩子")

        positioning = Positioning(
            genre=genre_name,
            audience=f"{genre_name}核心读者，偏好强钩子与明确升级线",
            core_premise=premise or f"《{title}》：普通人被卷入异常世界，靠独门优势破局。",
            selling_points=selling_points,
            tone_target="紧张为主，爽点章距稳定在 2-3 章",
        )
        volume = VolumeOutline(
            volume_title="第一卷·新手局",
            volume_goal="主角认清规则、活过第一次危机并拿到立足资本",
            main_conflict="主角的求生目标与异常世界的运行规则正面冲突",
            ending_hook="更大势力登场，主角被迫离开安全区",
        )
        chapters = [
            ChapterOutline(
                order=i,
                title=f"第{i}步",
                core_event=self._chapter_event(i),
                opening_hook=self._chapter_hook(i),
                ending_hook=(
                    self._chapter_ending_hook(i)
                    if i < self.chapter_count
                    else "卷尾：更大势力现身"
                ),
            )
            for i in range(1, self.chapter_count + 1)
        ]
        protagonist = ProtagonistProfile(
            name="陆沉（占位名，可改）",
            background="普通职业出身，灾变时意外存活",
            personality="冷静务实，遇事先算代价",
            desire="在新世界活下去并查清灾变成因",
            flaw="过度依赖理性算计，关键时刻容易低估情感羁绊",
            signature_ability="在高压下快速拆解规则、组织资源",
        )
        golden_finger = GoldenFingerSpec(
            name="规则视界",
            form="被动感知型天赋",
            ability="能看到异常事物的部分运行规则与成功概率",
            cost_limitation="每次使用消耗精力并会被异常存在感知，一日三次为上限",
            growth_path="从只读规则，到能小范围改写规则，但代价同步升级",
        )
        draft = _draft_from_dict(
            {
                "positioning": positioning.model_dump(),
                "volume": volume.model_dump(),
                "chapters": [c.model_dump() for c in chapters],
                "protagonist": protagonist.model_dump(),
                "golden_finger": golden_finger.model_dump(),
            },
            model_id=self.model_id,
            prompt_version=self.prompt_version,
        )
        return draft

    @staticmethod
    def _chapter_event(order: int) -> str:
        """确定性的十步事件模板：遭遇→认知→试探→危机→反击→代价→立足→新敌→升级→卷尾。

        超过 10 章时按十步循环（第 11 章回到第 1 步的变体），保证长跑演练不越界。
        """
        events = [
            "灾变降临，主角死里逃生并初次发现异常",
            "确认世界规则变化，被迫离开熟悉环境",
            "第一次主动试探金手指，吃到信息红利",
            "遭遇首个强力威胁，金手指暴露副作用",
            "被卷入官方/势力冲突，做出关键选择",
            "为获胜付出沉重代价（资源/关系/身份）",
            "收拢第一批同伴或立足资源",
            "隐藏敌人浮出水面，旧日因果回收",
            "金手指首次升级，主角确立短期目标",
            "卷尾对决惨胜，更大世界的门被打开",
        ]
        cycle, step = divmod(order - 1, len(events))
        suffix = f"（第{cycle + 1}轮）" if cycle else ""
        return f"{events[step]}{suffix}"

    @staticmethod
    def _chapter_hook(order: int) -> str:
        """确定性的章首钩子模板。"""
        return f"以反常现象或一句致命对白开场（第{order}章）"

    @staticmethod
    def _chapter_ending_hook(order: int) -> str:
        """章尾钩按十步循环轮转。

        原来 30 章共用同一句「新的异常信号出现」，导致伏笔台账里 29 条一模一样的
        记录；离线写手把它们连着列出来时必然撞 ``repeat_span``（12 字片段重复 4 次）。
        这是假数据的缺陷，不是闸门太严——闸门正确地拦住了它。
        """
        hooks = [
            "新的异常信号出现",
            "旧识带着伤找上门",
            "金手指忽然失灵一瞬",
            "有人开始怀疑主角的身份",
            "代价第一次真正落在旁人身上",
            "势力递来一个无法拒绝的条件",
            "同伴的选择出现裂痕",
            "被掩盖的旧案露出一角",
            "能力升级伴随新的限制",
            "更大的门缝里透出光",
        ]
        cycle, step = divmod(order - 1, len(hooks))
        suffix = f"（第{cycle + 1}轮）" if cycle else ""
        return f"{hooks[step]}{suffix}"


class OpenAICompatCreator:
    """真实创作器：复用 OpenAICompatProvider 的 JSON 通道与重试。"""

    model_id: str
    prompt_version = prompts.PROMPT_VERSION

    def __init__(self, provider: OpenAICompatProvider, *, chapter_count: int = 10) -> None:
        """包装一个已配置 key 的拆书 provider（共用鉴权/传输/诊断）。"""
        if chapter_count < 1:
            raise ValueError(f"chapter_count 必须 ≥1，实际：{chapter_count}")
        self._provider = provider
        self.chapter_count = chapter_count
        self.model_id = f"openai-compat:{provider.model}"

    def generate_draft(
        self,
        title: str,
        genre: str,
        premise: str,
        reference_stats: list[dict],
        knowledge_snippets: list[dict] | None = None,
    ) -> CreationDraft:
        """请求完整骨架 JSON 并严格校验；非 JSON 或结构缺失按响应/schema 错误处理。"""
        messages = [
            {"role": "system", "content": prompts.SYSTEM_MESSAGE},
            {
                "role": "user",
                "content": prompts.full_draft_user_message(
                    title, genre, premise, reference_stats, knowledge_snippets,
                    chapter_count=self.chapter_count,
                ),
            },
        ]
        # 同包复用内部 JSON 通道，避免重复造传输栈
        data = self._provider._chat_json(messages)  # noqa: SLF001
        try:
            return _draft_from_dict(data, self.model_id, self.prompt_version)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderResponseError(f"创作骨架 JSON 不合法：{exc}") from exc
