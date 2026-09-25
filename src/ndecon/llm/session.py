"""LLM 会话配置：进程内、线程安全、不落盘；含连通性测试与 provider 工厂。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field

from ndecon.llm.catalog import get_preset
from ndecon.providers.errors import ProviderError
from ndecon.providers.openai_compat import OpenAICompatProvider


class LLMSessionConfig(BaseModel):
    """面板当前选中的 LLM 配置（校验形态）。"""

    provider: str = Field(min_length=1)
    api_key: str = ""
    base_url: str = ""
    model: str = Field(min_length=1)


@dataclass
class LLMSession:
    """进程内会话：key 只在内存，重启即清空；读写加锁。"""

    provider: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """初始化可重入锁。"""
        self._lock = threading.RLock()

    def is_configured(self) -> bool:
        """是否已完成一次有效配置。"""
        with self._lock:
            preset = get_preset(self.provider)
            if preset is None:
                return False
            return preset.key_optional or bool(self.api_key)

    def update(self, config: LLMSessionConfig) -> None:
        """用通过校验的配置覆盖会话。"""
        with self._lock:
            self.provider = config.provider
            self.api_key = config.api_key
            self.base_url = config.base_url.rstrip("/")
            self.model = config.model

    def clear(self) -> None:
        """清空会话（如切换供应商时）。"""
        with self._lock:
            self.provider = ""
            self.api_key = ""
            self.base_url = ""
            self.model = ""

    def public_view(self) -> dict:
        """返回不含 key 的安全视图供前端展示。"""
        with self._lock:
            preset = get_preset(self.provider)
            return {
                "provider": self.provider,
                "provider_name": preset.name if preset else "",
                "base_url": self.base_url,
                "model": self.model,
                "configured": self.is_configured(),
                "has_key": bool(self.api_key),
            }

    def require_provider(self) -> OpenAICompatProvider:
        """按当前会话构造真实 provider；未配置时给可读错误（绝不静默回退 Fake）。"""
        with self._lock:
            preset = get_preset(self.provider)
            if preset is None:
                raise ProviderError("尚未选择模型供应商，请先在面板'模型设置'中配置")
            if not preset.key_optional and not self.api_key:
                raise ProviderError(f"已选择 {preset.name}，但未填写 API key，请先在模型设置中保存")
            return OpenAICompatProvider(
                api_key=self.api_key or "ollama",
                base_url=self.base_url,
                model=self.model,
            )


def validate_config_payload(payload: dict) -> tuple[LLMSessionConfig | None, str | None]:
    """校验前端提交的供应商配置；成功返回配置，失败返回错误信息。"""
    provider_id = str(payload.get("provider", "")).strip()
    preset = get_preset(provider_id)
    if preset is None:
        return None, f"未知供应商：{provider_id!r}"

    api_key = str(payload.get("api_key", "")).strip()
    base_url = str(payload.get("base_url", "")).strip().rstrip("/") or preset.base_url
    model = str(payload.get("model", "")).strip() or preset.default_model

    if preset.id == "custom":
        if not base_url:
            return None, "自定义供应商必须填写 base_url"
        if not model:
            return None, "自定义供应商必须填写模型名"
    if not preset.key_optional and not api_key:
        return None, f"{preset.name} 需要 API key（{preset.key_hint}）"
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        return None, "base_url 必须以 http:// 或 https:// 开头"

    return LLMSessionConfig(provider=provider_id, api_key=api_key, base_url=base_url, model=model), None


def test_connection(config: LLMSessionConfig, *, timeout: float = 20.0) -> dict:
    """用极简 chat 请求测试连通性；返回 {ok, latency_ms, detail}，不抛传输异常。"""
    started = time.perf_counter()
    try:
        with httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {config.api_key or 'ollama'}"},
        ) as client:
            # 不带 response_format，兼容更多本地/第三方端点；max_tokens 1 只验证链路
            response = client.post(
                f"{config.base_url}/chat/completions",
                json={
                    "model": config.model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0,
                },
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "detail": f"HTTP {response.status_code}：{response.text[:300]}",
            }
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if not str(content).strip():
            return {"ok": False, "latency_ms": latency_ms, "detail": "连接成功但返回空内容"}
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "detail": f"连接成功，模型 {config.model} 正常响应（{latency_ms}ms）",
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "detail": f"网络错误：{type(exc).__name__}: {str(exc)[:200]}",
        }
    except (KeyError, IndexError, ValueError) as exc:
        return {
            "ok": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "detail": f"端点响应不符合 OpenAI 格式：{exc}",
        }
