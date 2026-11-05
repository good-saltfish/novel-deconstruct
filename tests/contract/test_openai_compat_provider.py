"""OpenAICompatProvider 契约测试（httpx.MockTransport 零网络）。"""

import json

import httpx
import pytest

from ndecon.providers.errors import (
    ProviderConfigError,
    ProviderHTTPError,
    ProviderResponseError,
    ProviderSchemaError,
    ProviderTransportError,
)
from ndecon.providers.openai_compat import OpenAICompatProvider, _resolve_timeout

CHAPTER = (
    "第1章 雨夜归人\n"
    "陈默推开木门，雨水顺着戏袍滴落。他看见父亲坐在灯下发呆。"
    "父亲猛地站起，桌上的油灯不停摇晃。他低头连喝三碗凉水，门外雷声轰鸣不止。"
    "母亲从厨房冲出来捂住了嘴，这件事绝不能传到外面去。"
)


def _chat_response(payload: dict) -> httpx.Response:
    """构造标准 chat/completions 成功响应。"""
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)}}]
        },
    )


def _summary_payload() -> dict:
    """构造一份全部 quote 都能在 CHAPTER 中精确找到的合法摘要响应。"""
    return {
        "gist": "陈默雨夜归家，父母反应异常。",
        "characters": ["陈默", "父亲", "母亲"],
        "plot_points": [
            {
                "summary": "陈默雨夜推门回家。",
                "tone": "压抑",
                "theme_tags": ["亲情"],
                "characters": ["陈默"],
                "quote": "陈默推开木门，雨水顺着戏袍滴落。",
            },
            {
                "summary": "父亲震惊起身。",
                "tone": "紧张",
                "theme_tags": ["悬念", "亲情"],
                "characters": ["父亲"],
                "quote": "父亲猛地站起，桌上的油灯不停摇晃。",
            },
            {
                "summary": "母亲冲出来捂嘴。",
                "tone": "恐怖",
                "theme_tags": ["亲情"],
                "characters": ["母亲"],
                "quote": "母亲从厨房冲出来捂住了嘴",
            },
        ],
    }


def _make_provider(handler) -> tuple[OpenAICompatProvider, list[httpx.Request]]:
    """构造挂载 MockTransport 的 provider，并记录所有请求以便断言调用次数。"""
    seen: list[httpx.Request] = []

    def _logged(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    provider = OpenAICompatProvider(
        api_key="test-key",
        base_url="https://api.test/v1",
        model="test-model",
        transport=httpx.MockTransport(_logged),
        sleeper=lambda _s: None,
    )
    return provider, seen


def test_missing_api_key_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 key 且无环境变量时必须立即报配置错误。"""
    monkeypatch.delenv("NOVEL_DECON_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderConfigError):
        OpenAICompatProvider()


def test_successful_summary_anchors_evidence() -> None:
    """正常响应：情节点入库，quote 全部锚定回原文，来源版本齐全。"""
    provider, _ = _make_provider(lambda _r: _chat_response(_summary_payload()))
    summary = provider.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h" * 8)
    assert summary.gist
    assert len(summary.plot_points) == 3
    for point in summary.plot_points:
        assert point.source.is_located_in(CHAPTER)
    assert summary.model_id == "openai-compat:test-model"
    assert summary.prompt_version.startswith("oc-v")
    assert summary.characters == ["陈默", "父亲", "母亲"]


def test_retry_on_429_then_success() -> None:
    """429 应指数退避重试，第二次成功；诊断记录 1 次重试，无等待（注入 sleeper）。"""
    responses = [httpx.Response(429, text="rate limited"), _chat_response(_summary_payload())]

    def handler(_request: httpx.Request) -> httpx.Response:
        """按调用顺序返回 429 后成功。"""
        return responses.pop(0)

    provider, seen = _make_provider(handler)
    summary = provider.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h" * 8)
    assert len(seen) == 2
    assert provider.diagnostics.http_retries == 1
    assert len(summary.plot_points) == 3


def test_401_fails_fast_without_retry() -> None:
    """401 是配置类错误，不重试，立即抛出且保留状态码。"""
    provider, seen = _make_provider(lambda _r: httpx.Response(401, text="unauthorized"))
    with pytest.raises(ProviderHTTPError) as exc:
        provider.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h")
    assert exc.value.status_code == 401
    assert exc.value.retryable is False
    assert len(seen) == 1


def test_invalid_enum_raises_schema_error() -> None:
    """模型返回非法基调值必须整体判 schema 失败，不允许脏值入库。"""
    payload = _summary_payload()
    payload["plot_points"][1]["tone"] = "狂喜"
    provider, _ = _make_provider(lambda _r: _chat_response(payload))
    with pytest.raises(ProviderSchemaError):
        provider.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h")


def test_unlocatable_quote_is_dropped_and_all_fail_raises() -> None:
    """quote 锚定失败的情节点被丢弃计数；全部失败时判 schema 错误而非写入无证据数据。"""
    payload = _summary_payload()
    payload["plot_points"][0]["quote"] = "原文里根本没有这句话的任何痕迹呀"
    provider, _ = _make_provider(lambda _r: _chat_response(payload))
    summary = provider.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h")
    assert len(summary.plot_points) == 2
    assert provider.diagnostics.dropped_plot_points == 1
    assert provider.diagnostics.unresolved_quotes == 1

    payload_bad = _summary_payload()
    for point in payload_bad["plot_points"]:
        point["quote"] = "完全不存在的证据文本内容啊啊啊啊"
    provider2, _ = _make_provider(lambda _r: _chat_response(payload_bad))
    with pytest.raises(ProviderSchemaError):
        provider2.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h")


def test_markdown_fence_is_tolerated_but_garbage_raises() -> None:
    """带 ```json 围栏的响应可解析；纯文本垃圾响应判响应错误。"""
    provider_ok, _ = _make_provider(
        lambda _r: httpx.Response(
            200,
            json={"choices": [{"message": {"content": "```json\n" + json.dumps(_summary_payload(), ensure_ascii=False) + "\n```"}}]},
        )
    )
    summary = provider_ok.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h")
    assert len(summary.plot_points) == 3

    provider_bad, _ = _make_provider(
        lambda _r: httpx.Response(200, json={"choices": [{"message": {"content": "我觉得这章写得不错"}}]})
    )
    with pytest.raises(ProviderResponseError):
        provider_bad.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h")


def test_golden_report_never_accepts_takeaways() -> None:
    """即使模型擅自返回 takeaways，也必须被物理丢弃；锚定失败的可选 quote 置空计数。"""
    payload = {
        "opening_hook_note": "以雨夜红衣归家的画面开场。",
        "opening_quote": "陈默推开木门，雨水顺着戏袍滴落。",
        "worldview_revealed": ["存在油灯等退化设施"],
        "structure_beats": [{"name": "归家", "note": "陈默推门进屋"}],
        "cliffhanger_quote": "原文中不存在的章尾句子啊啊啊",
        "takeaways": ["1. 末句反转法值得学习"],  # 模型越界产出的评论
    }
    provider, _ = _make_provider(lambda _r: _chat_response(payload))
    report = provider.golden_report(1, "雨夜归人", CHAPTER, chapter_hash="h")
    assert report.takeaways == []
    assert report.opening_hook_quote
    assert report.cliffhanger_quote == ""
    assert provider.diagnostics.empty_optional_quotes == 1


def test_read_timeout_becomes_transport_error() -> None:
    """读超时必须归类成 ProviderTransportError，而不是裸抛 httpx 异常。

    这是真实故障的回归测试：裸抛 ReadTimeout 会逃出面板的 HTTP 处理器
    （它只 catch ProviderError），连接断开且写不出响应，插件侧只能看到
    `fetch failed`，界面上报成「无法连接引擎」——把模型超时误报成引擎挂了。
    """

    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider, seen = _make_provider(_timeout)
    with pytest.raises(ProviderTransportError) as exc:
        provider.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h")
    # 文案要能定位：模型名 + 实际秒数 + 下一步做什么
    assert "test-model" in str(exc.value)
    assert "900" in str(exc.value)
    assert "重启引擎" in str(exc.value)
    # 读超时不重试：默认超时已抬到 180 秒，撞上它说明这条路真的走不通。
    # 重试 4 次只会让用户干等 12 分钟才看到同一个错误（实测用户在第 3 次
    # 重试期间就来问「为什么写了 0 章」）。
    assert exc.value.retryable is False
    assert len(seen) == 1


def test_connect_error_becomes_transport_error() -> None:
    """连不上端点同样归类为传输错误，并带上端点地址便于排查。"""

    def _refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider, _ = _make_provider(_refused)
    with pytest.raises(ProviderTransportError) as exc:
        provider.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h")
    assert "https://api.test/v1" in str(exc.value)


def test_http_error_message_is_human_readable() -> None:
    """上游错误体是 OpenAI 风格的嵌套 JSON 时，错误文案要抠出 message。

    真实故障（2026-11-04）：用户换模型后看到
    `上游返回 HTTP 502: {"error":{"message":"模型没返回内容（max-tokens）",
    "code":"model-failed"}}` —— 转义引号糊成一片，有用的那句话被埋在最里面。
    插件桥就是这么返回的，所以这条必须覆盖。
    """

    def _bad_gateway(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            json={"error": {"message": "模型没返回内容（max-tokens）", "code": "model-failed"}},
            request=request,
        )

    provider, _ = _make_provider(_bad_gateway)
    with pytest.raises(ProviderHTTPError) as exc:
        provider.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h")
    message = str(exc.value)
    # 有用的那句话在最前面，且带状态码与上游 code
    assert "模型没返回内容" in message
    assert "502" in message
    assert "model-failed" in message
    # 不能把整段 JSON 原样塞进去
    assert '{"error"' not in message


def test_timeout_resolution_prefers_explicit_then_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超时值优先级：显式参数 > NOVEL_DECON_TIMEOUT > 默认 900 秒；非法环境变量回退默认。"""
    monkeypatch.setenv("NOVEL_DECON_TIMEOUT", "180")
    assert _resolve_timeout(None) == 180.0
    assert _resolve_timeout(45.0) == 45.0
    monkeypatch.setenv("NOVEL_DECON_TIMEOUT", "abc")
    assert _resolve_timeout(None) == 900.0
    monkeypatch.setenv("NOVEL_DECON_TIMEOUT", "-5")
    assert _resolve_timeout(None) == 900.0
    monkeypatch.delenv("NOVEL_DECON_TIMEOUT", raising=False)
    assert _resolve_timeout(None) == 900.0


def test_provider_records_effective_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider 必须记住实际生效的超时值，错误文案才能写出真实秒数。"""
    monkeypatch.setenv("NOVEL_DECON_TIMEOUT", "240")
    provider = OpenAICompatProvider(
        api_key="test-key",
        base_url="https://api.test/v1",
        model="test-model",
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={})),
    )
    assert provider._timeout_seconds == 240.0

def test_parse_json_repairs_unescaped_quote_inside_string() -> None:
    """模型在 JSON 字符串值里写裸引号时必须就地修好，不能整章白写。

    真实故障（2026-11-04）：glm-5.3-flash 写了 那条"绿灯路权"。，
    整段响应体报 Expecting ',' delimiter，一章 35 秒白写；
    同一模型换个采样又正常，所以是间歇性的。一次重试要再等 35 秒，
    不如就地修一次。
    """

    broken = '{"content": "他说那条"绿灯路权"。\\n\\n后面还有。", "review": {"hook_strength": 5}}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(broken)
    data = OpenAICompatProvider._parse_json(broken)
    # \\n 在 JSON 里是换行转义，解析后是真实换行
    assert data["content"] == '他说那条"绿灯路权"。\n\n后面还有。'
    assert data["review"] == {"hook_strength": 5}


def test_parse_json_leaves_valid_json_untouched() -> None:
    """合法 JSON 必须原样通过：修复器只允许在解析失败后介入。"""
    valid = json.dumps({"content": "正文", "review": {"hook_strength": 5}}, ensure_ascii=False)
    data = OpenAICompatProvider._parse_json(valid)
    assert data["content"] == "正文"


def test_parse_json_still_reports_truly_broken_payload() -> None:
    """修不好时必须报错，不能假装成功。"""
    with pytest.raises(ProviderResponseError) as exc:
        OpenAICompatProvider._parse_json('{"content": "没有收尾引号')
    assert "模型未返回合法 JSON" in str(exc.value)


def test_parse_json_rejects_non_object() -> None:
    """顶层不是对象要报错，否则调用方会在取键时炸在别处。"""
    with pytest.raises(ProviderResponseError) as exc:
        OpenAICompatProvider._parse_json("[1, 2, 3]")
    assert "顶层不是对象" in str(exc.value)

def test_parse_json_drops_trailing_explanation() -> None:
    """对象写完后模型又续一句解释时必须截掉尾巴，不能整章丢弃。

    真实故障（2026-11-04）：glm-5.3-flash 一轮返回
    Extra data: line 1 column 2235 (char 2234) —— 前面是完整对象，
    后面多了一段说明。内容已经写对了，只是包裹坏了。
    """
    payload = '{"content": "正文", "review": {"hook_strength": 5}}\n\n以上就是这一章。'
    data = OpenAICompatProvider._parse_json(payload)
    assert data["content"] == "正文"
    assert data["review"] == {"hook_strength": 5}


def test_parse_json_repairs_quotes_and_trailing_text_together() -> None:
    """两种坏法同时出现也要能修（同一轮实测确实会叠加）。"""
    payload = (
        '{"content": "他说那条"绿灯路权"。", "review": {"hook_strength": 5}}'
        '\n\n补充说明：这一章字数达标。'
    )
    data = OpenAICompatProvider._parse_json(payload)
    assert data["content"] == '他说那条"绿灯路权"。'


def test_parse_json_does_not_pretend_truncated_object_is_ok() -> None:
    """对象本身截断时必须报错——不能把半截当完整交付。"""
    with pytest.raises(ProviderResponseError):
        OpenAICompatProvider._parse_json('{"content": "正文", "review": {"hook_strength": 5')


# ---------- 输出预算（max_tokens）----------
#
# 这三条钉住的是「预算怎么传出去」，不是「模型写多少字」。为什么值得单独测：
# 不传 max_tokens 时上游用自己的默认上限，实测有中转站默认只给 2048，
# 一章 2600 字必然被砍在中间，闸门判「生成被截断」，重写两次仍砍在同一处。


def _request_body(request: httpx.Request) -> dict:
    """取出请求体 JSON，供断言字段是否出现。"""
    import json as _json

    return _json.loads(request.content)


def test_no_max_tokens_field_by_default() -> None:
    """实例未指定预算时，请求体里不应出现 max_tokens（交给上游默认）。"""
    provider, seen = _make_provider(lambda _r: _chat_response(_summary_payload()))
    provider.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h" * 8)
    assert "max_tokens" not in _request_body(seen[0])


def test_instance_budget_is_sent() -> None:
    """实例级预算要落到请求体里。"""
    provider = OpenAICompatProvider(
        api_key="test-key",
        base_url="https://api.test/v1",
        model="test-model",
        max_tokens=4096,
        transport=httpx.MockTransport(lambda _r: _chat_response(_summary_payload())),
        sleeper=lambda _s: None,
    )
    provider.summarize_chapter(1, "雨夜归人", CHAPTER, chapter_hash="h" * 8)
    assert provider.max_tokens == 4096


def test_per_call_budget_overrides_instance() -> None:
    """单次调用的预算覆盖实例默认值——写正文要按本章目标字数单独算。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _chat_response({"content": "正文", "review": {}})

    provider = OpenAICompatProvider(
        api_key="test-key",
        base_url="https://api.test/v1",
        model="test-model",
        max_tokens=2048,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    provider._chat_json([{"role": "user", "content": "写一章"}], max_tokens=6240)  # noqa: SLF001
    assert _request_body(seen[0])["max_tokens"] == 6240
