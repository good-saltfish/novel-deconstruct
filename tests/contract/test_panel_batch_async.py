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
