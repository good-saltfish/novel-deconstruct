"""工作区存储测试：原子持久化、ID 安全、类型还原、不复制原文。"""

import json

import pytest

from ndecon.creation.models import (
    CHAPTER_ORDER_MAX,
    CreationDraft,
    GoldenFingerSpec,
    Positioning,
)
from ndecon.workspace.models import (
    CreationProject,
    ProjectMeta,
    ReferenceProject,
)
from ndecon.workspace.store import MAX_CHAPTER_VERSIONS, WorkspaceStore, new_project_id


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
        confirmed_parts={"positioning": "human-confirmed"},
    )
    store.save_creation(project)
    loaded = store.get("creation-mybook-1")
    assert isinstance(loaded, CreationProject)
    assert loaded.draft.positioning.genre == "末世"
    assert loaded.draft.golden_finger.cost_limitation == "一日三次"
    assert loaded.confirmed_parts["positioning"] == "human-confirmed"
    assert store.list_projects()[0].title == "我的书"


def test_legacy_bool_confirmed_parts_migrated(tmp_path) -> None:
    """旧工作区的 bool 状态必须被迁移：True -> human-confirmed，False -> 删键。"""
    store = WorkspaceStore(tmp_path)
    legacy = {
        "meta": {
            "id": "creation-legacy-1",
            "kind": "creation",
            "title": "旧书",
            "created_at": "2026-10-01T00:00:00",
        },
        "genre": "末世",
        "premise": "",
        "reference_ids": [],
        "draft": {"chapters": []},
        "confirmed_parts": {"positioning": True, "volume": False, "protagonist": True},
    }
    project_dir = store.root / "projects" / "creation-legacy-1"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
    )
    loaded = store.get("creation-legacy-1")
    assert isinstance(loaded, CreationProject)
    assert loaded.confirmed_parts == {
        "positioning": "human-confirmed",
        "protagonist": "human-confirmed",
    }
    assert "volume" not in loaded.confirmed_parts


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


def test_manuscript_write_read_and_scoped_path(tmp_path) -> None:
    """章节正文写入 manuscripts/chNNN.md 并可读回；非法章号拒绝。"""
    store = WorkspaceStore(tmp_path)
    project = CreationProject(
        meta=_meta(store, "creation-ms-1", "creation", "正文测试"),
    )
    store.save_creation(project)
    path = store.write_manuscript("creation-ms-1", 1, "# 不写入章题\n\n第一段。")
    assert path.name == "ch001.md"
    assert path.parent.name == "manuscripts"
    assert store.read_manuscript("creation-ms-1", 1) is not None
    assert store.read_manuscript("creation-ms-1", 2) is None
    for bad_order in (0, CHAPTER_ORDER_MAX + 1, -1, "1"):
        with pytest.raises(ValueError):
            store.write_manuscript("creation-ms-1", bad_order, "x")  # type: ignore[arg-type]


def test_manuscript_versions_archived_and_restorable(tmp_path) -> None:
    """改稿必须留版本：覆盖前先存档，能列出、能读、能回退。"""
    store = WorkspaceStore(tmp_path)
    store.save_creation(CreationProject(meta=_meta(store, "creation-vr-1", "creation", "版本")))
    store.write_manuscript("creation-vr-1", 1, "第一版。")
    store.write_manuscript("creation-vr-1", 1, "第二版。")
    stamps = store.list_versions("creation-vr-1", 1)
    assert len(stamps) == 1
    assert store.read_version("creation-vr-1", 1, stamps[0]) == "第一版。"
    assert store.read_manuscript("creation-vr-1", 1) == "第二版。"

    restored = store.restore_version("creation-vr-1", 1, stamps[0])
    assert restored == "第一版。"
    assert store.read_manuscript("creation-vr-1", 1) == "第一版。"
    # 回退本身也可撤销：第二版已被存档
    assert len(store.list_versions("creation-vr-1", 1)) == 2

    # 时间戳白名单：穿越写法一律读不到
    assert store.read_version("creation-vr-1", 1, "../../etc/passwd") is None


def test_manuscript_versions_capped(tmp_path) -> None:
    """版本目录只保留最近 MAX_CHAPTER_VERSIONS 份，避免连跑把磁盘写满。"""
    store = WorkspaceStore(tmp_path)
    store.save_creation(CreationProject(meta=_meta(store, "creation-vc-1", "creation", "裁剪")))
    for index in range(MAX_CHAPTER_VERSIONS + 5):
        store.write_manuscript("creation-vc-1", 1, f"版本 {index}。")
    assert len(store.list_versions("creation-vc-1", 1)) == MAX_CHAPTER_VERSIONS


def test_delete_removes_manuscript_subdir(tmp_path) -> None:
    """删除项目时递归清掉 manuscripts 子目录（旧版只删文件会残留）。"""
    store = WorkspaceStore(tmp_path)
    project = CreationProject(
        meta=_meta(store, "creation-ms-2", "creation", "删除测试"),
    )
    store.save_creation(project)
    store.write_manuscript("creation-ms-2", 3, "正文")
    store.delete("creation-ms-2")
    assert not (tmp_path / "projects" / "creation-ms-2").exists()


def test_project_artifact_filename_whitelist(tmp_path) -> None:
    """项目内 JSON 产物只接受安全文件名，拒绝目录穿越与可执行后缀。"""
    store = WorkspaceStore(tmp_path)
    project = CreationProject(
        meta=_meta(store, "creation-art-1", "creation", "产物测试"),
    )
    store.save_creation(project)
    path = store.write_project_artifact("creation-art-1", "index.json", {"ok": True})
    assert path.name == "index.json"
    for bad_name in ("../evil.json", "a/b.json", "index.JS", "run.exe", ""):
        with pytest.raises(ValueError):
            store.write_project_artifact("creation-art-1", bad_name, {})
