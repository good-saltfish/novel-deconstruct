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
import shutil
from datetime import datetime
from pathlib import Path

from ndecon.creation.models import CHAPTER_ORDER_MAX
from ndecon.retrieval.bm25 import BM25Index
from ndecon.workspace.models import (
    CreationProject,
    ProjectMeta,
    ReferenceProject,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-一-鿿]{1,64}$")
# 项目内 JSON 产物文件名白名单（如检索索引 index.json），杜绝目录穿越写法
_SAFE_ARTIFACT = re.compile(r"^[a-z0-9][a-z0-9_-]*\.json$")
# 章节历史版本时间戳白名单（由 _archive_version 生成：YYYYMMDD-HHMMSS-微秒）
_SAFE_STAMP = re.compile(r"^\d{8}-\d{6}-\d{6}$")
# 每章最多保留的历史版本数：够回退到"前几版"，又不会把工作区撑爆
MAX_CHAPTER_VERSIONS = 20


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

    def load_project_index(self, project_id: str) -> BM25Index | None:
        """读取 reindex 落盘的章节检索索引（index.json）；不存在时返回 None。

        t3 缺陷 #2 修复：reindex 此前只写不读（死写）。本方法让运行时能真正消费
        该索引；CLI 仍保持"显式按需重建"语义（不引入常驻向量/索引服务）。
        """
        import json

        from ndecon.retrieval.bm25 import BM25Index

        path = self._project_dir(project_id) / "index.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return BM25Index.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None

    def _manuscript_path(self, project_id: str, order: int) -> Path:
        """返回某章正文文件路径；章号上限见 CHAPTER_ORDER_MAX，文件名服务端生成防穿越。"""
        if not isinstance(order, int) or not 1 <= order <= CHAPTER_ORDER_MAX:
            raise ValueError(f"非法章号：{order!r}（允许 1-{CHAPTER_ORDER_MAX}）")
        return self._project_dir(project_id) / "manuscripts" / f"ch{order:03d}.md"

    def _version_dir(self, project_id: str, order: int) -> Path:
        """章节历史版本目录：manuscripts/versions/chNNN/。"""
        if not isinstance(order, int) or not 1 <= order <= CHAPTER_ORDER_MAX:
            raise ValueError(f"非法章号：{order!r}（允许 1-{CHAPTER_ORDER_MAX}）")
        return self._project_dir(project_id) / "manuscripts" / "versions" / f"ch{order:03d}"

    def write_manuscript(
        self, project_id: str, order: int, content: str, *, keep_version: bool = True
    ) -> Path:
        """原子写入章节正文 Markdown（manuscripts/chNNN.md），返回文件路径。

        keep_version=True 时，若旧文件存在且内容不同，先把旧正文存一份到
        manuscripts/versions/chNNN/<时间戳>.md（最多保留 MAX_CHAPTER_VERSIONS 份）。
        全自动连跑时这一步是"写坏了能回退"的唯一保险。
        """
        path = self._manuscript_path(project_id, order)
        path.parent.mkdir(parents=True, exist_ok=True)
        if keep_version and path.is_file():
            previous = path.read_text(encoding="utf-8")
            if previous != content:
                self._archive_version(project_id, order, previous)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
        return path

    def _archive_version(self, project_id: str, order: int, content: str) -> Path | None:
        """把一份旧正文存进版本目录，并裁剪到最近 MAX_CHAPTER_VERSIONS 份。"""
        if not content.strip():
            return None
        version_dir = self._version_dir(project_id, order)
        version_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = version_dir / f"{stamp}.md"
        target.write_text(content, encoding="utf-8")
        versions = sorted(version_dir.glob("*.md"))
        for stale in versions[:-MAX_CHAPTER_VERSIONS]:
            stale.unlink(missing_ok=True)
        return target

    def list_versions(self, project_id: str, order: int) -> list[str]:
        """列出某章的历史版本时间戳（从旧到新）。"""
        version_dir = self._version_dir(project_id, order)
        if not version_dir.is_dir():
            return []
        return [item.stem for item in sorted(version_dir.glob("*.md"))]

    def read_version(self, project_id: str, order: int, stamp: str) -> str | None:
        """读取某章的一个历史版本；时间戳非法或不存在返回 None（不抛错）。"""
        if not _SAFE_STAMP.match(stamp):
            return None
        path = self._version_dir(project_id, order) / f"{stamp}.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def restore_version(self, project_id: str, order: int, stamp: str) -> str | None:
        """把某章回退到指定历史版本（当前正文会先被存档，回退本身可撤销）。"""
        content = self.read_version(project_id, order, stamp)
        if content is None:
            return None
        self.write_manuscript(project_id, order, content, keep_version=True)
        return content

    def read_manuscript(self, project_id: str, order: int) -> str | None:
        """读取章节正文；尚未生成时返回 None。"""
        path = self._manuscript_path(project_id, order)
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def delete(self, project_id: str) -> None:
        """删除整个项目目录（含 manuscripts 子目录）；不存在时静默（删除幂等）。"""
        _assert_safe(project_id)
        path = self.projects_dir / project_id
        if path.is_dir():
            shutil.rmtree(path)
