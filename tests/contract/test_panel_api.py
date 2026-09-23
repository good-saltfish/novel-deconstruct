"""面板 API 端到端契约：导入→学习统计→生成→编辑确认→删除。"""

from pathlib import Path

import pytest

from ndecon.panel.server import api_request, serve_in_thread

FIXTURE = Path(__file__).parent.parent / "fixtures" / "synthetic_novel.txt"


@pytest.fixture()
def server(tmp_path):
    """在随机端口启动面板服务器，测试结束后关闭。"""
    srv, _thread = serve_in_thread(tmp_path, port=0)
    yield srv
    srv.shutdown()
    srv.server_close()


def test_health_and_empty_list(server) -> None:
    """健康检查与空项目列表。"""
    assert api_request(server, "GET", "/api/health") == (200, {"ok": True})
    assert api_request(server, "GET", "/api/projects") == (200, [])


def test_full_loop_import_generate_edit_delete(server) -> None:
    """完整闭环：导入拆书→建创作项目（挂参考书）→生成→统计注入→保存部件→删除。"""
    # 1) 导入参考书
    status, ref = api_request(
        server, "POST", "/api/projects/import",
        {"name": "合成参考书", "file_path": str(FIXTURE)},
    )
    assert status == 201
    ref_id = ref["meta"]["id"]
    assert ref["chapter_count"] == 4
    assert ref["total_plot_points"] > 0
    assert ref["aggregation"] is not None

    # 2) 新建创作项目并关联参考书
    status, cre = api_request(
        server, "POST", "/api/projects/create",
        {"title": "我的新长篇", "genre": "末世", "premise": "规则末世求生", "reference_ids": [ref_id]},
    )
    assert status == 201
    cre_id = cre["meta"]["id"]

    # 3) 生成骨架（学习统计注入）
    status, generated = api_request(server, "POST", f"/api/projects/{cre_id}/generate")
    assert status == 200
    draft = generated["draft"]
    assert [c["order"] for c in draft["chapters"]] == list(range(1, 11))
    assert draft["golden_finger"]["cost_limitation"]
    # 候选态：尚未确认
    assert generated["confirmed_parts"] == {}

    # 4) PATCH 基础设定
    status, patched = api_request(
        server, "PATCH", f"/api/projects/{cre_id}", {"genre": "末世求生"}
    )
    assert status == 200 and patched["genre"] == "末世求生"

    # 5) 保存金手指为确认内容
    finger = dict(draft["golden_finger"])
    finger["cost_limitation"] = "每次使用折寿一个月（已人工确认）"
    status, saved = api_request(
        server, "PUT", f"/api/projects/{cre_id}/parts/golden_finger", {"content": finger}
    )
    assert status == 200
    assert saved["confirmed_parts"]["golden_finger"] is True
    assert saved["draft"]["provenance"]["golden_finger"] == "user-confirmed"

    # 6) 列表含两个项目；删除创作项目后只剩参考书
    _, projects = api_request(server, "GET", "/api/projects")
    assert len(projects) == 2
    status, _ = api_request(server, "DELETE", f"/api/projects/{cre_id}")
    assert status == 200
    _, projects = api_request(server, "GET", "/api/projects")
    assert [p["id"] for p in projects] == [ref_id]


def test_invalid_part_rejected(server) -> None:
    """保存部件时结构不合法返回 422，不允许脏数据落盘。"""
    _, cre = api_request(
        server, "POST", "/api/projects/create", {"title": "书", "genre": "", "premise": ""}
    )
    api_request(server, "POST", f"/api/projects/{cre['meta']['id']}/generate")
    status, body = api_request(
        server, "PUT", f"/api/projects/{cre['meta']['id']}/parts/golden_finger",
        {"content": {"name": "金手指"}},  # 缺必填的 cost_limitation
    )
    assert status == 422
    assert "不合法" in body["error"]


def test_import_missing_file_400(server) -> None:
    """导入不存在的文件返回 400。"""
    status, body = api_request(
        server, "POST", "/api/projects/import",
        {"name": "x", "file_path": str(Path("Z:/no/such/file.txt"))},
    )
    assert status == 400
    assert "不存在" in body["error"]


def test_static_index_served(server) -> None:
    """面板首页与静态资源可被服务（浏览器入口）。"""
    import urllib.request

    host, port = server.server_address
    with urllib.request.urlopen(f"http://{host}:{port}/", timeout=5) as resp:
        html = resp.read().decode("utf-8")
    assert "ndecon" in html and "小说创作面板" in html
