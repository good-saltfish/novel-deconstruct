"""面板设定/细纲候选端点的契约测试。

契约要点（与冻结红线一致）：
- 两套接口路径不同、prompt_version 不同（否则等于同一件事叫两个名字）；
- 未配置 AI 时必须 400，**绝不静默降级 Fake**；
- 端点只返回候选，不写项目（候选不代笔 —— 写项目必须走 accept）。
"""

import json
from pathlib import Path

import pytest

from ndecon.panel.server import api_request, serve_in_thread

FIXTURE = Path(__file__).parent.parent / "fixtures" / "synthetic_novel.txt"


@pytest.fixture()
def server(tmp_path):
    """在工作区临时目录启动面板（随机端口）。"""
    srv, _thread = serve_in_thread(tmp_path, port=0)
    yield srv
    srv.shutdown()
    srv.server_close()


def _create_creation(server) -> str:
    """用面板自己的创建接口建一个创作项目（不重复实现 id/时间字段规则）。"""
    status, created = api_request(
        server,
        "POST",
        "/api/projects/create",
        {"title": "测试书", "genre": "都市异能", "premise": "主角能回放现场最后十秒"},
    )
    assert status == 201, created
    return created["meta"]["id"]


def test_plan_settings_and_outline_are_two_different_things(server):
    """两条路由 + 两个 prompt_version：调用方据此知道它们学习方法不同。"""
    project_id = _create_creation(server)

    status, payload = api_request(
        server, "POST", f"/api/projects/{project_id}/plan/settings", {"provider": "fake"}
    )
    assert status == 200, payload
    assert payload["plan"]["prompt_version"].startswith("cr-s")
    assert payload["plan"]["entries"]

    status, payload = api_request(
        server, "POST", f"/api/projects/{project_id}/plan/outline", {"provider": "fake"}
    )
    assert status == 200, payload
    assert payload["plan"]["prompt_version"].startswith("cr-o")
    assert payload["plan"]["chapters"]


def test_plan_endpoints_do_not_write_project(server):
    """端点只给候选：跑完 settings 后项目 settings 必须仍为空（确认才写）。"""
    project_id = _create_creation(server)
    api_request(server, "POST", f"/api/projects/{project_id}/plan/settings", {})

    reloaded = server.workspace_store.get(project_id)
    assert reloaded.settings == []


def test_plan_ai_without_key_is_400_not_silent_fake(server):
    """冻结红线：AI 未配置必须 400，不静默降级 Fake。"""
    project_id = _create_creation(server)
    for kind in ("settings", "outline"):
        status, payload = api_request(
            server, "POST", f"/api/projects/{project_id}/plan/{kind}", {"provider": "ai"}
        )
        assert status == 400, (kind, payload)
        assert "error" in payload


def test_plan_on_reference_project_rejected(server):
    """参考书项目没有设定与细纲，必须 400 而不是给一份空候选。"""
    status, ref = api_request(
        server,
        "POST",
        "/api/projects/import",
        {"name": "合成参考书", "file_path": str(FIXTURE)},
    )
    assert status == 201, ref
    ref_id = ref["meta"]["id"]

    status, payload = api_request(server, "POST", f"/api/projects/{ref_id}/plan/settings", {})
    assert status == 400, payload
    assert "创作项目" in json.dumps(payload, ensure_ascii=False)


def test_plan_unknown_project_is_404(server):
    status, _payload = api_request(server, "POST", "/api/projects/nope/plan/settings", {})
    assert status == 404


def _create_with_skeleton(server) -> str:
    """建创作项目并生成 Fake 骨架（细纲确认需要 10 章存在）。"""
    project_id = _create_creation(server)
    status, generated = api_request(
        server, "POST", f"/api/projects/{project_id}/generate", {"provider": "fake"}
    )
    assert status == 200, generated
    return project_id


def test_accept_settings_candidate_writes_project(server):
    """确认设定候选：条目进 project.settings，金手指进 draft（此前不写）。"""
    project_id = _create_with_skeleton(server)
    status, payload = api_request(
        server, "POST", f"/api/projects/{project_id}/plan/settings", {"provider": "fake"}
    )
    assert status == 200, payload
    plan = payload["plan"]

    status, project = api_request(
        server,
        "PUT",
        f"/api/projects/{project_id}/plan/accept/settings",
        {"entries": plan["entries"], "golden_finger": plan["golden_finger"]},
    )
    assert status == 200, project
    assert len(project["settings"]) == len(plan["entries"])
    assert project["draft"]["golden_finger"]["name"] == plan["golden_finger"]["name"]
    assert project["draft"]["golden_finger"]["cost_limitation"]


def test_accept_settings_is_accumulative_not_overwrite(server):
    """设定是累积库：再确认一次不同条目，老条目不能被覆盖掉。"""
    project_id = _create_with_skeleton(server)
    _, first = api_request(server, "POST", f"/api/projects/{project_id}/plan/settings", {})
    plan = first["plan"]
    api_request(
        server,
        "PUT",
        f"/api/projects/{project_id}/plan/accept/settings",
        {"entries": plan["entries"]},
    )
    extra = dict(plan["entries"][0])
    extra["name"] = extra["name"] + "（补充）"
    status, project = api_request(
        server,
        "PUT",
        f"/api/projects/{project_id}/plan/accept/settings",
        {"entries": [extra]},
    )
    assert status == 200, project
    names = [entry["name"] for entry in project["settings"]]
    assert extra["name"] in names
    assert len(names) == len(plan["entries"]) + 1


def test_accept_outline_candidate_attaches_beats(server):
    """确认细纲候选：节拍挂到对应章，其余章不受影响（其余章 beats 仍为 []）。"""
    project_id = _create_with_skeleton(server)
    status, payload = api_request(
        server, "POST", f"/api/projects/{project_id}/plan/outline", {"provider": "fake"}
    )
    assert status == 200, payload
    plan = payload["plan"]

    status, project = api_request(
        server,
        "PUT",
        f"/api/projects/{project_id}/plan/accept/outline",
        {"chapters": plan["chapters"]},
    )
    assert status == 200, project
    by_order = {chapter["order"]: chapter["beats"] for chapter in project["draft"]["chapters"]}
    planned = {chapter["order"]: chapter["beats"] for chapter in plan["chapters"]}
    for order, beats in planned.items():
        assert len(by_order[order]) == len(beats)
        assert [beat["order"] for beat in by_order[order]] == list(range(1, len(beats) + 1))
    untouched = set(by_order) - set(planned)
    assert untouched and all(by_order[order] == [] for order in untouched)


def test_accept_endpoints_reject_empty_and_bad_payload(server):
    """空候选 / 结构不合法 / 未知资源必须是 4xx，不静默成功。"""
    project_id = _create_with_skeleton(server)

    status, _ = api_request(
        server, "PUT", f"/api/projects/{project_id}/plan/accept/settings", {"entries": []}
    )
    assert status == 422
    status, _ = api_request(
        server,
        "PUT",
        f"/api/projects/{project_id}/plan/accept/settings",
        {"entries": [{"entry_type": "faction"}]},
    )
    assert status == 422
    status, _ = api_request(
        server, "PUT", f"/api/projects/{project_id}/plan/accept/outline", {"chapters": []}
    )
    assert status == 422
    status, _ = api_request(
        server, "PUT", f"/api/projects/{project_id}/plan/accept/typo", {"entries": []}
    )
    assert status == 404


def test_accept_outline_rejects_unknown_chapter(server):
    """候选里出现项目骨架没有的章号必须 400 报错，而不是悄悄跳过。"""
    project_id = _create_creation(server)  # 没有 generate，故无骨架
    status, payload = api_request(
        server, "POST", f"/api/projects/{project_id}/plan/outline", {"provider": "fake"}
    )
    assert status == 200, payload
    status, body = api_request(
        server,
        "PUT",
        f"/api/projects/{project_id}/plan/accept/outline",
        {"chapters": payload["plan"]["chapters"]},
    )
    assert status == 400, body
    assert "细纲" in json.dumps(body, ensure_ascii=False)
