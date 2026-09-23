"""面板应用服务：REST JSON API + 静态单页资源。

安全边界：
- 只绑定 127.0.0.1，不对外暴露；
- 静态资源只从包内 web/ 目录取，不接受任意路径；
- 导入参考书只读用户显式给出的源文件路径，且只持久化聚合结果，不复制原文。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request

from pydantic import ValidationError

from ndecon.creation.models import (
    ChapterOutline,
    GoldenFingerSpec,
    Positioning,
    ProtagonistProfile,
    VolumeOutline,
)
from ndecon.creation.providers import FakeCreator
from ndecon.pipeline.runner import run_analyze
from ndecon.providers.fake import FakeProvider
from ndecon.workspace.models import (
    CreationProject,
    ProjectMeta,
    ReferenceProject,
)
from ndecon.workspace.store import WorkspaceStore, new_project_id

# 各部件到模型的映射；PUT 保存时按此做结构校验
_PART_MODELS = {
    "positioning": Positioning,
    "volume": VolumeOutline,
    "protagonist": ProtagonistProfile,
    "golden_finger": GoldenFingerSpec,
}
_WEB_DIR = Path(__file__).parent / "web"
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


def _reference_stats(store: WorkspaceStore, reference_ids: list[str]) -> list[dict]:
    """把参考书聚合结果压缩为生成 prompt 用的数字统计（不含原文/quote）。"""
    stats: list[dict] = []
    for project_id in reference_ids:
        try:
            project = store.get(project_id)
        except KeyError:
            continue
        if not isinstance(project, ReferenceProject) or project.aggregation is None:
            continue
        agg = project.aggregation
        stats.append(
            {
                "title": project.meta.title,
                "chapters": project.chapter_count,
                "themes": agg.theme_distribution,
                "pacing": agg.pacing.model_dump(),
            }
        )
    return stats


def create_handler(store: WorkspaceStore) -> type[BaseHTTPRequestHandler]:
    """生成绑定特定工作区的请求处理器类。"""

    class PanelHandler(BaseHTTPRequestHandler):
        """面板 HTTP 处理器：JSON API 与静态资源。"""

        server_version = "ndecon-panel/0.1"

        def log_message(self, *_args) -> None:
            """静默默认访问日志，避免面板运行时刷屏（错误仍由异常路径打印）。"""

        # ---- 基础收发 ----

        def _send_json(self, status: int, payload: dict | list) -> None:
            """以 UTF-8 JSON 响应。"""
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            """读取并解析请求体 JSON；非法 JSON 由调用方统一 400。"""
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8"))

        def _error(self, status: int, message: str) -> None:
            """返回统一错误结构。"""
            self._send_json(status, {"error": message})

        # ---- 路由 ----

        def do_GET(self) -> None:  # noqa: N802 (stdlib 命名)
            """处理 GET：静态资源与查询类 API。"""
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/api/health":
                self._send_json(200, {"ok": True})
                return
            if path == "/api/projects":
                self._send_json(200, [m.model_dump() for m in store.list_projects()])
                return
            if path.startswith("/api/projects/"):
                self._get_project(path.split("/")[3])
                return
            self._serve_static(path)

        def do_POST(self) -> None:  # noqa: N802
            """处理 POST：导入参考书、新建创作项目、生成骨架。"""
            path = unquote(urlparse(self.path).path)
            try:
                payload = self._read_json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._error(400, "请求体不是合法 JSON")
                return
            if path == "/api/projects/import":
                self._import_reference(payload)
            elif path == "/api/projects/create":
                self._create_project(payload)
            elif path.startswith("/api/projects/") and path.endswith("/generate"):
                self._generate(path.split("/")[3])
            else:
                self._error(404, "未知接口")

        def do_PUT(self) -> None:  # noqa: N802
            """处理 PUT：保存用户确认后的某部件内容。"""
            path = unquote(urlparse(self.path).path)
            parts = path.strip("/").split("/")
            # /api/projects/{id}/parts/{part}
            if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "parts":
                self._save_part(parts[2], parts[4])
            else:
                self._error(404, "未知接口")

        def do_PATCH(self) -> None:  # noqa: N802
            """处理 PATCH：更新创作项目的基础设定（题材/一句话设定/参考书）。"""
            path = unquote(urlparse(self.path).path)
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                self._update_base(parts[2])
            else:
                self._error(404, "未知接口")

        def do_DELETE(self) -> None:  # noqa: N802
            """处理 DELETE：删除项目。"""
            path = unquote(urlparse(self.path).path)
            if path.startswith("/api/projects/"):
                project_id = path.split("/")[3]
                store.delete(project_id)
                self._send_json(200, {"deleted": project_id})
            else:
                self._error(404, "未知接口")

        # ---- API 实现 ----

        def _get_project(self, project_id: str) -> None:
            """返回项目完整内容。"""
            try:
                self._send_json(200, store.get(project_id).model_dump())
            except KeyError:
                self._error(404, "项目不存在")

        def _import_reference(self, payload: dict) -> None:
            """导入小说：跑离线 Fake 拆书管道并入库（不复制原文）。"""
            name = str(payload.get("name", "")).strip()
            source = str(payload.get("file_path", "")).strip()
            source_path = Path(source)
            if not source_path.is_file():
                self._error(400, f"源文件不存在：{source}")
                return
            try:
                text = source_path.read_text(encoding="utf-8")
                bundle = run_analyze(text, name or source_path.stem, FakeProvider())
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                self._error(400, f"导入失败：{exc}")
                return
            project_id = new_project_id("reference", name or source_path.stem)
            project = ReferenceProject(
                meta=ProjectMeta(
                    id=project_id, kind="reference", title=bundle.book_title,
                    created_at=store.now_iso(),
                ),
                source_path=str(source_path.resolve()),
                chapter_count=len(bundle.entries),
                total_plot_points=sum(len(s.plot_points) for s in bundle.summaries),
                aggregation=bundle.aggregation,
            )
            store.save_reference(project)
            self._send_json(201, project.model_dump())

        def _create_project(self, payload: dict) -> None:
            """创建空的创作项目。"""
            title = str(payload.get("title", "")).strip()
            if not title:
                self._error(400, "title 必填")
                return
            reference_ids = [str(x) for x in payload.get("reference_ids", [])]
            project_id = new_project_id("creation", title)
            project = CreationProject(
                meta=ProjectMeta(
                    id=project_id, kind="creation", title=title, created_at=store.now_iso()
                ),
                genre=str(payload.get("genre", "")).strip(),
                premise=str(payload.get("premise", "")).strip(),
                reference_ids=reference_ids,
            )
            store.save_creation(project)
            self._send_json(201, project.model_dump())

        def _generate(self, project_id: str) -> None:
            """用 FakeCreator 生成完整骨架（离线可重放），候选态入库。"""
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目可以生成骨架")
                return
            creator = FakeCreator()
            stats = _reference_stats(store, project.reference_ids)
            project.draft = creator.generate_draft(
                project.meta.title, project.genre, project.premise, stats
            )
            project.confirmed_parts = {}  # 新候选生成后重置确认标记
            store.save_creation(project)
            self._send_json(200, project.model_dump())

        def _update_base(self, project_id: str) -> None:
            """更新创作项目基础设定：只允许 genre/premise/reference_ids 三个字段。"""
            try:
                payload = self._read_json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._error(400, "请求体不是合法 JSON")
                return
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目可更新基础设定")
                return
            if "genre" in payload:
                project.genre = str(payload["genre"]).strip()
            if "premise" in payload:
                project.premise = str(payload["premise"]).strip()
            if "reference_ids" in payload:
                project.reference_ids = [str(x) for x in payload["reference_ids"]]
            store.save_creation(project)
            self._send_json(200, project.model_dump())

        def _save_part(self, project_id: str, part: str) -> None:
            """校验并保存单个部件为用户确认内容。"""
            try:
                payload = self._read_json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._error(400, "请求体不是合法 JSON")
                return
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目可编辑部件")
                return
            content = payload.get("content")
            try:
                if part == "chapters":
                    validated = [ChapterOutline(**item) for item in content]
                    if [c.order for c in validated] != list(range(1, 11)):
                        raise ValueError("细纲必须恰好覆盖第 1-10 章")
                elif part in _PART_MODELS:
                    validated = _PART_MODELS[part](**content)
                else:
                    self._error(400, f"未知部件：{part}")
                    return
            except (ValidationError, TypeError, ValueError) as exc:
                self._error(422, f"部件内容不合法：{exc}")
                return
            setattr(project.draft, part, validated)
            project.draft.provenance[part] = "user-confirmed"
            project.confirmed_parts[part] = True
            store.save_creation(project)
            self._send_json(200, project.model_dump())

        # ---- 静态资源 ----

        def _serve_static(self, path: str) -> None:
            """只服务包内 web 目录中的白名单文件；/static/* 映射到 web/*。"""
            if path.startswith("/static/"):
                rel = path[len("/static/") :]
            else:
                rel = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (_WEB_DIR / rel).resolve()
            web_root = _WEB_DIR.resolve()
            if target != web_root and web_root not in target.parents:
                self._error(403, "非法路径")
                return
            if not target.is_file():
                self._error(404, "资源不存在")
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return PanelHandler


def run_server(
    workspace: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """构建并返回面板服务器（已 listen，未 serve_forever；便于测试与 CLI 复用）。"""
    store = WorkspaceStore(workspace)
    server = ThreadingHTTPServer((host, port), create_handler(store))
    server.workspace_store = store  # type: ignore[attr-defined]
    return server


def serve_in_thread(workspace: Path, *, port: int = 8765) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """在守护线程中启动面板，主要供测试使用。"""
    server = run_server(workspace, port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def api_request(server: ThreadingHTTPServer, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    """测试辅助：不经网络栈直接向服务器发 JSON 请求（使用本地 socket）。"""
    import urllib.request

    host, port = server.server_address[0], server.server_address[1]
    safe_path = quote(path, safe="/?=&")
    url = f"http://{host}:{port}{safe_path}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"} if data is not None else {}
    request: Request = Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
