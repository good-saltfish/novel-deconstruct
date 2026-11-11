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


def test_update_creation_only_touches_named_fields(tmp_path) -> None:
    """`update_creation` 必须保留盘上的最新值，不能整份覆盖。

    真事故（功能审计 B-2）：连写线程在生成前读一份快照，生成结束整份写回，
    期间用户在面板里做的编辑被静默抹掉。这里直接模拟那个时序：
    拿到快照 → 别人改盘 → 自己只改 cursor → 断言别人的改动还在。
    """
    store = WorkspaceStore(tmp_path)
    project = CreationProject(
        meta=_meta(store, "creation-race-1", "creation", "抢写书"),
        genre="末世",
        premise="最初前提",
    )
    store.save_creation(project)

    stale = store.get("creation-race-1")
    assert isinstance(stale, CreationProject)

    # 另一个线程（HTTP 侧）在这期间改了设定并保存
    other = store.get("creation-race-1")
    assert isinstance(other, CreationProject)
    other.premise = "用户在生成期间改过的最新前提"
    other.confirmed_parts["positioning"] = "human-confirmed"
    store.save_creation(other)

    # 连写线程跑完，只声明自己改了 cursor
    def _apply(latest: CreationProject) -> None:
        latest.cursor = 7

    updated = store.update_creation("creation-race-1", _apply)
    assert updated.cursor == 7
    # 关键断言：过期快照没把别人的改动冲掉
    assert updated.premise == "用户在生成期间改过的最新前提"
    assert updated.confirmed_parts["positioning"] == "human-confirmed"

    reloaded = store.get("creation-race-1")
    assert isinstance(reloaded, CreationProject)
    assert reloaded.premise == "用户在生成期间改过的最新前提"


def test_update_creation_raises_keyerror_and_creates_no_dir(tmp_path) -> None:
    """项目不存在时必须抛 KeyError，且不能顺手造一个空目录。"""
    store = WorkspaceStore(tmp_path)

    def _noop(_: CreationProject) -> None:
        raise AssertionError("mutate 不该被调用")

    with pytest.raises(KeyError):
        store.update_creation("creation-不存在的书-000000", _noop)
    assert not (tmp_path / "projects" / "creation-不存在的书-000000").exists()


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
    """读取不存在项目抛 KeyError；删除不存在项目不报错但要说清楚没删到。"""
    store = WorkspaceStore(tmp_path)
    with pytest.raises(KeyError):
        store.get("nope")
    assert store.delete("nope") is False


def test_allocate_project_id_is_unique_within_same_second(tmp_path) -> None:
    """同一秒用同一个标题连建两次，必须拿到两个不同 ID。

    直接的动机：`new_project_id` 只把时间戳拼到秒，同秒同标题必然撞；
    而 `_project_dir` 是 `mkdir(exist_ok=True)`，第二次创建会静默覆盖
    第一次的 project.json（功能审计 B-1：用户看到新建成功，书没了）。
    """
    store = WorkspaceStore(tmp_path)
    first = store.allocate_project_id("creation", "撞名测试")
    second = store.allocate_project_id("creation", "撞名测试")
    assert first != second
    assert (tmp_path / "projects" / first).is_dir()
    assert (tmp_path / "projects" / second).is_dir()


def test_allocate_project_id_never_reuses_existing_dir(tmp_path) -> None:
    """已存在的目录（哪怕人工造出来）必须被跳过，不能被当成自己的。"""
    store = WorkspaceStore(tmp_path)
    existing = store.allocate_project_id("creation", "占位")
    (tmp_path / "projects" / existing / "project.json").write_text("{}", encoding="utf-8")
    fresh = store.allocate_project_id("creation", "占位")
    assert fresh != existing
    assert (tmp_path / "projects" / existing / "project.json").read_text(encoding="utf-8") == "{}"


def test_allocate_project_id_raises_after_attempts_exhausted(tmp_path) -> None:
    """抢不到就抛错，而不是返回一个会覆盖别人的 ID。"""
    store = WorkspaceStore(tmp_path)
    first = store.allocate_project_id("creation", "满额", attempts=1)
    with pytest.raises(RuntimeError):
        store.allocate_project_id("creation", "满额", attempts=1)
    assert (tmp_path / "projects" / first).is_dir()


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


def test_legacy_inline_summaries_are_migrated(tmp_path) -> None:
    """旧布局（摘要塞在 project.json 里）读一次就自动搬到 summaries/，不静默丢摘要。"""
    from ndecon.domain.models import ChapterSummary

    store = WorkspaceStore(tmp_path)
    store.save_creation(CreationProject(meta=_meta(store, "creation-legacy-1", "creation", "旧布局")))
    project = store.get("creation-legacy-1")
    summary = ChapterSummary(
        chapter_id="CH0001",
        order=1,
        title="旧章",
        gist="旧布局里的摘要",
        model_id="rule:test",
        prompt_version="cs-v0",
        plot_points=[],
    )
    # 直接写回带旧字段的 project.json，模拟升级前的工作区
    payload = project.model_dump()
    payload["summaries"] = {"ch1": summary.model_dump()}
    store._atomic_write_json(store._path_for("creation-legacy-1"), payload)  # noqa: SLF001

    migrated = store.get("creation-legacy-1")
    assert migrated.summaries == {}
    assert store.read_summary("creation-legacy-1", 1)["gist"] == "旧布局里的摘要"
    # 迁移后重读仍是空的，说明只搬一次、不会反复重写
    assert store.get("creation-legacy-1").summaries == {}


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
