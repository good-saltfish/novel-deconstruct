"""Planner Agent 循环（ADR-0006）。

模型每轮产出一个 JSON 动作 -> 本地执行白名单工具 -> observation 回喂，直到 finish。
安全机制（clean-room 自 goink 实测设计）：
- MaxTurns 硬上限；
- 死循环检测：近 4 轮调用模式 ≤2 种且全为只读 -> 中断；
- 工具/JSON 错误不中断，截断后作为 observation 回喂自愈；
- 每步落 trace，结果与候选确定性地由工具层产出。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from ndecon.agent.models import (
    AgentAction,
    AgentDrafts,
    AgentRunResult,
    TaskType,
    TraceStep,
)
from ndecon.agent.tools import READ_ONLY_TOOLS, ToolContext, execute_tool, tool_catalog_text
from ndecon.workspace.models import CreationProject

MAX_TURNS = 12
STUCK_WINDOW = 4
STUCK_MAX_PATTERNS = 2
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _iter_json_candidates(text: str):
    """从每个 `{` 起做**真正的平衡括号**抽取（字符串感知），产出候选子串。

    容忍真实模型在 JSON 前后写废话、或废话里夹花括号（ARQs 式鲁棒）：
    逐个 `{` 作为起点扫描配对 `}`，字符串字面量内的括号不计入深度，
    未闭合的起点自动跳过。调用方对每个候选尝试 json.loads 取第一个合法 dict。
    """
    n = len(text)
    for start in range(n):
        if text[start] != "{":
            continue
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, n):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[start:i + 1]
                    break

PLANNER_SYSTEM = (
    "你是一个网文细纲与设定规划 agent。你在一个很窄的任务内工作：\n"
    "- 任务是 expand_chapter 时，先查相邻细纲、开放伏笔与已确认设定，再参考方法论，"
    "提交 3-6 个忠于细纲核心事件的节拍，最后 finish；\n"
    "- 任务是 add_settings 时，先查已确认设定避免冲突，再按需检索方法论，"
    "提交若干条结构化设定，最后 finish。\n"
    "硬性规则：只使用给定工具；不要写小说正文；不要臆造与已确认设定矛盾的内容；"
    "发现矛盾时不要自行修改，写进 finish 的汇报。\n"
    "每轮只回复一个 JSON 对象（不要 markdown 围栏、不要多余文字）：\n"
    '{"thought": "你这一步为什么这么做", "tool": "工具名", "args": {参数}}\n'
    "完成全部候选后用 {\"thought\": \"...\", \"tool\": \"finish\", \"args\": {\"summary\": \"汇报\"}} 结束。\n\n"
    + tool_catalog_text()
    + (
        "\n\n--- 运行约束（AgentGuide Context Builder 静态化）---\n"
        f"- 你最多有 {MAX_TURNS} 步，请尽早提交候选并 finish；\n"
        "- 若某轮工具报错，先读 [工具结果] 中的 hint，自己修正参数或换工具重试，不要重复同一错误；\n"
        "- 不要连续只读查询空转（死循环会被自动终止，已提交候选仍保留）；\n"
        "- 绝不调用不存在的工具，绝不写小说正文；所有产出只是候选，由用户确认后才入库。"
    )
)


class PlannerLLM(Protocol):
    """规划器模型协议：给定消息历史，返回下一个动作 JSON 字符串。"""

    model_id: str

    def next_action(self, messages: list[dict]) -> str:
        """产出动作 JSON 文本（Fake 返回脚本动作，真实 provider 调 LLM）。"""
        ...


def _task_brief(task: TaskType, chapter_order: int | None) -> str:
    """生成给模型的任务描述。"""
    if task == "expand_chapter":
        return f"任务：把第 {chapter_order} 章的细纲扩写为 3-6 个节拍。"
    return "任务：围绕当前题材补充结构化设定（势力/配角/地点/规则/道具/力量体系），提交 2-4 条。"


def _parse_action(raw: str) -> tuple[AgentAction | None, str | None]:
    """解析模型返回为动作；失败返回错误文本（回喂而非崩溃）。

    ARQs 式鲁棒：先去 markdown 围栏，再尝试直接解析；若仍失败，逐候选做字符串感知的
    平衡括号抽取（容忍真实模型在 JSON 前后写废话、或废话里夹花括号），取第一个
    解析为 dict 的对象。都没救才报结构化错误。
    """
    cleaned = _JSON_FENCE.sub("", raw.strip()).strip()
    data: object | None = None
    if cleaned:
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            data = None
    if data is None:
        for candidate in _iter_json_candidates(cleaned or raw):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                data = parsed
                break
    if not isinstance(data, dict):
        return (
            None,
            "你的回复不是合法 JSON 动作对象。请只回复一个 JSON 对象（不要 markdown 围栏、不要多余文字），"
            "形如 {\"thought\": \"...\", \"tool\": \"工具名\", \"args\": {...}}。",
        )
    try:
        return AgentAction.model_validate(data), None
    except ValidationError as exc:
        return None, f"动作字段不合法：{str(exc)[:200]}。请按工具 schema 补齐 thought/tool/args 后重试。"


def _tool_pattern(tool: str, args: dict) -> str:
    """生成单轮调用模式串（用于死循环检测；参数截断防扰动）。"""
    return f"{tool}:{json.dumps(args, ensure_ascii=False, sort_keys=True)[:100]}"


def _is_stuck_loop(patterns: list[str], tools: list[str]) -> bool:
    """近 STUCK_WINDOW 轮模式去重 ≤STUCK_MAX_PATTERNS 且全部只读，判定为空转。"""
    if len(patterns) < STUCK_WINDOW:
        return False
    recent = patterns[-STUCK_WINDOW:]
    if len(set(recent)) > STUCK_MAX_PATTERNS:
        return False
    return all(tool in READ_ONLY_TOOLS for tool in tools[-STUCK_WINDOW:])


def run_planner(
    *,
    llm: PlannerLLM,
    project: CreationProject,
    task: TaskType,
    workspace: str | Path,
    chapter_order: int | None = None,
    max_turns: int = MAX_TURNS,
) -> AgentRunResult:
    """执行 planner 循环，返回候选、trace 与停止原因（本函数不做任何持久化）。"""
    if task == "expand_chapter" and chapter_order is None:
        raise ValueError("expand_chapter 任务必须提供 chapter_order")

    drafts = AgentDrafts()
    ctx = ToolContext(
        project=project,
        task=task,
        chapter_order=chapter_order,
        workspace=Path(workspace),
        drafts=drafts,
    )
    messages: list[dict] = [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": _task_brief(task, chapter_order)},
    ]
    trace: list[TraceStep] = []
    patterns: list[str] = []
    tool_sequence: list[str] = []
    summary = ""
    stop_reason = "max_turns"
    total_latency = 0.0
    # AgentGuide Trace：只对真实模型计耗时；离线/Fake 路径保持 trace 确定性（不注入计时噪声）
    offline = getattr(llm, "offline", False)

    for turn in range(1, max_turns + 1):
        call_start = time.perf_counter()
        raw = llm.next_action(messages)
        if offline:
            elapsed_ms = None
        else:
            elapsed_ms = (time.perf_counter() - call_start) * 1000.0
            total_latency += elapsed_ms
        action, parse_error = _parse_action(raw)
        if parse_error is not None:
            # 解析失败作为一次"系统观察"回喂，不计工具模式（Reflexion：结构化错误促自愈）
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"[系统] {parse_error}"})
            trace.append(
                TraceStep(turn=turn, thought="<unparseable>", tool="<parse_error>", args={},
                          ok=False, observation=parse_error, latency_ms=elapsed_ms)
            )
            continue

        assert action is not None

        if action.tool == "finish":
            summary = str(action.args.get("summary", "")).strip()
            stop_reason = "finished"
            trace.append(
                TraceStep(turn=turn, thought=action.thought, tool="finish", args=action.args,
                          ok=True, observation="任务结束。", latency_ms=elapsed_ms)
            )
            messages.append({"role": "assistant", "content": raw})
            break

        ok, output, *_ = execute_tool(action.tool, action.args, ctx)
        trace.append(
            TraceStep(turn=turn, thought=action.thought, tool=action.tool, args=action.args,
                      ok=ok, observation=output, latency_ms=elapsed_ms)
        )
        patterns.append(_tool_pattern(action.tool, action.args))
        tool_sequence.append(action.tool)

        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": f"[工具结果]\n{output}"})

        # 死循环检测：只读空转
        if _is_stuck_loop(patterns, tool_sequence):
            stop_reason = "stuck_loop"
            # 反思式摘要：点明具体空转的只读工具，便于人工/模型定位
            stuck_tools = sorted({t for t in tool_sequence[-STUCK_WINDOW:] if t in READ_ONLY_TOOLS})
            tool_hint = "、".join(stuck_tools) if stuck_tools else "只读工具"
            summary = (
                f"检测到连续只读空转（近 {STUCK_WINDOW} 轮只在重复 {tool_hint}），自动停止；"
                "已提交的候选予以保留。请改用 draft_beats / draft_setting 提交候选并 finish。"
            )
            break
    else:
        summary = f"达到 {max_turns} 步上限仍未 finish；已提交的候选予以保留，请人工检查。"

    return AgentRunResult(
        task=task,
        finished=(stop_reason == "finished"),
        stop_reason=stop_reason,
        turns_used=len(trace),
        summary=summary,
        drafts=drafts,
        trace=trace,
        total_latency_ms=total_latency if total_latency > 0 else None,
    )
