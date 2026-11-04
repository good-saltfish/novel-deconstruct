"""工作区守卫的单元测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ndecon.workspace.guard import is_temp_path, require_temp_workspace


def test_temp_root_is_allowed() -> None:
    """系统临时目录本身算合法工作区。"""
    assert is_temp_path(tempfile.gettempdir()) is True


def test_project_dir_is_rejected() -> None:
    """项目目录不在临时目录下，必须判为不合法。"""
    assert is_temp_path(Path(r"D:\myproject\.ndecon-workspace")) is False


def test_require_accepts_temp_dir() -> None:
    """临时目录下的工作区放行，并返回解析后的路径。"""
    ws = Path(tempfile.mkdtemp(prefix="ndecon-verify-"))
    resolved = require_temp_workspace(ws)
    assert resolved == ws.resolve()


def test_require_rejects_project_dir(capsys: pytest.CaptureFixture[str]) -> None:
    """非临时目录直接退出，并把原因写到 stderr。"""
    with pytest.raises(SystemExit) as excinfo:
        require_temp_workspace(Path(r"D:\myproject\.ndecon-workspace"))
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "拒绝在非临时目录上做写操作验证" in stderr
    assert "2026-11-04 事故" in stderr
