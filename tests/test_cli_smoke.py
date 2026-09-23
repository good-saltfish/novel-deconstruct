"""CLI 冒烟测试：split / analyze / reindex 命令对真实文件落盘预期产物。"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ndecon.cli import app
from ndecon.creation.models import CreationDraft, Positioning
from ndecon.workspace.models import CreationProject, ProjectMeta, ReferenceProject
from ndecon.workspace.store import WorkspaceStore

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_novel.txt"


def _save_creation_project(tmp_path: Path, *, confirmed: bool) -> str:
    """在临时工作区构造一个最小创作项目并返回项目 ID。"""
    store = WorkspaceStore(tmp_path)
    project = CreationProject(
        meta=ProjectMeta(
            id="creation-smoke-1", kind="creation", title="冒烟书", created_at=store.now_iso()
        ),
        draft=CreationDraft(positioning=Positioning(genre="都市异能", core_premise="路灯封印") if confirmed else None),
        confirmed_parts={"positioning": True} if confirmed else {},
    )
    store.save_creation(project)
    return project.meta.id


def _save_reference_project(tmp_path: Path) -> str:
    """在临时工作区构造一个最小参考书项目并返回项目 ID。"""
    store = WorkspaceStore(tmp_path)
    project = ReferenceProject(
        meta=ProjectMeta(
            id="reference-smoke-1", kind="reference", title="参考书", created_at=store.now_iso()
        ),
        source_path=str(tmp_path / "novel.txt"),
        chapter_count=1,
        total_plot_points=1,
    )
    store.save_reference(project)
    return project.meta.id


def test_split_command(tmp_path: Path) -> None:
    """split 无需模型，应退出 0 并落盘概要与 entries.jsonl。"""
    out = tmp_path / "book"
    result = CliRunner().invoke(app, ["split", str(FIXTURE), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "概要.md").is_file()
    assert (out / "data" / "entries.jsonl").is_file()


def test_analyze_command(tmp_path: Path) -> None:
    """analyze --provider fake 应落盘摘要、深度拆解与三份 JSONL。"""
    out = tmp_path / "book"
    result = CliRunner().invoke(
        app, ["analyze", str(FIXTURE), "--out", str(out), "--provider", "fake"]
    )
    assert result.exit_code == 0, result.output
    assert (out / "概要.md").is_file()
    assert (out / "data" / "chapters.jsonl").is_file()
    assert (out / "data" / "reports.jsonl").is_file()
    # Stage 3 聚合产物：节奏页与机读 JSONL
    assert (out / "剧情" / "节奏.md").is_file()
    assert (out / "data" / "aggregation.jsonl").is_file()
    # 黄金三章：第 1-3 章深度拆解
    for order in range(1, 4):
        assert (out / "章节" / f"第{order:04d}章_深度拆解.md").is_file()


def test_unknown_provider_rejected(tmp_path: Path) -> None:
    """非 fake provider 在 v0.1 必须被明确拒绝。"""
    result = CliRunner().invoke(
        app, ["analyze", str(FIXTURE), "--out", str(tmp_path / "x"), "--provider", "gpt-xyz"]
    )
    assert result.exit_code != 0


def test_openai_compat_without_key_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """openai-compat 缺少 key 时给出可读错误而非 traceback。"""
    monkeypatch.delenv("NOVEL_DECON_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = CliRunner().invoke(
        app,
        ["analyze", str(FIXTURE), "--out", str(tmp_path / "x"), "--provider", "openai-compat"],
    )
    assert result.exit_code != 0
    assert "API key" in result.output or "key" in result.output.lower()


def test_reindex_command_writes_index(tmp_path: Path) -> None:
    """reindex 对创作项目退出 0，并在项目目录落盘含语料的 index.json。"""
    project_id = _save_creation_project(tmp_path, confirmed=True)
    result = CliRunner().invoke(app, ["reindex", project_id, "--workspace", str(tmp_path)])
    assert result.exit_code == 0, result.output
    index_file = tmp_path / "projects" / project_id / "index.json"
    assert index_file.is_file()
    payload = json.loads(index_file.read_text(encoding="utf-8"))
    assert payload["doc_count"] == 1
    assert payload["documents"][0]["doc_type"] == "positioning"


def test_reindex_rejects_missing_and_reference(tmp_path: Path) -> None:
    """reindex 对不存在的项目与参考书项目都必须非零退出。"""
    missing = CliRunner().invoke(app, ["reindex", "creation-nope", "--workspace", str(tmp_path)])
    assert missing.exit_code != 0

    reference_id = _save_reference_project(tmp_path)
    reference = CliRunner().invoke(app, ["reindex", reference_id, "--workspace", str(tmp_path)])
    assert reference.exit_code != 0
    assert "创作项目" in reference.output
