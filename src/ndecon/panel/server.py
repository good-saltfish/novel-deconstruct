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
from ndecon.creation import plans as creation_plans
from ndecon.creation.chapters import FakeChapterWriter, OpenAICompatChapterWriter
from ndecon.creation.models import (
    CHAPTER_ORDER_MAX,
    Beat,
    ChapterBeatPlan,
    ChapterOutline,
    GoldenFingerSpec,
    ManuscriptRecord,
    Positioning,
    ProtagonistProfile,
    SettingEntry,
    VolumeOutline,
)
from ndecon.creation.plan_providers import (
    FakeOutlinePlanner,
    FakeSettingPlanner,
    OpenAICompatOutlinePlanner,
    OpenAICompatSettingPlanner,
    PlanError,
)
from ndecon.creation.providers import FakeCreator, OpenAICompatCreator
from ndecon.creation.summarize import content_hash, summarize_chapter
from ndecon.domain.models import ChapterSummary
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
from ndecon.quality.fingerprint import scan_chapter
from ndecon.retrieval.context import build_context_pack
from ndecon.workspace.models import (
    CreationProject,
    ProjectMeta,
    ReferenceProject,
    is_usable,
)
from ndecon.workspace.store import WorkspaceStore, new_project_id

# Planner Agent 合法任务（契约测试据此给出可读报错）
_VALID_AGENT_TASKS = ("expand_chapter", "add_settings")

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


def _dropped_parts(project: CreationProject) -> list[dict]:
    """列出「有内容但状态不可用」的骨架部件。

    界面必须能区分两种空：一种是本来就没生成，另一种是生成了但没被接受。
    后者会让后续章节在上下文里缺掉定位/主角/金手指，是连写崩掉的常见起点，
    静默显示成「空」等于把故障藏起来。
    """
    dropped: list[dict] = []
    for part in ("positioning", "volume", "protagonist", "golden_finger", "chapters"):
        state = project.confirmed_parts.get(part)
        if is_usable(state):
            continue
        value = getattr(project.draft, part, None)
        has_content = bool(value) if not isinstance(value, list) else len(value) > 0
        if not has_content:
            continue
        dropped.append({"part": part, "state": state or "draft"})
    return dropped


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
                if len(parts) == 6 and parts[3] == "chapters" and parts[5] == "lint":
                    self._lint_chapter(parts[2], parts[4])
                elif len(parts) == 6 and parts[3] == "chapters" and parts[5] == "context":
                    self._get_chapter_context(parts[2], parts[4])
                elif len(parts) == 6 and parts[3] == "chapters" and parts[5] == "versions":
                    self._list_chapter_versions(parts[2], parts[4])
                elif len(parts) == 5 and parts[3] == "chapters":
                    self._get_chapter(parts[2], parts[4])
                elif len(parts) == 4 and parts[3] == "runs":
                    self._list_runs(parts[2])
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
            elif chapter_post:
                if parts[5] == "generate":
                    self._generate_chapter(parts[2], parts[4], payload)
                else:
                    self._confirm_chapter(parts[2], parts[4])
            elif path.startswith("/api/projects/") and path.endswith("/accept-skeleton"):
                self._accept_skeleton(path.split("/")[3], payload)
            elif path.startswith("/api/projects/") and path.endswith("/generate"):
                self._generate(path.split("/")[3], payload)
            elif path.startswith("/api/projects/") and path.endswith("/agent/runs"):
                self._run_agent(path.split("/")[3], payload)
            elif path.startswith("/api/projects/") and path.endswith("/plan/settings"):
                self._plan_settings(path.split("/")[3], payload)
            elif path.startswith("/api/projects/") and path.endswith("/plan/outline"):
                self._plan_outline(path.split("/")[3], payload)
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
            # /api/projects/{id}/agent/accept/{beats,settings}
            elif len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3] == "agent" and parts[4] == "accept":
                if parts[5] == "beats":
                    self._accept_beats(parts[2])
                elif parts[5] == "settings":
                    self._accept_settings(parts[2])
                else:
                    self._error(404, "未知接口")
            # /api/projects/{id}/plan/accept/{settings,outline}
            elif (
                len(parts) == 6
                and parts[:2] == ["api", "projects"]
                and parts[3] == "plan"
                and parts[4] == "accept"
            ):
                if parts[5] == "settings":
                    self._accept_plan_settings(parts[2])
                elif parts[5] == "outline":
                    self._accept_plan_outline(parts[2])
                else:
                    self._error(404, "未知接口")
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
            # 细纲章数：可到 CHAPTER_ORDER_MAX；缺省沿用 10 章，保持旧行为不变。
            raw_count = payload.get("chapter_count")
            chapter_count = 10
            if raw_count is not None:
                try:
                    chapter_count = int(raw_count)
                except (TypeError, ValueError):
                    self._error(400, f"chapter_count 必须是整数：{raw_count!r}")
                    return
                if not 1 <= chapter_count <= CHAPTER_ORDER_MAX:
                    self._error(400, f"chapter_count 必须在 1-{CHAPTER_ORDER_MAX} 之间")
                    return
            use_ai = str(payload.get("provider", "fake")).strip() in ("ai", "openai-compat")
            if use_ai:
                try:
                    provider = llm_session.require_provider()
                    creator = OpenAICompatCreator(provider, chapter_count=chapter_count)
                except ProviderError as exc:
                    self._error(400, str(exc))
                    return
            else:
                creator = FakeCreator(chapter_count=chapter_count)
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

        # ---- Planner Agent（#25）：跑自主循环 / 确认候选入库 ----

        def _run_agent(self, project_id: str, payload: dict) -> None:
            """运行一次 Planner Agent：provider=fake（离线）或 ai（面板已配 LLM）。

            未配置的 ai 直接 400 引导，绝不静默降级 Fake（冻结红线）。
            """
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目可运行 Agent")
                return
            task = str(payload.get("task", "")).strip()
            if task not in _VALID_AGENT_TASKS:
                self._error(400, f"非法任务：{task!r}（可选 expand_chapter / add_settings）")
                return
            chapter_order = payload.get("chapter_order")
            if task == "expand_chapter":
                if chapter_order is None:
                    self._error(400, "expand_chapter 必须提供 chapter_order")
                    return
                try:
                    chapter_order = int(chapter_order)
                except (TypeError, ValueError):
                    self._error(400, f"chapter_order 必须是整数：{chapter_order!r}")
                    return
                if not 1 <= chapter_order <= CHAPTER_ORDER_MAX:
                    self._error(400, f"章号必须在 1-{CHAPTER_ORDER_MAX} 之间")
                    return
            else:
                chapter_order = None
            provider = str(payload.get("provider", "fake")).strip()
            use_ai = provider in ("ai", "openai-compat")
            try:
                planner = (
                    OpenAICompatPlanner(llm_session.require_provider())
                    if use_ai
                    else FakePlanner(task, chapter_order)
                )
            except ProviderError as exc:
                self._error(400, str(exc))
                return
            result = run_planner(
                llm=planner,
                project=project,
                task=task,  # type: ignore[arg-type]
                workspace=store.root,
                chapter_order=chapter_order,
            )
            self._send_json(200, result.model_dump())

        def _accept_beats(self, project_id: str) -> None:
            """把 Agent 提交的节拍候选确认为某章权威内容（候选不代笔，由用户触发）。"""
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
                self._error(400, "只有创作项目可确认节拍")
                return
            raw_beats = payload.get("beats")
            if not isinstance(raw_beats, list) or not raw_beats:
                self._error(422, "beats 必须为非空列表")
                return
            try:
                beats = [Beat.model_validate(b) for b in raw_beats]
            except (ValidationError, TypeError, ValueError) as exc:
                self._error(422, f"节拍结构不合法：{exc}")
                return
            try:
                order = int(payload["chapter_order"])
            except (KeyError, TypeError, ValueError):
                self._error(400, "chapter_order 必填且为整数")
                return
            chapter = next((c for c in project.draft.chapters if c.order == order), None)
            if chapter is None:
                self._error(400, "请先生成并保存包含该章的细纲")
                return
            # 按 order 排序，保证展示与重放确定性（原始 #25 语义）
            chapter.beats = sorted(beats, key=lambda b: b.order)
            store.save_creation(project)
            self._send_json(200, project.model_dump())

        def _accept_settings(self, project_id: str) -> None:
            """把 Agent 提交的设定候选确认为项目设定（候选不代笔，由用户触发）。

            同名同类型去重后追加（设定是累积库，保留历史条目；不整体覆盖以免丢失既有设定）。
            """
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
                self._error(400, "只有创作项目可确认设定")
                return
            raw_settings = payload.get("settings")
            if not isinstance(raw_settings, list) or not raw_settings:
                self._error(422, "settings 必须为非空列表")
                return
            try:
                entries = [SettingEntry.model_validate(s) for s in raw_settings]
            except (ValidationError, TypeError, ValueError) as exc:
                self._error(422, f"设定结构不合法：{exc}")
                return
            # 追加 + 同名同类型去重（原 #25 累积语义）
            existing = {(s.entry_type, s.name) for s in project.settings}
            for entry in entries:
                if (entry.entry_type, entry.name) not in existing:
                    project.settings.append(entry)
                    existing.add((entry.entry_type, entry.name))
            store.save_creation(project)
            self._send_json(200, project.model_dump())

        # ---- 设定 / 细纲两套候选（学习方法不同，见 creation/plans.py 对照表）----

        def _plan_settings(self, project_id: str, payload: dict) -> None:
            """生成设定候选：返回候选本身，不写项目（候选不代笔，确认另走 accept）。"""
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目可生成设定")
                return
            use_ai = str(payload.get("provider", "fake")).strip() in ("ai", "openai-compat")
            try:
                planner = (
                    OpenAICompatSettingPlanner(llm_session.require_provider())
                    if use_ai
                    else FakeSettingPlanner()
                )
            except ProviderError as exc:
                # 冻结红线：未配置 AI 必须报错，不静默降级 Fake
                self._error(400, str(exc))
                return
            # 检索词瞄准"怎么写设定"，不是书名/情节——否则会回报情节梗概
            snippets = [
                snippet.model_dump()
                for snippet in search_knowledge(store.root, creation_plans.SETTING_KB_QUERY)
            ]
            try:
                plan = planner.plan_settings(
                    project.meta.title, project.genre, project.premise, snippets
                )
            except PlanError as exc:
                self._error(502, str(exc))
                return
            self._send_json(200, {"plan": plan.model_dump(), "knowledge": snippets})

        def _plan_outline(self, project_id: str, payload: dict) -> None:
            """生成细纲候选：已确认设定作为硬约束传入，防止细纲引入设定外的能力。"""
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目可生成细纲")
                return
            use_ai = str(payload.get("provider", "fake")).strip() in ("ai", "openai-compat")
            try:
                planner = (
                    OpenAICompatOutlinePlanner(llm_session.require_provider())
                    if use_ai
                    else FakeOutlinePlanner()
                )
            except ProviderError as exc:
                self._error(400, str(exc))
                return
            snippets = [
                snippet.model_dump()
                for snippet in search_knowledge(store.root, creation_plans.OUTLINE_KB_QUERY)
            ]
            confirmed = [entry.model_dump() for entry in project.settings]
            finger = None
            if project.draft is not None and project.draft.golden_finger is not None:
                finger = project.draft.golden_finger.model_dump()
            try:
                plan = planner.plan_outline(
                    project.meta.title,
                    project.genre,
                    project.premise,
                    confirmed,
                    snippets,
                    golden_finger=finger,
                )
            except PlanError as exc:
                self._error(502, str(exc))
                return
            self._send_json(200, {"plan": plan.model_dump(), "knowledge": snippets})

        def _accept_plan_settings(self, project_id: str) -> None:
            """确认「设定候选」整体（含金手指）写回项目（候选不代笔，由用户触发）。

            与 `_accept_settings` 的区别：那条只吃 SettingEntry 列表并**累积追加**；
            这条接受 `_plan_settings` 的完整候选，除设定条目外还接管金手指。
            """
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
                self._error(400, "只有创作项目可确认设定")
                return
            raw_entries = payload.get("entries")
            if not isinstance(raw_entries, list) or not raw_entries:
                self._error(422, "entries 必须为非空列表")
                return
            try:
                entries = [SettingEntry.model_validate(item) for item in raw_entries]
            except (ValidationError, TypeError, ValueError) as exc:
                self._error(422, f"设定结构不合法：{exc}")
                return
            # 设定是累积库：同名同类型不覆盖（与该项既有的 accept 语义保持一致）
            existing = {(entry.entry_type, entry.name) for entry in project.settings}
            for entry in entries:
                if (entry.entry_type, entry.name) not in existing:
                    project.settings.append(entry)
                    existing.add((entry.entry_type, entry.name))

            # 金手指可选：只在候选里带了才写，避免把已有金手指清空
            raw_finger = payload.get("golden_finger")
            if raw_finger:
                try:
                    project.draft.golden_finger = GoldenFingerSpec.model_validate(raw_finger)
                except (ValidationError, TypeError, ValueError) as exc:
                    self._error(422, f"金手指结构不合法：{exc}")
                    return
                project.draft.provenance["golden_finger"] = "user-confirmed"
                project.confirmed_parts["golden_finger"] = "human-confirmed"

            store.save_creation(project)
            self._send_json(200, project.model_dump())

        def _accept_plan_outline(self, project_id: str) -> None:
            """确认「细纲候选」：把每章节拍挂到对应 ChapterOutline.beats 上。

            逐章**替换**该章节拍（一次细纲候选是一份完整提案，不是增量）；
            候选里没提到的章保持原样，不做整体清空。
            """
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
                self._error(400, "只有创作项目可确认细纲")
                return
            raw_chapters = payload.get("chapters")
            if not isinstance(raw_chapters, list) or not raw_chapters:
                self._error(422, "chapters 必须为非空列表")
                return
            if len(raw_chapters) > CHAPTER_ORDER_MAX:
                self._error(422, f"细纲候选一次最多 {CHAPTER_ORDER_MAX} 章")
                return
            plans: list[ChapterBeatPlan] = []
            for item in raw_chapters:
                try:
                    plans.append(ChapterBeatPlan.model_validate(item))
                except (ValidationError, TypeError, ValueError) as exc:
                    self._error(422, f"细纲结构不合法：{exc}")
                    return
            for plan in plans:
                target = next((c for c in project.draft.chapters if c.order == plan.order), None)
                if target is None:
                    self._error(400, f"候选含第 {plan.order} 章，但项目细纲里没有这一章（请先生成骨架）")
                    return
                # 整章替换 + 按拍号排序保证重放确定性
                target.beats = sorted(plan.beats, key=lambda b: b.order)
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
                    # 细纲必须从第 1 章起连续递增，长度只受上限约束（B1 解除后不再要求恰好 10 章）
                    if not validated:
                        raise ValueError("细纲至少需要 1 章")
                    if [c.order for c in validated] != list(range(1, len(validated) + 1)):
                        raise ValueError("细纲章号必须从 1 起连续递增，不允许跳号或重复")
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
            project.confirmed_parts[part] = "human-confirmed"
            store.save_creation(project)
            self._send_json(200, project.model_dump())

        # ---- 骨架自动验收（A1 卷末闸门的骨架侧）----

        def _accept_skeleton(self, project_id: str, payload: dict) -> None:
            """把骨架里已生成但未确认的部件标成 `auto-accepted`。

            为什么需要这个端点：`_generate` 生成候选后会把 `confirmed_parts` 清空，
            所有部件停在 `draft`；而 `is_usable()` 只认 auto-accepted / human-confirmed，
            于是上下文包会把这些部件全部丢弃（`dropped` 里逐个列出），后续每一章都
            缺定位/主角/金手指，写出来必然崩。全自动连写时没有人来点「确认」，
            必须有一个「判据通过即入库」的出口。

            判据（都可机械判定，不靠人看）：
              - 部件内容非空；
              - 细纲章号从 1 起连续递增（跳号意味着后面会写空章）；
              - 主角有名字、金手指有描述（空壳部件等于没有）。
            任何一条不过就整体不验收，并在响应里逐条说明原因——静默通过比不通过更坏。

            请求体可带 `parts: ["positioning", ...]` 只验收指定部件（默认全部）。
            """
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目可以验收骨架")
                return

            requested = payload.get("parts")
            if requested is not None and not isinstance(requested, list):
                self._error(422, "parts 必须是列表")
                return
            targets = [str(p) for p in requested] if requested else list(_PART_MODELS) + ["chapters"]

            accepted: list[str] = []
            rejected: list[dict] = []
            for part in targets:
                if part == "chapters":
                    chapters = project.draft.chapters
                    if not chapters:
                        rejected.append({"part": part, "reason": "细纲为空"})
                        continue
                    orders = [c.order for c in chapters]
                    if orders != list(range(1, len(orders) + 1)):
                        rejected.append({
                            "part": part,
                            "reason": f"细纲章号不连续：{orders[:12]}{'…' if len(orders) > 12 else ''}",
                        })
                        continue
                elif part in _PART_MODELS:
                    if getattr(project.draft, part, None) is None:
                        rejected.append({"part": part, "reason": "尚未生成"})
                        continue
                    if part == "protagonist" and not str(getattr(project.draft, part).name or "").strip():
                        rejected.append({"part": part, "reason": "主角没有名字"})
                        continue
                    if part == "golden_finger":
                        finger = project.draft.golden_finger
                        # 字段名照 GoldenFingerSpec 的真实定义（name/form/ability/
                        # cost_limitation/growth_path），没有 description 字段。
                        if not str(getattr(finger, "ability", "") or "").strip():
                            rejected.append({"part": part, "reason": "金手指没有「能做什么」"})
                            continue
                        if not str(getattr(finger, "cost_limitation", "") or "").strip():
                            rejected.append({"part": part, "reason": "金手指没有限制与代价（会写成无敌流）"})
                            continue
                else:
                    rejected.append({"part": part, "reason": "未知部件"})
                    continue
                project.confirmed_parts[part] = "auto-accepted"
                accepted.append(part)

            if accepted:
                store.save_creation(project)
            self._send_json(
                200 if accepted else 422,
                {
                    "accepted": accepted,
                    "rejected": rejected,
                    "confirmed_parts": dict(project.confirmed_parts),
                },
            )

        # ---- 章节正文（#16）----

        def _load_project_summaries(self, project_id: str) -> list[ChapterSummary]:
            """从 summaries/ 目录加载本书全部章节摘要（摘要已外置，按需读取）。"""
            items: list[ChapterSummary] = []
            for payload in store.list_summaries(project_id):
                try:
                    items.append(ChapterSummary(**payload))
                except (TypeError, ValueError):
                    continue
            return items

        def _load_creation_for_chapter(
            self, project_id: str, order_text: str
        ) -> tuple[CreationProject, ChapterOutline] | None:
            """加载创作项目并定位细纲中的目标章；失败时已自行回复错误。"""
            try:
                order = int(order_text)
            except (TypeError, ValueError):
                self._error(400, f"章号必须是整数：{order_text!r}")
                return None
            if not 1 <= order <= CHAPTER_ORDER_MAX:
                self._error(400, f"章号必须在 1-{CHAPTER_ORDER_MAX} 之间")
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

        # ---- 只读观测面：上下文包 / 版本 / run 记录 ----
        #
        # 这三个端点存在的唯一理由：界面要显示「引擎到底打算喂给模型什么」。
        # 少了它，右栏只能显示生成结果，用户无法判断一次失败是模型写得烂，
        # 还是上下文本来就是空的 —— 后者才是连写崩掉的真正原因。

        def _get_chapter_context(self, project_id: str, order_text: str) -> None:
            """返回第 N 章的 ContextPack 原文（与 CLI `context --json` 同源）。"""
            loaded = self._load_creation_for_chapter(project_id, order_text)
            if loaded is None:
                return
            project, target = loaded
            summaries = self._load_project_summaries(project_id)
            try:
                pack = build_context_pack(project, target.order, summaries=summaries or None)
            except ValueError as exc:
                self._error(400, str(exc))
                return
            payload = pack.model_dump()
            # 未注入部件必须显式列出：前端不许把「被闸门丢掉」显示成「本来就没有」。
            payload["dropped"] = _dropped_parts(project)
            self._send_json(200, payload)

        def _list_chapter_versions(self, project_id: str, order_text: str) -> None:
            """列出某章的历史版本（每次覆盖写都会先存档）。"""
            try:
                order = int(order_text)
            except (TypeError, ValueError):
                self._error(400, f"章号必须是整数：{order_text!r}")
                return
            if not 1 <= order <= CHAPTER_ORDER_MAX:
                self._error(400, f"章号必须在 1-{CHAPTER_ORDER_MAX} 之间")
                return
            try:
                stamps = store.list_versions(project_id, order)
            except KeyError:
                self._error(404, "项目不存在")
                return
            self._send_json(200, {"order": order, "versions": stamps})

        def _list_runs(self, project_id: str) -> None:
            """列出本项目的 run 记录目录与每章终态（可重放的执行台账）。"""
            try:
                store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            runs_dir = store.root / "projects" / project_id / "runs"
            if not runs_dir.is_dir():
                self._send_json(200, {"runs": []})
                return
            runs = []
            for run_dir in sorted(runs_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                records = []
                for path in sorted(run_dir.glob("ch*.json")):
                    try:
                        records.append(json.loads(path.read_text(encoding="utf-8")))
                    except (OSError, json.JSONDecodeError):
                        continue
                runs.append({
                    "run_id": run_dir.name,
                    "chapters": [
                        {
                            "order": record.get("order"),
                            "status": record.get("status"),
                            "verdict": record.get("gate_verdict"),
                            "attempts": record.get("attempts"),
                            "cjk_chars": record.get("cjk_chars"),
                        }
                        for record in records
                    ],
                })
            self._send_json(200, {"runs": runs})

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

            # 唯一上下文来源：ContextPack（不含参考书原文/源路径）；
            # 已落盘的章节摘要一并注入，使后续章节能检索到本书已写内容（t3 缺陷 #3）。
            summaries = self._load_project_summaries(project_id)
            pack = build_context_pack(project, target.order, summaries=summaries or None)
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

            # 写后闭环：由本次使用的 Provider 产出本章结构化摘要并落盘，使"已写章节"
            # 能进入检索索引（P0① 修复）。Provider 错误只影响闭环，不污染已生成的正文。
            chapter_hash = content_hash(writing.content)
            summary = summarize_chapter(
                writer,
                target.order,
                target.title,
                writing.content,
                chapter_hash=chapter_hash,
            )
            if summary is not None:
                store.write_summary(project_id, target.order, summary.model_dump())

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

        def _lint_chapter(self, project_id: str, order_text: str) -> None:
            """对已保存的本章正文做 AI 指纹扫描；只读，不改正文一个字。"""
            loaded = self._load_creation_for_chapter(project_id, order_text)
            if loaded is None:
                return
            project, _target = loaded
            content = store.read_manuscript(project.meta.id, int(order_text))
            if not content or not content.strip():
                self._error(404, "本章尚无正文，无法扫描")
                return
            self._send_json(200, scan_chapter(content).model_dump())

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
