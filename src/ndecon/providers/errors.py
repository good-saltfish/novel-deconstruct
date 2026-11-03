"""Provider 错误分类：让调用方能够区分"可重试/配置错/数据不合格"。"""

from __future__ import annotations


class ProviderError(RuntimeError):
    """所有 Provider 错误的基类。"""


class ProviderConfigError(ProviderError):
    """配置缺失或非法（如缺少 API key、base_url 格式错误）；不可重试。"""


class ProviderHTTPError(ProviderError):
    """HTTP 层错误，携带状态码与是否可重试标记。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        """记录状态码与可重试性，供重试策略与 CLI 文案使用。"""
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class ProviderResponseError(ProviderError):
    """上游返回内容无法解析为约定 JSON（空响应、截断、非 JSON）。"""


class ProviderTransportError(ProviderError):
    """传输层失败：读超时、连接被重置、DNS/网络不可达。

    单独一类而不是塞进 ProviderHTTPError：它没有 HTTP 状态码，但同样可能是
    瞬时的（重试一次往往就好），所以带 retryable 标记。

    为什么必须归类：以前 `httpx.ReadTimeout` 直接裸抛，逃出 HTTP 请求处理器
    （`_run_chapter_txn` 只 catch `ProviderError`），连接被断开而没写出任何
    响应。插件侧只能看到 `fetch failed`，于是界面上显示的是
    「无法连接引擎（…）：fetch failed」——用户以为引擎挂了，
    真实原因却是模型那一边超时。错误必须留在自己的层里。
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        """记录可重试性；默认可重试（网络抖动是常态）。"""
        super().__init__(message)
        self.retryable = retryable


class ProviderSchemaError(ProviderError):
    """JSON 可解析但不符合受控 schema（非法枚举、缺字段、证据缺失）。

    这类错误说明 prompt/模型输出不合规，直接失败优于静默写入脏数据。
    """
