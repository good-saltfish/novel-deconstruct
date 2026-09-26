"""Planner 的两个执行器：FakePlanner（离线确定性脚本动作）与 OpenAICompatPlanner。"""

from __future__ import annotations

import json

from ndecon.agent.models import AGENT_PROMPT_VERSION
from ndecon.agent.runner import TaskType
from ndecon.providers.openai_compat import OpenAICompatProvider


def _act(thought: str, tool: str, args: dict | None = None) -> str:
    """把一个动作序列化为 Fake 脚本行。"""
    return json.dumps(
        {"thought": thought, "tool": tool, "args": args or {}}, ensure_ascii=False
    )


class FakePlanner:
    """离线规划器：按固定动作序列模拟真实 agent 的自主循环。

    序列体现 agent 的"思考"路径：查相邻细纲→查伏笔→查设定→查方法论→提交→finish；
    同一项目同一任务结果逐字一致，供 CI 与离线演示。
    """

    model_id = "rule:fake-planner"
    prompt_version = AGENT_PROMPT_VERSION

    def __init__(self, task: TaskType, chapter_order: int | None = None) -> None:
        """根据任务类型预生成动作脚本。"""
        self.task = task
        self.chapter_order = chapter_order or 1
        self._script = self._build_script()
        self._index = 0

    def _build_script(self) -> list[str]:
        """构造确定性动作 JSON 序列。"""
        n = self.chapter_order
        prev_order = max(1, n - 1)
        if self.task == "expand_chapter":
            beats = [
                {
                    "order": 1,
                    "scene": "承接上一章结尾的现场",
                    "characters": ["主角"],
                    "triangle": ["victim"],
                    "emotion": "悬念→紧张",
                    "event": "主角被异常现象逼到必须行动",
                },
                {
                    "order": 2,
                    "scene": "冲突升级点",
                    "characters": ["主角"],
                    "triangle": ["persecutor", "victim"],
                    "emotion": "紧张升级",
                    "event": "对手/异常显形并施压",
                },
                {
                    "order": 3,
                    "scene": "金手指使用现场",
                    "characters": ["主角"],
                    "triangle": ["persecutor", "victim", "savior"],
                    "emotion": "压抑→转机",
                    "event": "主角付出代价动用能力反击",
                    "plant_foreshadow": "能力副作用的隐患",
                },
                {
                    "order": 4,
                    "scene": "章尾新钩子",
                    "characters": ["主角"],
                    "triangle": ["persecutor"],
                    "emotion": "刚松一口气",
                    "event": "更大的威胁露头",
                },
            ]
            return [
                _act("先看上一章结尾，保证承接", "get_outline", {"order": prev_order}),
                _act("检查有哪些未回收伏笔要在本章照应", "list_open_foreshadows"),
                _act("确认金手指限制，节拍不能违反", "get_confirmed_setting",
                     {"part": "golden_finger"}),
                _act("检索节拍与冲突设计的方法论", "search_methodology",
                     {"query": "章节节拍 冲突三角 情绪"}),
                _act("按细纲核心事件提交 4 个节拍", "draft_beats", {"beats": beats}),
                _act("节拍覆盖细纲且未违反限制，结束", "finish",
                     {"summary": "已提交 4 个节拍：承接、冲突升级、代价反击、章尾钩子；金手指守限制。"}),
            ]

        faction = {
            "entry_type": "faction",
            "name": "管理异常秩序的官方机构",
            "content": (
                "负责封锁异常事件、登记与监管守灯人；"
                "与主角既合作又提防，是规则与自由的长期矛盾来源。"
            ),
            "tags": ["对立势力", "官方"],
            "related_chapters": [2, 3],
        }
        mentor = {
            "entry_type": "character",
            "name": "引路人前辈",
            "content": "知晓规则真相的老守灯人，前期提供指引，隐瞒着与主角能力有关的过去。",
            "tags": ["导师", "伏笔人物"],
            "related_chapters": [1],
        }
        return [
            _act("先看已确认的金手指，新设定不能冲突", "get_confirmed_setting",
                 {"part": "golden_finger"}),
            _act("检索势力组织设计的方法论", "search_methodology",
                 {"query": "势力 组织 对立 设计"}),
            _act("提交一个与金手指体系相关的对立势力", "draft_setting", {"entry": faction}),
            _act("再提交一个关键配角", "draft_setting", {"entry": mentor}),
            _act("两条设定与已确认内容不矛盾，结束", "finish",
                 {"summary": "已提交 2 条设定：对立官方机构与引路人前辈；均未与金手指限制冲突。"}),
        ]

    def next_action(self, messages: list[dict]) -> str:
        """按序返回脚本动作；脚本耗尽则强制 finish（防御，不应发生）。"""
        if self._index < len(self._script):
            raw = self._script[self._index]
            self._index += 1
            return raw
        return _act("脚本已耗尽", "finish", {"summary": "（Fake 脚本结束）"})


class OpenAICompatPlanner:
    """真实模型规划器：复用 OpenAI 兼容 JSON 通道，要求其只回一个动作 JSON。"""

    def __init__(self, provider: OpenAICompatProvider) -> None:
        """包装已配置的 provider（来自面板会话）。"""
        self._provider = provider
        self.model_id = f"openai-compat:{provider.model}"

    def next_action(self, messages: list[dict]) -> str:
        """请求 chat 接口返回下一个动作 JSON（system prompt 已强制只回 JSON 对象）。"""
        return self._provider._post_chat(messages)  # noqa: SLF001
