"""连通性测试的判据（回归测试）。

这一组测试存在的理由：`test_connection` 原先只看 `content` 是否非空，
把「推理模型先把预算花在 reasoning 上」误判成「链路不通」。实测
sensenova 与 aliyun 在 1/32/256 token 下全部被误杀（真实数据见
`_scratch/probe_probe_verdict.py`）。判据错了比功能缺了更糟：用户会
以为这个模型不能用，从而放弃一个本来能写正文的供应商。

所以这里把四种真实响应形态都钉住：
1. 正常有正文 → 通过
2. 正文空但 reasoning 有、completion_tokens > 0 → **通过**（这正是被误杀的）
3. 正文空、completion_tokens = 0、finish=stop → 通过（端点确实应答了）
4. 空内容且没有 finish_reason → 失败（这不是「模型在推理」，是响应坏了）
"""

from __future__ import annotations

import httpx

# 故意用模块别名 + 改名，而不是 `from ... import test_connection`：
# 那个函数名本身以 `test_` 开头，一旦以同名进入测试模块命名空间，pytest
# 会把它当成一个测试用例收集，再把它的 `config` 参数当成 fixture，
# 报 `fixture 'config' not found`。**改名是必需的**——只换成模块访问
# 但保留同名，pytest 照样收集。这个坑踩过两次。
from ndecon.llm import session as llm_session

probe_connection = llm_session.test_connection
LLMSessionConfig = llm_session.LLMSessionConfig
PROBE_MAX_TOKENS = llm_session.PROBE_MAX_TOKENS


def _config() -> LLMSessionConfig:
    """一个形态合法、不依赖真实凭据的配置。"""
    return LLMSessionConfig(
        provider="custom",
        api_key="test-key",
        base_url="http://127.0.0.1:1/v1",
        model="some-model",
    )


def _transport(payload: dict) -> httpx.MockTransport:
    """把给定响应体喂给 test_connection，不发真实请求。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _choice(content: str, finish: str = "stop") -> dict:
    """构造一条 choices 元素；reasoning 不计入 message.content（OpenAI 约定）。"""
    return {"message": {"role": "assistant", "content": content}, "finish_reason": finish}


def test_normal_content_passes() -> None:
    """有正文就是通。"""
    result = probe_connection(_config(), transport=_transport({
        "choices": [_choice("可用")],
        "usage": {"completion_tokens": 3},
    }))
    assert result["ok"] is True


def test_reasoning_only_content_passes() -> None:
    """**核心回归**：正文为空但模型确实产出了 token —— 这必须判通过。

    sensenova 在 max_tokens=32 时的真实响应就是 content=""、
    finish_reason="length"、completion_tokens=32。链路是通的。
    """
    result = probe_connection(_config(), transport=_transport({
        "choices": [_choice("", finish="length")],
        "usage": {"completion_tokens": 32},
    }))
    assert result["ok"] is True
    assert "正文为空" in result["detail"]


def test_empty_content_with_stop_passes() -> None:
    """模型正常停下但没写字：仍然证明端点应答了。"""
    result = probe_connection(_config(), transport=_transport({
        "choices": [_choice("", finish="stop")],
        "usage": {"completion_tokens": 0},
    }))
    assert result["ok"] is True


def test_missing_finish_and_zero_tokens_fails() -> None:
    """空内容、零 token、连 finish_reason 都没有 —— 响应坏了，必须失败。"""
    result = probe_connection(_config(), transport=_transport({
        "choices": [{"message": {"role": "assistant", "content": ""}}],
    }))
    assert result["ok"] is False
    assert "没有产出任何 token" in result["detail"]


def test_http_error_is_reported_not_raised() -> None:
    """上游 401 必须变成 ok=False，不能抛异常打断面板。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    result = probe_connection(_config(), transport=httpx.MockTransport(handler))
    assert result["ok"] is False
    assert "401" in result["detail"]


def test_probe_budget_is_not_one() -> None:
    """预算是 256 而不是 1 —— 1 会让推理模型必然吐不出正文。

    这条断言存在的理由：预算是判据的一部分。改回 1 就等于把误杀重新装回去。
    """
    assert PROBE_MAX_TOKENS >= 256


def test_request_carries_probe_budget() -> None:
    """实际请求体里带的 max_tokens 必须是 PROBE_MAX_TOKENS。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [_choice("可用")],
            "usage": {"completion_tokens": 2},
        })

    probe_connection(_config(), transport=httpx.MockTransport(handler))
    assert seen["max_tokens"] == PROBE_MAX_TOKENS
