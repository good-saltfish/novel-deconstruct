"""设定 / 细纲两套提示词与候选生成器的单元测试。

重点不是"文案好不好看"，而是几条**一破就退化成一套万能 prompt**的性质：
- 两套 system message 学习方法不同、且都把"禁止照抄知识库"写成硬约束；
- 各自的检索词不同（设定找规则/代价，细纲找节奏/密度），否则 KB 会回报同一批片段；
- 细纲把已确认设定当**硬约束**传入，反之设定阶段不消费细纲（边界单向）；
- 产物一律是候选：`entry_type` 越界、节拍数越界、空产物都要拒绝而不是降格接收。
"""

import pytest

from ndecon.creation import plans
from ndecon.creation.models import SettingEntry
from ndecon.creation.plan_providers import (
    MAX_BEATS_PER_CHAPTER,
    MIN_BEATS_PER_CHAPTER,
    FakeOutlinePlanner,
    FakeSettingPlanner,
    PlanError,
    _outline_plan_from_dict,
    _setting_plan_from_dict,
)
from ndecon.deconstruct.models import MAX_QUOTE_CHARS

SNIPPET = [
    {
        "source": "kb/deconstructed/诡舍_KB.md",
        "heading": "可迁移做法",
        "text": "每 2-3 章安排一次爽点；章尾必须留未收束的线。",
    }
]


# --------------------------------------------------------------------------
# 两套提示词必须真的不同
# --------------------------------------------------------------------------


def test_two_system_messages_teach_different_things():
    """设定是收敛（边界→代价→人），细纲是铺开（时间轴排张力）——两者不能是同一段话。"""
    assert plans.SETTING_PROMPT_VERSION != plans.OUTLINE_PROMPT_VERSION
    setting = plans.SETTING_SYSTEM_MESSAGE
    outline = plans.OUTLINE_SYSTEM_MESSAGE
    assert setting != outline
    # 设定侧的关键方法词
    assert "代价" in setting and "边界" in setting
    # 细纲侧的关键方法词
    assert "节拍" in outline and "章尾钩" in outline
    # 两者都写死引用上限与禁代笔
    for message in (setting, outline):
        assert str(MAX_QUOTE_CHARS) in message
        assert "不代笔" in message or "禁止写出可以直接贴进正文" in message


def test_kb_queries_target_different_methods():
    """检索词必须分家：否则两个任务会从同一个知识库拿到同一批片段，等于没分。"""
    assert plans.SETTING_KB_QUERY != plans.OUTLINE_KB_QUERY
    assert "代价" in plans.SETTING_KB_QUERY and "力量体系" in plans.SETTING_KB_QUERY
    assert "节奏" in plans.OUTLINE_KB_QUERY and "钩子" in plans.OUTLINE_KB_QUERY


def test_setting_message_does_not_consume_outline_and_vice_versa():
    """边界单向：设定 prompt 里不该出现"节拍"，细纲 prompt 才需要它。"""
    setting_message = plans.setting_user_message("书名", "都市", "一句话", SNIPPET)
    assert "节拍" not in setting_message
    assert "方法论参考" in setting_message
    assert "来源 kb/" in setting_message
    assert "禁止照抄" in setting_message


def test_outline_message_passes_confirmed_settings_as_hard_constraint():
    """已确认设定进细纲 prompt 是硬约束，且金手指代价被显式带上（防细纲无视代价）。"""
    settings = [
        {"entry_type": "world_rule", "name": "异常不可直视", "content": "直视者被记住气息"}
    ]
    message = plans.outline_user_message(
        "书名",
        "都市",
        "一句话",
        confirmed_settings=settings,
        knowledge_snippets=SNIPPET,
        golden_finger={"name": "气息回放", "ability": "回放十秒", "cost_limitation": "每日三次"},
    )
    assert "【本项目已确认设定" in message
    assert "异常不可直视" in message
    assert "每日三次" in message
    assert "不得越界" in message


# --------------------------------------------------------------------------
# 候选校验：坏产物必须拒绝，而不是降格接收
# --------------------------------------------------------------------------


def test_setting_entry_type_vocabulary_is_enforced():
    with pytest.raises(ValueError):
        SettingEntry(entry_type="plot", name="桥段", content="这是情节不是设定")


def test_empty_setting_plan_rejected():
    with pytest.raises(PlanError):
        _setting_plan_from_dict({}, "m", "v")


def test_illegal_setting_entry_rejected():
    with pytest.raises(PlanError):
        _setting_plan_from_dict({"entries": [{"name": "缺类型"}]}, "m", "v")


def test_outline_chapter_order_must_be_1_to_10():
    with pytest.raises(PlanError):
        _outline_plan_from_dict({"chapters": [{"order": 11, "beats": []}]}, "m", "v")


def test_outline_rejects_chapter_without_enough_beats():
    beat = {"order": 1, "scene": "巷口", "event": "他推开门"}
    with pytest.raises(PlanError):
        _outline_plan_from_dict({"chapters": [{"order": 1, "beats": [beat]}]}, "m", "v")


def test_outline_renumbers_beats_within_chapter():
    """章内节拍序号乱序时按序号重排为 1..n —— 顺序错乱的稿子不能拿去写正文。"""
    beats = [
        {"order": 3, "scene": "c", "event": "第三拍"},
        {"order": 1, "scene": "a", "event": "第一拍"},
        {"order": 2, "scene": "b", "event": "第二拍"},
    ]
    plan = _outline_plan_from_dict({"chapters": [{"order": 1, "beats": beats}]}, "m", "v")
    assert [beat.order for beat in plan.chapters[0].beats] == [1, 2, 3]
    assert [beat.event for beat in plan.chapters[0].beats] == ["第一拍", "第二拍", "第三拍"]


# --------------------------------------------------------------------------
# 离线生成器：确定性、可重放、如实报告知识库使用情况
# --------------------------------------------------------------------------


def test_fake_setting_planner_is_deterministic_and_valid():
    planner = FakeSettingPlanner()
    first = planner.plan_settings("书名", "都市异能", "一句话", SNIPPET)
    second = planner.plan_settings("书名", "都市异能", "一句话", SNIPPET)
    assert first.model_dump() == second.model_dump()
    assert first.prompt_version == plans.SETTING_PROMPT_VERSION
    assert len(first.entries) >= 3
    assert {entry.entry_type for entry in first.entries} >= {"world_rule", "power_system"}
    # 每条设定都要能回答"违反会怎样"，体现在 content 长度而不是空话
    assert all(len(entry.content) > 20 for entry in first.entries)
    assert first.golden_finger is not None and first.golden_finger.cost_limitation
    # 升级路径必须每级同步加重代价，不许只涨能力
    assert first.golden_finger.growth_path.count("代价") >= 2


def test_fake_setting_planner_reports_no_kb_when_empty():
    """没命中知识库必须如实说，不许伪装成"已参考方法论"。"""
    plan = FakeSettingPlanner().plan_settings("书名", "", "一句话")
    assert plan.notes and plan.notes[0].startswith("未使用")


def test_fake_outline_planner_is_deterministic_and_valid():
    planner = FakeOutlinePlanner()
    first = planner.plan_outline("书名", "都市异能", "一句话", SNIPPET)
    second = planner.plan_outline("书名", "都市异能", "一句话", SNIPPET)
    assert first.model_dump() == second.model_dump()
    assert first.prompt_version == plans.OUTLINE_PROMPT_VERSION
    for chapter in first.chapters:
        assert MIN_BEATS_PER_CHAPTER <= len(chapter.beats) <= MAX_BEATS_PER_CHAPTER
        assert chapter.emotion_track
    # 每章至少一拍给出完整三角（迫害者/受害者/拯救者齐全）
    assert any(
        len(beat.triangle) == 3 and all(beat.triangle) for chapter in first.chapters for beat in chapter.beats
    )


def test_fake_plans_are_candidates_not_confirmed_content():
    """产物必须带 model_id 标记来源，作者据此知道这是候选而非自己写的内容。"""
    setting = FakeSettingPlanner().plan_settings("书名", "", "一句话")
    outline = FakeOutlinePlanner().plan_outline("书名", "", "一句话")
    assert setting.model_id.startswith("rule:")
    assert outline.model_id.startswith("rule:")
