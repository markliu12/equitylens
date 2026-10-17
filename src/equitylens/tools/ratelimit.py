"""按域名的令牌桶限流。

数据源（akshare / 巨潮 / 东财）都是公开接口，没有 SLA。并发打过去的结果不是"更快"，
而是"被封"。所以这里默认 1 QPS，宁可慢一点。
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate_per_second: float = 1.0, capacity: float = 2.0) -> None:
        self.rate = max(rate_per_second, 0.01)
        self.capacity = max(capacity, 1.0)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                await asyncio.sleep((tokens - self._tokens) / self.rate)


class NullLimiter:
    async def acquire(self, tokens: float = 1.0) -> None:
        return None
