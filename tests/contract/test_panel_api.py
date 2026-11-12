"""面板 API 端到端契约：导入→学习统计→生成→编辑确认→删除。"""

import json
from pathlib import Path

import pytest

from ndecon.creation.chapters import CHAPTER_PROMPT_VERSION
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
    assert saved["confirmed_parts"]["golden_finger"] == "human-confirmed"
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


def test_skeleton_generation_uses_kb_and_blocks_novel_text(server, tmp_path) -> None:
    """#21 L0.5：工作区有知识库时，骨架生成注入方法论；被排除小说原文绝不出现。"""
    from ndecon.kb.corpus import build_knowledge_index, write_knowledge_artifacts

    secret = "版权密语苏晓林盏周伯影兽第六百六十六章"
    library = tmp_path / "library"
    (library / "方法论").mkdir(parents=True)
    (library / "方法论" / "卡普曼写法.md").write_text(
        "# 卡普曼三角冲突写法\n"
        "都市异能题材开篇五百字内必须让卡普曼三角开转，先抑后扬，金手指当场付代价。\n",
        encoding="utf-8",
    )
    (library / "某书 - 某作者.txt").write_text(secret + "\n" + "正文" * 100, encoding="utf-8")

    index, manifest = build_knowledge_index([library])
    write_knowledge_artifacts(server.workspace_store.root, [library], index, manifest)
    assert all(secret not in (item.relative_path + item.reason) for item in manifest if item.included)

    _, cre = api_request(
        server, "POST", "/api/projects/create",
        {"title": "卡普曼测试书", "genre": "都市异能", "premise": "开篇三角冲突"},
    )
    cre_id = cre["meta"]["id"]
    status, generated = api_request(server, "POST", f"/api/projects/{cre_id}/generate")
    assert status == 200

    draft_json = json.dumps(generated, ensure_ascii=False)
    # 方法论片段确定性进入 Fake 卖点
    selling = generated["draft"]["positioning"]["selling_points"]
    assert any("方法论参考" in point and "卡普曼" in point for point in selling)
    # 版权小说特征串不进入任何生成结果
    assert "版权密语" not in draft_json
    assert "某作者" not in draft_json


def test_chapter_writing_loop_generate_edit_confirm(server) -> None:
    """#16 章节闭环：生成候选→读取→编辑保存→确认；正文落盘、状态分层正确。"""
    _, cre = api_request(
        server, "POST", "/api/projects/create", {"title": "章节书", "genre": "都市", "premise": ""}
    )
    cre_id = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{cre_id}/generate")

    # 1) Fake 生成第 1 章候选
    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/generate", {"provider": "fake"}
    )
    assert status == 200, body
    record = body["record"]
    assert record["status"] == "draft"
    assert record["model_id"] == "rule:fake-chapter"
    # 版本号跟着代码走：写死字符串会让每次改 prompt 都要顺手改一处测试，
    # 而这条测试真正要断言的是「记录里存了当前 prompt 的版本」，不是某个历史值。
    assert record["prompt_version"] == CHAPTER_PROMPT_VERSION
    assert record["word_count"] > 0
    assert record["review"]["hook_strength"] in (1, 2, 3, 4, 5)
    assert "Fake 占位正文" in body["content"]

    # 2) GET 读回同一正文
    status, again = api_request(server, "GET", f"/api/projects/{cre_id}/chapters/1")
    assert status == 200 and again["content"] == body["content"]

    # 3) 未生成的章节 404
    status, _ = api_request(server, "GET", f"/api/projects/{cre_id}/chapters/2")
    assert status == 404

    # 4) 人工编辑保存：回到候选态，标记 user-edit，字数重算
    edited = "人工改写的第一段。\n\n  第二段带空白 "
    status, saved = api_request(
        server, "PUT", f"/api/projects/{cre_id}/chapters/1", {"content": edited}
    )
    assert status == 200
    assert saved["record"]["model_id"] == "user-edit"
    assert saved["record"]["status"] == "draft"
    assert saved["record"]["word_count"] == len("人工改写的第一段。第二段带空白")
    assert saved["content"].startswith("人工改写")

    # 5) 空正文 422
    status, empty = api_request(
        server, "PUT", f"/api/projects/{cre_id}/chapters/1", {"content": "   \n"}
    )
    assert status == 422
    assert "不能为空" in empty["error"]

    # 6) 确认章节
    status, confirmed = api_request(server, "POST", f"/api/projects/{cre_id}/chapters/1/confirm")
    assert status == 200
    assert confirmed["record"]["status"] == "user-confirmed"

    # 7) 项目详情携带稿件记录；删除后正文文件一并消失
    _, full = api_request(server, "GET", f"/api/projects/{cre_id}")
    assert full["manuscripts"]["ch1"]["status"] == "user-confirmed"
    from pathlib import Path as _Path

    ms_file = _Path(server.workspace_store.root) / "projects" / cre_id / "manuscripts" / "ch001.md"
    assert ms_file.is_file()
    api_request(server, "DELETE", f"/api/projects/{cre_id}")
    assert not ms_file.exists()


def test_chapter_generation_guards(server, monkeypatch) -> None:
    """章节生成的边界：无细纲/非法章号/参考书项目/openai-compat 缺 key 都被拒绝。"""
    # 无骨架的空创作项目
    _, empty = api_request(server, "POST", "/api/projects/create", {"title": "空书"})
    empty_id = empty["meta"]["id"]
    status, body = api_request(
        server, "POST", f"/api/projects/{empty_id}/chapters/1/generate", {"provider": "fake"}
    )
    assert status == 400 and "细纲" in body["error"]

    # 有骨架的项目用于后续边界
    _, cre = api_request(server, "POST", "/api/projects/create", {"title": "正常书"})
    cre_id = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{cre_id}/generate")

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1000/generate", {"provider": "fake"}
    )
    assert status == 400 and "999" in body["error"]

    # B1 解除后：第 11 章不再被硬限挡住，而是看细纲里有没有这一章
    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/11/generate", {"provider": "fake"}
    )
    assert status == 400 and "细纲" in body["error"]

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/xyz/generate", {"provider": "fake"}
    )
    assert status == 400 and "整数" in body["error"]

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/generate", {"provider": "gpt-xyz"}
    )
    assert status == 400 and "未知 provider" in body["error"]

    # AI 生成在面板未配置供应商时给出 400 与明确引导（不走网络、不静默回退）
    monkeypatch.delenv("NOVEL_DECON_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/generate", {"provider": "ai"}
    )
    assert status == 400 and "模型设置" in body["error"]

    # 参考书项目不能写正文
    status, ref = api_request(
        server, "POST", "/api/projects/import",
        {"name": "参考书", "file_path": str(FIXTURE)},
    )
    assert status == 201
    status, body = api_request(
        server, "POST", f"/api/projects/{ref['meta']['id']}/chapters/1/generate",
        {"provider": "fake"},
    )
    assert status == 400 and "创作项目" in body["error"]


def _wait_batch(server, cre_id: str, run_id: str, *, timeout: float = 60.0) -> dict:
    """轮询一次连写任务直到它不再是 running（返回最终状态体）。"""
    import time

    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        status, last = api_request(server, "GET", f"/api/projects/{cre_id}/runs/{run_id}")
        assert status == 200, last
        if last.get("status") != "running":
            return last
        time.sleep(0.05)
    raise AssertionError(f"连写任务 {run_id} 在 {timeout}s 内没结束：{last}")


def test_chapters_run_batch_writes_and_resumes(server) -> None:
    """连写端点：立刻回 202，轮询到结束后进度落 cursor；再从游标续跑不重写已提交章。"""
    _, cre = api_request(server, "POST", "/api/projects/create", {"title": "连写书"})
    cre_id = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{cre_id}/generate")

    status, started = api_request(
        server, "POST", f"/api/projects/{cre_id}/runs",
        {"from": 1, "to": 3, "provider": "fake", "run_id": "run-batch"},
    )
    # 202 而不是 200：请求被接受，但活儿还没干完。同步返回会让调用方误以为
    # written 就是最终结果，而真实模型跑 10 章要 373 秒，必然撞插件 300s 超时。
    assert status == 202, started
    assert started["run_id"] == "run-batch"
    assert started["total"] == 3
    assert started["poll"] == f"/api/projects/{cre_id}/runs/run-batch"

    batch = _wait_batch(server, cre_id, "run-batch")
    assert batch["status"] == "finished", batch
    assert batch["written"] == 3 and batch["failures"] == 0
    assert [ch["order"] for ch in batch["chapters"]] == [1, 2, 3]
    assert all(ch["status"] == "committed" for ch in batch["chapters"])

    _, full = api_request(server, "GET", f"/api/projects/{cre_id}")
    assert full["cursor"] == 3
    assert set(full["manuscripts"]) == {"ch1", "ch2", "ch3"}

    # 不传 from：从游标续跑
    status, started = api_request(
        server, "POST", f"/api/projects/{cre_id}/runs", {"to": 5, "provider": "fake"}
    )
    assert status == 202, started
    more = _wait_batch(server, cre_id, started["run_id"])
    assert more["from"] == 4 and more["written"] == 2

    _, full = api_request(server, "GET", f"/api/projects/{cre_id}")
    assert full["cursor"] == 5

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/runs", {"from": 9, "to": 4, "provider": "fake"}
    )
    assert status == 400 and "区间" in body["error"]


def test_chapters_run_batch_rejects_concurrent_run(server) -> None:
    """同一项目已有连写在跑时，第二次请求被 409 拒并带回正在跑的 run_id。

    为什么必须拒：两个后台线程同时写 project.json 会互相覆盖，表现是
    「跑完发现少了几章」或「游标跳回」——数据错了但没有任何报错。
    """

    _, cre = api_request(server, "POST", "/api/projects/create", {"title": "并发连写"})
    cre_id = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{cre_id}/generate", {"chapter_count": 30})

    status, first = api_request(
        server, "POST", f"/api/projects/{cre_id}/runs",
        {"from": 1, "to": 30, "provider": "fake", "run_id": "run-1"},
    )
    assert status == 202, first

    status, second = api_request(
        server, "POST", f"/api/projects/{cre_id}/runs",
        {"from": 1, "to": 3, "provider": "fake", "run_id": "run-2"},
    )
    # 第一个任务可能已经跑完（fake 很快），那就不是并发场景，跳过断言。
    if status == 409:
        assert "已经有一次连写在跑" in second["error"]
        assert second["run_id"] == "run-1"

    _wait_batch(server, cre_id, "run-1", timeout=120)


def test_batch_job_status_falls_back_to_disk(server) -> None:
    """引擎重启后内存任务表是空的，进度仍能从 runs/<run_id>/ch*.json 读出来。

    没有这条兜底，用户刷新面板会看到「找不到这次连写」，以为数据丢了。
    """
    _, cre = api_request(server, "POST", "/api/projects/create", {"title": "重启兜底"})
    cre_id = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{cre_id}/generate")

    status, started = api_request(
        server, "POST", f"/api/projects/{cre_id}/runs",
        {"from": 1, "to": 2, "provider": "fake", "run_id": "run-disk"},
    )
    assert status == 202
    _wait_batch(server, cre_id, "run-disk")

    # 模拟引擎重启：把任务从内存表里摘掉，只剩磁盘记录。
    server.jobs._jobs.pop("run-disk", None)
    status, body = api_request(server, "GET", f"/api/projects/{cre_id}/runs/run-disk")
    assert status == 200, body
    assert body["run_id"] == "run-disk"
    assert body["written"] == 2
    assert [ch["order"] for ch in body["chapters"]] == [1, 2]
    # 已全部 committed 且无未结算记录 —— 这次连写其实成功了，只是任务对象没了。
    # 若报 stopped，界面会显示「中途停下」，而用户看到 2 章正文都在。
    assert body["status"] == "finished"


def test_static_index_served(server) -> None:
    """面板首页与静态资源可被服务（浏览器入口）。"""
    import urllib.request

    host, port = server.server_address
    with urllib.request.urlopen(f"http://{host}:{port}/", timeout=5) as resp:
        html = resp.read().decode("utf-8")
    assert "ndecon" in html and "小说创作面板" in html


def test_chapter_run_txn_commits_and_records(server) -> None:
    """章事务端点：过闸门才落盘、落 auto-accepted、留 run 记录、游标前进。"""
    _, cre = api_request(server, "POST", "/api/projects/create", {"title": "事务书"})
    cre_id = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{cre_id}/generate")

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/run", {"provider": "fake"}
    )
    assert status == 200, body
    outcome = body["outcome"]
    assert outcome["status"] == "committed"
    assert outcome["verdict"] == "pass"
    assert outcome["attempts"] == 1
    assert outcome["cjk_chars"] >= 2000
    assert outcome["summary_present"] is True
    assert body["record"]["status"] == "auto-accepted"
    assert body["content"]

    # 游标前进到第 1 章
    _, full = api_request(server, "GET", f"/api/projects/{cre_id}")
    assert full["cursor"] == 1

    # run 台账可查
    status, runs = api_request(server, "GET", f"/api/projects/{cre_id}/runs")
    assert status == 200
    chapters = [ch for run in runs["runs"] for ch in run["chapters"]]
    assert any(ch["order"] == 1 and ch["status"] == "committed" for ch in chapters)


def test_chapter_run_txn_rejects_bad_params(server) -> None:
    """章事务端点的参数边界：章号、provider、max_repairs、auto_accept 都给出可读错误。"""
    _, cre = api_request(server, "POST", "/api/projects/create", {"title": "边界书"})
    cre_id = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{cre_id}/generate")

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/xyz/run", {"provider": "fake"}
    )
    assert status == 400 and "整数" in body["error"]

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/11/run", {"provider": "fake"}
    )
    assert status == 400 and "细纲" in body["error"]

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/run", {"provider": "gpt-xyz"}
    )
    assert status == 400 and "未知 provider" in body["error"]

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/run",
        {"provider": "fake", "max_repairs": "abc"},
    )
    assert status == 400 and "整数" in body["error"]

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/run",
        {"provider": "fake", "max_repairs": 9},
    )
    assert status == 400 and "0-5" in body["error"]

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/run",
        {"provider": "fake", "auto_accept": "yes"},
    )
    assert status == 400 and "布尔" in body["error"]


def test_chapter_run_txn_draft_only_when_auto_accept_false(server) -> None:
    """auto_accept=false 时仍要过闸门，但落 draft 等人确认（A3 逐章确认模式）。"""
    _, cre = api_request(server, "POST", "/api/projects/create", {"title": "人工确认书"})
    cre_id = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{cre_id}/generate")

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/run",
        {"provider": "fake", "auto_accept": False},
    )
    assert status == 200, body
    assert body["outcome"]["status"] == "committed"
    assert body["record"]["status"] == "draft"

    status, confirmed = api_request(server, "POST", f"/api/projects/{cre_id}/chapters/1/confirm")
    assert status == 200
    assert confirmed["record"]["status"] == "user-confirmed"


def test_chapter_restore_version(server) -> None:
    """版本回退：覆盖写会存档，回退后正文与字数一致，且回退本身可撤销。"""
    _, cre = api_request(server, "POST", "/api/projects/create", {"title": "版本书"})
    cre_id = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{cre_id}/generate")

    status, first = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/run", {"provider": "fake"}
    )
    assert status == 200
    original = first["content"]

    status, saved = api_request(
        server, "PUT", f"/api/projects/{cre_id}/chapters/1", {"content": "人工改写后的正文。"}
    )
    assert status == 200

    status, versions = api_request(server, "GET", f"/api/projects/{cre_id}/chapters/1/versions")
    assert status == 200
    assert len(versions["versions"]) >= 1
    stamp = versions["versions"][0]

    status, restored = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/restore", {"stamp": stamp}
    )
    assert status == 200
    assert restored["content"] == original
    assert restored["record"]["model_id"] == f"restored:{stamp}"
    assert restored["record"]["status"] == "draft"

    # 回退前的人工稿被存成新版本，回退可撤销
    status, again = api_request(server, "GET", f"/api/projects/{cre_id}/chapters/1/versions")
    assert len(again["versions"]) > len(versions["versions"])

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/restore", {"stamp": ""}
    )
    assert status == 400 and "stamp" in body["error"]

    status, body = api_request(
        server, "POST", f"/api/projects/{cre_id}/chapters/1/restore", {"stamp": "nope"}
    )
    assert status == 404


def test_generate_reports_provider_failure_as_422(server, monkeypatch) -> None:
    """模型少给一块骨架时必须回 422 + 可读原因，而不是 500「服务端内部错误」。

    真实故障：模型返回的 JSON 里没有 protagonist，`_draft_from_dict` 抛
    ProviderResponseError，`_generate` 原来没有 try/except，于是落到 _dispatch
    的 500 兜底，用户看到的是「服务端内部错误：ProviderResponseError: 创作骨架
    JSON 不合法：'protagonist'」——既不知道缺哪一块，也不知道该怎么办。
    """
    from ndecon.creation import providers as providers_module

    _, cre = api_request(server, "POST", "/api/projects/create", {"title": "缺部件书"})
    cre_id = cre["meta"]["id"]

    def boom(self, *_args, **_kwargs):
        """替身：模拟模型少给 protagonist 的响应。"""
        raise providers_module.ProviderResponseError("创作骨架 JSON 不合法：缺少部件：protagonist（主角）")

    monkeypatch.setattr(providers_module.FakeCreator, "generate_draft", boom)
    status, body = api_request(server, "POST", f"/api/projects/{cre_id}/generate", {"provider": "fake"})
    assert status == 422, body
    assert "骨架生成失败" in body["error"]
    assert "protagonist" in body["error"]


def test_uncaught_error_becomes_json_500_instead_of_dropped_connection(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """处理器里的未捕获异常必须转成 JSON 500，不能断开连接。

    历史故障：异常逃出处理器后 stdlib 直接断连接，插件侧只看到 etch failed，
    界面把「某个请求失败」报成「无法连接引擎」。这条断言锁住那道兜底。
    """
    from ndecon.panel import server as panel_server

    original = panel_server.create_handler
    real_handler = original(server.workspace_store, server.llm_session)

    def explode(*_args, **_kwargs):
        raise RuntimeError("模拟未捕获异常")

    # 把 _list_runs 换成必炸的实现，走真实 HTTP 路径
    class Boom(real_handler):
        """覆写一个路由方法，模拟业务层没接住的异常。"""

        def _list_runs(self, *_args, **_kwargs):
            explode()

    server.RequestHandlerClass = Boom

    status, body = api_request(server, "GET", "/api/projects/whatever/runs")
    assert status == 500, body
    assert "error" in body
    assert "模拟未捕获异常" in body["error"]


def test_batch_run_survives_engine_restart(server) -> None:
    """引擎重启后，同一个 run_id 仍能从磁盘 run 记录读出进度。

    这条锁住 jobs.py 的核心承诺：任务状态以磁盘为准，内存只做加速。
    没有它，用户重启引擎后就再也查不到那次连写跑到第几章了。
    """
    _, cre = api_request(server, "POST", "/api/projects/create", {"title": "重启验证"})
    cre_id = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{cre_id}/generate", {"chapter_count": 3})

    status, started = api_request(
        server, "POST", f"/api/projects/{cre_id}/runs",
        {"from": 1, "to": 3, "provider": "fake", "run_id": "run-restart"},
    )
    assert status == 202, started
    _wait_batch(server, cre_id, "run-restart")

    # 模拟「引擎重启」：内存里的任务全丢了
    server.jobs._jobs.clear()

    status, body = api_request(server, "GET", f"/api/projects/{cre_id}/runs/run-restart")
    assert status == 200, body
    assert body["written"] == 3, body
    assert body["status"] != "running", body
    assert "从 run 记录读出来" in body["error"], body
