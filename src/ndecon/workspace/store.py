"""工作区存储：JSON 文件持久化的项目仓库。

纪律：
- 每个项目一个目录、一个 project.json；写入用"临时文件+替换"原子落盘；
- 参考书项目只存源路径与聚合结果，复制源原文的入口在这里就不存在；
- 路径全部限定在工作区 projects/ 内，项目 ID 只允许安全字符，防穿越。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from ndecon.workspace.models import (
    CreationProject,
    ProjectMeta,
    ReferenceProject,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-一-鿿]{1,64}$")
# 项目内 JSON 产物文件名白名单（如检索索引 index.json），杜绝目录穿越写法
_SAFE_ARTIFACT = re.compile(r"^[a-z0-9][a-z0-9_-]*\.json$")


def new_project_id(kind: str, title: str) -> str:
    """生成人类可读且文件系统安全的项目 ID，例如 reference-wbsx-1/creation-mybook-2。"""
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", title.strip()).strip("-")[:24] or "untitled"
    stamp = datetime.now().strftime("%H%M%S")
    return f"{kind}-{slug}-{stamp}"


def _assert_safe(project_id: str) -> None:
    """拒绝含路径分隔或越界字符的项目 ID。"""
    if not _SAFE_ID.match(project_id):
        raise ValueError(f"非法项目 ID：{project_id!r}")


class WorkspaceStore:
    """基于目录的 JSON 项目仓库。"""

    def __init__(self, root: str | Path) -> None:
        """记录工作区根目录并确保 projects 子目录存在。"""
        self.root = Path(root)
        self.projects_dir = self.root / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, project_id: str) -> Path:
        """返回（并确保）某项目目录；ID 非法时抛错。"""
        _assert_safe(project_id)
        path = self.projects_dir / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        """先写同目录临时文件再替换，保证 project.json 不会写一半损坏。"""
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    @staticmethod
    def now_iso() -> str:
        """生成秒级本地时间戳（ISO 8601，无时区依赖）。"""
        return datetime.now().isoformat(timespec="seconds")

    def list_projects(self) -> list[ProjectMeta]:
        """列出全部项目元数据，按创建时间升序；损坏的单个项目不拖垮列表。"""
        metas: list[ProjectMeta] = []
        for project_dir in sorted(self.projects_dir.iterdir()):
            file = project_dir / "project.json"
            if not file.is_file():
                continue
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                metas.append(ProjectMeta(**data["meta"]))
            except (KeyError, json.JSONDecodeError, ValueError):
                continue
        metas.sort(key=lambda m: m.created_at)
        return metas

    def _path_for(self, project_id: str) -> Path:
        """返回项目 JSON 文件路径。"""
        return self._project_dir(project_id) / "project.json"

    def save_reference(self, project: ReferenceProject) -> None:
        """持久化参考书项目。"""
        self._atomic_write_json(self._path_for(project.meta.id), project.model_dump())

    def save_creation(self, project: CreationProject) -> None:
        """持久化创作项目。"""
        self._atomic_write_json(self._path_for(project.meta.id), project.model_dump())

    def get(self, project_id: str) -> ReferenceProject | CreationProject:
        """读取项目并按 kind 还原为对应模型；不存在时抛 KeyError。"""
        file = self._path_for(project_id)
        if not file.is_file():
            raise KeyError(f"项目不存在：{project_id}")
        data = json.loads(file.read_text(encoding="utf-8"))
        if data["meta"]["kind"] == "reference":
            return ReferenceProject(**data)
        return CreationProject(**data)

    def write_project_artifact(self, project_id: str, filename: str, payload: dict) -> Path:
        """在项目目录内原子写入一个 JSON 产物（如检索索引）；文件名走白名单防穿越。"""
        if not _SAFE_ARTIFACT.match(filename):
            raise ValueError(f"非法产物文件名：{filename!r}")
        path = self._project_dir(project_id) / filename
        self._atomic_write_json(path, payload)
        return path

    def delete(self, project_id: str) -> None:
        """删除整个项目目录；不存在时静默（删除操作天然幂等）。"""
        _assert_safe(project_id)
        path = self.projects_dir / project_id
        if path.is_dir():
            for child in path.iterdir():
                child.unlink()
            path.rmdir()
