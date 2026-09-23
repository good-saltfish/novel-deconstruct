"""OpenAICompatProvider 契约测试（httpx.MockTransport 零网络）。"""

import json

import httpx
import pytest

from ndecon.providers.errors import (
    ProviderConfigError,
    ProviderHTTPError,
    ProviderResponseError,
    ProviderSchemaError,
)
from ndecon.providers.openai_compat import OpenAICompatProvider

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
