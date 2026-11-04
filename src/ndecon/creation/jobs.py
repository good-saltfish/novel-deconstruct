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

import threading
from dataclasses import dataclass, field
from pathlib import Path

from ndecon.creation.runlog import RunLog, new_run_id, now_iso
from ndecon.creation.txn import run_chapter
from ndecon.workspace.models import CreationProject
from ndecon.workspace.store import WorkspaceStore

# 任务状态：running 在跑；finished 全部跑完；stopped 遇失败提前停；
# interrupted 内存里没有这个任务但磁盘上有 in_progress 记录（引擎被重启/被杀）。
JOB_RUNNING = "running"
JOB_FINISHED = "finished"
JOB_STOPPED = "stopped"
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
                # 每章重新读项目：上一章的游标与摘要必须对本轮可见，
                # 否则会重复写已提交章。
                fresh = store.get(job.project_id)
                if not isinstance(fresh, CreationProject):
                    job.error = "项目已不存在或不再是创作项目"
                    job.state = JOB_STOPPED
                    break
                outcome = run_chapter(
                    store,
                    fresh,
                    order,
                    provider=provider,
                    provider_obj=provider_obj,
                    run_id=job.run_id,
                    auto_accept=auto_accept,
                    max_repairs=max_repairs,
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
            else:
                job.state = JOB_FINISHED
        except Exception as exc:  # noqa: BLE001 - 后台线程不能让异常逃出去
            job.error = f"连写中断：{exc}"
            job.state = JOB_STOPPED
        finally:
            job.ended_at = now_iso()


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
        }

    # 磁盘推断：任务不在内存里（引擎重启过，或这是上一次会话的 run_id）。
    project_dir = store.projects_dir / project_id
    log = RunLog(project_dir, run_id)
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
        }
    orders = [rec.order for rec in records]
    written = sum(1 for rec in records if rec.status == "committed")
    failures = sum(1 for rec in records if rec.status != "committed")
    unsettled = any(not rec.settled for rec in records)
    return {
        "run_id": run_id,
        "status": JOB_INTERRUPTED if unsettled else JOB_STOPPED,
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
                "error": rec.error,
            }
            for rec in records
        ],
        "error": "引擎重启过，任务已不在内存里；上面的进度是从 run 记录读出来的" if unsettled else "",
        "started_at": records[0].started_at,
        "ended_at": records[-1].ended_at,
    }


def project_dir_for(store: WorkspaceStore, project_id: str) -> Path:
    """项目目录（连写记录落在这里的 runs/ 下）。"""
    return store.projects_dir / project_id
