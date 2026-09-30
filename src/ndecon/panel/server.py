"""面板应用服务：REST JSON API + 静态单页资源。

安全边界：
- 只绑定 127.0.0.1，不对外暴露；
- 静态资源只从包内 web/ 目录取，不接受任意路径；
- 导入参考书只读用户显式给出的源文件路径，且只持久化聚合结果，不复制原文。
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request

from pydantic import ValidationError

from ndecon.agent.planners import FakePlanner, OpenAICompatPlanner
from ndecon.agent.runner import run_planner
from ndecon.creation.chapters import FakeChapterWriter, OpenAICompatChapterWriter
from ndecon.creation.models import (
    Beat,
    ChapterOutline,
    GoldenFingerSpec,
    ManuscriptRecord,
    Positioning,
    ProtagonistProfile,
    SettingEntry,
    VolumeOutline,
)
from ndecon.creation.providers import FakeCreator, OpenAICompatCreator
from ndecon.kb.corpus import search_knowledge
from ndecon.llm.catalog import catalog_payload
from ndecon.llm.session import (
    LLMSession,
    LLMSessionConfig,
    test_connection,
    validate_config_payload,
)
from ndecon.pipeline.runner import run_analyze
from ndecon.providers.errors import ProviderError
from ndecon.providers.fake import FakeProvider
from ndecon.retrieval.context import build_context_pack
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


def create_handler(store: WorkspaceStore, llm_session: LLMSession) -> type[BaseHTTPRequestHandler]:
    """生成绑定特定工作区与 LLM 会话的请求处理器类。"""

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
            if path == "/api/llm/providers":
                self._send_json(200, catalog_payload())
                return
            if path == "/api/llm/settings":
                self._send_json(200, llm_session.public_view())
                return
            if path == "/api/projects":
                self._send_json(200, [m.model_dump() for m in store.list_projects()])
                return
            if path.startswith("/api/projects/"):
                parts = path.strip("/").split("/")
                if len(parts) == 5 and parts[3] == "chapters":
                    self._get_chapter(parts[2], parts[4])
                else:
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
            parts = path.strip("/").split("/")
            chapter_post = (
                len(parts) == 6
                and parts[:2] == ["api", "projects"]
                and parts[3] == "chapters"
                and parts[5] in ("generate", "confirm")
            )
            if path == "/api/projects/import":
                self._import_reference(payload)
            elif path == "/api/projects/create":
                self._create_project(payload)
            elif path == "/api/llm/test":
                self._test_llm(payload)
            elif path.startswith("/api/projects/") and path.endswith("/agent/runs"):
                parts = path.strip("/").split("/")
                self._start_agent_run(parts[2], payload)
            elif chapter_post:
                if parts[5] == "generate":
                    self._generate_chapter(parts[2], parts[4], payload)
                else:
                    self._confirm_chapter(parts[2], parts[4])
            elif path.startswith("/api/projects/") and path.endswith("/generate"):
                self._generate(path.split("/")[3], payload)
            else:
                self._error(404, "未知接口")

        def do_PUT(self) -> None:  # noqa: N802
            """处理 PUT：保存 LLM 配置、骨架部件或编辑章节正文。"""
            path = unquote(urlparse(self.path).path)
            parts = path.strip("/").split("/")
            # /api/llm/settings
            if parts == ["api", "llm", "settings"]:
                try:
                    payload = self._read_json()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._error(400, "请求体不是合法 JSON")
                    return
                self._save_llm_settings(payload)
                return
            # /api/projects/{id}/parts/{part}
            if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "parts":
                self._save_part(parts[2], parts[4])
            # /api/projects/{id}/chapters/{order}
            elif len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "chapters":
                self._save_chapter(parts[2], parts[4])
            elif (
                len(parts) == 6
                and parts[:2] == ["api", "projects"]
                and parts[3] == "agent"
                and parts[4] == "accept"
            ):
                # /api/projects/{id}/agent/accept/{kind}  kind=beats|settings
                try:
                    payload = self._read_json()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._error(400, "请求体不是合法 JSON")
                    return
                self._accept_agent_drafts(parts[2], parts[5], payload)
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

        def _save_llm_settings(self, payload: dict) -> None:
            """校验并保存面板 LLM 配置（key 仅进进程内存）。

            同一供应商再次保存且 key 留空时，保留会话中已有的 key（便于只改模型/URL）。
            """
            payload = dict(payload)
            if (
                not str(payload.get("api_key", "")).strip()
                and llm_session.provider == str(payload.get("provider", "")).strip()
                and llm_session.api_key
            ):
                payload["api_key"] = llm_session.api_key
            config, error = validate_config_payload(payload)
            if error is not None:
                self._error(400, error)
                return
            llm_session.update(config)
            self._send_json(200, llm_session.public_view())

        def _test_llm(self, payload: dict) -> None:
            """用请求中的配置做连通性测试（不保存）；空 body 时测试已保存配置。"""
            if payload:
                config, error = validate_config_payload(payload)
                if error is not None:
                    self._error(400, error)
                    return
            else:
                if not llm_session.is_configured():
                    self._error(400, "尚未保存配置，且本次测试未提供配置")
                    return
                config = LLMSessionConfig(
                    provider=llm_session.provider,
                    api_key=llm_session.api_key,
                    base_url=llm_session.base_url,
                    model=llm_session.model,
                )
            result = test_connection(config)
            if not result["ok"]:
                result["error"] = result["detail"]
            self._send_json(200 if result["ok"] else 502, result)

        def _generate(self, project_id: str, payload: dict) -> None:
            """生成完整骨架：provider=fake（默认离线）或 ai（面板已保存的 LLM 配置）。"""
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目可以生成骨架")
                return
            use_ai = str(payload.get("provider", "fake")).strip() in ("ai", "openai-compat")
            if use_ai:
                try:
                    provider = llm_session.require_provider()
                    creator = OpenAICompatCreator(provider)
                except ProviderError as exc:
                    self._error(400, str(exc))
                    return
            else:
                creator = FakeCreator()
            stats = _reference_stats(store, project.reference_ids)
            # L0.5 学习型知识库：工作区已建库时，按书名/题材/设定检索方法论片段（无索引则空）
            knowledge_query = "\n".join(
                part for part in (project.meta.title, project.genre, project.premise) if part
            )
            knowledge = [snippet.model_dump() for snippet in search_knowledge(store.root, knowledge_query)]
            project.draft = creator.generate_draft(
                project.meta.title, project.genre, project.premise, stats, knowledge
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

        # ---- Planner Agent（#25）----

        def _start_agent_run(self, project_id: str, payload: dict) -> None:
            """运行细纲/设定规划 agent（同步返回 trace 与候选；provider=fake|ai）。"""
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目可以运行规划 agent")
                return

            task = str(payload.get("task", "")).strip()
            if task not in ("expand_chapter", "add_settings"):
                self._error(400, "task 必须是 expand_chapter 或 add_settings")
                return
            chapter_order = payload.get("chapter_order")
            if task == "expand_chapter":
                if not isinstance(chapter_order, int) or not 1 <= chapter_order <= 10:
                    self._error(400, "expand_chapter 需要 1-10 的整数 chapter_order")
                    return
                if not next((c for c in project.draft.chapters if c.order == chapter_order), None):
                    self._error(400, f"第 {chapter_order} 章细纲不存在，请先生成骨架")
                    return

            provider_name = str(payload.get("provider", "fake")).strip()
            if provider_name == "ai":
                try:
                    llm = OpenAICompatPlanner(llm_session.require_provider())
                except ProviderError as exc:
                    self._error(400, str(exc))
                    return
            else:
                llm = FakePlanner(task, chapter_order)

            result = run_planner(
                llm=llm,
                project=project,
                task=task,  # type: ignore[arg-type]
                workspace=store.root,
                chapter_order=chapter_order,
            )
            self._send_json(200, result.model_dump())

        def _accept_agent_drafts(self, project_id: str, kind: str, payload: dict) -> None:
            """把用户确认的 agent 候选并入权威数据：beats 挂章节、settings 入设定库。"""
            if kind not in ("beats", "settings"):
                self._error(400, "kind 必须是 beats 或 settings")
                return
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目可接受规划候选")
                return
            try:
                if kind == "beats":
                    order = int(payload["chapter_order"])
                    beats = [Beat.model_validate(item) for item in payload.get("beats", [])]
                    if not beats:
                        raise ValueError("没有节拍")
                    chapter = next((c for c in project.draft.chapters if c.order == order), None)
                    if chapter is None:
                        self._error(400, f"第 {order} 章不存在")
                        return
                    chapter.beats = sorted(beats, key=lambda b: b.order)
                else:
                    entries = [SettingEntry.model_validate(item) for item in payload.get("settings", [])]
                    if not entries:
                        raise ValueError("没有设定")
                    # 同名同类型去重后追加
                    existing = {(s.entry_type, s.name) for s in project.settings}
                    for entry in entries:
                        if (entry.entry_type, entry.name) not in existing:
                            project.settings.append(entry)
                            existing.add((entry.entry_type, entry.name))
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                self._error(422, f"候选内容不合法：{exc}")
                return
            store.save_creation(project)
            self._send_json(200, project.model_dump())

        # ---- 章节正文（#16）----

        def _load_creation_for_chapter(
            self, project_id: str, order_text: str
        ) -> tuple[CreationProject, ChapterOutline] | None:
            """加载创作项目并定位细纲中的目标章；失败时已自行回复错误。"""
            try:
                order = int(order_text)
            except (TypeError, ValueError):
                self._error(400, f"章号必须是整数：{order_text!r}")
                return None
            if not 1 <= order <= 10:
                self._error(400, "仅支持第 1-10 章")
                return None
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return None
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目可以写章节正文")
                return None
            target = next((c for c in project.draft.chapters if c.order == order), None)
            if target is None:
                self._error(400, "请先生成并保存包含该章的细纲")
                return None
            return project, target

        def _chapter_payload(self, project: CreationProject, order: int) -> dict:
            """组装章节响应：元数据记录 + 正文全文。"""
            record = project.manuscripts.get(f"ch{order}")
            return {
                "record": record.model_dump() if record is not None else None,
                "content": store.read_manuscript(project.meta.id, order),
            }

        def _generate_chapter(self, project_id: str, order_text: str, payload: dict) -> None:
            """基于 ContextPack 生成一章正文候选与结构化自评（fake 离线/openai-compat）。"""
            loaded = self._load_creation_for_chapter(project_id, order_text)
            if loaded is None:
                return
            project, target = loaded
            provider_name = str(payload.get("provider", "fake")).strip() or "fake"
            if provider_name == "fake":
                writer = FakeChapterWriter()
            elif provider_name in ("openai-compat", "ai"):
                try:
                    writer = OpenAICompatChapterWriter(llm_session.require_provider())
                except ProviderError as exc:
                    self._error(400, str(exc))
                    return
            else:
                self._error(400, f"未知 provider：{provider_name}（可选 fake / ai）")
                return

            # 唯一上下文来源：ContextPack（不含参考书原文/源路径）
            pack = build_context_pack(project, target.order)
            try:
                writing = writer.write_chapter(pack)
            except ProviderError as exc:
                self._error(502, f"章节生成失败：{exc}")
                return
            store.write_manuscript(project_id, target.order, writing.content)
            word_count = len(re.sub(r"\s+", "", writing.content))
            project.manuscripts[f"ch{target.order}"] = ManuscriptRecord(
                order=target.order,
                title=target.title,
                status="draft",
                word_count=word_count,
                model_id=writer.model_id,
                prompt_version=writer.prompt_version,
                updated_at=store.now_iso(),
                review=writing.review,
            )
            store.save_creation(project)
            self._send_json(200, self._chapter_payload(project, target.order))

        def _get_chapter(self, project_id: str, order_text: str) -> None:
            """读取一章的正文与元数据；尚未生成返回 404。"""
            loaded = self._load_creation_for_chapter(project_id, order_text)
            if loaded is None:
                return
            project, target = loaded
            if f"ch{target.order}" not in project.manuscripts:
                self._error(404, "本章尚未生成正文")
                return
            self._send_json(200, self._chapter_payload(project, target.order))

        def _save_chapter(self, project_id: str, order_text: str) -> None:
            """保存人工编辑后的正文；回到候选态，自评保留但标记为模型期产物。"""
            try:
                payload = self._read_json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._error(400, "请求体不是合法 JSON")
                return
            loaded = self._load_creation_for_chapter(project_id, order_text)
            if loaded is None:
                return
            project, target = loaded
            if f"ch{target.order}" not in project.manuscripts:
                self._error(400, "本章尚无正文，请先生成")
                return
            content = str(payload.get("content", ""))
            if not content.strip():
                self._error(422, "正文不能为空")
                return
            store.write_manuscript(project_id, target.order, content)
            record = project.manuscripts[f"ch{target.order}"]
            record.title = target.title
            record.status = "draft"
            record.word_count = len(re.sub(r"\s+", "", content))
            record.model_id = "user-edit"
            record.updated_at = store.now_iso()
            store.save_creation(project)
            self._send_json(200, self._chapter_payload(project, target.order))

        def _confirm_chapter(self, project_id: str, order_text: str) -> None:
            """把一章正文标记为用户确认；不改正文一个字。"""
            loaded = self._load_creation_for_chapter(project_id, order_text)
            if loaded is None:
                return
            project, target = loaded
            key = f"ch{target.order}"
            if key not in project.manuscripts:
                self._error(400, "本章尚无正文，无法确认")
                return
            project.manuscripts[key].status = "user-confirmed"
            project.manuscripts[key].updated_at = store.now_iso()
            store.save_creation(project)
            self._send_json(200, self._chapter_payload(project, target.order))

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
    llm_session = LLMSession()
    server = ThreadingHTTPServer((host, port), create_handler(store, llm_session))
    server.workspace_store = store  # type: ignore[attr-defined]
    server.llm_session = llm_session  # type: ignore[attr-defined]
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
