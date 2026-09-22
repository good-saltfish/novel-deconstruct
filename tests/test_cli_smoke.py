"""CLI 冒烟测试：split 与 analyze 命令对真实文件落盘预期产物。"""

from pathlib import Path

from typer.testing import CliRunner

from ndecon.cli import app

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_novel.txt"


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
    # 黄金三章：第 1-3 章深度拆解
    for order in range(1, 4):
        assert (out / "章节" / f"第{order:04d}章_深度拆解.md").is_file()


def test_unknown_provider_rejected(tmp_path: Path) -> None:
    """非 fake provider 在 v0.1 必须被明确拒绝。"""
    result = CliRunner().invoke(
        app, ["analyze", str(FIXTURE), "--out", str(tmp_path / "x"), "--provider", "gpt-xyz"]
    )
    assert result.exit_code != 0
