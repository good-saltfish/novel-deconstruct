"""写作台账脚手架单元测试：只搭骨架、不代笔、不静默覆盖。"""

from ndecon.creation.ledger import (
    SCAFFOLD_FILENAMES,
    SCAFFOLD_VERSION,
    build_scaffold,
    write_scaffold,
)
from ndecon.creation.models import (
    ChapterOutline,
    CreationDraft,
    ProtagonistProfile,
)
from ndecon.workspace.models import CreationProject, ProjectMeta


def _project() -> CreationProject:
    """一个带人设与两章细纲的创作项目。"""
    return CreationProject(
        meta=ProjectMeta(id="creation-x", kind="creation", title="夜色保险", created_at="2026-10-04T00:00:00"),
        genre="都市异能",
        premise="路灯封着夜色",
        draft=CreationDraft(
            protagonist=ProtagonistProfile(
                name="陆沉",
                background="保险理赔员",
                personality="冷静务实",
                desire="查清灾变成因",
                flaw="过度依赖理性算计",
                signature_ability="快速拆解规则",
            ),
            chapters=[
                ChapterOutline(
                    order=1,
                    title="报案",
                    core_event="接到一份不该存在的保单",
                    opening_hook="半夜来的单子",
                    ending_hook="保单背面印着他自己的名字",
                ),
                ChapterOutline(
                    order=2,
                    title="回访",
                    core_event="回访投保人",
                    opening_hook="投保人三年前就死了",
                    ending_hook="",  # 无章尾钩：不该进伏笔账本（宁缺毋滥）
                ),
            ],
        ),
    )


def test_scaffold_has_three_files_with_banner():
    files = build_scaffold()
    assert set(files) == set(SCAFFOLD_FILENAMES)
    for content in files.values():
        assert SCAFFOLD_VERSION in content
        assert "内容由你写" in content


def test_character_card_carries_protagonist():
    card = build_scaffold(_project())["character-card.md"]
    assert "陆沉" in card
    assert "过度依赖理性算计" in card
    # 语音卡与三角位是模板必须留出的空栏（不代笔）
    assert "语音特征" in card and "区分度自检" in card


def test_foreshadow_ledger_only_lists_explicit_hooks():
    """第 2 章没有章尾钩 -> 不进账本，宁缺毋滥。"""
    ledger = build_scaffold(_project())["foreshadow-ledger.md"]
    assert "保单背面印着他自己的名字" in ledger
    assert "hook-ch1" in ledger
    assert "hook-ch2" not in ledger


def test_chapter_blueprint_carries_outline():
    blueprint = build_scaffold(_project())["chapter-blueprint.md"]
    assert "报案" in blueprint and "回访" in blueprint
    assert "未写" in blueprint  # 状态列默认未写，不假装已写


def test_write_scaffold_skips_existing_unless_force(tmp_path):
    written, skipped = write_scaffold(tmp_path)
    assert len(written) == 3 and skipped == []

    # 第二次：默认跳过，不静默覆盖作者已写的内容
    again_written, again_skipped = write_scaffold(tmp_path)
    assert again_written == [] and len(again_skipped) == 3

    forced, _ = write_scaffold(tmp_path, force=True)
    assert len(forced) == 3


def test_write_scaffold_is_deterministic(tmp_path):
    """同一项目两次生成的内容逐字一致（纯函数，不含时间戳等易变量）。"""
    a = write_scaffold(tmp_path, _project())[0][0].read_text(encoding="utf-8")
    b = write_scaffold(tmp_path, _project(), force=True)[0][0].read_text(encoding="utf-8")
    assert a == b
