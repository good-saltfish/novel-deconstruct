"""面板应用服务：REST JSON API + 静态单页资源。

安全边界：
- 只绑定 127.0.0.1，不对外暴露；
- 静态资源只从包内 web/ 目录取，不接受任意路径；
- 导入参考书只读用户显式给出的源文件路径，且只持久化聚合结果，不复制原文。
"""

from __future__ import annotations

import contextlib
import hmac
import json
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request

from pydantic import ValidationError

from ndecon.agent.planners import FakePlanner, OpenAICompatPlanner
from ndecon.agent.runner import run_planner
from ndecon.creation import plans as creation_plans
from ndecon.creation.candidates import (
    DEFAULT_CANDIDATES,
    generate_candidates,
    read_candidates,
    select_candidate,
)
from ndecon.creation.chapters import FakeChapterWriter, OpenAICompatChapterWriter
from ndecon.creation.jobs import JOB_INTERRUPTED, JOB_RUNNING, JobRegistry, job_status
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
from ndecon.creation.runlog import RunLog
from ndecon.creation.summarize import backfill_summaries, content_hash, summarize_chapter
from ndecon.creation.txn import MAX_REPAIR_ATTEMPTS, run_chapter
from ndecon.domain.models import ChapterSummary
from ndecon.kb.corpus import search_knowledge
from ndecon.llm.catalog import catalog_payload
from ndecon.llm.session import (
    LLMSession,
    LLMSessionConfig,
    same_origin,
    test_connection,
    validate_config_payload,
)
from ndecon.pipeline.runner import run_analyze
from ndecon.providers.errors import ProviderError
from ndecon.providers.fake import FakeProvider
from ndecon.quality.fingerprint import scan_chapter
from ndecon.retrieval.context import build_context_pack
from ndecon.workspace.atomic import read_text_with_retry
from ndecon.workspace.models import (
    CreationProject,
    ProjectMeta,
    ReferenceProject,
    is_usable,
)
from ndecon.workspace.store import WorkspaceStore

# 汉字计数：台账要回答「这一章多少字」，老 run 记录缺字段时得从正文现算。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 错误文案里要抹掉的绝对路径（安全审计 H-2）。
#
# 为什么在出口统一抹，而不是逐个 `self._error(...)` 改：面板有二十多处把异常
# 原文回给前端，逐点脱敏一定会漏，以后新加的路由又会漏一遍。路径泄露的危险
# 也不在某一条文案里——`OSError` 的 str、pydantic 的 `ValidationError` 都会把
# 「D:\Users\某某\...」带出来，等于告诉任何能打到面板的人这台机器上有哪些目录、
# 用户名是什么。
#
# 只抹路径，不动其余文字：错误原因（HTTP 状态码、schema 哪个字段不对、模型返回
# 了什么）全部保留。要看全量原文就去看 engine.log，那里有 traceback——可诊断性
# 没丢，只是不往网络上去。
_WIN_ABS_PATH_RE = re.compile(
    r"(?<![A-Za-z])[A-Za-z]:[\\/]+(?:[^\\/\s\"'<>|,*?:]+[\\/]+)*[^\\/\s\"'<>|,*?:]*"
)
_POSIX_ABS_PATH_RE = re.compile(r"/(?:Users|home|root|private|var|opt|tmp)/[^\s\"'<>|,;]*")
_URL_PATH_RE = re.compile(r"\b(https?://[^/\s:\"'<>]+)[^\s\"'<>]*")


def scrub_paths(message: str) -> str:
    """把错误文案里的本机绝对路径换成占位符，保留其余全部信息。

    URL 也一起处理：保留 scheme 与 host（否则「连不上哪个服务器」这类报错没法
    用），抹掉后面的路径——`base_url` 里可能藏着内网主机名与端口后的具体接口。

    顺序有讲究：先抹 URL，再抹 Windows 路径。倒过来的话 `https://x` 里的 `s:`
    会被当成盘符匹配掉一半（`htt` + `<路径>` 这种鬼东西）。
    """
    if not message:
        return message
    text = _URL_PATH_RE.sub(r"\1", message)
    text = _WIN_ABS_PATH_RE.sub("<路径>", text)
    return _POSIX_ABS_PATH_RE.sub("<路径>", text)


def _display_name(path_str: str) -> str:
    """从路径里取「文件名」用于报错，两个平台的两种分隔符都认。

    不能用 `Path(x).name`：Windows 上 `Path(r"a\\b.txt").name == "b.txt"`，而
    POSIX 上反斜杠不是分隔符，`Path(r"D:\\myproject\\书.txt").name` 会原样返回
    整个字符串。这个函数的输出紧接着要过 `scrub_paths`，于是 CI（Linux）上
    整条被替换成 `<路径>`，用户看不到到底是哪个文件没找到——本机永远复现不了。
    """
    return re.split(r"[\\/]", path_str.strip())[-1]


def _display_stem(path_str: str) -> str:
    """`_display_name` 去掉扩展名后的部分，用作书名/项目标题的候选。

    同上：`Path.stem` 在 POSIX 上不认识反斜杠，会吐出带盘符的整串。
    """
    name = _display_name(path_str)
    stem, dot, _suffix = name.rpartition(".")
    return stem if dot and stem else name


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


def create_handler(
    store: WorkspaceStore,
    llm_session: LLMSession,
    jobs: JobRegistry | None = None,
    *,
    token: str = "",
) -> type[BaseHTTPRequestHandler]:
    """生成绑定特定工作区、LLM 会话与连写任务表的请求处理器类。

    jobs 必须由调用方传入（
un_server 传 server.jobs），不能在这里新建：
    连写任务的状态要跨请求存活，每次 new 一个就等于每次查询都是新任务表。

    token 为空表示不鉴权（用户自己 `ndecon panel` 打开网页的默认用法）；
    非空时要求每个 API 请求带 `X-Ndecon-Token` 或 `Authorization: Bearer`。
    为什么必须可选：面板既能被插件当子进程拉起，也能被用户用浏览器直接打开。
    强制鉴权会让浏览器场景直接瘫掉，所以由调用方（插件）决定是否开启。
    """
    job_registry = jobs if jobs is not None else JobRegistry()

    class PanelHandler(BaseHTTPRequestHandler):
        """面板 HTTP 处理器：JSON API 与静态资源。"""

        server_version = "ndecon-panel/0.1"
        panel_token = token

        def log_message(self, *_args) -> None:
            """静默默认访问日志，避免面板运行时刷屏（错误仍由异常路径打印）。"""

        # ---- 基础收发 ----

        def _read_json(self) -> dict:
            """读取并解析请求体 JSON；非法 JSON 由调用方统一 400。"""
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            self._body_read = True
            return json.loads(raw.decode("utf-8"))

        def _send_json(self, status: int, payload: dict | list) -> None:
            """返回 JSON；写响应前先确保请求体已经读掉（见 `_drain_body`）。"""
            self._drain_body()
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, message: str) -> None:
            """返回统一错误结构；文案里的本机绝对路径在这里统一抹掉。"""
            self._send_json(status, {"error": scrub_paths(message)})

        # ---- 访问校验 ----

        #: 丢弃请求体的上限。超过这个体积就不读了，改成回完直接关连接。
        _DRAIN_LIMIT = 1 << 20

        #: 本次请求体是否已经被读过（无论读去解析还是读去丢弃）。
        _body_read = False

        def _drain_body(self) -> None:
            """把还没读的请求体读掉，再回响应。

            为什么非读不可：客户端（urllib、浏览器 fetch）POST 时是「先写头、
            再写体」，服务端如果在读完体之前就回响应并关连接，客户端还在写的那
            一段会撞上 RST。Windows 上的表现是 `ConnectionAbortedError
            [WinError 10053]`，用户看到的是「fetch failed」而不是「你没权限」——
            排查方向会整个错掉。

            这不是理论问题：全量跑 340 个用例时 `test_write_endpoints_are_guarded`
            就挂在这里，单跑该文件却全绿（连接复用与写入时序不同）。同一个坑在
            422 的提前返回路径上又出现过一次（`test_panel_plans.py`），所以这里
            不挂在某一条分支上，而是挂在 `_send_json` 里，让**所有**响应都覆盖到。

            超大请求体直接放弃读取并关连接：为了回一条错误去读几十兆是白送一个
            DoS 入口，而真客户端不会往被拒的接口塞那么大的体。
            """
            if self._body_read:
                return
            self._body_read = True
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except (TypeError, ValueError):
                length = 0
            if length <= 0:
                return
            if length > self._DRAIN_LIMIT:
                self.close_connection = True
                return
            with contextlib.suppress(OSError, ValueError):
                self.rfile.read(length)

        def _request_path(self) -> str:
            """返回不带 query 的已解码路径。"""
            return unquote(urlparse(self.path).path)

        def _is_public(self) -> bool:
            """判断本次请求是否免鉴权。

            免鉴权三类，每一类都有明确理由：
            - `/api/health`：插件用它探活，探活前它还没拿到/还没下发 token；
            - `/`、`/index.html`、`/static/*`：浏览器要能先加载页面，页面里的
              JS 才可能去读 token 并带上。静态资源本身不含任何用户数据；
            - `OPTIONS`：CORS 预检不带自定义头，挡住会让浏览器场景直接报跨域错。
            """
            if self.command == "OPTIONS":
                return True
            path = self._request_path()
            if path == "/api/health":
                return True
            if path in ("/", "/index.html"):
                return True
            return path.startswith("/static/")

        def _presented_token(self) -> str:
            """取出客户端提供的令牌：优先自定义头，其次 Bearer。"""
            direct = str(self.headers.get("X-Ndecon-Token") or "").strip()
            if direct:
                return direct
            authorization = str(self.headers.get("Authorization") or "").strip()
            if authorization.lower().startswith("bearer "):
                return authorization[len("bearer ") :].strip()
            return ""

        def _token_ok(self) -> bool:
            """常量时间比较令牌，避免按字节流的时间差猜 token。"""
            if not self.panel_token:
                return True
            presented = self._presented_token()
            if not presented:
                return False
            return hmac.compare_digest(presented, self.panel_token)

        def _dispatch(self, handler) -> None:
            """执行一次请求处理；任何未捕获异常都转成 JSON 500，绝不断开连接。

            为什么必须有这一层：面板是插件的子进程，插件用 fetch 打它。
            处理器里一旦有异常逃出去（历史案例：httpx.ReadTimeout 不是
            ProviderError，业务层没接住），stdlib 只会打印 traceback 并断开
            连接，客户端拿到的是 `fetch failed`，界面上显示「无法连接引擎」。
            用户会去查引擎为什么崩，而真相是某个业务异常没被分类。
            兜底这一层让「引擎连不上」与「这个请求失败了」变成两件不同的事。

            鉴权放在这里：五个 do_* 全走它，漏一条路的可能性为零。
            401 必须直接写响应后 return，不能 raise —— 抛异常会被下面的
            except 包成 500，客户端就分不清「没权限」和「服务端炸了」。
            """
            try:
                if not self._is_public() and not self._token_ok():
                    self._drain_body()
                    self._error(
                        401,
                        "缺少或错误的访问令牌：请从插件面板打开；"
                        "如果是自己用命令行起的引擎，去掉 --token 即可恢复免鉴权。",
                    )
                    return
                handler()
            except Exception as exc:  # noqa: BLE001 - 兜底必须吞掉一切，否则断连接
                traceback.print_exc()
                message = f"{type(exc).__name__}: {exc}"
                # 响应可能已经开始写（客户端已收到 header），此时不能再改状态码
                with contextlib.suppress(Exception):
                    self._error(500, f"服务端内部错误：{message}")

        def do_GET(self) -> None:  # noqa: N802
            """GET 入口：交给 _dispatch 兜住所有异常。"""
            self._dispatch(self._route_GET)

        def do_POST(self) -> None:  # noqa: N802
            """POST 入口：交给 _dispatch 兜住所有异常。"""
            self._dispatch(self._route_POST)

        def do_PUT(self) -> None:  # noqa: N802
            """PUT 入口：交给 _dispatch 兜住所有异常。"""
            self._dispatch(self._route_PUT)

        def do_PATCH(self) -> None:  # noqa: N802
            """PATCH 入口：交给 _dispatch 兜住所有异常。"""
            self._dispatch(self._route_PATCH)

        def do_DELETE(self) -> None:  # noqa: N802
            """DELETE 入口：交给 _dispatch 兜住所有异常。"""
            self._dispatch(self._route_DELETE)

        # ---- 路由 ----

        def _route_GET(self) -> None:
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
                elif len(parts) == 6 and parts[3] == "chapters" and parts[5] == "candidates":
                    self._get_candidates(parts[2], parts[4])
                elif len(parts) == 5 and parts[3] == "chapters":
                    self._get_chapter(parts[2], parts[4])
                elif len(parts) == 5 and parts[3] == "runs":
                    self._get_batch_job(parts[2], parts[4])
                elif len(parts) == 4 and parts[3] == "runs":
                    self._list_runs(parts[2])
                elif len(parts) == 3:
                    self._get_project(parts[2])
                else:
                    # 到这里说明路径是 /api/projects/<id>/<未知尾巴>。
                    # 早前这里是 `self._get_project(path.split("/")[3])`，
                    # 等于把任何多段尾巴都当成「取整个项目」——路径写错也照样
                    # 回 200 全量数据，客户端会把拼错的路由误当成成功。
                    self._error(404, "未知接口")
                return
            self._serve_static(path)

        def _route_POST(self) -> None:
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
                and parts[5] in ("generate", "confirm", "run", "restore", "candidates")
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
                elif parts[5] == "run":
                    self._run_chapter_txn(parts[2], parts[4], payload)
                elif parts[5] == "restore":
                    self._restore_chapter_version(parts[2], parts[4], payload)
                elif parts[5] == "candidates":
                    # 生成与选中挤在一个 URL 上：两者都是「候选」这一个资源上的动作，
                    # 靠 body 里的 action 分流，省掉一条要同步维护的路由白名单。
                    action = str(payload.get("action", "generate")).strip() or "generate"
                    if action == "generate":
                        self._generate_chapter_candidates(parts[2], parts[4], payload)
                    elif action == "select":
                        self._select_chapter_candidate(parts[2], parts[4], payload)
                    else:
                        self._error(400, f"未知 action：{action}（可选 generate / select）")
                else:
                    self._confirm_chapter(parts[2], parts[4])
            elif path.startswith("/api/projects/") and path.endswith("/accept-skeleton"):
                self._accept_skeleton(path.split("/")[3], payload)
            elif path.startswith("/api/projects/") and path.endswith("/summaries/backfill"):
                self._backfill_summaries(path.split("/")[3], payload)
            elif path.startswith("/api/projects/") and path.endswith("/generate"):
                self._generate(path.split("/")[3], payload)
            elif path.startswith("/api/projects/") and path.endswith("/agent/runs"):
                self._run_agent(path.split("/")[3], payload)
            elif (
                len(parts) == 6
                and parts[:2] == ["api", "projects"]
                and parts[3] == "runs"
                and parts[5] == "pause"
            ):
                self._pause_batch_job(parts[2], parts[4])
            elif path.startswith("/api/projects/") and path.endswith("/runs"):
                self._run_chapters_txn(path.split("/")[3], payload)
            elif path.startswith("/api/projects/") and path.endswith("/plan/settings"):
                self._plan_settings(path.split("/")[3], payload)
            elif path.startswith("/api/projects/") and path.endswith("/plan/outline"):
                self._plan_outline(path.split("/")[3], payload)
            else:
                self._error(404, "未知接口")

        def _route_PUT(self) -> None:
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

        def _route_PATCH(self) -> None:
            """处理 PATCH：更新创作项目的基础设定（题材/一句话设定/参考书）。"""
            path = unquote(urlparse(self.path).path)
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                self._update_base(parts[2])
            else:
                self._error(404, "未知接口")

        def _route_DELETE(self) -> None:
            """处理 DELETE：删除项目。

            只接受 /api/projects/<id> 这一种形状。早前这里用
            `path.startswith("/api/projects/")` 判断，于是
            `/api/projects/<id>/任意后缀` 也会删掉整个项目——路径写错不会报错，
            只会让书没了。`_route_PATCH` 早就用的是精确段数校验，这里跟它对齐。

            另外两条判据（功能审计 B-4）：

            * 项目本来就不存在时**仍然回 200**，但在响应体里如实标
              `"existed": false`。为什么不是 404：DELETE 在 REST 里是幂等的，
              界面上的删除按钮真的会被连点两次（第一次请求还在飞，第二次已经
              发出），第二次回 404 会让前端弹一个「项目不存在」的红字，用户
              以为删失败了。`test/ops-e2e.mjs` 也把幂等当既有契约断言。
              诚实靠 `existed` 这个字段，而不是靠状态码撒谎。
            * 有连写在跑时回 409。后台线程每章都会 `mkdir(parents=True,
              exist_ok=True)` 重建目录树，边写边删的结局是半个项目目录——
              正文写了一半、project.json 没了。
            """
            path = unquote(urlparse(self.path).path)
            parts = path.strip("/").split("/")
            if not (len(parts) == 3 and parts[:2] == ["api", "projects"]):
                self._error(404, "未知接口")
                return
            project_id = parts[2]
            running = job_registry.running_for(project_id)
            if running is not None:
                self._send_json(409, {
                    "error": f"这个项目正在连写（{running.run_id}），先暂停并等当前章写完再删。",
                    "run_id": running.run_id,
                })
                return
            try:
                removed = store.delete(project_id)
            except ValueError as exc:
                self._error(400, str(exc))
                return
            self._send_json(200, {"deleted": project_id, "existed": removed})

        # ---- API 实现 ----

        def _get_project(self, project_id: str) -> None:
            """返回项目完整内容。"""
            try:
                self._send_json(200, store.get(project_id).model_dump())
            except KeyError:
                self._error(404, "项目不存在")

        def _import_reference(self, payload: dict) -> None:
            """导入小说：跑离线 Fake 拆书管道并入库（不复制原文）。

            错误文案里只回文件名，不回完整路径。原实现把用户给的 `file_path`
            与底层 `OSError` 的原文一起回显——`OSError` 的 str 里带着解析后的
            绝对路径，等于把「这台机器上哪个目录存在」告诉任何能打到面板的人。
            """
            name = str(payload.get("name", "")).strip()
            source = str(payload.get("file_path", "")).strip()
            source_path = Path(source)
            if not source_path.is_file():
                shown = _display_name(source) or "（未提供路径）"
                self._error(400, f"源文件不存在：{shown}")
                return
            try:
                text = source_path.read_text(encoding="utf-8")
                bundle = run_analyze(text, name or _display_stem(source) or "未命名", FakeProvider())
            except (OSError, UnicodeDecodeError) as exc:
                self._error(400, f"读取源文件失败：{type(exc).__name__}")
                return
            except ValueError as exc:
                self._error(400, f"拆书失败：{exc}")
                return
            project_id = store.allocate_project_id("reference", name or _display_stem(source) or "未命名")
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
            project_id = store.allocate_project_id("creation", title)
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

            但「同一供应商」不够 —— 还得是**同一台服务器**。原实现只比 provider id：
            攻击者发一个 `provider` 不变、`base_url` 指向自己的请求，就能让引擎把
            已存的真 key 作为 `Authorization: Bearer` 发出去（`llm/session.py` 的
            `test_connection` 与 provider 都直接用它）。插件自己就用 `provider:'custom'`，
            所以这条链是完全可执行的。

            现在的判据：provider 相同 **且** origin 相同（scheme+host+port）才复用 key。
            只比 origin 不比整串 URL，是因为换路径前缀（`/v1` → `/openai/v1`）很常见；
            而 host 一变就意味着密钥要去别人家，必须让用户重新填。
            """
            payload = dict(payload)
            if not str(payload.get("api_key", "")).strip():
                same_provider = llm_session.provider == str(payload.get("provider", "")).strip()
                incoming = str(payload.get("base_url", "")).strip()
                # 空 base_url 表示「沿用预设默认」，那是可信的预置值，按同源处理。
                same_host = (not incoming) or same_origin(incoming, llm_session.base_url)
                if same_provider and same_host and llm_session.api_key:
                    payload["api_key"] = llm_session.api_key
                elif same_provider and not same_host and llm_session.api_key:
                    self._error(
                        400,
                        "改了服务器地址就必须重新填写 API key："
                        "已保存的 key 只在原地址上复用，不会跟着新地址发出去。",
                    )
                    return
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
            try:
                project.draft = creator.generate_draft(
                    project.meta.title, project.genre, project.premise, stats, knowledge
                )
            except ProviderError as exc:
                # 骨架生成失败是业务结果，不是服务端故障：模型少给一块、JSON 被截断、
                # 端点超时，用户能做的事（换模型 / 重试 / 调小章数）各不相同，
                # 所以必须给出可读原因，而不是落到 500「服务端内部错误」。
                self._error(422, f"骨架生成失败：{exc}")
                return
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

        def _backfill_summaries(self, project_id: str, payload: dict) -> None:
            """把「正文已落盘、摘要缺失」的章一次性补齐（存量修复，不重写已有摘要）。

            为什么需要它：章事务里的机械兜底只在**生成当时**生效，盘上已有的缺口
            永远不会被补。用户真实项目 12 章正文全在，但 ch001/ch006-ch009 没有摘要，
            于是每写下一章 ``previous_summary`` 都是空的——模型忘一章，闸门不检查。

            请求体：
              - ``dry_run: true`` 只列出缺口，不写盘（默认 false）；
              - ``provider: "ai"`` 用当前已配置模型产摘要，失败自动退机械兜底；
                不传就是纯机械兜底（不调模型、零成本、不依赖可用模型）。
            """
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目才有章节摘要")
                return

            dry_run = bool(payload.get("dry_run", False))
            provider_name = str(payload.get("provider", "") or "").strip()
            writer: object | None = None
            if provider_name in ("ai", "openai-compat"):
                resolved = self._resolve_provider(provider_name)
                if resolved is None:
                    return
                _, provider_obj = resolved
                writer = OpenAICompatChapterWriter(provider_obj)  # type: ignore[arg-type]

            results = backfill_summaries(store, project, writer=writer, dry_run=dry_run)
            self._send_json(
                200,
                {
                    "backfilled": results,
                    "count": len(results),
                    "dry_run": dry_run,
                    "cursor": project.cursor,
                },
            )

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
                # 死记录先回收：内存里没有这个任务，说明写它的线程已经没了。
                # 不回收的话台账会永远显示「这一章在跑」——那是一条既不会完成、
                # 也不会重跑、还每次打开都能看见的陈年事故。
                live = job_registry.get(run_dir.name)
                log = RunLog(runs_dir.parent, run_dir.name)
                if live is None:
                    log.reclaim_stale(
                        reason="引擎重启/退出导致这一章没有跑完，已回收为失败"
                    )
                records = []
                for path in sorted(run_dir.glob("ch*.json")):
                    try:
                        records.append(json.loads(read_text_with_retry(path)))
                    except (OSError, json.JSONDecodeError):
                        continue
                runs.append({
                    "run_id": run_dir.name,
                    # 这个 run 现在是不是还在跑：界面靠它找到「正在跑的那一次」，
                    # 从而在**重新进入项目页**后也能显示进度和暂停按钮。
                    # 只靠发起连写那次页面的内存 state 是不够的——刷新一次就没了。
                    "live": live is not None and live.state == JOB_RUNNING,
                    "job": job_status(store, project_id, run_dir.name, live),
                    "chapters": [
                        {
                            "order": record.get("order"),
                            "status": record.get("status"),
                            "verdict": record.get("gate_verdict"),
                            "attempts": record.get("attempts"),
                            # 老 run 记录（修复前写的）没有 cjk_chars 字段。
                            # 不能显示空白：正文还在 manuscripts/ 里，现算一遍即可。
                            "cjk_chars": record.get("cjk_chars")
                            or self._cjk_of_manuscript(project_id, record.get("order")),
                            "summary_source": record.get("summary_source") or "",
                            "hard_failures": record.get("gate_failures") or [],
                            "hard_failure_details": record.get("gate_details") or [],
                            # 字数提示：这章入库了，但字数不在目标带内。
                            # 它不是失败，界面必须单独显示，否则用户会以为
                            # 「字数不达标所以一直不生成」——而事实是它早就写好了。
                            "advisories": record.get("advisories") or [],
                            "phase": record.get("phase") or "",
                            "phase_detail": record.get("phase_detail") or "",
                            "error": record.get("error") or "",
                        }
                        for record in records
                    ],
                })
            self._send_json(200, {"runs": runs})

        def _cjk_of_manuscript(self, project_id: str, order: object) -> int:
            """从已落盘正文现算汉字数（老 run 记录缺 cjk_chars 时兜底）。

            台账要回答「这一章多少字」，历史记录里没有这个字段不代表没写过正文。
            读不到正文就返回 0，不抛异常——台账不该因为一章文件缺失整体失败。
            """
            if not isinstance(order, int):
                return 0
            try:
                content = store.read_manuscript(project_id, order)
            except (OSError, ValueError):
                return 0
            if not content:
                return 0
            return len(_CJK_RE.findall(content))

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

        # ---- 并行候选：同一章生成多版，读者挑一版才落盘 ------------------------
        #
        # 与 /run 的区别不是「多跑几次」：/run 每跑一次都会写正文、摘要与游标，
        # 连跑三次只留最后一版，前两版连痕迹都不剩。候选层把 N 版全存进
        # candidates_chNNN.json（各带闸门判决、预览与字数），读者点「用这一版」
        # 才走 select_candidate 提交。选中之前，盘上的正文一个字都不动。

        def _get_candidates(self, project_id: str, order_text: str) -> None:
            """读某章的候选产物；没有就回空壳，界面据此显示「还没生成」。"""
            loaded = self._load_creation_for_chapter(project_id, order_text)
            if loaded is None:
                return
            project, target = loaded
            payload = read_candidates(store, project.meta.id, target.order)
            if payload is None:
                self._send_json(
                    200,
                    {
                        "order": target.order,
                        "title": target.title,
                        "exists": False,
                        "selected_index": None,
                        "selected_at": "",
                        "candidates": [],
                    },
                )
                return
            payload["exists"] = True
            self._send_json(200, payload)

        def _generate_chapter_candidates(self, project_id: str, order_text: str, payload: dict) -> None:
            """并行生成 N 版候选；只写候选产物，不碰正文、摘要与游标。"""
            loaded = self._load_creation_for_chapter(project_id, order_text)
            if loaded is None:
                return
            project, target = loaded
            provider_name = str(payload.get("provider", "fake")).strip() or "fake"
            resolved = self._resolve_provider(provider_name)
            if resolved is None:
                return
            name, provider_obj = resolved
            try:
                count = int(payload.get("count", DEFAULT_CANDIDATES))
            except (TypeError, ValueError):
                self._error(400, f"count 必须是整数：{payload.get('count')!r}")
                return
            # 数量范围交给 generate_candidates 判：它抛 ValueError 时还没写任何东西，
            # 在这里再抄一份上下限只会多一处会跟引擎走偏的数字。
            length_gate = payload.get("length_gate", True)
            if not isinstance(length_gate, bool):
                self._error(400, f"length_gate 必须是布尔值：{length_gate!r}")
                return
            try:
                result = generate_candidates(
                    store,
                    project,
                    target.order,
                    provider=name,
                    provider_obj=provider_obj,
                    count=count,
                    length_gate=length_gate,
                )
            except ValueError as exc:
                self._error(400, str(exc))
                return
            result["exists"] = True
            self._send_json(200, result)

        def _select_chapter_candidate(self, project_id: str, order_text: str, payload: dict) -> None:
            """把读者选中的那一版提交成正式正文（正文 + 摘要 + 记录 + 游标）。"""
            loaded = self._load_creation_for_chapter(project_id, order_text)
            if loaded is None:
                return
            project, target = loaded
            raw_index = payload.get("index")
            try:
                index = int(raw_index)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                self._error(400, f"index 必须是整数：{raw_index!r}")
                return
            provider_name = str(payload.get("provider", "fake")).strip() or "fake"
            if provider_name == "fake":
                name, provider_obj = "fake", None
            elif provider_name in ("openai-compat", "ai"):
                try:
                    name, provider_obj = "openai-compat", llm_session.require_provider()
                except ProviderError:
                    # 模型没配好不能拦住提交：正文才是读者挑的那一版。
                    # provider_obj=None 会让 select_candidate 退机械摘要保底，
                    # 而不是塞一段规则生成的假摘要进真书。
                    name, provider_obj = "openai-compat", None
            else:
                self._error(400, f"未知 provider：{provider_name}（可选 fake / ai）")
                return
            auto_accept = payload.get("auto_accept", True)
            if not isinstance(auto_accept, bool):
                self._error(400, f"auto_accept 必须是布尔值：{auto_accept!r}")
                return
            try:
                selection = select_candidate(
                    store,
                    project,
                    target.order,
                    index,
                    provider=name,
                    provider_obj=provider_obj,
                    auto_accept=auto_accept,
                )
            except ValueError as exc:
                self._error(400, str(exc))
                return
            # 提交已改写 project.json（游标与 manuscripts），重读后再组装响应。
            try:
                fresh = store.get(project_id)
            except KeyError:
                fresh = project
            body = self._chapter_payload(fresh, target.order) if isinstance(fresh, CreationProject) else {}
            body["selection"] = selection
            body["candidates"] = read_candidates(store, project.meta.id, target.order)
            self._send_json(200, body)

        # ---- 写操作：走章事务（带闸门、run 记录、失败回滚）--------------------
        #
        # `_generate_chapter`（/generate）保留为"只产候选、不过闸门"的旧路径，
        # 面板要的是 /run：它调 run_chapter，判 pass 才落盘，并留下可重放的 run 记录。
        # 两条路径并存不是冗余——候选预览不需要闸门，自动连写必须经过闸门。

        def _resolve_provider(self, provider_name: str) -> tuple[str, object | None] | None:
            """把 provider 名解析成 (规范名, provider 对象)；失败时已回错误并返回 None。"""
            if provider_name == "fake":
                return "fake", None
            if provider_name in ("openai-compat", "ai"):
                try:
                    return "openai-compat", llm_session.require_provider()
                except ProviderError as exc:
                    self._error(400, str(exc))
                    return None
            self._error(400, f"未知 provider：{provider_name}（可选 fake / ai）")
            return None

        def _run_chapter_txn(self, project_id: str, order_text: str, payload: dict) -> None:
            """跑一章事务：生成 → 硬门 → 修复重写 ≤2 次 → 摘要 → 提交，返回章事务结果。"""
            loaded = self._load_creation_for_chapter(project_id, order_text)
            if loaded is None:
                return
            project, target = loaded
            provider_name = str(payload.get("provider", "fake")).strip() or "fake"
            resolved = self._resolve_provider(provider_name)
            if resolved is None:
                return
            name, provider_obj = resolved
            try:
                max_repairs = int(payload.get("max_repairs", MAX_REPAIR_ATTEMPTS))
            except (TypeError, ValueError):
                self._error(400, f"max_repairs 必须是整数：{payload.get('max_repairs')!r}")
                return
            if max_repairs < 0 or max_repairs > 5:
                self._error(400, "max_repairs 必须在 0-5 之间")
                return
            auto_accept = payload.get("auto_accept", True)
            if not isinstance(auto_accept, bool):
                self._error(400, f"auto_accept 必须是布尔值：{auto_accept!r}")
                return

            # 字数是否算硬门：默认不算（见 gates.check_chapter 的说明）。
            # 校验必须先做完再跑事务：`self._error` 只保证「不会写两次响应」，
            # 它拦不住后面的 run_chapter —— 那样会先报 400 再真写一章。
            length_gate = payload.get("length_gate", False)
            if not isinstance(length_gate, bool):
                self._error(400, f"length_gate 必须是布尔值：{length_gate!r}")
                return

            try:
                outcome = run_chapter(
                    store,
                    project,
                    target.order,
                    provider=name,
                    provider_obj=provider_obj,
                    auto_accept=auto_accept,
                    max_repairs=max_repairs,
                    length_gate=length_gate,
                )
            except ValueError as exc:
                self._error(400, str(exc))
                return
            # 事务可能已改写 project.json，重新读一遍再组装响应，避免返回旧游标。
            try:
                fresh = store.get(project_id)
            except KeyError:
                fresh = project
            body = self._chapter_payload(fresh, target.order) if isinstance(fresh, CreationProject) else {}
            body["outcome"] = {
                "order": outcome.order,
                "status": outcome.status,
                "verdict": outcome.verdict,
                "attempts": outcome.attempts,
                "cjk_chars": outcome.cjk_chars,
                "summary_present": outcome.summary_present,
                "summary_source": outcome.summary_source,
                "hard_failures": outcome.hard_failures,
                "hard_failure_details": outcome.hard_failure_details,
                # 字数提示：入库了但字数偏少。必须带出去，界面才知道
                # 这不是失败（用户的原话：「有没有可能是字数不达标导致一直不生成」）。
                "advisories": outcome.advisories,
                "sample_quotes": outcome.sample_quotes,
                "error": outcome.error,
                "run_id": outcome.run_id,
            }
            # 没提交时返回 409：正文没落盘，调用方必须知道这不是成功。
            self._send_json(200 if outcome.committed else 409, body)

        def _run_chapters_txn(self, project_id: str, payload: dict) -> None:
            """连写多章：**立刻返回 run_id**，后台线程逐章跑。

            为什么必须异步（实测事故）：同步版本跑 10 章要 373 秒，而插件侧
            对引擎有 300 秒硬超时。于是插件在第 300 秒放弃连接，引擎在第 373
            秒把 10 章全部写完，回写响应时撞 `ConnectionAbortedError`。
            用户看到「引擎 300s 内没有响应」，以为全失败，实际全成功 ——
            这类「活儿干完了但用户看到失败」比真失败更糟。

            现在：立刻回 202 + run_id，进度由 `GET /runs/<run_id>` 轮询。
            进度以磁盘 run 记录为准，所以引擎被杀重启后仍能查。

            单章 `POST /chapters/<n>/run` 保持同步：它本来就在 300 秒内，
            同步返回让「点一下写一章」有确定结果，不需要轮询。
            """
            try:
                project = store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            if not isinstance(project, CreationProject):
                self._error(400, "只有创作项目可以写章节正文")
                return
            total = len(project.draft.chapters)
            if total == 0:
                self._error(400, "请先生成并保存细纲")
                return

            def _int_field(name: str, default: int) -> int | None:
                raw = payload.get(name, default)
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    self._error(400, f"{name} 必须是整数：{raw!r}")
                    return None

            start = _int_field("from", 0)
            if start is None:
                return
            end = _int_field("to", 0)
            if end is None:
                return
            max_repairs = _int_field("max_repairs", MAX_REPAIR_ATTEMPTS)
            if max_repairs is None:
                return
            if max_repairs < 0 or max_repairs > 5:
                self._error(400, "max_repairs 必须在 0-5 之间")
                return
            provider_name = str(payload.get("provider", "fake")).strip() or "fake"
            resolved = self._resolve_provider(provider_name)
            if resolved is None:
                return
            name, provider_obj = resolved
            auto_accept = payload.get("auto_accept", True)
            if not isinstance(auto_accept, bool):
                self._error(400, f"auto_accept 必须是布尔值：{auto_accept!r}")
                return
            # 字数是否算硬门：默认不算（见 gates.check_chapter 的说明）。
            # 连写这条路也必须能传，否则「我想严格控字数」在批量模式下做不到。
            length_gate = payload.get("length_gate", False)
            if not isinstance(length_gate, bool):
                self._error(400, f"length_gate 必须是布尔值：{length_gate!r}")
                return

            first = start or (project.cursor + 1)
            last = end or total
            if not start and first > total:
                # 已经写到最后一章：这不是「区间不合法」，用户没填区间，
                # 报 7-6 只会让人以为是自己填错了。
                self._error(400, f"这本书已经写到最后一章了（细纲共 {total} 章，游标 {project.cursor}）")
                return
            if first < 1 or last > total or first > last:
                self._error(400, f"章号区间不合法：{first}-{last}（细纲共 {total} 章）")
                return

            # 同一项目同时只允许一个连写：两个线程同时写 project.json 会互相覆盖，
            # 表现为「跑完发现少了几章」或「游标跳回」。
            running = job_registry.running_for(project_id)
            if running is not None:
                self._send_json(409, {
                    "error": f"这个项目已经有一次连写在跑（{running.run_id}，第 {running.first}-{running.last} 章）",
                    "run_id": running.run_id,
                })
                return

            job = job_registry.start(
                store,
                project,
                first=first,
                last=last,
                provider=name,
                provider_obj=provider_obj,
                auto_accept=auto_accept,
                max_repairs=max_repairs,
                length_gate=length_gate,
                run_id=payload.get("run_id"),
            )
            # 202 而不是 200：请求被接受，但活儿还没干完。用 200 会让调用方
            # 以为 `written` 就是最终结果。
            self._send_json(202, {
                "run_id": job.run_id,
                "status": job.state,
                "from": job.first,
                "to": job.last,
                "total": job.total,
                "poll": f"/api/projects/{project_id}/runs/{job.run_id}",
            })

        def _pause_batch_job(self, project_id: str, run_id: str) -> None:
            """请求暂停一次连写：只在两章之间停，当前章照常提交完。

            为什么不做「立刻中断」：一章的事务是原子的（写 run 记录 → 生成 →
            过闸门 → 覆盖写正文与摘要 → 提交游标）。生成中途硬掐会留下半截正文，
            而 `write_manuscript` 是整篇覆盖写 —— 半截正文会把上一版好内容冲掉。
            所以这里只是举手，后台线程在下一章开始前退出，用户拿到的是
            「这一章写完就停」。
            """
            try:
                store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            job = job_registry.pause(run_id)
            if job is None or job.state != JOB_RUNNING:
                # 内存里没有，或已经跑完了：可能是引擎重启过，或 run_id 写错了。
                # 从磁盘读一次，能把「其实已经写完了」说清楚，而不是笼统报 404。
                disk = job_status(store, project_id, run_id, job)
                if disk["status"] == JOB_INTERRUPTED and not disk["chapters"]:
                    self._error(404, f"找不到这次连写：{run_id}")
                    return
                self._send_json(409, {
                    "error": "这次连写已经不在运行了，无法暂停",
                    "job": disk,
                })
                return
            self._send_json(202, {
                "run_id": job.run_id,
                "status": JOB_RUNNING,
                "pause_requested": True,
                "note": "当前这一章会写完再停，已提交的章不受影响",
            })

        def _get_batch_job(self, project_id: str, run_id: str) -> None:
            """轮询一次连写任务进度。"""
            try:
                store.get(project_id)
            except KeyError:
                self._error(404, "项目不存在")
                return
            job = job_registry.get(run_id)
            self._send_json(200, job_status(store, project_id, run_id, job))

        def _restore_chapter_version(self, project_id: str, order_text: str, payload: dict) -> None:
            """把某章回退到历史版本；当前正文会先被存档，回退本身可撤销。"""
            loaded = self._load_creation_for_chapter(project_id, order_text)
            if loaded is None:
                return
            project, target = loaded
            stamp = str(payload.get("stamp", "")).strip()
            if not stamp:
                self._error(400, "缺少 stamp（版本时间戳）")
                return
            content = store.restore_version(project_id, target.order, stamp)
            if content is None:
                self._error(404, "找不到该版本")
                return
            record = project.manuscripts.get(f"ch{target.order}")
            if record is not None:
                record.status = "draft"
                record.word_count = len(re.sub(r"\s+", "", content))
                record.model_id = f"restored:{stamp}"
                record.updated_at = store.now_iso()
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
    token: str = "",
) -> ThreadingHTTPServer:
    """构建并返回面板服务器（已 listen，未 serve_forever；便于测试与 CLI 复用）。

    token 非空时开启访问校验，并把 token 挂到 server 上供 api_request 使用。
    """
    store = WorkspaceStore(workspace)
    llm_session = LLMSession()
    jobs = JobRegistry()
    server = ThreadingHTTPServer(
        (host, port), create_handler(store, llm_session, jobs, token=token)
    )
    server.workspace_store = store  # type: ignore[attr-defined]
    server.llm_session = llm_session  # type: ignore[attr-defined]
    server.jobs = jobs  # type: ignore[attr-defined]
    server.panel_token = token  # type: ignore[attr-defined]
    return server


def serve_in_thread(
    workspace: Path, *, port: int = 8765, token: str = ""
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """在守护线程中启动面板，主要供测试使用。"""
    server = run_server(workspace, port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def api_request(server: ThreadingHTTPServer, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    """测试辅助：不经网络栈直接向服务器发 JSON 请求（使用本地 socket）。

    服务器开了访问校验时，这里自动带上 `X-Ndecon-Token`：测试用例不该
    为了鉴权在每个断言里重复拼头，鉴权本身另有专门用例覆盖。
    """
    import urllib.request

    host, port = server.server_address[0], server.server_address[1]
    safe_path = quote(path, safe="/?=&")
    url = f"http://{host}:{port}{safe_path}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"} if data is not None else {}
    token = getattr(server, "panel_token", "")
    if token:
        headers["X-Ndecon-Token"] = token
    request: Request = Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
