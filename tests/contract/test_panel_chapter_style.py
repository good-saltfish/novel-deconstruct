"""面板章节笔法体检契约：只读比对人类基线，不改正文一个字。

与 ``/lint`` 的区别：``/lint`` 查的是「AI 指纹」（破折号、不是X而是Y这类
可枚举的词法特征），``/style`` 查的是**节奏分布**——段落长度、句长、分句
长短这些人类与 AI 系统性错位的维度。两者都只报告、都不拦。
"""

import pytest

from ndecon.panel.server import api_request, serve_in_thread
from ndecon.quality.style import STYLE_VERSION, baseline_meta

# 人类腔：一行一句。
_HUMAN_SENTENCES = [
    "他把那份合同翻到第二页看了一遍，月薪四千，试用期三个月。",
    "王振没有抬头，手指在桌面上敲了两下又停住，像是在算什么。",
    "窗外有人在喊口号，声音隔着玻璃传进来，变得又闷又远。",
    "陈默把笔放回笔筒里，问了一句，这个事情现在还能改吗。",
    "苏晚推门进来，手里抱着一叠打印纸，边走边数还剩下多少。",
    "走廊尽头的灯忽明忽暗，把两个人的影子拉得很长很长。",
]


def _human_text(repeat: int = 12) -> str:
    """足够长的人类腔正文：每行一句、句子够长、没有破折号。"""
    lines = [_HUMAN_SENTENCES[i % len(_HUMAN_SENTENCES)] for i in range(repeat)]
    return "\n".join(lines)


def _ai_text(repeat: int = 12) -> str:
    """AI 腔：三句挤一段、破折号与身体外化密集。"""
    block = (
        "他低头看着合同。月薪四千——试用期三个月——违约金三十万。"
        "胸口发紧，喉咙发干，指尖发凉。"
    )
    return "\n".join(block for _ in range(repeat))


@pytest.fixture()
def server(tmp_path):
    srv, _thread = serve_in_thread(tmp_path, port=0)
    yield srv
    srv.shutdown()
    srv.server_close()


def _create_with_manuscript(server) -> str:
    _, cre = api_request(
        server,
        "POST",
        "/api/projects/create",
        {"title": "笔法体检书", "genre": "都市竞技", "premise": "电竞重生"},
    )
    pid = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{pid}/generate", {"provider": "fake"})
    api_request(server, "POST", f"/api/projects/{pid}/chapters/1/generate", {"provider": "fake"})
    return pid


def test_style_returns_baseline_report_without_touching_text(server) -> None:
    """返回完整报告与基线出处；正文前后完全一致（只读）。"""
    pid = _create_with_manuscript(server)
    _, before = api_request(server, "GET", f"/api/projects/{pid}/chapters/1")
    assert before["content"]

    status, report = api_request(server, "GET", f"/api/projects/{pid}/chapters/1/style")
    assert status == 200
    assert report["version"] == STYLE_VERSION
    assert isinstance(report["cjk_chars"], int) and report["cjk_chars"] > 0
    assert 0.0 <= report["score"] <= 1.0
    # 基线必须自报家门：用户看到「越界」时要能判断这条线是谁定的、基于多少章。
    meta = baseline_meta()
    assert report["baseline"]["books"] == meta["books"]
    assert report["baseline"]["chapters"] == meta["chapters"]
    assert report["baseline"]["holdout_books"] == meta["holdout_books"]
    # 每条越界都要有实测值、阈值、方向和可读说明，否则用户无从下手。
    for finding in report["findings"]:
        assert finding["metric"] and finding["bound"]
        assert finding["direction"] in ("hi", "lo")
        assert finding["detail"].startswith(finding["metric"])
    # 结论不能写成「这是 AI 写的」——它只是节奏分布信号。
    assert "不是「是不是 AI」的判定" in report["note"]

    _, after = api_request(server, "GET", f"/api/projects/{pid}/chapters/1")
    assert after["content"] == before["content"], "体检不得改动正文"


def test_style_requires_manuscript(server) -> None:
    """尚无正文的章节不能体检。"""
    _, cre = api_request(
        server,
        "POST",
        "/api/projects/create",
        {"title": "空章书", "genre": "都市", "premise": "还没有正文"},
    )
    pid = cre["meta"]["id"]
    api_request(server, "POST", f"/api/projects/{pid}/generate", {"provider": "fake"})
    status, body = api_request(server, "GET", f"/api/projects/{pid}/chapters/2/style")
    assert status == 404 and "尚无正文" in body["error"]


def test_human_layout_scores_better_than_ai_layout(server) -> None:
    """同样长度的两种排版：一行一句的越界项必须少于三句挤一段的。

    这是这条判据存在的唯一理由——如果换了排版分数不动，那它测的就不是节奏。
    """
    pid = _create_with_manuscript(server)

    api_request(
        server, "PUT", f"/api/projects/{pid}/chapters/1", {"content": _human_text()}
    )
    _, human = api_request(server, "GET", f"/api/projects/{pid}/chapters/1/style")

    api_request(server, "PUT", f"/api/projects/{pid}/chapters/1", {"content": _ai_text()})
    _, ai = api_request(server, "GET", f"/api/projects/{pid}/chapters/1/style")

    assert human["cjk_chars"] > 0 and ai["cjk_chars"] > 0
    assert len(human["findings"]) < len(ai["findings"])
    assert human["score"] < ai["score"]
    # 三句挤一段会同时踩中「段均句数」与「单句段占比」两条相反的判据——
    # 这正是它能被抓住的原因：AI 的病灶是节奏单峰，不是某一句写得差。
    metrics = {f["metric"] for f in ai["findings"]}
    assert "段均句数" in metrics
    assert "单句段占比" in metrics
