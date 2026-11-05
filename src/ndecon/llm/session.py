"""LLM 会话配置：进程内、线程安全、不落盘；含连通性测试与 provider 工厂。"""

from __future__ import annotations

import hashlib
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


def _key_fingerprint(api_key: str) -> str:
    """key 的短指纹：用于核对「引擎手里的是不是我刚下发的那个 key」。

    只取 sha256 前 12 位，反推不出原文；空 key 返回空串。
    """
    if not api_key:
        return ""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


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
        """返回不含 key 的安全视图供前端展示。

        额外给一个 `key_fingerprint`：`has_key` 只能说明「有 key」，不能说明
        「还是我刚才下发的那个 key」。插件每次拉起引擎都会换一个桥 token，
        只比对 base_url/model 的话，两边都显示「配好了」而实际 token 已经对不上，
        用户要到点生成时才吃一个 401。指纹是 key 的 sha256 前 12 位，
        反推不出原文，但足够判断是否同一个。
        """
        with self._lock:
            preset = get_preset(self.provider)
            return {
                "provider": self.provider,
                "provider_name": preset.name if preset else "",
                "base_url": self.base_url,
                "model": self.model,
                "configured": self.is_configured(),
                "has_key": bool(self.api_key),
                "key_fingerprint": _key_fingerprint(self.api_key),
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


# 连通性测试的预算。不是 1：推理型模型（sensenova、aliyun 的 deepseek 系）
# 会先把这点预算全花在 reasoning_content 上，正文一个字符都留不下，
# 于是链路明明是通的却被判「返回空内容」。256 是实测能让这类模型吐出正文
# 的最小值（sensenova 在 256 时仍 finish=length，1024 才稳；但连通性测试
# 只需要「有没有人应答」，不必等它把话说完，见下方判据）。
PROBE_MAX_TOKENS = 256


def test_connection(
    config: LLMSessionConfig,
    *,
    timeout: float = 20.0,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """用极简 chat 请求测试连通性；返回 {ok, latency_ms, detail}，不抛传输异常。

    判据是**链路通不通**，不是「模型有没有说出话」。这两件事在推理型模型上
    会分离：预算被 reasoning 吃光时 `content` 为空而 `finish_reason` 是
    `length`，恰恰证明请求送达、模型响应了。

    原判据只看 `content` 非空，实测把 sensenova 与 aliyun 在 1/32/256 token
    下全部误判成失败（`_scratch/probe_probe_verdict.py` 四档记录）。那种误判
    会被用户读成「这个模型不能用」，从而白白放弃一个本来能写正文的供应商。

    ``transport`` 可注入（httpx.MockTransport），测试零网络——这条判据本身
    就是被误判坑过的，必须有回归测试钉住，否则下次改回 `content` 判据没人拦。
    """
    started = time.perf_counter()
    try:
        with httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {config.api_key or 'ollama'}"},
            # 同 openai_compat：trust_env=True 会读系统代理/证书设置，
            # 实测把 https 中转站拦在 TLS 握手（EOF occurred in violation of protocol），
            # 而 urllib 直连同地址是通的。连通性测试必须走和正文一样的传输路径，
            # 否则「测试通过、生成失败」这种最难查的分裂就会出现。
            trust_env=False,
            transport=transport,
        ) as client:
            # 不带 response_format，兼容更多本地/第三方端点
            response = client.post(
                f"{config.base_url}/chat/completions",
                json={
                    "model": config.model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": PROBE_MAX_TOKENS,
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
        choice = payload["choices"][0]
        content = str((choice.get("message") or {}).get("content") or "")
        finish = str(choice.get("finish_reason") or "")
        usage = payload.get("usage") or {}
        completion = usage.get("completion_tokens")
        # 判据：模型确实产出了 token（content 或 reasoning 任一），或者它
        # 正常停下来了。只要 completion_tokens > 0 就说明请求被处理过。
        produced = bool(content.strip()) or (isinstance(completion, int) and completion > 0)
        if not produced and finish not in ("stop", "length"):
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "detail": (
                    f"端点有响应但没有产出任何 token（finish_reason={finish or '缺失'}，"
                    f"completion_tokens={completion}）"
                ),
            }
        note = "" if content.strip() else "（本次正文为空，但模型确实响应了）"
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "detail": f"连接成功，模型 {config.model} 正常响应（{latency_ms}ms）{note}",
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
