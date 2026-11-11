"""错误文案脱敏：路径不能出网，其余信息必须保留。

安全审计 H-2 的现场：面板二十多处把异常原文回给前端，`OSError` 的 str 里带着
绝对路径、pydantic 的 `ValidationError` 带着输入值。前端又是原样渲染的，于是
「这个目录在你机器上存在」「你的用户名是某某」变成任何人打一次面板就能拿到的
信息。修法是在 `_error` 出口统一抹一次，而不是逐个改二十几处——逐点改一定会漏，
以后新加的路由又会漏一遍。

这些测试钉住三件事：
1. 绝对路径（Windows/POSIX、单双反斜杠、正斜杠）必须被抹成 `<路径>`；
2. 抹完之后错误原因还在（状态码、字段名、异常类名不能一起消失，
   否则用户彻底看不懂发生了什么）；
3. URL 只保留 host，不保留 path（base_url 里可能藏内网主机与接口）。
"""

from __future__ import annotations

import pytest

from ndecon.panel.server import _display_name, _display_stem, api_request, scrub_paths, serve_in_thread


@pytest.fixture()
def server(tmp_path):
    """在随机端口起面板，用完关掉（同 `test_panel_api.py` 的写法）。"""
    srv, _thread = serve_in_thread(tmp_path, port=0)
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.mark.parametrize(
    "raw",
    [
        r"读取源文件失败: D:\Users\卢端炜\Documents\书.txt 不存在",
        r"FileNotFoundError: [WinError 3] 系统找不到指定的路径: 'D:\\myproject\\a\\b'",
        r'File "D:\Python\Python310\lib\site-packages\httpx\_client.py", line 3',
        "读 D:/myproject/novel-deconstruct/src 失败",
        "/var/log/syslog 打不开",
        "/Users/someone/secret.txt",
    ],
)
def test_absolute_paths_are_scrubbed(raw: str) -> None:
    """任何形态的本机绝对路径都不能出现在响应里。"""
    cleaned = scrub_paths(raw)
    assert "<路径>" in cleaned
    for leak in ("卢端炜", "myproject", "Python310", "site-packages", "syslog", "secret.txt"):
        assert leak not in cleaned


def test_reason_survives_scrubbing() -> None:
    """抹路径不能把错误原因一起抹掉——否则用户看不懂发生了什么。"""
    cleaned = scrub_paths(r"读取 D:\data\book.txt 失败：UnicodeDecodeError")
    assert "UnicodeDecodeError" in cleaned
    assert "失败" in cleaned
    assert "book.txt" not in cleaned


def test_plain_message_is_untouched() -> None:
    """没有路径的文案必须逐字不变。"""
    raw = "服务端内部错误：ProviderResponseError: 创作骨架缺少 parts 字段"
    assert scrub_paths(raw) == raw
    assert scrub_paths("源文件不存在：novel.txt") == "源文件不存在：novel.txt"


def test_empty_message_passes_through() -> None:
    """空文案不炸（`_error` 有可能收到空串）。"""
    assert scrub_paths("") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTP 401: https://api.example.com/v1/chat/completions 返回 unauthorized", "https://api.example.com"),
        ("http://127.0.0.1:41899/v1", "http://127.0.0.1"),
        ("模型网关 https://open.bigmodel.cn/api/paas/v4 返回 429", "https://open.bigmodel.cn"),
    ],
)
def test_url_keeps_host_drops_path(raw: str, expected: str) -> None:
    """URL 保留 host（否则「连不上哪个服务器」没法排查），抹掉 path。"""
    cleaned = scrub_paths(raw)
    assert expected in cleaned
    assert "/v1" not in cleaned
    assert "chat/completions" not in cleaned


def test_url_is_handled_before_windows_path() -> None:
    """`https://x` 里的 `s:` 不能被当成盘符吃掉一半。

    这条单独钉住顺序问题：先抹 Windows 路径会把 `https` 变成 `htt<路径>`。
    """
    cleaned = scrub_paths("https://open.bigmodel.cn/api/paas/v4")
    assert cleaned.startswith("https://open.bigmodel.cn")
    assert "htt<路径>" not in cleaned


def test_display_name_handles_both_separators() -> None:
    """文件名必须两个平台都切得对。

    这是 CI 上唯一红的测试暴露出来的坑：`Path(r"D:\\x\\书.txt").name` 在
    Windows 上是 `书.txt`，在 Linux 上因为反斜杠不是分隔符而返回整串
    `D:\\x\\书.txt`，接着被脱敏正则换成 `<路径>`，用户看不到是哪个文件。
    """
    assert _display_name(r"D:\myproject\不存在的一本书.txt") == "不存在的一本书.txt"
    assert _display_name("/home/u/书.txt") == "书.txt"
    assert _display_name("书.txt") == "书.txt"
    assert _display_name("  D:\\a\\b\\c.md  ") == "c.md"
    assert _display_name("") == ""


def test_display_stem_drops_extension_across_platforms() -> None:
    assert _display_stem(r"D:\myproject\守灯人.txt") == "守灯人"
    assert _display_stem("/home/u/守灯人.md") == "守灯人"
    # 没有扩展名时原样返回，不能把「第一卷」这种名字截成「第一卷」
    assert _display_stem(r"D:\myproject\第一卷") == "第一卷"
    # 以点开头的隐藏文件名不该被切空
    assert _display_stem("/home/u/.env") == ".env"


def test_error_endpoint_scrubs_in_practice(server) -> None:
    """端到端：真打一个会带路径的失败请求，响应体里不能有本机路径。"""
    status, body = api_request(
        server,
        "POST",
        "/api/projects/import",
        {"file_path": r"D:\myproject\不存在的一本书.txt", "name": "x"},
    )
    assert status == 400
    assert "D:" not in body["error"]
    assert "myproject" not in body["error"]
    # 但用户仍要知道是哪个文件没找到。这条在 Linux 上曾经必挂：`Path.name`
    # 在 POSIX 不认反斜杠，整个字符串被当成文件名，随后又被脱敏正则换成
    # `<路径>`，文件名一起没了——所以文件名必须用 `_display_name` 自己切。
    assert "不存在的一本书.txt" in body["error"]
