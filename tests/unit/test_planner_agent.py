"""Planner Agent 测试（#25）：Fake 循环、工具边界、死循环/坏 JSON/步数/写候选权限。"""

import json

import pytest

from ndecon.agent.models import Beat, SettingEntry
from ndecon.agent.planners import FakePlanner
from ndecon.agent.runner import run_planner
from ndecon.agent.tools import READ_ONLY_TOOLS, TOOL_REGISTRY, execute_tool, tool_catalog_text
from ndecon.creation.models import (
    ChapterOutline,
    CreationDraft,
    GoldenFingerSpec,
    Positioning,
    ProtagonistProfile,
)
from ndecon.workspace.models import CreationProject, ProjectMeta
from ndecon.workspace.store import WorkspaceStore


def _project(store: WorkspaceStore) -> CreationProject:
    """构造已确认三章骨架的创作项目。"""
    return CreationProject(
        meta=ProjectMeta(id="creation-agent-1", kind="creation", title="灯火", created_at=store.now_iso()),
        draft=CreationDraft(
            positioning=Positioning(genre="都市异能", core_premise="路灯封着夜色"),
            protagonist=ProtagonistProfile(name="林盏", desire="守灯", flaw="过度理性"),
            golden_finger=GoldenFingerSpec(name="铜灯", cost_limitation="一日三燃"),
            chapters=[
                ChapterOutline(order=1, title="停电夜", core_event="发现路灯异常", ending_hook="周伯出现"),
                ChapterOutline(order=2, title="新守灯人", core_event="被登记守灯", ending_hook="井底抓挠声"),
                ChapterOutline(order=3, title="井里的东西", core_event="封住影兽", ending_hook="陈主任冷眼"),
            ],
        ),
        confirmed_parts={"positioning": True, "protagonist": True, "golden_finger": True, "chapters": True},
    )


def test_tool_catalog_and_permissions() -> None:
    """工具集 7 个（5 只读+2 写；finish 为协议动作不注册为工具）；无删除/确认/外网工具。"""
    assert len(TOOL_REGISTRY) == 7
    writable = {name for name in TOOL_REGISTRY if name not in READ_ONLY_TOOLS}
    assert writable == {"draft_beats", "draft_setting"}
    # 越权/外网工具不得出现（"confirm" 允许：get_confirmed_setting 是只读已确认设定）
    forbidden = ["delete", "write_chapter", "web_search", "http://", "update_", "remove", "approve"]
    catalog = tool_catalog_text()
    assert all(word not in catalog for word in forbidden)


def test_fake_expand_chapter_run(tmp_path) -> None:
    """Fake 扩章：走完查伏笔→方法论→提交 4 节拍→finish，trace 完整。"""
    store = WorkspaceStore(tmp_path)
    result = run_planner(
        llm=FakePlanner("expand_chapter", 3),
        project=_project(store),
        task="expand_chapter",
        workspace=tmp_path,
        chapter_order=3,
    )
    assert result.finished is True
    assert result.stop_reason == "finished"
    assert len(result.drafts.beats) == 4
    assert [b.order for b in result.drafts.beats] == [1, 2, 3, 4]
    tools = [s.tool for s in result.trace]
    assert tools[0] == "get_outline"
    assert "list_open_foreshadows" in tools and "search_methodology" in tools
    assert tools[-1] == "finish"
    # 第 2 章未回收伏笔被工具读到（thought 不强制，观察结果里要有内容）
    foreshadow_step = next(s for s in result.trace if s.tool == "list_open_foreshadows")
    assert "井底抓挠声" in foreshadow_step.observation


def test_fake_add_settings_run(tmp_path) -> None:
    """Fake 补设定：提交势力+配角两条候选并 finish。"""
    store = WorkspaceStore(tmp_path)
    result = run_planner(
        llm=FakePlanner("add_settings"),
        project=_project(store),
        task="add_settings",
        workspace=tmp_path,
    )
    assert result.finished is True
    assert len(result.drafts.settings) == 2
    types = {s.entry_type for s in result.drafts.settings}
    assert types == {"faction", "character"}


def test_fake_planner_deterministic(tmp_path) -> None:
    """同一任务两次 run 的动作序列与候选逐字一致。"""
    store = WorkspaceStore(tmp_path)
    project = _project(store)
    r1 = run_planner(llm=FakePlanner("expand_chapter", 2), project=project,
                     task="expand_chapter", workspace=tmp_path, chapter_order=2)
    r2 = run_planner(llm=FakePlanner("expand_chapter", 2), project=project,
                     task="expand_chapter", workspace=tmp_path, chapter_order=2)
    assert [s.model_dump() for s in r1.trace] == [s.model_dump() for s in r2.trace]
    assert r1.drafts.model_dump_json() == r2.drafts.model_dump_json()


class _ScriptedLLM:
    """测试用脚本 LLM：按给定文本序列返回动作。"""

    model_id = "scripted"

    def __init__(self, replies: list[str]) -> None:
        """记录回复序列。"""
        self._replies = replies
        self._i = 0

    def next_action(self, messages: list[dict]) -> str:
        """逐条返回；耗尽后返回 finish。"""
        if self._i < len(self._replies):
            text = self._replies[self._i]
            self._i += 1
            return text
        return json.dumps({"thought": "done", "tool": "finish", "args": {"summary": "end"}},
                          ensure_ascii=False)


def test_bad_json_is_fed_back_not_crashing(tmp_path) -> None:
    """模型回坏 JSON 不崩溃：错误作为观察回喂，模型随后可恢复。"""
    store = WorkspaceStore(tmp_path)
    llm = _ScriptedLLM([
        "我不是JSON",
        json.dumps({"thought": "恢复后结束", "tool": "finish", "args": {"summary": "ok"}},
                   ensure_ascii=False),
    ])
    result = run_planner(llm=llm, project=_project(store), task="add_settings", workspace=tmp_path,
                         max_turns=5)
    assert result.finished is True
    assert result.trace[0].tool == "<parse_error>"
    assert "合法 JSON" in result.trace[0].observation


def test_unknown_tool_and_bad_args_returned_as_observations(tmp_path) -> None:
    """未知工具/参数非法返回失败观察（不中断），模型看到后改路 finish。"""
    store = WorkspaceStore(tmp_path)
    llm = _ScriptedLLM([
        json.dumps({"thought": "瞎调", "tool": "rm_rf", "args": {}}, ensure_ascii=False),
        json.dumps({"thought": "参数错", "tool": "get_outline", "args": {"order": "bad"}},
                   ensure_ascii=False),
        json.dumps({"thought": "放弃", "tool": "finish", "args": {"summary": "ok"}},
                   ensure_ascii=False),
    ])
    result = run_planner(llm=llm, project=_project(store), task="add_settings", workspace=tmp_path,
                         max_turns=5)
    assert result.finished is True
    assert "未知工具" in result.trace[0].observation
    assert "参数不合法" in result.trace[1].observation


def test_stuck_loop_detected_for_readonly_spin(tmp_path) -> None:
    """连续 4 轮只读且调用模式 ≤2 种，判死循环中断并保留候选（本例无候选但正常停止）。"""
    store = WorkspaceStore(tmp_path)
    action_a = json.dumps({"thought": "看", "tool": "get_outline", "args": {"order": 1}},
                          ensure_ascii=False)
    action_b = json.dumps({"thought": "又看", "tool": "list_open_foreshadows", "args": {}},
                          ensure_ascii=False)
    llm = _ScriptedLLM([action_a, action_b, action_a, action_b,
                        json.dumps({"thought": "还想写", "tool": "draft_setting",
                                    "args": {"entry": {"entry_type": "item", "name": "x", "content": "y"}}},
                                   ensure_ascii=False)])
    result = run_planner(llm=llm, project=_project(store), task="add_settings", workspace=tmp_path,
                         max_turns=10)
    assert result.finished is False
    assert result.stop_reason == "stuck_loop"
    # 死循环在第 4 轮即被拦截，第 5 个写动作不会执行
    assert result.drafts.settings == []


def test_max_turns_preserves_submitted_drafts(tmp_path) -> None:
    """到上限未 finish：已提交的节拍保留，标记 incomplete。"""
    store = WorkspaceStore(tmp_path)
    submit = json.dumps({"thought": "先交节拍", "tool": "draft_beats", "args": {"beats": [
        Beat(order=1, scene="s", event="e").model_dump()
    ]}}, ensure_ascii=False)
    # 持续以 3 种不同只读模式磨蹭（模式足够多样，不触发死循环），永不 finish
    spin = [
        json.dumps({"thought": f"看{i}", "tool": tool, "args": args}, ensure_ascii=False)
        for i in range(20)
        for tool, args in (
            ("get_confirmed_setting", {"part": "volume"}),
            ("get_outline", {"order": (i % 3) + 1}),
            ("search_own_book", {"query": f"线索{i}"}),
        )
    ]
    llm = _ScriptedLLM([submit] + spin)
    result = run_planner(llm=llm, project=_project(store), task="expand_chapter",
                         workspace=tmp_path, chapter_order=2, max_turns=6)
    assert result.finished is False
    assert result.stop_reason == "max_turns"
    assert len(result.drafts.beats) == 1  # 已提交候选保留


def test_write_tool_rejects_invalid_beat(tmp_path) -> None:
    """节拍缺必填字段（event）时 execute_tool 返回参数错误，不会写入候选。"""
    from ndecon.agent.models import AgentDrafts
    from ndecon.agent.tools import ToolContext

    store = WorkspaceStore(tmp_path)
    drafts = AgentDrafts()
    ctx = ToolContext(project=_project(store), task="expand_chapter", chapter_order=2,
                      workspace=tmp_path, drafts=drafts)
    # order 越界（>12）被 schema 拦截
    ok, output = execute_tool("draft_beats", {"beats": [{"order": 99, "scene": "x", "event": "e"}]}, ctx)
    assert ok is False
    assert "参数不合法" in output
    assert drafts.beats == []


def test_draft_setting_merges_same_name(tmp_path) -> None:
    """同名同类型设定重复提交时后者覆盖，不产生重复条目。"""
    from ndecon.agent.models import AgentDrafts
    from ndecon.agent.tools import ToolContext

    store = WorkspaceStore(tmp_path)
    drafts = AgentDrafts()
    ctx = ToolContext(project=_project(store), task="add_settings", chapter_order=None,
                      workspace=tmp_path, drafts=drafts)
    entry = SettingEntry(entry_type="faction", name="机构", content="v1").model_dump()
    execute_tool("draft_setting", {"entry": entry}, ctx)
    entry["content"] = "v2"
    ok, _ = execute_tool("draft_setting", {"entry": entry}, ctx)
    assert ok is True
    assert len(drafts.settings) == 1 and drafts.settings[0].content == "v2"


def test_expand_chapter_requires_order() -> None:
    """expand_chapter 缺章号直接报错（编程错误，不应进入循环）。"""
    with pytest.raises(ValueError):
        run_planner(llm=FakePlanner("expand_chapter"), project=None, task="expand_chapter",  # type: ignore[arg-type]
                    workspace=".")
