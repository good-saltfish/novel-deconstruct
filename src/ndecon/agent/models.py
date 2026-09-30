"""Planner Agent 的数据模型：节拍、设定条目、动作、运行轨迹。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ndecon.creation.models import Beat, SettingEntry  # noqa: F401  再导出，工具层统一引用

AGENT_PROMPT_VERSION = "ag-v0"

# 任务类型
TaskType = Literal["expand_chapter", "add_settings"]

# 冲突三角角色（取自项目方法论：卡普曼三角；节拍 triangle 为宽松字符串列表）
TriangleRole = Literal["persecutor", "victim", "savior"]


class AgentAction(BaseModel):
    """模型一轮输出的动作（JSON 协议，不依赖厂商 function calling）。"""

    thought: str = Field(min_length=1, description="本行动机/推理，供 trace 审计")
    tool: str = Field(min_length=1)
    args: dict = Field(default_factory=dict)


class ToolObservation(BaseModel):
    """一次工具执行结果（成功或失败都可喂回模型）。"""

    ok: bool
    output: str = Field(description="模型可读的文本结果；失败时为截断后的错误")


class TraceStep(BaseModel):
    """运行轨迹中的一步（完整思考链，落盘供用户审计）。"""

    turn: int
    thought: str
    tool: str
    args: dict
    ok: bool
    observation: str


class AgentDrafts(BaseModel):
    """Agent 在一次 run 中累积提交的候选（只写候选，绝不确认）。"""

    beats: list[Beat] = Field(default_factory=list)
    settings: list[SettingEntry] = Field(default_factory=list)


class AgentRunResult(BaseModel):
    """一次 agent run 的最终结果。"""

    task: TaskType
    finished: bool = Field(description="是否正常调 finish；False=步数耗尽/死循环中断")
    stop_reason: str
    turns_used: int
    summary: str = Field(default="", description="finish 时的汇报（查询路径/发现的矛盾）")
    drafts: AgentDrafts
    trace: list[TraceStep]
