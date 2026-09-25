"""LLM 供应商目录与会话的单元测试（#23）：校验规则与 key 不回传。"""

from ndecon.llm.catalog import catalog_payload, get_preset
from ndecon.llm.session import LLMSession, validate_config_payload


def test_catalog_payload_shape() -> None:
    """目录字段完整且不含任何凭据字段。"""
    payload = catalog_payload()
    assert payload
    for item in payload:
        assert {"id", "name", "base_url", "models", "default_model", "key_optional"} <= set(item)
        assert "api_key" not in item


def test_validate_rules() -> None:
    """校验：缺 key/未知供应商/自定义缺 URL 被拒；Ollama 免 key。"""
    cfg, err = validate_config_payload({"provider": "deepseek", "api_key": "", "model": "", "base_url": ""})
    assert cfg is None and "API key" in err

    cfg, err = validate_config_payload({"provider": "ghost", "api_key": "x"})
    assert cfg is None and "未知供应商" in err

    cfg, err = validate_config_payload({"provider": "custom", "api_key": "x", "base_url": "", "model": ""})
    assert cfg is None and "base_url" in err

    cfg, err = validate_config_payload(
        {"provider": "ollama", "api_key": "", "model": "qwen2.5:7b", "base_url": "http://127.0.0.1:11434/v1"}
    )
    assert err is None and cfg is not None and cfg.api_key == ""

    # 预置供应商可省略 base_url/model，取默认值
    cfg, err = validate_config_payload({"provider": "zhipu", "api_key": "k"})
    assert err is None and cfg.model == get_preset("zhipu").default_model
    assert cfg.base_url == get_preset("zhipu").base_url


def test_session_public_view_never_contains_key() -> None:
    """会话视图只暴露配置状态，不含 key 明文。"""
    session = LLMSession()
    cfg, _ = validate_config_payload(
        {"provider": "deepseek", "api_key": "sk-secret", "model": "deepseek-chat",
         "base_url": "https://api.deepseek.com/v1"}
    )
    session.update(cfg)
    view = session.public_view()
    assert view["configured"] is True and view["has_key"] is True
    assert "api_key" not in view and "sk-secret" not in str(view)
    assert session.api_key == "sk-secret"  # 内部仍保留供调用
    session.clear()
    assert session.is_configured() is False


def test_require_provider_error_messages() -> None:
    """未配置/缺 key 时的错误文案明确，引导用户去面板设置。"""
    import pytest

    from ndecon.providers.errors import ProviderError

    session = LLMSession()
    with pytest.raises(ProviderError, match="模型设置"):
        session.require_provider()

