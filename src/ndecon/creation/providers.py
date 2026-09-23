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
        self, title: str, genre: str, premise: str, reference_stats: list[dict]
    ) -> CreationDraft:
        """一次产出完整骨架（五个部件）。"""
        ...


def _draft_from_dict(data: dict, model_id: str, prompt_version: str) -> CreationDraft:
    """把模型 JSON 严格转换为 CreationDraft；章节固定 10 条、顺序 1-10。

    任一部件字段不合法直接抛错（pydantic ValidationError 上抛），
    防止半成品骨架写入工作区。
    """
    chapters = [ChapterOutline(**item) for item in data.get("chapters", [])]
    orders = [c.order for c in chapters]
    if orders != list(range(1, 11)):
        raise ValueError(f"细纲必须恰好覆盖第 1-10 章，实际：{orders}")
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

    def generate_draft(
        self, title: str, genre: str, premise: str, reference_stats: list[dict]
    ) -> CreationDraft:
        """用确定性模板生成五部件；金手指代价为强制字段，模板也必须给出。"""
        genre_name = genre or "都市异能"
        top_themes: list[str] = []
        if reference_stats:
            stats = reference_stats[0].get("themes", {})
            top_themes = sorted(stats, key=stats.get, reverse=True)[:2]
        theme_hint = "、".join(top_themes) if top_themes else "成长"

        positioning = Positioning(
            genre=genre_name,
            audience=f"{genre_name}核心读者，偏好强钩子与明确升级线",
            core_premise=premise or f"《{title}》：普通人被卷入异常世界，靠独门优势破局。",
            selling_points=[
                f"开篇三章即抛出核心冲突（参考高频主题：{theme_hint}）",
                "金手指带硬代价，强弱节奏可控",
            ],
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
                ending_hook="新的异常信号出现" if i < 10 else "卷尾：更大势力现身",
            )
            for i in range(1, 11)
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
        """确定性的十步事件模板：遭遇→认知→试探→危机→反击→代价→立足→新敌→升级→卷尾。"""
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
        return events[order - 1]

    @staticmethod
    def _chapter_hook(order: int) -> str:
        """确定性的章首钩子模板。"""
        return f"以反常现象或一句致命对白开场（第{order}章）"


class OpenAICompatCreator:
    """真实创作器：复用 OpenAICompatProvider 的 JSON 通道与重试。"""

    model_id: str
    prompt_version = prompts.PROMPT_VERSION

    def __init__(self, provider: OpenAICompatProvider) -> None:
        """包装一个已配置 key 的拆书 provider（共用鉴权/传输/诊断）。"""
        self._provider = provider
        self.model_id = f"openai-compat:{provider.model}"

    def generate_draft(
        self, title: str, genre: str, premise: str, reference_stats: list[dict]
    ) -> CreationDraft:
        """请求完整骨架 JSON 并严格校验；非 JSON 或结构缺失按响应/schema 错误处理。"""
        messages = [
            {"role": "system", "content": prompts.SYSTEM_MESSAGE},
            {
                "role": "user",
                "content": prompts.full_draft_user_message(title, genre, premise, reference_stats),
            },
        ]
        data = self._provider._chat_json(messages)  # noqa: SLF001（同包复用，避免重复传输栈）
        try:
            return _draft_from_dict(data, self.model_id, self.prompt_version)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderResponseError(f"创作骨架 JSON 不合法：{exc}") from exc
