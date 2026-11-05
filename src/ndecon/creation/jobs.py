"""后台连写任务：把「跑 N 章」从 HTTP 请求里摘出来。

为什么必须有这一层（实测事故）：

    原来 `POST /api/projects/<id>/runs` 是**同步**端点 —— 请求进来，逐章生成，
    全部跑完才写响应。插件侧对引擎有 300 秒硬超时（`PROXY_TIMEOUT_MS`），
    而实测一次 10 章连写耗时 373 秒（每章 24-81 秒）。于是：

      - 插件在第 300 秒放弃连接；
      - 引擎在第 373 秒把 10 章全部写完（`cursor=10`，正文与摘要都在），
        然后回写响应时撞上 `ConnectionAbortedError: [WinError 10053]`。

    用户看到的是「引擎 300s 内没有响应」，以为全失败了，实际全成功。
    这类「活儿干完了但用户看到失败」比真失败更糟：用户会重试，而重试时
    `cursor` 已是 10，会从第 11 章开始 —— 他不会重复写，但他不知道。

修法：把批量连写改成后台任务。

    POST /runs        -> 立刻返回 202 + run_id，后台线程逐章跑
    GET  /runs/<id>   -> 轮询进度（从磁盘 run 记录读，所以引擎重启后仍可查）

单章生成（`POST /chapters/<n>/run`）保持同步：它本来就在 300 秒内，同步返回
让「点一下写一章」有确定结果，不需要轮询。

纪律：

1. 任务状态以**磁盘 run 记录**为准，内存只做加速。引擎被杀后重新查同一个
   run_id，仍能从 `runs/<run_id>/ch*.json` 拼出「跑到第几章」；
2. 后台线程里的任何异常都必须被接住并记进任务状态，绝不让它逃到
   `ThreadingHTTPServer` 的线程池里（那会只剩一条 stderr，用户什么也看不到）；
3. 同一项目同时只允许一个连写任务：两个线程同时写 `project.json` 会互相覆盖。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ndecon.creation.runlog import RunLog, new_run_id, now_iso
from ndecon.creation.txn import run_chapter
from ndecon.workspace.atomic import atomic_write_json, read_text_with_retry
from ndecon.workspace.models import CreationProject
from ndecon.workspace.store import WorkspaceStore

# 任务状态：running 在跑；finished 全部跑完；stopped 遇失败提前停；
# paused 用户手动暂停（停在两章之间，当前章的事务已经提交）；
# interrupted 内存里没有这个任务但磁盘上有 in_progress 记录（引擎被重启/被杀）。
JOB_RUNNING = "running"
JOB_FINISHED = "finished"
JOB_STOPPED = "stopped"
JOB_PAUSED = "paused"
JOB_INTERRUPTED = "interrupted"


@dataclass
class BatchJob:
    """一次连写任务的运行态（内存侧；磁盘侧以 RunLog 为准）。"""

    run_id: str
    project_id: str
    first: int
    last: int
    state: str = JOB_RUNNING
    written: int = 0
    failures: int = 0
    error: str = ""
    started_at: str = ""
    ended_at: str = ""
    chapters: list[dict] = field(default_factory=list)
    pause_requested: bool = False
    # 当前正在写第几章、处在哪一步、什么时候开始的。
    # 这三个字段存在的唯一理由：让界面能回答「现在在干嘛、干了多久」。
    # 没有它们，用户面对的只有「连写中…」四个字，而一章真模型要几分钟——
    # 于是他会以为卡死了，去点「重新生成」，撞上「已经有一次连写在跑」。
    current_order: int = 0
    current_phase: str = ""
    current_detail: str = ""
    current_since: str = ""
    # 字数是否当硬门（默认否）。连写也必须能选，否则「想严格控字数」在批量
    # 模式下做不到，用户只能一章一章手点。
    length_gate: bool = False

    @property
    def total(self) -> int:
        """本次任务的章数。"""
        return self.last - self.first + 1


class JobRegistry:
    """进程内的连写任务表。

    只存运行态，不存业务数据：正文、摘要、run 记录全在磁盘上，任务表丢了
    （引擎重启）也不影响数据完整性，只是状态要从磁盘重新推断。
    """

    def __init__(self) -> None:
        """建一个空任务表。"""
        self._jobs: dict[str, BatchJob] = {}
        self._lock = threading.Lock()

    def get(self, run_id: str) -> BatchJob | None:
        """按 run_id 取任务；不存在返回 None。"""
        with self._lock:
            return self._jobs.get(run_id)

    def running_for(self, project_id: str) -> BatchJob | None:
        """该项目当前是否有连写在跑（同项目不允许并发写 project.json）。"""
        with self._lock:
            for job in self._jobs.values():
                if job.project_id == project_id and job.state == JOB_RUNNING:
                    return job
        return None

    def register(self, job: BatchJob) -> None:
        """把任务放进表里。"""
        with self._lock:
            self._jobs[job.run_id] = job

    def pause(self, run_id: str) -> BatchJob | None:
        """请求暂停某个任务；返回任务本身（不存在返回 None）。

        为什么只置标记、不杀线程：一章的事务是原子的（先写 run 记录 → 生成 →
        过闸门 → 写正文与摘要 → 提交游标）。在生成中途硬掐会留下半截正文，
        而 `write_manuscript` 是整篇覆盖写 —— 半截正文会把上一版好内容冲掉。
        所以暂停只能发生在两章之间：这里只举手，后台线程在下一章开始前
        检查标记并退出循环。

        已经在跑的最后一章不会被打断，用户拿到的是「这一章写完就停」。

        已经跑完/已停下的任务不置标记：否则用户会对一个结束了的任务
        「暂停成功」，而实际什么也没发生。
        """
        with self._lock:
            job = self._jobs.get(run_id)
            if job is None:
                return None
            if job.state == JOB_RUNNING:
                job.pause_requested = True
            return job

    def start(
        self,
        store: WorkspaceStore,
        project: CreationProject,
        *,
        first: int,
        last: int,
        provider: str,
        provider_obj: object | None,
        auto_accept: bool,
        max_repairs: int,
        length_gate: bool = False,
        run_id: str | None = None,
    ) -> BatchJob:
        """起一个后台连写任务并立刻返回（不等第一章跑完）。

        为什么用普通 `Thread` 而不是 `ThreadPoolExecutor`：任务要活到几十分钟，
        线程池的工作线程会被回收；这里每个项目最多一个任务，线程数天然有界。
        """
        job = BatchJob(
            run_id=(run_id or "").strip() or new_run_id(),
            project_id=project.meta.id,
            first=first,
            last=last,
            started_at=now_iso(),
            length_gate=length_gate,
        )
        self.register(job)
        thread = threading.Thread(
            target=self._run,
            args=(store, job, provider, provider_obj, auto_accept, max_repairs),
            name=f"ndecon-batch-{job.run_id}",
            daemon=True,
        )
        thread.start()
        return job

    def _run(
        self,
        store: WorkspaceStore,
        job: BatchJob,
        provider: str,
        provider_obj: object | None,
        auto_accept: bool,
        max_repairs: int,
    ) -> None:
        """后台线程主体：逐章跑事务，任何异常都记进任务状态。"""
        try:
            for order in range(job.first, job.last + 1):
                # 用户点了暂停：只在两章之间停。当前章的事务已经完整提交，
                # 所以这里退出不会留下半截正文，也不会丢已写好的章。
                if job.pause_requested:
                    job.state = JOB_PAUSED
                    job.error = f"已暂停：第 {job.first}-{order - 1} 章已提交，第 {order}-{job.last} 章未写"
                    return
                # 每章重新读项目：上一章的游标与摘要必须对本轮可见，
                # 否则会重复写已提交章。
                fresh = store.get(job.project_id)
                if not isinstance(fresh, CreationProject):
                    job.error = "项目已不存在或不再是创作项目"
                    job.state = JOB_STOPPED
                    break
                # 进度回调：把「现在在第几章、哪一步」写进内存任务对象。
                # 每章调好几次，界面轮询 2 秒一次就能看到它动 —— 这是
                # 「慢」和「死」之间唯一的区别。
                def on_phase(name: str, detail: str, *, _order: int = order) -> None:
                    job.current_order = _order
                    job.current_phase = name
                    job.current_detail = detail
                    job.current_since = now_iso()

                job.current_order = order
                job.current_phase = "start"
                job.current_detail = f"第 {order} 章开始"
                job.current_since = now_iso()
                outcome = run_chapter(
                    store,
                    fresh,
                    order,
                    provider=provider,
                    provider_obj=provider_obj,
                    run_id=job.run_id,
                    auto_accept=auto_accept,
                    max_repairs=max_repairs,
                    length_gate=job.length_gate,
                    on_phase=on_phase,
                )
                job.chapters.append({
                    "order": outcome.order,
                    "status": outcome.status,
                    "verdict": outcome.verdict,
                    "attempts": outcome.attempts,
                    "cjk_chars": outcome.cjk_chars,
                    "summary_present": outcome.summary_present,
                    "summary_source": outcome.summary_source,
                    "hard_failures": outcome.hard_failures,
                    "hard_failure_details": outcome.hard_failure_details,
                    "advisories": outcome.advisories,
                    "error": outcome.error,
                })
                if outcome.committed:
                    job.written += 1
                else:
                    job.failures += 1
                    # 连续失败通常是系统性问题（provider 挂了、提示词崩了），
                    # 硬写下去只会把整本写坏，所以停下。
                    job.state = JOB_STOPPED
                    break
                job.current_phase = "done"
                job.current_detail = f"第 {order} 章已提交"
            else:
                job.state = JOB_FINISHED
        except Exception as exc:  # noqa: BLE001 - 后台线程不能让异常逃出去
            job.error = f"连写中断：{exc}"
            job.state = JOB_STOPPED
        finally:
            job.ended_at = now_iso()
            job.current_phase = "done" if job.state == JOB_FINISHED else job.current_phase
            # 把终态写进 run 目录：引擎重启后内存任务表是空的，光看 ch*.json
            # 分不清「用户点了暂停」和「半路崩了」——两者都表现为「写了几章就没了」。
            _write_job_state(store, job)


def _job_state_path(store: WorkspaceStore, project_id: str, run_id: str) -> Path:
    """任务终态的落盘位置（runs/<run_id>/job.json）。"""
    return project_dir_for(store, project_id) / "runs" / run_id / "job.json"


def _write_job_state(store: WorkspaceStore, job: BatchJob) -> None:
    """把任务终态写到磁盘；写失败不影响已提交的章节。

    为什么需要它：`runs/<run_id>/ch*.json` 只记录「哪几章跑过」，分不清
    「用户点了暂停」和「引擎半路崩了」——两者在磁盘上都是「写了几章就没下文」。
    引擎重启后内存任务表是空的，`job_status` 只能从磁盘推断，那时就只能报
    「中断」，用户会以为暂停是崩溃。落一份 job.json 才能把结论固定下来。
    """
    payload = {
        "run_id": job.run_id,
        "project_id": job.project_id,
        "status": job.state,
        "from": job.first,
        "to": job.last,
        "written": job.written,
        "failures": job.failures,
        "error": job.error,
        "started_at": job.started_at,
        "ended_at": job.ended_at,
    }
    try:
        atomic_write_json(_job_state_path(store, job.project_id, job.run_id), payload)
    except OSError:
        return


def _read_job_state(store: WorkspaceStore, project_id: str, run_id: str) -> dict | None:
    """读回任务终态；不存在或损坏返回 None。"""
    path = _job_state_path(store, project_id, run_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(read_text_with_retry(path))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def job_status(store: WorkspaceStore, project_id: str, run_id: str, job: BatchJob | None) -> dict:
    """把任务状态摊平成可序列化结构；内存里没有就从磁盘推断。

    引擎重启后内存任务表是空的，但 `runs/<run_id>/ch*.json` 还在 —— 必须
    能从磁盘读出「跑到第几章、有没有失败」，否则用户刷新页面就以为全丢了。
    """
    if job is not None:
        return {
            "run_id": job.run_id,
            "status": job.state,
            "from": job.first,
            "to": job.last,
            "total": job.total,
            "written": job.written,
            "failures": job.failures,
            "chapters": job.chapters,
            "error": job.error,
            "started_at": job.started_at,
            "ended_at": job.ended_at,
            "pause_requested": job.pause_requested,
            # 活进度：界面靠这四个字段回答「现在在干嘛、干了多久」。
            "current_order": job.current_order,
            "current_phase": job.current_phase,
            "current_detail": job.current_detail,
            "current_since": job.current_since,
        }

    # 磁盘推断：任务不在内存里（引擎重启过，或这是上一次会话的 run_id）。
    project_dir = store.projects_dir / project_id
    log = RunLog(project_dir, run_id)
    # 先回收死记录：内存里没有这个任务，说明它的线程已经死了。那些 in_progress
    # 记录如果不结算，台账会永远显示「这一章在跑」，而它既不会重跑也不会完成。
    # 回收发生在推断之前，所以下面的 unsettled 判断看到的是已经结算过的盘。
    reclaimed = log.reclaim_stale(reason="引擎重启/退出导致这一章没有跑完，已回收为失败")
    records = log.list_records()
    if not records:
        return {
            "run_id": run_id,
            "status": JOB_INTERRUPTED,
            "from": 0,
            "to": 0,
            "total": 0,
            "written": 0,
            "failures": 0,
            "chapters": [],
            "error": "找不到这次连写的记录",
            "started_at": "",
            "ended_at": "",
            "pause_requested": False,
        }
    orders = [rec.order for rec in records]
    written = sum(1 for rec in records if rec.status == "committed")
    failures = sum(1 for rec in records if rec.status != "committed")
    # 回收过的记录已经是 failed 了，所以不能再拿「有没有 unsettled」当判据——
    # 那次中断的事实只能从回收结果里读回来。
    unsettled = bool(reclaimed) or any(not rec.settled for rec in records)

    # 状态推断必须区分「跑完了」和「半路停下」：
    # 全部 committed 且没有未结算记录 = 这次连写其实成功了，只是任务对象被回收
    # （引擎重启或内存表清理）。若一律报 stopped，界面会显示「中途停下」，
    # 而用户看到的是 10 章全在 —— 又是一次「活儿干完了但显示失败」。
    #
    # 落盘的 job.json 优先：用户主动暂停时，磁盘上同样只有「写了几章就没下文」，
    # 光靠记录推断会报成 interrupted（崩溃），把用户的正常操作说成故障。
    saved = _read_job_state(store, project_id, run_id)
    if isinstance(saved, dict) and saved.get("status") in {
        JOB_PAUSED,
        JOB_STOPPED,
        JOB_FINISHED,
        JOB_INTERRUPTED,
    }:
        state = str(saved.get("status"))
        saved_error = str(saved.get("error") or "")
    elif unsettled:
        state = JOB_INTERRUPTED
        saved_error = ""
    elif failures:
        state = JOB_STOPPED
        saved_error = ""
    else:
        state = JOB_FINISHED
        saved_error = ""
    # 最后一条记录承载「跑到哪一步」：phase 由 txn 在每步开头落盘，
    # 所以即使引擎重启，这里也能说出「中断在第 N 章的生成阶段」。
    last = records[-1] if records else None
    return {
        "run_id": run_id,
        "status": state,
        "from": min(orders),
        "to": max(orders),
        "total": len(records),
        "written": written,
        "failures": failures,
        "chapters": [
            {
                "order": rec.order,
                "status": rec.status,
                "verdict": rec.gate_verdict,
                "attempts": rec.attempts,
                "cjk_chars": rec.cjk_chars,
                "summary_present": rec.summary_present,
                "summary_source": rec.summary_source,
                "hard_failures": rec.gate_failures,
                "hard_failure_details": rec.gate_details,
                "advisories": rec.advisories,
                "error": rec.error,
            }
            for rec in records
        ],
        "error": saved_error
        or (
            "引擎重启过，任务已不在内存里；上面的进度是从 run 记录读出来的"
            if unsettled
            else "任务已不在内存里（引擎重启过），进度是从 run 记录读出来的"
        ),
        "started_at": records[0].started_at,
        "ended_at": records[-1].ended_at,
        "pause_requested": False,
        # 活进度只能从磁盘上最后一条记录读回来：引擎重启后内存任务表是空的，
        # 但那一章「死在哪一步」还写在 ch*.json 里。没有这一步，界面只能说
        # 「中断」，说不出「中断在第 13 章生成阶段」。
        "current_order": last.order if last else 0,
        "current_phase": last.phase if last else "",
        "current_detail": last.phase_detail if last else "",
        "current_since": last.phase_at if last else "",
    }


def project_dir_for(store: WorkspaceStore, project_id: str) -> Path:
    """项目目录（连写记录落在这里的 runs/ 下）。"""
    return store.projects_dir / project_id
