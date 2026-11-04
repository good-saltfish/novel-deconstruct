"""带退避的可重试调用（不引入 tenacity，零额外依赖且便于测试）。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from ndecon.providers.errors import ProviderHTTPError, ProviderTransportError

T = TypeVar("T")


def call_with_retry(
    func: Callable[[], T],
    *,
    max_attempts: int = 4,
    base_delay: float = 0.5,
    sleeper: Callable[[float], None] = time.sleep,
) -> T:
    """对可重试的 Provider 错误做指数退避重试。

    - 429 与 5xx（ProviderHTTPError.retryable）与传输层失败（ProviderTransportError）重试；
    - 4xx 配置类错误立即抛出；
    - 第 n 次重试等待 base_delay * 2**(n-1)；sleeper 可注入以便测试零等待；
    - 重试用尽后抛出最后一次错误。

    读超时不重试：那是「模型确实慢」而不是「网络抖了一下」。默认超时 180 秒，
    重试 4 次意味着用户要干等 12 分钟才被告知失败——实测用户在第 3 次重试时
    就已经在问「为什么写了 0 章」。慢模型要换模型或改超时，不该靠重试救。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 必须 >= 1")
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except (ProviderHTTPError, ProviderTransportError) as exc:
            last_error = exc
            if not exc.retryable or attempt == max_attempts:
                raise
            sleeper(base_delay * 2 ** (attempt - 1))
    assert last_error is not None  # 逻辑上不可达
    raise last_error