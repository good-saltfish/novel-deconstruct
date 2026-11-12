"""进度可见性：phase 落盘、活进度字段、字数提示不阻断。

用户的原话是「慢的让我觉得是生成失败了」和「想暂停该生成都不行」。
这两句背后是同一件事：界面上没有任何东西能证明任务还活着。这些测试钉住
「活着这件事必须能从磁盘读出来」——引擎重启后内存任务表是空的，
只剩 ch*.json 里的 phase。
"""

from __future__ import annotations

import json
import time

import pytest
from tests.unit.test_creation_txn import _long_text, _project, _store

from ndecon.creation.jobs import JOB_FINISHED, JOB_RUNNING, BatchJob, job_status
from ndecon.creation.runlog import ChapterRun, RunLog, new_run_id


@pytest.mark.parametrize(
    "bad",
    ["..", ".", "con", "CON", "a/b", "a\\b", "ch:1", "", "   ", "x" * 70],
)
def test_run_id_rejects_traversal_and_reserved(tmp_path, bad) -> None:
    """run_id 来自 HTTP 请求体，必须是单段安全文件名。

    原判据只挡 `/` 与 `\\`，而 `..` 两个都不含——它会让 ch001.json 与
    job.json 落到项目目录之外（功能审计 B-3）。Windows 侧还要挡设备名，
    否则 `con` 会打开控制台设备而不是文件。
    """
    with pytest.raises(ValueError):
        RunLog(tmp_path, bad)


@pytest.mark.parametrize("good", ["run-20261109-232900", "run-x", "a.b", "R_1"])
def test_run_id_accepts_normal_ids(tmp_path, good) -> None:
    """正常的 run_id 不能被误杀。"""
    log = RunLog(tmp_path, good)
    assert log.run_id == good
    assert log.dir == tmp_path / "runs" / good


def test_touch_phase_writes_to_disk(tmp_path) -> None:
    """阶段必须落盘：界面读的是磁盘，不是内存。"""
    store = _store(tmp_path)
    project = _project(store)
    log = RunLog(store.projects_dir / project.meta.id, new_run_id())
    record = ChapterRun(
        run_id=log.run_id,
        project_id=project.meta.id,
        order=1,
        provider="fake",
        started_at="2026-11-05T18:00:00",
    )
    log.write(record)
    log.touch_phase(1, "writing", "第 1 次生成（目标 2600 字）")

    saved = log.read(1)
    assert saved is not None
    assert saved.phase == "writing"
    assert saved.phase_detail == "第 1 次生成（目标 2600 字）"
    assert saved.phase_at, "必须记时间，否则界面算不出『已经等了多久』"


def test_touch_phase_ignores_settled_record(tmp_path) -> None:
    """已结算的章不该被进度覆盖：那会让台账把跑完的章显示成还在写。"""
    store = _store(tmp_path)
    project = _project(store)
    log = RunLog(store.projects_dir / project.meta.id, new_run_id())
    record = ChapterRun(
        run_id=log.run_id,
        project_id=project.meta.id,
        order=1,
        provider="fake",
        started_at="2026-11-05T18:00:00",
        status="committed",
        ended_at="2026-11-05T18:01:00",
    )
    log.write(record)
    log.touch_phase(1, "writing", "不该写进去")
    saved = log.read(1)
    assert saved is not None
    assert saved.phase == ""


def test_committed_chapter_ends_at_done_phase(tmp_path, monkeypatch) -> None:
    """成功的那一章，盘上的 phase 必须是 done，不能停在 commit。"""
    from tests.unit.test_creation_txn import _GoodWriter

    import ndecon.creation.txn as txn

    store = _store(tmp_path)
    project = _project(store)
    writer = _GoodWriter(_long_text(2600))
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)

    outcome = txn.run_chapter(store, project, 1, provider="fake")
    assert outcome.status == "committed"
    record = RunLog(store.projects_dir / project.meta.id, outcome.run_id).read(1)
    assert record is not None
    assert record.phase == "done"
    assert "已提交" in record.phase_detail


def test_phases_are_reported_in_order(tmp_path, monkeypatch) -> None:
    """回调按真实顺序报阶段：拼上下文 → 生成 → 闸门 → 摘要 → 提交 → 完成。"""
    from tests.unit.test_creation_txn import _GoodWriter

    import ndecon.creation.txn as txn

    store = _store(tmp_path)
    project = _project(store)
    writer = _GoodWriter(_long_text(2600))
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)

    seen: list[str] = []
    txn.run_chapter(store, project, 1, provider="fake", on_phase=lambda name, detail: seen.append(name))
    assert seen == ["context", "writing", "gate", "summary", "commit", "done"]


def test_phase_callback_error_does_not_break_chapter(tmp_path, monkeypatch) -> None:
    """进度显示坏了不能毁掉这一章：回调抛异常也要写完并提交。"""
    from tests.unit.test_creation_txn import _GoodWriter

    import ndecon.creation.txn as txn

    store = _store(tmp_path)
    project = _project(store)
    writer = _GoodWriter(_long_text(2600))
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)

    def boom(name: str, detail: str) -> None:
        raise RuntimeError("界面炸了")

    outcome = txn.run_chapter(store, project, 1, provider="fake", on_phase=boom)
    assert outcome.status == "committed"


def test_job_status_exposes_live_progress(tmp_path) -> None:
    """内存任务必须带出「第几章、哪一步、什么时候开始的」。"""
    store = _store(tmp_path)
    project = _project(store)
    job = BatchJob(
        run_id="run-live",
        project_id=project.meta.id,
        first=1,
        last=3,
        state=JOB_RUNNING,
        started_at="2026-11-05T18:00:00",
        current_order=2,
        current_phase="writing",
        current_detail="第 2 次生成（目标 2600 字）",
        current_since="2026-11-05T18:03:00",
    )
    payload = job_status(store, project.meta.id, job.run_id, job)
    assert payload["current_order"] == 2
    assert payload["current_phase"] == "writing"
    assert payload["current_detail"] == "第 2 次生成（目标 2600 字）"
    assert payload["current_since"] == "2026-11-05T18:03:00"


def test_job_status_reads_phase_from_disk_after_restart(tmp_path, monkeypatch) -> None:
    """引擎重启后内存表空了，但「死在哪一步」必须还能从磁盘读出来。"""
    from tests.unit.test_creation_txn import _GoodWriter

    import ndecon.creation.txn as txn

    store = _store(tmp_path)
    project = _project(store)
    writer = _GoodWriter(_long_text(2600))
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)

    run_id = new_run_id()
    log = RunLog(store.projects_dir / project.meta.id, run_id)
    record = ChapterRun(
        run_id=run_id,
        project_id=project.meta.id,
        order=1,
        provider="fake",
        started_at="2026-11-05T18:00:00",
    )
    log.write(record)
    log.touch_phase(1, "writing", "第 1 次生成（目标 2600 字）")

    # job=None 模拟引擎重启：内存任务表里已经没有这个 run 了
    payload = job_status(store, project.meta.id, run_id, None)
    assert payload["current_order"] == 1
    assert payload["current_phase"] == "writing"
    assert payload["current_detail"] == "第 1 次生成（目标 2600 字）"


def test_list_runs_marks_live_run(tmp_path, monkeypatch) -> None:
    """run 列表必须标出「哪一次还在跑」：界面靠它恢复暂停按钮。"""
    from tests.unit.test_creation_txn import _GoodWriter

    import ndecon.creation.txn as txn

    store = _store(tmp_path)
    project = _project(store)
    writer = _GoodWriter(_long_text(2600))
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)

    outcome = txn.run_chapter(store, project, 1, provider="fake")
    job = BatchJob(
        run_id=outcome.run_id,
        project_id=project.meta.id,
        first=2,
        last=3,
        state=JOB_FINISHED,
        started_at="2026-11-05T18:05:00",
    )
    payload = job_status(store, project.meta.id, outcome.run_id, job)
    assert payload["status"] == JOB_FINISHED
    # 已结束的任务不该被当成「还在跑」
    assert payload["current_phase"] in {"", "done"}


def test_advisories_land_in_chapter_record(tmp_path, monkeypatch) -> None:
    """字数提示要落进 ch*.json：界面靠它区分「入库了但字数偏少」和「失败了」。"""
    from tests.unit.test_creation_txn import _GoodWriter

    import ndecon.creation.txn as txn

    store = _store(tmp_path)
    project = _project(store)
    writer = _GoodWriter(_long_text(1200))  # 干净但偏短
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)

    outcome = txn.run_chapter(store, project, 1, provider="fake", max_repairs=1)
    assert outcome.status == "committed"
    path = store.projects_dir / project.meta.id / "runs" / outcome.run_id / "ch001.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["advisories"]
    assert any("字数不在目标带内" in note for note in payload["advisories"])


def test_pause_only_between_chapters(tmp_path, monkeypatch) -> None:
    """暂停是「举手」而不是「掐断」：当前章必须完整提交后才停。"""
    from tests.unit.test_creation_txn import _GoodWriter

    import ndecon.creation.jobs as jobs_module
    import ndecon.creation.txn as txn

    class _SlowWriter(_GoodWriter):
        """每章慢一点：让「暂停确实落在第 1、2 章之间」成为确定的事。

        `_GoodWriter` 本身是瞬时返回的，三章可能在测试来得及举手之前就跑完了
        ——那会让 `assert live.state == JOB_PAUSED` 变成靠线程调度的运气。
        这里只是把写正文拖慢一点，正文内容与新鲜度门的行为都不变。
        """

        def write_chapter(self, pack):  # noqa: ANN001
            time.sleep(0.15)
            return super().write_chapter(pack)

    store = _store(tmp_path)
    project = _project(store, chapters=3)
    # 各章正文必须不同：同一个 run 里连写三章，若三章正文一模一样会被
    # `cross_chapter_similarity` 硬门正确拦下（`_GoodWriter` 的 docstring 也这么写）。
    # 这里本来就只看「暂停有没有把在写的那一章切成半截」，不该被新鲜度门干扰。
    writer = _SlowWriter(lambda order: _long_text(2600, seed=20261101 + order))
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)
    monkeypatch.setattr(jobs_module, "run_chapter", txn.run_chapter)

    registry = jobs_module.JobRegistry()
    job = registry.start(
        store,
        store.get(project.meta.id),
        first=1,
        last=3,
        provider="fake",
        provider_obj=None,
        auto_accept=True,
        max_repairs=1,
    )
    # 在**第一章写着的时候**举手：这才是「当前章照常提交完，下一章不开始」。
    # 直接在 start() 之后立刻 pause 是靠线程调度赌运气——赌赢了暂停落在开写
    # 之前（written=0，也是合法结果），赌输了落在一章中间。这里等它真的进入
    # 第一章的某个进行中阶段再举手，语义就固定了。
    _wait_until(lambda: job.current_order >= 1 and job.current_phase not in ("", "start"), job)
    registry.pause(job.run_id)
    for _ in range(500):
        if registry.get(job.run_id) is None or registry.get(job.run_id).state != JOB_RUNNING:
            break
        time.sleep(0.02)
    live = registry.get(job.run_id)
    assert live is not None
    assert live.state == jobs_module.JOB_PAUSED
    # 举手时第一章还没提交完，但它必须被完整写完才停 —— 这正是「举手不掐断」
    assert live.written == 1
    assert live.written < 3, "暂停后不许把剩下的章偷偷写完"
    saved = store.read_manuscript(project.meta.id, 1)
    assert saved and len(saved) > 1000


def _run_batch(store, project, *, first, last, writer, run_id=None):
    """起一个连写任务并等它跑完，返回最终的 BatchJob。"""
    import ndecon.creation.jobs as jobs_module

    registry = jobs_module.JobRegistry()
    job = registry.start(
        store,
        store.get(project.meta.id),
        first=first,
        last=last,
        provider="fake",
        provider_obj=None,
        auto_accept=True,
        max_repairs=1,
        run_id=run_id,
    )
    for _ in range(3000):
        if job.state != JOB_RUNNING:
            break
        time.sleep(0.01)
    return job


def test_resume_skips_committed_chapters(tmp_path, monkeypatch) -> None:
    """同一个 run_id 再跑一次：已 committed 的章必须跳过，不重写、不推游标。

    用户的原话是「中途失败只能手动重新开始」。这里钉住续跑的两个后果：
    已经写好的章不再白烧一轮 token，也不会把用户可能改过的稿子覆盖掉；
    跳过的章单独计入 `skipped`，`written` 只算本轮真写的。
    """
    from tests.unit.test_creation_txn import _GoodWriter

    import ndecon.creation.jobs as jobs_module
    import ndecon.creation.txn as txn

    store = _store(tmp_path)
    project = _project(store, chapters=3)
    writer = _GoodWriter(lambda order: _long_text(2600, seed=20261101 + order))
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)
    monkeypatch.setattr(jobs_module, "run_chapter", txn.run_chapter)

    first_pass = _run_batch(store, project, first=1, last=2, writer=writer, run_id="run-resume")
    assert first_pass.state == jobs_module.JOB_FINISHED
    assert first_pass.written == 2
    assert first_pass.skipped == 0
    # 用户改了第一章的稿子：续跑绝不能覆盖它
    store.write_manuscript(project.meta.id, 1, "用户手改过的第一章。")

    second_pass = _run_batch(store, project, first=1, last=3, writer=writer, run_id="run-resume")
    assert second_pass.state == jobs_module.JOB_FINISHED
    assert second_pass.written == 1, "本轮只该真写第 3 章"
    assert second_pass.skipped == 2, "第 1、2 章已 committed，必须跳过"
    assert [ch["order"] for ch in second_pass.chapters] == [3]
    assert store.read_manuscript(project.meta.id, 1) == "用户手改过的第一章。"
    assert store.get(project.meta.id).cursor == 3


def test_resume_retries_chapter_left_in_progress(tmp_path, monkeypatch) -> None:
    """上次进程被杀留下的 in_progress 记录必须被回收并重跑那一章。

    真实现场：`txn.run_chapter` 开跑第一件事就是写 `in_progress`，所以进程
    一被杀就一定留下这条死记录。如果只按「有记录」判断「这章跑过」，它会
    永远既不重跑也不完成——界面一直显示「这一章在跑」。
    """
    from tests.unit.test_creation_txn import _GoodWriter

    import ndecon.creation.jobs as jobs_module
    import ndecon.creation.txn as txn

    store = _store(tmp_path)
    project = _project(store, chapters=2)
    writer = _GoodWriter(lambda order: _long_text(2600, seed=20261101 + order))
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)
    monkeypatch.setattr(jobs_module, "run_chapter", txn.run_chapter)

    # 手工造一条「上次被杀」的死记录：in_progress、没有正文
    log = RunLog(store.projects_dir / project.meta.id, "run-killed")
    log.write(
        ChapterRun(
            run_id="run-killed",
            project_id=project.meta.id,
            order=1,
            provider="fake",
            started_at="2026-11-12T20:00:00",
        )
    )
    assert log.read(1) is not None and not log.read(1).settled  # type: ignore[union-attr]

    job = _run_batch(store, project, first=1, last=2, writer=writer, run_id="run-killed")
    assert job.state == jobs_module.JOB_FINISHED
    assert job.skipped == 0, "死记录不算已提交"
    assert job.written == 2
    assert store.read_manuscript(project.meta.id, 1) is not None
    assert log.read(1) is not None and log.read(1).status == "committed"  # type: ignore[union-attr]


def _wait_until(predicate, job, *, timeout: float = 30.0) -> None:
    """等后台线程把条件跑成真；超时就把任务当前状态报出来，别只留一个 assert False。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if job.state != JOB_RUNNING:
            break
        time.sleep(0.02)
    raise AssertionError(
        f"等不到条件成立：state={job.state} written={job.written} "
        f"current_order={job.current_order} current_phase={job.current_phase} "
        f"error={job.error}"
    )
