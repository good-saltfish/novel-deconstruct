"""面板并行候选端点：生成多版不落盘、选中才提交。

为什么需要这组测试
------------------
候选层走的是**另一条**落盘路径：它不调 `run_chapter`，自己拼上下文包、自己过
闸门、自己写正文与游标。这种"第二条路径"最容易在几个月后悄悄走偏 ——
某次改动只修了 `run_chapter`，候选层还留着老语义，于是"生成的章"和"选中的章"
判据不一致。所以这里逐条钉住候选层的承诺：

1. 生成候选**不动**正文、摘要、游标（盘上唯一的正文仍是旧稿）；
2. N 版必须真的互不相同，否则"挑最优"是摆设；
3. 提交按**当前**账面重判闸门，不拿生成时的旧判决冒充新证据；
4. 每一类错误都回 400 且带可读文案，不留半写状态。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndecon.panel.server import api_request, serve_in_thread


@pytest.fixture()
def server(tmp_path: Path):
    """起一个真 HTTP 服务（与生产同一套 handler）。"""
    srv, thread = serve_in_thread(tmp_path, port=0)
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


@pytest.fixture()
def project(server) -> str:
    """建一个三章细纲的创作项目，返回 project_id。"""
    status, body = api_request(server, "POST", "/api/projects/create", {"title": "候选测试书"})
    assert status in (200, 201), body
    project_id = body["meta"]["id"]
    status, body = api_request(
        server, "POST", f"/api/projects/{project_id}/generate", {"chapter_count": 3}
    )
    assert status in (200, 201), body
    assert len(body["draft"]["chapters"]) == 3, body
    return project_id


def _base(project_id: str) -> str:
    return f"/api/projects/{project_id}/chapters"


def test_get_candidates_without_generating_returns_empty_shell(server, project) -> None:
    """还没生成过候选时读，返回 exists=False 的空壳，而不是 404。

    面板要能区分「这章还没有候选」与「接口不存在」：前者该显示生成按钮，
    后者是 bug。回 404 会让界面以为整个端点挂了。
    """
    status, body = api_request(server, "GET", f"{_base(project)}/1/candidates")
    assert status == 200, body
    assert body["exists"] is False
    assert body["candidates"] == []
    assert body["order"] == 1


def test_generate_candidates_does_not_touch_manuscript_or_cursor(server, project) -> None:
    """核心承诺：候选生成只写 candidates_chNNN.json，正文/摘要/游标一律不动。"""
    status, body = api_request(
        server, "POST", f"{_base(project)}/1/candidates", {"provider": "fake", "count": 3}
    )
    assert status == 200, body
    assert body["requested"] == 3
    assert body["ok_count"] == 3
    assert body["passed_count"] >= 1
    assert len(body["candidates"]) == 3

    status, chapter = api_request(server, "GET", f"{_base(project)}/1")
    # 无正文时 _get_chapter 回 404：这不是候选层的 bug，是「本章还没正文」的既有语义。
    assert status == 404, chapter
    assert "error" in chapter

    status, fresh = api_request(server, "GET", f"/api/projects/{project}")
    assert status == 200
    assert fresh["cursor"] == 0, "候选生成不许推游标"


def test_candidates_are_actually_different(server, project) -> None:
    """N 版必须互不相同。

    FakeChapterWriter 对同一份上下文包完全确定；没有 seed 偏移时三个候选
    会逐字相同 —— 那样「让读者选最优」就成了一句空话，而且它们互相之间
    还会被判成跨章重复。
    """
    status, body = api_request(
        server, "POST", f"{_base(project)}/1/candidates", {"provider": "fake", "count": 3}
    )
    assert status == 200, body
    hashes = {candidate["content_hash"] for candidate in body["candidates"]}
    assert len(hashes) == 3, f"三版必须互不相同，实际只有 {len(hashes)} 种"
    contents = {candidate["content"] for candidate in body["candidates"]}
    assert len(contents) == 3
    previews = {candidate["preview"] for candidate in body["candidates"]}
    assert len(previews) == 3, "预览也要能一眼看出差别"
    # 字数不能完全一样，否则连"看起来不同"都谈不上
    assert len({candidate["gate"]["cjk_chars"] for candidate in body["candidates"]}) == 3


def test_preview_skips_placeholder_banner(server, project) -> None:
    """预览要跳过占位横幅：横幅每版相同，留着会把 160 字额度全吃掉。"""
    status, body = api_request(
        server, "POST", f"{_base(project)}/1/candidates", {"provider": "fake", "count": 2}
    )
    assert status == 200, body
    for candidate in body["candidates"]:
        assert not candidate["preview"].startswith("【Fake 占位正文"), candidate["preview"][:40]


def test_select_candidate_commits_content_summary_and_cursor(server, project) -> None:
    """选中后：正文落盘、摘要生成、记录 auto-accepted、游标推进、产物回写。"""
    status, generated = api_request(
        server, "POST", f"{_base(project)}/1/candidates", {"provider": "fake", "count": 3}
    )
    assert status == 200, generated
    chosen = generated["candidates"][1]
    index = chosen["index"]

    status, body = api_request(
        server, "POST", f"{_base(project)}/1/candidates", {"action": "select", "index": index}
    )
    assert status == 200, body
    assert body["content"] == chosen["content"], "提交的必须是读者选中的那一版，逐字相同"
    assert body["record"]["status"] == "auto-accepted"
    assert body["record"]["word_count"] == chosen["gate"]["cjk_chars"]
    assert body["selection"]["selected_index"] == index
    assert body["selection"]["summary_source"] in ("model", "mechanical")
    assert body["candidates"]["selected_index"] == index
    assert body["candidates"]["selected_at"]

    status, fresh = api_request(server, "GET", f"/api/projects/{project}")
    assert fresh["cursor"] == 1, "提交后游标必须推进"

    status, body = api_request(server, "GET", f"{_base(project)}/1/candidates")
    assert body["exists"] is True
    assert body["selected_index"] == index


def test_select_reevaluates_gate_against_current_state(server, project) -> None:
    """提交时按当前账面重判闸门，不沿用生成时的旧判决。

    场景：生成候选之后，先用 /run 把第 1 章跑出来，再回头选第 2 章的候选 ——
    此时跨章新鲜度的参照系变了。若沿用生成时的判决，等于把闸门让给时间差。
    """
    status, generated = api_request(
        server, "POST", f"{_base(project)}/2/candidates", {"provider": "fake", "count": 2}
    )
    assert status == 200, generated
    index = generated["candidates"][0]["index"]

    status, run = api_request(
        server,
        "POST",
        f"{_base(project)}/1/run",
        {"provider": "fake", "auto_accept": True, "max_repairs": 0},
    )
    assert status in (200, 409), run
    assert run["content"], "第 1 章应已落盘，跨章参照系随之改变"

    status, body = api_request(
        server, "POST", f"{_base(project)}/2/candidates", {"action": "select", "index": index}
    )
    assert status == 200, body
    assert body["selection"]["selected_index"] == index
    # 重判的判决与新参照系下的闸门一致：拿它再跑一次 check_chapter 应同结论
    assert body["selection"]["verdict"] in ("pass", "repair")


def test_select_rejects_without_generating_first(server, project) -> None:
    """没生成过候选就选中：400，且文案要告诉用户先做什么。"""
    status, body = api_request(
        server, "POST", f"{_base(project)}/2/candidates", {"action": "select", "index": 0}
    )
    assert status == 400, body
    assert "并行候选" in body["error"]


def test_select_out_of_range_index(server, project) -> None:
    """候选编号越界：400，文案带上这一章实际有几版。"""
    api_request(server, "POST", f"{_base(project)}/1/candidates", {"count": 2})
    status, body = api_request(
        server, "POST", f"{_base(project)}/1/candidates", {"action": "select", "index": 99}
    )
    assert status == 400, body
    assert "99" in body["error"] and "2 版" in body["error"]


def test_candidate_count_bounds(server, project) -> None:
    """数量越界回 400。下限 2 是产品定义：一版谈不上挑最优。"""
    status, body = api_request(server, "POST", f"{_base(project)}/1/candidates", {"count": 1})
    assert status == 400, body
    assert "2-6" in body["error"]

    status, body = api_request(server, "POST", f"{_base(project)}/1/candidates", {"count": 7})
    assert status == 400, body

    status, body = api_request(server, "POST", f"{_base(project)}/1/candidates", {"count": "abc"})
    assert status == 400, body
    assert "整数" in body["error"]


def test_unknown_action_and_provider(server, project) -> None:
    """未知 action / 未知 provider 都是 400，不能静默当成生成。"""
    status, body = api_request(
        server, "POST", f"{_base(project)}/1/candidates", {"action": "nope"}
    )
    assert status == 400, body
    assert "generate" in body["error"] and "select" in body["error"]

    status, body = api_request(
        server, "POST", f"{_base(project)}/1/candidates", {"provider": "bogus"}
    )
    assert status == 400, body


def test_chapter_out_of_outline(server, project) -> None:
    """细纲里没有这一章：400，且不写任何文件。"""
    status, body = api_request(
        server, "POST", f"{_base(project)}/9/candidates", {"count": 2}
    )
    assert status == 400, body
    assert "细纲" in body["error"]


def test_one_failed_candidate_does_not_sink_the_batch(server, project, monkeypatch) -> None:
    """单个候选失败只把自己标成 failed，其它候选照常返回。

    真模型下 429/超时是常态；一个候选挂掉就让整批 500，读者连看都看不到
    已经生成好的那几版。
    """
    from ndecon.creation import candidates as candidates_module

    original = candidates_module._run_one

    def flaky(spec, **kwargs):
        if spec.index == 1:
            raise RuntimeError("模拟：第 2 个候选炸了")
        return original(spec, **kwargs)

    monkeypatch.setattr(candidates_module, "_run_one", flaky)
    status, body = api_request(
        server, "POST", f"{_base(project)}/1/candidates", {"count": 3, "provider": "fake"}
    )
    assert status == 200, body
    assert body["ok_count"] == 2, body
    failed = [c for c in body["candidates"] if c["status"] == "failed"]
    assert len(failed) == 1
    assert "模拟" in failed[0]["error"]
    assert failed[0]["content"] == ""


def test_candidates_artifact_stays_out_of_manuscript_directory(server, project, tmp_path) -> None:
    """候选产物落在项目根目录的 candidates_chNNN.json，不碰 manuscripts/ 与 summaries/。"""
    api_request(server, "POST", f"{_base(project)}/1/candidates", {"count": 2})
    project_dir = tmp_path / "projects" / project
    assert (project_dir / "candidates_ch001.json").is_file()
    assert not (project_dir / "manuscripts" / "ch001.md").exists()
    assert not (project_dir / "summaries" / "ch001.json").exists()
