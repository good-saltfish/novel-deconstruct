"""工作区存储测试：原子持久化、ID 安全、类型还原、不复制原文。"""

import pytest

from ndecon.creation.models import (
    CreationDraft,
    GoldenFingerSpec,
    Positioning,
)
from ndecon.workspace.models import (
    CreationProject,
    ProjectMeta,
    ReferenceProject,
)
from ndecon.workspace.store import WorkspaceStore, new_project_id


def _meta(store: WorkspaceStore, project_id: str, kind: str, title: str) -> ProjectMeta:
    """构造测试用项目元数据。"""
    return ProjectMeta(id=project_id, kind=kind, title=title, created_at=store.now_iso())


def test_id_is_filesystem_safe() -> None:
    """项目 ID 应保留中文与连字符但拒绝路径分隔。"""
    pid = new_project_id("creation", "我的 新书！")
    assert "/" not in pid and ".." not in pid and pid.startswith("creation-")


def test_reference_roundtrip_and_no_source_copy(tmp_path) -> None:
    """参考书入库可还原；工作区内不得出现源原文副本。"""
    store = WorkspaceStore(tmp_path)
    project = ReferenceProject(
        meta=_meta(store, "reference-book-1", "reference", "参考书名"),
        source_path=str(tmp_path / "novel.txt"),
        chapter_count=40,
        total_plot_points=1600,
        aggregation=None,
    )
    store.save_reference(project)
    loaded = store.get("reference-book-1")
    assert isinstance(loaded, ReferenceProject)
    assert loaded.chapter_count == 40
    # 项目目录里只有 project.json
    files = list((tmp_path / "projects" / "reference-book-1").iterdir())
    assert [f.name for f in files] == ["project.json"]


def test_creation_roundtrip_with_draft(tmp_path) -> None:
    """创作项目与部件候选/确认状态可持久化还原。"""
    store = WorkspaceStore(tmp_path)
    project = CreationProject(
        meta=_meta(store, "creation-mybook-1", "creation", "我的书"),
        genre="末世",
        premise="一句话设定",
        reference_ids=["reference-book-1"],
        draft=CreationDraft(
            positioning=Positioning(genre="末世", core_premise="x"),
            golden_finger=GoldenFingerSpec(name="规则视界", cost_limitation="一日三次"),
            provenance={"positioning": "rule:fake-creator"},
        ),
        confirmed_parts={"positioning": True},
    )
    store.save_creation(project)
    loaded = store.get("creation-mybook-1")
    assert isinstance(loaded, CreationProject)
    assert loaded.draft.positioning.genre == "末世"
    assert loaded.draft.golden_finger.cost_limitation == "一日三次"
    assert loaded.confirmed_parts["positioning"] is True
    assert store.list_projects()[0].title == "我的书"


def test_unsafe_id_rejected(tmp_path) -> None:
    """含路径穿越的 ID 必须被拒绝。"""
    store = WorkspaceStore(tmp_path)
    with pytest.raises(ValueError):
        store.get("../../etc/passwd")


def test_get_missing_raises_keyerror_and_delete_idempotent(tmp_path) -> None:
    """读取不存在项目抛 KeyError；删除不存在项目不报错。"""
    store = WorkspaceStore(tmp_path)
    with pytest.raises(KeyError):
        store.get("nope")
    store.delete("nope")  # 不抛异常
