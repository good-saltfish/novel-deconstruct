"""预置 OpenAI 兼容供应商目录（国内常用 + 本地 + 自定义）。

模型 ID 与 base_url 为 2026-09 核验的公开信息；用户可在面板上改成任何模型名，
目录只负责给默认值与可选项，不强制（各厂商会持续上新模型）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderPreset:
    """一个预置供应商的连接信息。"""

    id: str
    name: str
    base_url: str
    models: tuple[str, ...]
    default_model: str
    # True 表示本地/免密服务（如 Ollama），不强制要求填 key
    key_optional: bool = False
    key_hint: str = ""
    docs_url: str = ""


PROVIDERS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        id="deepseek",
        name="DeepSeek（深度求索）",
        base_url="https://api.deepseek.com/v1",
        models=("deepseek-chat", "deepseek-reasoner"),
        default_model="deepseek-chat",
        key_hint="在 platform.deepseek.com 控制台创建 API key",
        docs_url="https://api-docs.deepseek.com/",
    ),
    ProviderPreset(
        id="zhipu",
        name="智谱 GLM（BigModel）",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        models=("glm-4-flash", "glm-4-air", "glm-4-plus", "glm-4.5"),
        default_model="glm-4-flash",
        key_hint="glm-4-flash 为免费模型；在 bigmodel.cn 控制台创建 key",
        docs_url="https://open.bigmodel.cn/dev/api",
    ),
    ProviderPreset(
        id="siliconflow",
        name="硅基流动 SiliconFlow",
        models=(
            "Qwen/Qwen2.5-7B-Instruct",
            "Qwen/Qwen2.5-14B-Instruct",
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
        ),
        base_url="https://api.siliconflow.cn/v1",
        default_model="Qwen/Qwen2.5-7B-Instruct",
        key_hint="小尺寸 Qwen 模型有免费额度；在 cloud.siliconflow.cn 创建 key",
        docs_url="https://docs.siliconflow.cn/",
    ),
    ProviderPreset(
        id="moonshot",
        name="月之暗面 Kimi（Moonshot）",
        base_url="https://api.moonshot.cn/v1",
        models=("moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k", "kimi-k2-0905-preview"),
        default_model="moonshot-v1-8k",
        key_hint="在 platform.moonshot.cn 控制台创建 key",
        docs_url="https://platform.moonshot.cn/docs",
    ),
    ProviderPreset(
        id="dashscope",
        name="阿里通义千问（DashScope 兼容模式）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=("qwen-turbo", "qwen-plus", "qwen-max", "qwen-long"),
        default_model="qwen-turbo",
        key_hint="在阿里云 DashScope 控制台创建 API-KEY",
        docs_url="https://help.aliyun.com/zh/model-studio/developer-reference/compatibility-of-openai-with-dashscope",
    ),
    ProviderPreset(
        id="ollama",
        name="本地 Ollama（免费离线）",
        base_url="http://127.0.0.1:11434/v1",
        models=("qwen2.5:7b", "qwen2.5:14b", "qwen2.5:7b-instruct", "deepseek-r1:7b"),
        default_model="qwen2.5:7b",
        key_optional=True,
        key_hint="本地服务无需真实 key；需先 ollama pull 模型",
        docs_url="https://ollama.com/",
    ),
    ProviderPreset(
        id="openai",
        name="OpenAI（官方）",
        base_url="https://api.openai.com/v1",
        models=("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"),
        default_model="gpt-4o-mini",
        key_hint="在 platform.openai.com/api-keys 创建 key（需可访问官方端点）",
        docs_url="https://platform.openai.com/docs",
    ),
    ProviderPreset(
        id="custom",
        name="自定义（任何 OpenAI 兼容端点/中转站）",
        base_url="",
        models=(),
        default_model="",
        key_hint="自行填写 base_url、模型名与 key",
        docs_url="",
    ),
)

_PROVIDER_INDEX = {preset.id: preset for preset in PROVIDERS}


def get_preset(provider_id: str) -> ProviderPreset | None:
    """按 id 取供应商预置；未知 id 返回 None。"""
    return _PROVIDER_INDEX.get(provider_id)


def catalog_payload() -> list[dict]:
    """返回供前端渲染的目录字典（不含任何凭据）。"""
    return [
        {
            "id": preset.id,
            "name": preset.name,
            "base_url": preset.base_url,
            "models": list(preset.models),
            "default_model": preset.default_model,
            "key_optional": preset.key_optional,
            "key_hint": preset.key_hint,
            "docs_url": preset.docs_url,
        }
        for preset in PROVIDERS
    ]
