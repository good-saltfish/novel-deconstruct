"""进度可见性：phase 落盘、活进度字段、字数提示不阻断。

用户的原话是「慢的让我觉得是生成失败了」和「想暂停该生成都不行」。
这两句背后是同一件事：界面上没有任何东西能证明任务还活着。这些测试钉住
「活着这件事必须能从磁盘读出来」——引擎重启后内存任务表是空的，
只剩 ch*.json 里的 phase。
"""

from __future__ import annotations

import json

from tests.unit.test_creation_txn import _long_text, _project, _store

from ndecon.creation.jobs import JOB_FINISHED, JOB_RUNNING, BatchJob, job_status
from ndecon.creation.runlog import ChapterRun, RunLog, new_run_id


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

    store = _store(tmp_path)
    project = _project(store, chapters=3)
    writer = _GoodWriter(_long_text(2600))
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
    registry.pause(job.run_id)
    for _ in range(200):
        if registry.get(job.run_id) is None or registry.get(job.run_id).state != JOB_RUNNING:
            break
        import time

        time.sleep(0.02)
    live = registry.get(job.run_id)
    assert live is not None
    assert live.state == jobs_module.JOB_PAUSED
    # 暂停时已提交的章必须是真的写完了，不是半截
    assert live.written >= 1
    saved = store.read_manuscript(project.meta.id, 1)
    assert saved and len(saved) > 1000
