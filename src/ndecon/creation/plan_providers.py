"""设定/细纲两套候选的生成器：离线确定性版 + OpenAI 兼容真实版。

两套任务的学习方法不同（见 `plans.py` 的对照表），因此：
- 提示词、校验口径、失败判据都各自独立，不共用一份"万能" prompt；
- 产物一律是 `SettingPlan` / `OutlinePlan` **候选**，
  落到 `CreationProject` 前必须经作者显式确认（候选不代笔，冻结红线）。
"""

from __future__ import annotations

from typing import Protocol

from ndecon.creation import plans
from ndecon.creation.models import (
    Beat,
    ChapterBeatPlan,
    GoldenFingerSpec,
    OutlinePlan,
    SettingEntry,
    SettingPlan,
)
from ndecon.providers.errors import ProviderResponseError

# 一章节拍的上下限：少于 3 个说明没拆分，多于 10 个 Beat.order 装不下
MIN_BEATS_PER_CHAPTER = 3
MAX_BEATS_PER_CHAPTER = 10


class PlanError(RuntimeError):
    """候选生成失败：消息面向使用者，可直接显示。"""


class SettingPlanner(Protocol):
    """设定生成器协议。"""

    model_id: str
    prompt_version: str

    def plan_settings(
        self, title: str, genre: str, premise: str, knowledge_snippets: list[dict] | None = None
    ) -> SettingPlan:
        """产出设定候选；knowledge_snippets 是知识库命中的方法论片段。"""
        ...


class OutlinePlanner(Protocol):
    """细纲生成器协议。"""

    model_id: str
    prompt_version: str

    def plan_outline(
        self,
        title: str,
        genre: str,
        premise: str,
        confirmed_settings: list[dict] | None = None,
        knowledge_snippets: list[dict] | None = None,
        golden_finger: dict | None = None,
    ) -> OutlinePlan:
        """产出前 10 章节拍候选；已确认设定作为硬约束传入。"""
        ...


def _entry_from_dict(item: dict) -> SettingEntry:
    """把一条设定候选严格转成 SettingEntry；`entry_type` 不在受控词表即抛错。"""
    try:
        return SettingEntry(**item)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"设定条目字段不合法：{exc}") from exc


def _setting_plan_from_dict(data: dict, model_id: str, prompt_version: str) -> SettingPlan:
    entries = [_entry_from_dict(item) for item in (data.get("entries") or [])]
    finger_raw = data.get("golden_finger") or {}
    finger = GoldenFingerSpec(**finger_raw) if finger_raw else None
    if not entries and finger is None:
        raise PlanError("设定层没有产出任何条目，也没有金手指 —— 拒绝返回空候选")
    return SettingPlan(
        entries=entries,
        golden_finger=finger,
        notes=[str(n).strip() for n in (data.get("notes") or []) if str(n).strip()],
        model_id=model_id,
        prompt_version=prompt_version,
    )


def _beat_from_dict(item: dict) -> Beat:
    """把一个节拍候选严格转成 Beat（`Beat.order` 是章内序号，取值 1-10）。"""
    try:
        return Beat(**item)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"节拍字段不合法：{exc}") from exc


def _outline_plan_from_dict(data: dict, model_id: str, prompt_version: str) -> OutlinePlan:
    chapters: list[ChapterBeatPlan] = []
    for item in data.get("chapters") or []:
        try:
            order = int(item["order"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanError(f"章节候选缺合法 order：{exc}") from exc
        if not 1 <= order <= 10:
            raise PlanError(f"章号 {order} 越界：细纲只覆盖第 1-10 章")
        beats = [_beat_from_dict(beat) for beat in (item.get("beats") or [])]
        if not MIN_BEATS_PER_CHAPTER <= len(beats) <= MAX_BEATS_PER_CHAPTER:
            raise PlanError(
                f"第{order}章 {len(beats)} 个节拍：要求 "
                f"{MIN_BEATS_PER_CHAPTER}-{MAX_BEATS_PER_CHAPTER} 个"
            )
        # 章内节拍序号若是乱的，按序号排序并重排为 1..n，
        # 避免拿着一份顺序错乱的稿子去写正文
        beats = sorted(beats, key=lambda b: b.order)
        for index, beat in enumerate(beats, start=1):
            beat.order = index
        chapters.append(
            ChapterBeatPlan(
                order=order,
                beats=beats,
                emotion_track=str(item.get("emotion_track", "")).strip(),
            )
        )
    if not chapters:
        raise PlanError("细纲层没有产出任何章节 —— 拒绝返回空候选")
    return OutlinePlan(
        chapters=chapters,
        notes=[str(n).strip() for n in (data.get("notes") or []) if str(n).strip()],
        model_id=model_id,
        prompt_version=prompt_version,
    )


def _knowledge_learned(snippets: list[dict] | None) -> list[str]:
    """生成"实际借鉴了哪条方法论"的记录行；离线模板如实说清它用了什么。"""
    snippets = snippets or []
    if not snippets:
        return ["未使用：本地知识库无命中（先运行 ndecon kb index 建立知识库）"]
    top = snippets[0]
    source = top.get("source", "本地知识库")
    heading = top.get("heading") or ""
    used = (top.get("text", "") or "")[:60]
    return [f"离线模板已引用 1 条方法论：{source}《{heading}》——{used}"]


class FakeSettingPlanner:
    """离线确定性设定生成器：不联网，产物可重放；内容是**结构示范**，需作者改写。"""

    model_id = "rule:fake-setting"
    prompt_version = plans.SETTING_PROMPT_VERSION

    def plan_settings(
        self, title: str, genre: str, premise: str, knowledge_snippets: list[dict] | None = None
    ) -> SettingPlan:
        """给出覆盖 4 个类型的设定骨架，每条都带'违反会怎样'。"""
        del title, premise  # 离线模板不消费这些，仅保持与真实生成器同签名
        genre_name = genre or "都市异能"
        return _setting_plan_from_dict(
            {
                "entries": [
                    {
                        "entry_type": "world_rule",
                        "name": "异常不可直视",
                        "content": (
                            f"{genre_name}世界的异常存在无法被直视，只能通过倒影或录像观测；"
                            "违反者会在 24 小时内被对方记住气息。这条规则让主角每次侦查都要付时间成本"
                        ),
                        "tags": ["观测限制", "成本"],
                        "related_chapters": [1, 3],
                    },
                    {
                        "entry_type": "power_system",
                        "name": "三档适应者",
                        "content": (
                            "适应者分低中高三档，每跨一档需完成一次不可能的生还；"
                            "越档者会被同档排斥，因此升级即失援"
                        ),
                        "tags": ["阶梯", "升级代价"],
                        "related_chapters": [4, 8],
                    },
                    {
                        "entry_type": "faction",
                        "name": "登记处",
                        "content": (
                            "官方登记处以'登记换庇护'吸纳适应者；拒绝登记者可以自由行动，"
                            "但失去一切公开资源渠道"
                        ),
                        "tags": ["势力博弈"],
                        "related_chapters": [2, 6],
                    },
                    {
                        "entry_type": "item",
                        "name": "记痕胶卷",
                        "content": (
                            "能记录异常气息的胶卷，每张只够一次完整记录且无法复制；"
                            "用完就少一次机会"
                        ),
                        "tags": ["稀缺资源"],
                        "related_chapters": [3, 7],
                    },
                ],
                "golden_finger": {
                    "name": "气息回放",
                    "form": "物品（绑定胶卷）",
                    "ability": "重放任意一次已记录现场的最后十秒，包括当事人看不见的角度",
                    "cost_limitation": (
                        "每次回放消耗一张胶卷，且回放者的气味会留在现场；"
                        "每日上限三次，第四次起使用者会咳出黑色胶片碎片"
                    ),
                    "growth_path": (
                        "一阶十秒→二阶三十秒（代价：回放期间本人失去视觉）→"
                        "三阶可回放他人记忆（代价：每次流失一段自己的记忆）"
                    ),
                },
                "notes": _knowledge_learned(knowledge_snippets),
            },
            model_id=self.model_id,
            prompt_version=self.prompt_version,
        )


class FakeOutlinePlanner:
    """离线确定性细纲生成器：每章 3 个节拍的示范节奏，需作者改写。"""

    model_id = "rule:fake-outline"
    prompt_version = plans.OUTLINE_PROMPT_VERSION

    def plan_outline(
        self,
        title: str,
        genre: str,
        premise: str,
        confirmed_settings: list[dict] | None = None,
        knowledge_snippets: list[dict] | None = None,
        golden_finger: dict | None = None,
    ) -> OutlinePlan:
        """产出第 1-3 章的节拍示范；后面的章留给作者按同一节奏补。"""
        del title, premise
        genre_name = genre or "都市异能"
        constraint = (confirmed_settings or [{}])[0].get("name", "") if confirmed_settings else ""
        finger_name = (golden_finger or {}).get("name", "气息回放")
        scene = f"{genre_name}：旧录像厅" if not constraint else f"{constraint}覆盖下的街区"
        return _outline_plan_from_dict(
            {
                "chapters": [
                    {
                        "order": 1,
                        "emotion_track": "常→疑→压",
                        "beats": [
                            {
                                "order": 1,
                                "scene": scene,
                                "characters": ["主角", "放映员"],
                                "triangle": ["异常存在", "放映员", ""],
                                "emotion": "疑",
                                "event": "主角在放映员的登记簿上看见自己的名字已被划掉",
                                "plant_foreshadow": "登记簿被撕掉的那一页日期是未来",
                            },
                            {
                                "order": 2,
                                "scene": scene,
                                "characters": ["主角", "登记处职员"],
                                "triangle": ["登记处职员", "主角", "放映员"],
                                "emotion": "压",
                                "event": "登记处职员当场要求他三日内重新登记，否则按流落者处理",
                            },
                            {
                                "order": 3,
                                "scene": "回家路上",
                                "characters": ["主角"],
                                "triangle": ["未知观察者", "主角", ""],
                                "emotion": "压",
                                "event": f"他第一次动用{finger_name}，看清被人跟了三条街",
                                "plant_foreshadow": "跟踪者走路的姿势和他一模一样",
                            },
                        ],
                    },
                    {
                        "order": 2,
                        "emotion_track": "压→转→疑",
                        "beats": [
                            {
                                "order": 1,
                                "scene": "登记大厅",
                                "characters": ["主角", "登记处职员"],
                                "triangle": ["登记流程", "主角", ""],
                                "emotion": "压",
                                "event": "他排到窗口才发现登记要用一张从未听说的凭证",
                            },
                            {
                                "order": 2,
                                "scene": "大厅角落",
                                "characters": ["主角", "陌生同行"],
                                "triangle": ["陌生同行", "主角", "主角本人"],
                                "emotion": "转",
                                "event": "陌生同行替他补上凭证，代价是替对方记一次痕",
                            },
                            {
                                "order": 3,
                                "scene": "胶片室",
                                "characters": ["主角"],
                                "triangle": ["限量胶卷", "主角", ""],
                                "emotion": "疑",
                                "event": "他发现自己的三次机会里已经少了一次，却想不起何时用过",
                                "plant_foreshadow": "少掉的那次记录写着别人的人生",
                            },
                        ],
                    },
                    {
                        "order": 3,
                        "emotion_track": "疑→爽→再压",
                        "beats": [
                            {
                                "order": 1,
                                "scene": "旧录像厅后门",
                                "characters": ["主角", "放映员"],
                                "triangle": ["放映员", "主角", ""],
                                "emotion": "疑",
                                "event": "放映员承认划掉名字是替他躲债，但拒绝说债主是谁",
                            },
                            {
                                "order": 2,
                                "scene": "追踪途中",
                                "characters": ["主角", "跟踪者"],
                                "triangle": ["跟踪者", "主角", "主角本人"],
                                "emotion": "爽",
                                "event": "他用回放抢先半步绕到跟踪者背后，第一次占了上风",
                            },
                            {
                                "order": 3,
                                "scene": "镜子前",
                                "characters": ["主角"],
                                "triangle": ["镜中人", "主角", ""],
                                "emotion": "再压",
                                "event": "镜子里的人比他晚半秒抬手，说明回放记录的不是过去",
                                "plant_foreshadow": "镜中人的手上也有同样的胶片灼痕",
                            },
                        ],
                    },
                ],
                "notes": _knowledge_learned(knowledge_snippets)
                + ["离线模板节奏配比：第1章压、第2章给转机、第3章先给爽点再落新压"],
            },
            model_id=self.model_id,
            prompt_version=self.prompt_version,
        )


class OpenAICompatSettingPlanner:
    """真实设定生成器：走 plans.SETTING_SYSTEM_MESSAGE 的收敛式 prompt。"""

    model_id: str
    prompt_version = plans.SETTING_PROMPT_VERSION

    def __init__(self, provider) -> None:
        self._provider = provider
        self.model_id = f"openai-compat:{getattr(provider, 'model', 'unknown')}"

    def plan_settings(
        self, title: str, genre: str, premise: str, knowledge_snippets: list[dict] | None = None
    ) -> SettingPlan:
        messages = [
            {"role": "system", "content": plans.SETTING_SYSTEM_MESSAGE},
            {
                "role": "user",
                "content": plans.setting_user_message(title, genre, premise, knowledge_snippets),
            },
        ]
        data = self._chat(messages)
        try:
            return _setting_plan_from_dict(data, self.model_id, self.prompt_version)
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanError(f"设定候选不合法：{exc}") from exc

    def _chat(self, messages: list[dict]) -> dict:
        try:
            return self._provider._chat_json(messages)  # noqa: SLF001
        except ProviderResponseError:
            raise
        except Exception as exc:  # pragma: no cover - provider 异常面很宽，兜底只为可读
            raise PlanError(f"设定生成失败：{type(exc).__name__}: {exc}") from exc


class OpenAICompatOutlinePlanner:
    """真实细纲生成器：走 plans.OUTLINE_SYSTEM_MESSAGE 的铺开式 prompt。"""

    model_id: str
    prompt_version = plans.OUTLINE_PROMPT_VERSION

    def __init__(self, provider) -> None:
        self._provider = provider
        self.model_id = f"openai-compat:{getattr(provider, 'model', 'unknown')}"

    def plan_outline(
        self,
        title: str,
        genre: str,
        premise: str,
        confirmed_settings: list[dict] | None = None,
        knowledge_snippets: list[dict] | None = None,
        golden_finger: dict | None = None,
    ) -> OutlinePlan:
        messages = [
            {"role": "system", "content": plans.OUTLINE_SYSTEM_MESSAGE},
            {
                "role": "user",
                "content": plans.outline_user_message(
                    title,
                    genre,
                    premise,
                    confirmed_settings,
                    knowledge_snippets,
                    golden_finger=golden_finger,
                ),
            },
        ]
        data = self._chat(messages)
        try:
            return _outline_plan_from_dict(data, self.model_id, self.prompt_version)
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanError(f"细纲候选不合法：{exc}") from exc

    def _chat(self, messages: list[dict]) -> dict:
        try:
            return self._provider._chat_json(messages)  # noqa: SLF001
        except ProviderResponseError:
            raise
        except Exception as exc:  # pragma: no cover - provider 异常面很宽，兜底只为可读
            raise PlanError(f"细纲生成失败：{type(exc).__name__}: {exc}") from exc
