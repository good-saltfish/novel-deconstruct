"""面板章节指纹体检契约：只读扫描，不改正文一个字。"""

import pytest

from ndecon.panel.server import api_request, serve_in_thread
from ndecon.quality.fingerprint import AI_FINGERPRINT_VERSION


@pytest.fixture()
def server(tmp_path):
    """面板服务器。"""
    srv, _thread = serve_in_thread(tmp_path, port=0)
    yield srv
    srv.shutdown()
    srv.server_close()


def _create_with_manuscript(server) -> str:
    """建项目 -> 生成 Fake 骨架 -> 生成第 1 章正文。"""
    _, cre = api_request(
        server, "POST", "/api/projects/create",
        {"title": "体检书", "genre": "都市异能", "premise": "路灯封着夜色"},
    )
    pid = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{pid}/generate", {"provider": "fake"})
    api_request(server, "POST", f"/api/projects/{pid}/chapters/1/generate", {"provider": "fake"})
    return pid


def test_lint_returns_report_without_touching_text(server) -> None:
    """体检返回 fp-v1 报告；正文扫描前后完全一致（只读）。"""
    pid = _create_with_manuscript(server)
    _, before = api_request(server, "GET", f"/api/projects/{pid}/chapters/1")
    assert before["content"]

    status, report = api_request(server, "GET", f"/api/projects/{pid}/chapters/1/lint")
    assert status == 200
    assert report["version"] == AI_FINGERPRINT_VERSION
    assert isinstance(report["cjk_chars"], int)
    assert report["paragraph_count"] >= 1
    # 每条规则都有可读标签、实测值、阈值；info 规则不参与判定
    rules = {f["rule"] for f in report["findings"]}
    assert {"not_x_but_y", "em_dash", "period_per_paragraph", "cjk_chars"} <= rules
    for finding in report["findings"]:
        assert finding["label"]
        assert finding["severity"] in ("warn", "info")
        if finding["severity"] == "info":
            assert finding["passed"] is True
    assert report["passed"] is all(
        f["passed"] for f in report["findings"] if f["severity"] == "warn"
    )

    _, after = api_request(server, "GET", f"/api/projects/{pid}/chapters/1")
    assert after["content"] == before["content"], "体检不得改动正文"


def test_lint_requires_manuscript(server) -> None:
    """尚无正文的章节不能体检。"""
    _, cre = api_request(
        server, "POST", "/api/projects/create",
        {"title": "空章书", "genre": "都市", "premise": "还没有正文"},
    )
    pid = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{pid}/generate", {"provider": "fake"})
    status, body = api_request(server, "GET", f"/api/projects/{pid}/chapters/2/lint")
    assert status == 404 and "尚无正文" in body["error"]


def test_lint_detects_density_over_threshold(server) -> None:
    """人造 AI 味正文：保存后被体检标出（只标出，不给改写文本）。"""
    pid = _create_with_manuscript(server)
    ai_text = "\n\n".join(
        [
            "这不是一把刀，而是一把被诅咒过的刀——它闪烁着冷冽的光芒，"
            "像是深渊的眼睛一般注视着他，这不是恐惧，是某种更深的、无名的、古老的战栗。",
            "他的手掌的边缘的温度的触感渐渐消失。",
            "这不是选择，是命运。这不是命运，是选择。这不是结束——是开始。",
        ]
    )
    status, _ = api_request(
        server, "PUT", f"/api/projects/{pid}/chapters/1", {"content": ai_text}
    )
    assert status == 200

    _, report = api_request(server, "GET", f"/api/projects/{pid}/chapters/1/lint")
    assert report["passed"] is False
    bad = {f["rule"] for f in report["findings"] if f["severity"] == "warn" and not f["passed"]}
    assert "not_x_but_y" in bad
    assert "em_dash" in bad
    # 报告里绝不出现改写建议
    assert "改写" not in str(report) and "建议改为" not in str(report)
