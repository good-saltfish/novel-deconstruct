"""LLM 配置的地址校验：SSRF 与「换 host 抢已存 key」。

这两条是安全审计里的 blocker：

* B-3 SSRF —— `base_url` 完全由请求体决定，引擎会照着它发请求并回显远端
  300 字响应；配合 `/api/projects/import` 能读本机文件，等于一个可用跳板。
* B-2 抢 key —— 原实现只在「provider id 相同」时复用已存 key。攻击者发一个
  provider 不变、base_url 指向自己的请求，引擎就把真 key 当 Bearer 发出去。

这里的断言刻意贴着行为而不是实现：不检查内部私有函数叫了什么，只检查
「哪些地址被拒、key 会不会跟着新地址跑」。
"""

from __future__ import annotations

import pytest

from ndecon.llm.session import same_origin, validate_config_payload
from ndecon.panel.server import api_request, serve_in_thread


def _config(base_url: str) -> tuple[object, str | None]:
    return validate_config_payload(
        {"provider": "custom", "api_key": "k", "base_url": base_url, "model": "m"}
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:169.254.169.254]/",
        "http://0.0.0.0:8080/v1",
        "http://[fe80::1]/v1",
        "http://224.0.0.1/v1",
    ],
)
def test_metadata_and_link_local_are_rejected(base_url: str) -> None:
    config, error = _config(base_url)
    assert config is None
    assert error is not None and "不允许的地址" in error


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:41899/v1",  # 插件的模型桥就在这里
        "http://localhost:11434/v1",  # Ollama
        "http://192.168.1.5:11434/v1",  # 局域网自建推理
        "https://api.openai.com/v1",
    ],
)
def test_local_and_lan_targets_stay_allowed(base_url: str) -> None:
    config, error = _config(base_url)
    assert error is None, error
    assert config is not None


def test_same_origin_ignores_path_but_not_host() -> None:
    assert same_origin("http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/openai/v1")
    assert not same_origin("http://127.0.0.1:8000/v1", "http://127.0.0.1:9000/v1")
    assert not same_origin("https://api.openai.com/v1", "https://evil.example/v1")
    assert not same_origin("", "https://api.openai.com/v1")


@pytest.fixture()
def server(tmp_path):
    srv, _thread = serve_in_thread(tmp_path, port=0)
    yield srv
    srv.shutdown()
    srv.server_close()


def test_key_is_reused_only_on_same_origin(server) -> None:
    status, _ = api_request(
        server,
        "PUT",
        "/api/llm/settings",
        {
            "provider": "custom",
            "api_key": "sk-real",
            "base_url": "https://api.example.com/v1",
            "model": "m",
        },
    )
    assert status == 200

    # 同源换路径前缀：key 留空应当复用，用户不必重填。
    status, body = api_request(
        server,
        "PUT",
        "/api/llm/settings",
        {
            "provider": "custom",
            "api_key": "",
            "base_url": "https://api.example.com/openai/v1",
            "model": "m",
        },
    )
    assert status == 200, body

    # 换 host：必须拒绝，否则真 key 会被发到 evil 那边。
    status, body = api_request(
        server,
        "PUT",
        "/api/llm/settings",
        {
            "provider": "custom",
            "api_key": "",
            "base_url": "https://evil.example/v1",
            "model": "m",
        },
    )
    assert status == 400
    assert "重新填写" in body["error"]


def test_probe_endpoint_rejects_metadata_host(server) -> None:
    status, body = api_request(
        server,
        "POST",
        "/api/llm/test",
        {"provider": "custom", "base_url": "http://169.254.169.254/", "model": "m", "api_key": "k"},
    )
    assert status == 400
    assert "不允许的地址" in body["error"]
