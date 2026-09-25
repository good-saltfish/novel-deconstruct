"""面板 LLM 供应商设置与 AI 生成契约（#23）。全程不触网：真实调用以 stub provider 替代。"""

import pytest

from ndecon.panel.server import api_request, serve_in_thread


@pytest.fixture()
def server(tmp_path):
    """在随机端口启动面板服务器，测试结束后关闭。"""
    srv, _thread = serve_in_thread(tmp_path, port=0)
    yield srv
    srv.shutdown()
    srv.server_close()


def _valid_skeleton_data() -> dict:
    """构造一份能通过 _draft_from_dict 严格校验的骨架 JSON。"""
    return {
        "positioning": {
            "genre": "都市异能",
            "audience": "男频读者",
            "core_premise": "路灯下封着夜色",
            "selling_points": ["钩子前置"],
            "tone_target": "紧张",
        },
        "volume": {
            "volume_title": "第一卷",
            "volume_goal": "活下来",
            "main_conflict": "人与异常",
            "ending_hook": "更大危机",
        },
        "chapters": [
            {
                "order": i,
                "title": f"第{i}步",
                "core_event": f"事件{i}",
                "opening_hook": f"开{i}",
                "ending_hook": f"结{i}",
            }
            for i in range(1, 11)
        ],
        "protagonist": {
            "name": "林盏",
            "background": "普通人",
            "personality": "冷静",
            "desire": "守灯",
            "flaw": "过度理性",
            "signature_ability": "铜灯",
        },
        "golden_finger": {
            "name": "铜灯",
            "form": "器物",
            "ability": "照见异常",
            "cost_limitation": "一日三燃",
            "growth_path": "逐渐增强",
        },
    }


class _StubChatProvider:
    """OpenAICompatProvider 的零网络替身。"""

    model = "stub-model"

    def __init__(self) -> None:
        """记录调用消息。"""
        self.captured: list[dict] | None = None

    def _chat_json(self, messages: list[dict]) -> dict:
        """返回合法骨架并记录消息。"""
        self.captured = messages
        return _valid_skeleton_data()


def test_provider_catalog(server) -> None:
    """供应商目录可读取，覆盖国内主流厂商与本地/自定义。"""
    status, providers = api_request(server, "GET", "/api/llm/providers")
    assert status == 200
    ids = {p["id"] for p in providers}
    assert {"deepseek", "zhipu", "siliconflow", "moonshot", "dashscope", "ollama", "openai", "custom"} <= ids
    deepseek = next(p for p in providers if p["id"] == "deepseek")
    assert deepseek["base_url"].endswith("/v1") and deepseek["default_model"]


def test_initial_settings_unconfigured_and_no_key_ever_returned(server) -> None:
    """初始未配置；任何响应都不携带 api_key 字段。"""
    status, view = api_request(server, "GET", "/api/llm/settings")
    assert status == 200
    assert view["configured"] is False and "api_key" not in view and "has_key" in view


def test_save_settings_validation(server) -> None:
    """缺 key（非本地供应商）/未知供应商被拒；Ollama 免 key 可保存。"""
    status, body = api_request(
        server, "PUT", "/api/llm/settings",
        {"provider": "deepseek", "api_key": "", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
    )
    assert status == 400 and "API key" in body["error"]

    status, body = api_request(server, "PUT", "/api/llm/settings", {"provider": "who"})
    assert status == 400 and "未知供应商" in body["error"]

    status, view = api_request(
        server, "PUT", "/api/llm/settings",
        {"provider": "ollama", "api_key": "", "model": "qwen2.5:7b", "base_url": "http://127.0.0.1:11434/v1"},
    )
    assert status == 200 and view["configured"] is True
    assert "api_key" not in view


def test_key_stored_in_memory_but_never_serialized_back(server) -> None:
    """key 保存后 has_key=True，但视图与原始 JSON 均不含 key 明文。"""
    import json

    status, view = api_request(
        server, "PUT", "/api/llm/settings",
        {"provider": "deepseek", "api_key": "sk-secret-123456", "model": "deepseek-chat",
         "base_url": "https://api.deepseek.com/v1"},
    )
    assert status == 200 and view["has_key"] is True and "api_key" not in view
    assert "sk-secret-123456" not in json.dumps(view, ensure_ascii=False)

    # 同供应商再次保存、key 留空：保留旧 key（只改模型不报错）
    status, view2 = api_request(
        server, "PUT", "/api/llm/settings",
        {"provider": "deepseek", "api_key": "", "model": "deepseek-reasoner",
         "base_url": "https://api.deepseek.com/v1"},
    )
    assert status == 200 and view2["model"] == "deepseek-reasoner" and view2["has_key"] is True


def test_ai_skeleton_generation_uses_session_with_stub(server, monkeypatch) -> None:
    """provider=ai 时用面板会话中的模型（stub 零网络），产出标记真实模型来源。"""
    # 1) 未配置直接拒绝
    _, cre = api_request(server, "POST", "/api/projects/create", {"title": "AI书"})
    cre_id = cre["meta"]["id"]
    status, body = api_request(server, "POST", f"/api/projects/{cre_id}/generate", {"provider": "ai"})
    assert status == 400 and "模型设置" in body["error"]

    # 2) 注入会话配置并用 stub provider 顶替真实网络调用
    stub = _StubChatProvider()
    server.llm_session.provider = "deepseek"
    server.llm_session.api_key = "sk-test"
    server.llm_session.base_url = "https://api.deepseek.com/v1"
    server.llm_session.model = "stub-model"
    monkeypatch.setattr(server.llm_session, "require_provider", lambda: stub)

    status, generated = api_request(
        server, "POST", f"/api/projects/{cre_id}/generate", {"provider": "ai"}
    )
    assert status == 200, generated
    assert generated["draft"]["provenance"]["positioning"].startswith("openai-compat:")
    assert [c["order"] for c in generated["draft"]["chapters"]] == list(range(1, 11))
    # 确实把 stub 当成 openai-compat 通道调用了
    assert stub.captured is not None and stub.captured[0]["role"] == "system"


def test_test_endpoint_rejects_invalid_config_without_network(server) -> None:
    """测试端点对非法配置直接 400（不会发任何请求）。"""
    status, body = api_request(
        server, "POST", "/api/llm/test",
        {"provider": "custom", "api_key": "x", "base_url": "", "model": ""},
    )
    assert status == 400 and "base_url" in body["error"]

