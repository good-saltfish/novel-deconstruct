"""面板 Planner Agent API 契约（#25）：Fake run、AI 未配置拦截、候选确认入库。"""

import pytest

from ndecon.panel.server import api_request, serve_in_thread


@pytest.fixture()
def server(tmp_path):
    """面板服务器。"""
    srv, _thread = serve_in_thread(tmp_path, port=0)
    yield srv
    srv.shutdown()
    srv.server_close()


def _create_project_with_skeleton(server) -> str:
    """建项目并生成 Fake 骨架（含 10 章）。"""
    _, cre = api_request(
        server, "POST", "/api/projects/create",
        {"title": "Agent书", "genre": "都市异能", "premise": "路灯封着夜色"},
    )
    pid = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{pid}/generate", {"provider": "fake"})
    return pid


def test_fake_expand_chapter_run_via_api(server) -> None:
    """API 跑 Fake 扩章：返回 trace、4 节拍，候选尚未入权威细纲。

    「尚未入」的比较基准是**跑之前**的那份细纲，不是「beats 为空」：
    2026-11-16 起 Fake 骨架自己就带 4 拍（离线路径也要能演练节拍进 prompt），
    所以空数组不再是初始状态的正确描述。契约本身没变——跑完 agent 之后，
    权威细纲必须一个字都没动。
    """
    pid = _create_project_with_skeleton(server)
    _, before = api_request(server, "GET", f"/api/projects/{pid}")
    before_beats = {
        c["order"]: c["beats"] for c in before["draft"]["chapters"]
    }

    status, result = api_request(
        server, "POST", f"/api/projects/{pid}/agent/runs",
        {"task": "expand_chapter", "chapter_order": 2, "provider": "fake"},
    )
    assert status == 200
    assert result["finished"] is True
    assert result["stop_reason"] == "finished"
    assert len(result["drafts"]["beats"]) == 4
    tools = [step["tool"] for step in result["trace"]]
    assert tools[0] == "get_outline" and tools[-1] == "finish"
    # 确认前：细纲章上的节拍与跑之前逐字相同（候选只在 drafts 里）
    _, project = api_request(server, "GET", f"/api/projects/{pid}")
    after_beats = {c["order"]: c["beats"] for c in project["draft"]["chapters"]}
    assert after_beats == before_beats
    # 而且候选确实是新东西，不是把原样的骨架又递回来一遍
    assert result["drafts"]["beats"] != before_beats[2]


def test_fake_add_settings_run_and_accept(server) -> None:
    """Fake 补设定后确认入库：settings 出现在项目中。"""
    pid = _create_project_with_skeleton(server)
    status, result = api_request(
        server, "POST", f"/api/projects/{pid}/agent/runs",
        {"task": "add_settings", "provider": "fake"},
    )
    assert status == 200 and len(result["drafts"]["settings"]) == 2

    status, project = api_request(
        server, "PUT", f"/api/projects/{pid}/agent/accept/settings",
        {"settings": result["drafts"]["settings"]},
    )
    assert status == 200
    assert len(project["settings"]) == 2
    assert {s["entry_type"] for s in project["settings"]} == {"faction", "character"}


def test_accept_beats_attaches_to_chapter(server) -> None:
    """节拍确认后挂到对应章节，按 order 排序；其它章一个字节都不动。"""
    pid = _create_project_with_skeleton(server)
    _, before = api_request(server, "GET", f"/api/projects/{pid}")
    before_other = {
        c["order"]: c["beats"] for c in before["draft"]["chapters"] if c["order"] != 3
    }

    _, result = api_request(
        server, "POST", f"/api/projects/{pid}/agent/runs",
        {"task": "expand_chapter", "chapter_order": 3, "provider": "fake"},
    )
    beats = result["drafts"]["beats"]
    status, project = api_request(
        server, "PUT", f"/api/projects/{pid}/agent/accept/beats",
        {"chapter_order": 3, "beats": beats},
    )
    assert status == 200
    chapter3 = next(c for c in project["draft"]["chapters"] if c["order"] == 3)
    assert len(chapter3["beats"]) == 4
    assert [b["order"] for b in chapter3["beats"]] == [1, 2, 3, 4]
    # 其他章节不受影响（与确认前逐字相同；Fake 骨架自带节拍，不能拿空数组当基准）
    after_other = {
        c["order"]: c["beats"] for c in project["draft"]["chapters"] if c["order"] != 3
    }
    assert after_other == before_other


def test_agent_run_guards(server) -> None:
    """非法任务/缺章号/章不存在/AI 未配置均被拒。"""
    pid = _create_project_with_skeleton(server)
    status, body = api_request(
        server, "POST", f"/api/projects/{pid}/agent/runs", {"task": "write_novel"}
    )
    assert status == 400 and "expand_chapter" in body["error"]

    status, body = api_request(
        server, "POST", f"/api/projects/{pid}/agent/runs", {"task": "expand_chapter"}
    )
    assert status == 400 and "chapter_order" in body["error"]

    status, body = api_request(
        server, "POST", f"/api/projects/{pid}/agent/runs",
        {"task": "expand_chapter", "chapter_order": 1000},
    )
    assert status == 400

    status, body = api_request(
        server, "POST", f"/api/projects/{pid}/agent/runs",
        {"task": "add_settings", "provider": "ai"},
    )
    assert status == 400 and "模型设置" in body["error"]


def test_accept_invalid_drafts_rejected(server) -> None:
    """确认空候选/坏结构返回 4xx，不改权威数据。"""
    pid = _create_project_with_skeleton(server)
    status, body = api_request(
        server, "PUT", f"/api/projects/{pid}/agent/accept/beats",
        {"chapter_order": 1, "beats": []},
    )
    assert status == 422
    status, body = api_request(
        server, "PUT", f"/api/projects/{pid}/agent/accept/settings",
        {"settings": [{"entry_type": "faction"}]},  # 缺 name/content
    )
    assert status == 422
