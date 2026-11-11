"""工作区存储：JSON 文件持久化的项目仓库。

纪律：
- 每个项目一个目录、一个 project.json；写入用"临时文件+替换"原子落盘；
- 参考书项目只存源路径与聚合结果，复制源原文的入口在这里就不存在；
- 路径全部限定在工作区 projects/ 内，项目 ID 只允许安全字符，防穿越。
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from ndecon.creation.models import CHAPTER_ORDER_MAX
from ndecon.retrieval.bm25 import BM25Index
from ndecon.workspace.atomic import (
    atomic_write_json,
    atomic_write_text,
    path_lock,
    read_text_with_retry,
)
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
# 单章摘要文件名（summaries/chNNN.json）；章号位数与正文文件名一致
_SAFE_SUMMARY = re.compile(r"^ch(\d{3})\.json$")
# 每章最多保留的历史版本数：够回退到"前几版"，又不会把工作区撑爆
MAX_CHAPTER_VERSIONS = 20


def new_project_id(kind: str, title: str) -> str:
    """生成人类可读且文件系统安全的项目 ID，例如 reference-wbsx-1/creation-mybook-2。

    注意：这个函数**不保证唯一**——时间戳只到秒，同一秒里用同一个标题建两次
    会得到同一个 ID。要入库请用 `WorkspaceStore.allocate_project_id`。
    """
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

    def allocate_project_id(self, kind: str, title: str, *, attempts: int = 50) -> str:
        """分配一个**独占地**新建的项目 ID，并返回它。

        为什么不能只用 `new_project_id`：它把标题 + `%H%M%S` 拼成 ID，同一秒内
        用同一个标题建两次得到同一个 ID，`_project_dir` 又是 `mkdir(exist_ok=True)`,
        于是第二次创建会**静默覆盖**第一次的 project.json（功能审计 B-1）。
        用户看到的是「新建成功」，实际上一本刚建的书没了。

        这里的做法是先用 `mkdir(exist_ok=False)` 把目录抢下来，抢到才算 ID 归属于
        自己；撞了就换个后缀重试。目录在这里被建出来是合理的——调用方紧接着就要
        往里写 project.json，中途失败只会留下一个空目录（下次同 ID 会再撞一次，
        落到下一个后缀），不会破坏已有项目。
        """
        base = new_project_id(kind, title)
        for index in range(attempts):
            candidate = base if index == 0 else f"{base}-{index + 1}"
            _assert_safe(candidate)
            try:
                (self.projects_dir / candidate).mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            return candidate
        raise RuntimeError(f"连续 {attempts} 次都没抢到可用的项目 ID（基数：{base}）")

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        """先写同目录临时文件再替换，保证 project.json 不会写一半损坏。

        临时文件名由 ndecon.workspace.atomic 生成（唯一 + 带 pid），
        不是固定的 `path.with_suffix(".json.tmp")`。原先固定名字在并发下
        会撞车：HTTP 请求与后台连写线程同时写同一个 project.json 时共用同一个
        tmp，Windows 上 `os.replace` 直接报 `WinError 5`，整章被回滚。
        """
        atomic_write_json(path, payload)

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
                data = json.loads(read_text_with_retry(file))
                metas.append(ProjectMeta(**data["meta"]))
            except (KeyError, json.JSONDecodeError, ValueError):
                continue
            except OSError:
                # Windows 上另一个进程（编辑器、网盘同步、杀毒）正拿着这个文件时，
                # 读会抛 WinError 32。它跟「文件坏了」一样只是这一个项目读不出来，
                # 不该让整个项目列表 500——那会让用户以为书全没了。
                continue
        metas.sort(key=lambda m: m.created_at)
        return metas

    def _path_for(self, project_id: str) -> Path:
        """返回项目 JSON 文件路径（会顺手把目录建出来，供写入用）。"""
        return self._project_dir(project_id) / "project.json"

    def _existing_path_for(self, project_id: str) -> Path:
        """返回项目 JSON 路径，但**绝不创建目录**（供读取用）。

        为什么单要一个：`_path_for` 会 `mkdir(exist_ok=True)`，于是
        `store.get("打错的ID")` 在抛 KeyError 之前先在磁盘上造出一个空项目目录。
        后果不只是脏目录——`delete` 接着就会认为「目录在，删成功了」并返回 True，
        违反它自己「没删到就返回 False」的契约。读操作不该产生副作用。
        """
        _assert_safe(project_id)
        return self.projects_dir / project_id / "project.json"

    def save_reference(self, project: ReferenceProject) -> None:
        """持久化参考书项目。"""
        self._atomic_write_json(self._path_for(project.meta.id), project.model_dump())

    def save_creation(self, project: CreationProject) -> None:
        """持久化创作项目。"""
        self._atomic_write_json(self._path_for(project.meta.id), project.model_dump())

    def update_creation(self, project_id: str, mutate: Callable[[CreationProject], None]) -> CreationProject:
        """在文件锁内「读最新 → 改 → 写回」，返回改完的项目。

        ## 为什么不能继续用 `save_creation(project)`

        真事故（功能审计 B-2）：``run_chapter`` 在**生成之前**就把项目读进内存，
        真模型一章要几十秒到几分钟；这段时间里 HTTP 线程可能刚改过同一份
        ``project.json``（保存骨架部件、确认骨架、改设定、选候选版本）。
        章节跑完时 ``store.save_creation(project)`` 拿的是**生成开始时那份快照**，
        整份 ``model_dump()`` 覆盖写盘 —— 用户在那几分钟里做的所有编辑被静默抹掉。

        加锁挡不住这个：锁只保证两次写不交错，不保证第二次写的内容没过期。
        唯一治法是**写之前重新读盘**，只把本次真正要改的字段盖上去。

        调用方传进来的 ``mutate`` 只应修改它自己的那几个字段，其余字段
        自动保留盘上的最新值。项目不存在时抛 ``KeyError``（不新建空目录）。
        """
        path = self._existing_path_for(project_id)
        lock = path_lock(path)
        with lock:
            if not path.is_file():
                raise KeyError(f"项目不存在：{project_id}")
            project = CreationProject(**json.loads(read_text_with_retry(path)))
            mutate(project)
            self._atomic_write_json(path, project.model_dump())
            return project

    def get(self, project_id: str) -> ReferenceProject | CreationProject:
        """读取项目并按 kind 还原为对应模型；不存在时抛 KeyError。

        兼容旧布局：早期版本把章节摘要塞在 project.json 的 ``summaries`` 里，
        现在改为 ``summaries/chNNN.json``。读到非空旧字段时自动迁移出去，
        避免老工作区在摘要外置后"看起来一切正常、实际摘要全丢"。
        """
        file = self._existing_path_for(project_id)
        if not file.is_file():
            raise KeyError(f"项目不存在：{project_id}")
        data = json.loads(read_text_with_retry(file))
        if data["meta"]["kind"] == "reference":
            return ReferenceProject(**data)
        project = CreationProject(**data)
        if project.summaries:
            self._migrate_inline_summaries(project)
        return project

    def _migrate_inline_summaries(self, project: CreationProject) -> None:
        """把 project.json 里的旧摘要搬进 summaries/ 并清空旧字段。"""
        for key, summary in list(project.summaries.items()):
            match = re.match(r"^ch(\d+)$", key)
            if not match:
                continue
            order = int(match.group(1))
            if not self.read_summary(project.meta.id, order):
                self.write_summary(project.meta.id, order, summary.model_dump())
        project.summaries = {}
        self.save_creation(project)

    def write_project_artifact(self, project_id: str, filename: str, payload: dict) -> Path:
        """在项目目录内原子写入一个 JSON 产物（如检索索引）；文件名走白名单防穿越。"""
        if not _SAFE_ARTIFACT.match(filename):
            raise ValueError(f"非法产物文件名：{filename!r}")
        path = self._project_dir(project_id) / filename
        self._atomic_write_json(path, payload)
        return path

    def read_project_artifact(self, project_id: str, filename: str) -> Path | None:
        """返回项目内某产物的路径，不存在时返回 None；文件名同样走白名单。

        只回路径不回内容：调用方自己决定要不要解析（有的产物几百 KB，
        调用方可能只想知道「有没有」）。白名单与写入口共用，防住 `../` 之类。
        """
        if not _SAFE_ARTIFACT.match(filename):
            raise ValueError(f"非法产物文件名：{filename!r}")
        path = self._project_dir(project_id) / filename
        return path if path.is_file() else None

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
            data = json.loads(read_text_with_retry(path))
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
            previous = read_text_with_retry(path)
            if previous != content:
                self._archive_version(project_id, order, previous)
        atomic_write_text(path, content)
        return path

    def _archive_version(self, project_id: str, order: int, content: str) -> Path | None:
        """把一份旧正文存进版本目录，并裁剪到最近 MAX_CHAPTER_VERSIONS 份。

        时间戳必须唯一：Windows 上 ``datetime.now()`` 的粒度约 15ms，
        连续两次改稿可能落在同一刻度，直接覆盖会让「回退本身可撤销」失效。
        撞名时往后挪一微秒，直到拿到空位。
        """
        if not content.strip():
            return None
        version_dir = self._version_dir(project_id, order)
        version_dir.mkdir(parents=True, exist_ok=True)
        moment = datetime.now()
        for _ in range(1000):
            stamp = moment.strftime("%Y%m%d-%H%M%S-%f")
            target = version_dir / f"{stamp}.md"
            if not target.exists():
                break
            moment += timedelta(microseconds=1)
        else:  # pragma: no cover - 一千次撞名现实中不会发生
            raise RuntimeError(f"无法为第 {order} 章分配唯一版本时间戳")
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
        return read_text_with_retry(path) if path.is_file() else None

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
        return read_text_with_retry(path) if path.is_file() else None

    # ---- 章节摘要外置（project.json 不随章数膨胀）-------------------------
    #
    # 早期实现把 50 章摘要全塞在 project.json 里：单章摘要 12 KB（40 个情节点
    # 各带 quote + content_hash），50 章就到 1 MB、占文件 59%，按这个斜率 300 章
    # 会到 6 MB。而每生成一章都要"读全量 → 改一处 → 写全量"，等于每章都在
    # 反序列化整本书的摘要。改成每章一个 summaries/chNNN.json 之后，
    # project.json 只留 O(1) 的游标与状态，摘要按需读取。

    def _summary_dir(self, project_id: str) -> Path:
        """返回摘要目录 summaries/（不存在时按需创建）。"""
        path = self._project_dir(project_id) / "summaries"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def summary_path(self, project_id: str, order: int) -> Path:
        """返回某章摘要文件路径；章号校验与正文同口径。"""
        self._manuscript_path(project_id, order)  # 复用章号校验
        return self._summary_dir(project_id) / f"ch{order:03d}.json"

    def write_summary(self, project_id: str, order: int, payload: dict) -> Path:
        """原子写入单章摘要（调用方负责把模型 dump 成 dict）。"""
        path = self.summary_path(project_id, order)
        self._atomic_write_json(path, payload)
        return path

    def read_summary(self, project_id: str, order: int) -> dict | None:
        """读取单章摘要；不存在或损坏时返回 None（不拖垮整本读取）。"""
        path = self.summary_path(project_id, order)
        if not path.is_file():
            return None
        try:
            return json.loads(read_text_with_retry(path))
        except (OSError, json.JSONDecodeError):
            return None

    def list_summaries(self, project_id: str) -> list[dict]:
        """按章号升序读取全部摘要（供索引重建与上下文组装）。"""
        directory = self._project_dir(project_id) / "summaries"
        if not directory.is_dir():
            return []
        items: list[tuple[int, dict]] = []
        for path in directory.glob("ch*.json"):
            match = _SAFE_SUMMARY.match(path.name)
            if not match:
                continue
            try:
                items.append((int(match.group(1)), json.loads(read_text_with_retry(path))))
            except (OSError, json.JSONDecodeError):
                continue
        return [payload for _, payload in sorted(items)]

    def delete_summary(self, project_id: str, order: int) -> None:
        """删除单章摘要（章节回滚或重跑时用）；不存在时静默。"""
        self.summary_path(project_id, order).unlink(missing_ok=True)

    def delete(self, project_id: str) -> bool:
        """删除整个项目目录（含 manuscripts 子目录）。

        返回是否真的删掉了东西：不存在时返回 False 而不是静默成功。
        幂等性由调用方决定怎么用这个返回值——底层保持「删两次也不会炸」，
        但面板需要知道「这次到底删没删到」，否则 DELETE 一个打错的 ID 会
        回 200，用户以为书没了，实际什么也没发生。
        """
        _assert_safe(project_id)
        path = self.projects_dir / project_id
        if not path.is_dir():
            return False
        shutil.rmtree(path)
        return True
