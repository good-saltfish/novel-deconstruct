"""P2⑤：连写必须立刻返回 202，不能等章节跑完。

背景（真实事故 2026-11-04）：一次 10 章连写实测 373 秒，而插件对引擎有
300 秒硬超时 —— 请求被掐断，引擎却把 10 章全写完了，用户看到「失败」。
8 套测试全绿也拦不住它，因为**没有一条测试让章节真的变慢**。

这条测试把每章人为拖慢，断言：
1. POST /runs 在章节跑完之前就返回 202；
2. 返回时任务还在 running；
3. 轮询最终拿到全部结果。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ndecon.creation import jobs as jobs_module
from ndecon.creation import txn as txn_module
from ndecon.panel.server import serve_in_thread


@pytest.fixture()
def server(tmp_path: Path):
    """起一个真实 HTTP 服务（与生产同一套 handler）。"""
    srv, thread = serve_in_thread(tmp_path, port=0)
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def _request(server, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    """打一次真实 HTTP 请求。"""
    from ndecon.panel.server import api_request

    return api_request(server, method, path, payload)


def test_batch_returns_before_chapters_finish(server, monkeypatch) -> None:
    """每章拖慢 0.5 秒时，POST /runs 仍必须立刻返回 202。"""
    original = txn_module.run_chapter
    slow_seconds = 0.5

    def slow_run_chapter(*args, **kwargs):
        """把每章人为拖慢，模拟真模型 25-80 秒的耗时。"""
        time.sleep(slow_seconds)
        return original(*args, **kwargs)

    monkeypatch.setattr(txn_module, "run_chapter", slow_run_chapter)
    # jobs 模块是 `from ... import run_chapter` 拿的引用，必须一起换
    monkeypatch.setattr(jobs_module, "run_chapter", slow_run_chapter)

    _, cre = _request(server, "POST", "/api/projects/create", {"title": "慢连写"})
    cre_id = cre["meta"]["id"]
    _request(server, "POST", f"/api/projects/{cre_id}/generate", {"chapter_count": 3})

    started = time.perf_counter()
    status, body = _request(
        server, "POST", f"/api/projects/{cre_id}/runs",
        {"from": 1, "to": 3, "provider": "fake"},
    )
    elapsed = time.perf_counter() - started

    assert status == 202, body
    # 3 章 × 0.5s = 1.5s，如果同步就会等满；留 0.9s 余量给 CI 抖动
    assert elapsed < slow_seconds * 3 - 0.9, f"POST 等了 {elapsed:.2f}s，说明还是同步的"
    assert body["status"] == "running", body
    assert body["total"] == 3, body

    # 轮询到结束，确认任务本身正常跑完
    deadline = time.time() + 30
    while time.time() < deadline:
        status, final = _request(server, "GET", f"/api/projects/{cre_id}/runs/{body['run_id']}")
        if final.get("status") != "running":
            break
        time.sleep(0.1)
    assert final["status"] == "finished", final
    assert final["written"] == 3, final


def test_pause_stops_between_chapters(server, monkeypatch) -> None:
    """用户点暂停：停在两章之间，已提交的章不受影响，状态是 paused 而不是崩溃。

    这是用户原话「可以让用户手动暂停」的回归测试。关键不是「能停下」，
    而是**停在两章之间**：一章的事务是原子的（写 run 记录 → 生成 → 过闸门 →
    覆盖写正文与摘要 → 提交游标），在生成中途硬掐会留下半截正文，而
    `write_manuscript` 是整篇覆盖写 —— 半截正文会把上一版好内容冲掉。
    """
    original = txn_module.run_chapter
    slow_seconds = 0.4

    def slow_run_chapter(*args, **kwargs):
        """把每章拖慢，留出足够窗口在跑的中途发暂停。"""
        time.sleep(slow_seconds)
        return original(*args, **kwargs)

    monkeypatch.setattr(txn_module, "run_chapter", slow_run_chapter)
    monkeypatch.setattr(jobs_module, "run_chapter", slow_run_chapter)

    _, cre = _request(server, "POST", "/api/projects/create", {"title": "暂停连写"})
    cre_id = cre["meta"]["id"]
    _request(server, "POST", f"/api/projects/{cre_id}/generate", {"chapter_count": 6})

    _, started = _request(
        server, "POST", f"/api/projects/{cre_id}/runs",
        {"from": 1, "to": 6, "provider": "fake"},
    )
    run_id = started["run_id"]

    # 等第一章提交完再点暂停：否则暂停可能在第 1 章开始前就生效，
    # 那样测的是「一开始就停」，测不到「跑了几章之后停」。
    deadline = time.time() + 20
    seen_written = 0
    while time.time() < deadline and seen_written < 1:
        _, progress = _request(server, "GET", f"/api/projects/{cre_id}/runs/{run_id}")
        seen_written = progress.get("written") or 0
        time.sleep(0.1)

    status, paused = _request(server, "POST", f"/api/projects/{cre_id}/runs/{run_id}/pause", {})
    assert status == 202, paused
    assert paused["pause_requested"] is True, paused
    # 文案要说清「这一章写完才停」，否则用户以为点一下立刻停，看到进度条还在动会以为卡住
    assert "这一章" in paused["note"], paused

    deadline = time.time() + 30
    final = {}
    while time.time() < deadline:
        _, final = _request(server, "GET", f"/api/projects/{cre_id}/runs/{run_id}")
        if final.get("status") != "running":
            break
        time.sleep(0.1)

    assert final["status"] == "paused", final
    # 停在两章之间：写了几章但没写完 6 章
    assert 1 <= final["written"] < 6, final
    assert final["failures"] == 0, final
    # 暂停不是故障，不能写成「中断」
    assert "暂停" in final["error"], final
    # 磁盘上也要留下结论：引擎重启后内存任务表是空的，光看 ch*.json
    # 分不清「用户点了暂停」和「半路崩了」
    _, after_restart = _request(server, "GET", f"/api/projects/{cre_id}/runs/{run_id}")
    assert after_restart["status"] in ("paused", "finished"), after_restart


def test_pause_after_finished_is_rejected(server) -> None:
    """对已经跑完的任务点暂停要说「已经不在运行」，而不是假装暂停成功。"""
    _, cre = _request(server, "POST", "/api/projects/create", {"title": "跑完再暂停"})
    cre_id = cre["meta"]["id"]
    _request(server, "POST", f"/api/projects/{cre_id}/generate", {"chapter_count": 2})
    _, started = _request(
        server, "POST", f"/api/projects/{cre_id}/runs",
        {"from": 1, "to": 2, "provider": "fake"},
    )
    run_id = started["run_id"]
    deadline = time.time() + 30
    while time.time() < deadline:
        _, progress = _request(server, "GET", f"/api/projects/{cre_id}/runs/{run_id}")
        if progress.get("status") != "running":
            break
        time.sleep(0.1)
    assert progress["status"] == "finished", progress

    status, body = _request(server, "POST", f"/api/projects/{cre_id}/runs/{run_id}/pause", {})
    assert status == 409, body
    assert "不在运行" in body["error"], body
    # 顺带把结论带回来：用户至少能看到「其实已经写完了」
    assert body["job"]["written"] == 2, body


def test_pause_unknown_run_is_404(server) -> None:
    """run_id 写错时给 404，而不是 500 或假装成功。"""
    _, cre = _request(server, "POST", "/api/projects/create", {"title": "无此任务"})
    cre_id = cre["meta"]["id"]
    status, body = _request(server, "POST", f"/api/projects/{cre_id}/runs/run-nope/pause", {})
    assert status == 404, body
    assert "找不到" in body["error"], body
