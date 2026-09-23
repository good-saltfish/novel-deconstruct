"""带退避的可重试调用（不引入 tenacity，零额外依赖且便于测试）。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from ndecon.providers.errors import ProviderHTTPError

T = TypeVar("T")


def call_with_retry(
    func: Callable[[], T],
    *,
    max_attempts: int = 4,
    base_delay: float = 0.5,
    sleeper: Callable[[float], None] = time.sleep,
) -> T:
    """对抛 ProviderHTTPError(retryable=True) 的调用做指数退避重试。

    - 429 与 5xx 视为可重试；4xx 配置类错误立即抛出；
    - 第 n 次重试等待 base_delay * 2**(n-1)；sleeper 可注入以便测试零等待；
    - 重试用尽后抛出最后一次错误。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 必须 >= 1")
    last_error: ProviderHTTPError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except ProviderHTTPError as exc:
            last_error = exc
            if not exc.retryable or attempt == max_attempts:
                raise
            sleeper(base_delay * 2 ** (attempt - 1))
    assert last_error is not None  # 逻辑上不可达
    raise last_error
