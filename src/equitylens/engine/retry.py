"""重试与熔断。

失败分类比"重试三次"重要：schema 校验失败要带着错误信息重试（模型能被提示纠正），
限流要退避，而预算耗尽、合规拦截重试一万次也不会好——重试错对象比不重试更糟。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from equitylens.engine.types import FailureKind
from equitylens.errors import NodeError

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.6
    max_delay: float = 8.0
    jitter: float = 0.3
    retry_on: frozenset[FailureKind] = frozenset(
        {
            FailureKind.TRANSIENT,
            FailureKind.RATE_LIMIT,
            FailureKind.TIMEOUT,
            FailureKind.SCHEMA,
        }
    )

    def delay_for(self, attempt: int) -> float:
        raw = min(self.base_delay * (2 ** max(0, attempt - 1)), self.max_delay)
        return raw + random.uniform(0, raw * self.jitter)


async def run_with_retry[T](
    fn: Callable[[int], Awaitable[T]],
    policy: RetryPolicy,
    *,
    on_retry: Callable[[int, NodeError, float], Any] | None = None,
) -> tuple[T, int]:
    """返回 (结果, 实际尝试次数)。只对可重试的 FailureKind 重试。"""
    last_error: NodeError | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await fn(attempt), attempt
        except NodeError as exc:
            last_error = exc
            retryable = exc.kind in policy.retry_on
            if not retryable or attempt >= policy.max_attempts:
                raise
            delay = policy.delay_for(attempt)
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            await asyncio.sleep(delay)
    raise last_error or RuntimeError("unreachable")  # pragma: no cover


@dataclass
class CircuitBreaker:
    """滑动窗口失败率熔断。

    只按"最近 N 次"判断，是因为模型服务商故障通常是持续性的；用累计失败率会导致
    一次抖动后长时间打不开。
    """

    window: int = 10
    failure_threshold: float = 0.5
    min_samples: int = 4
    cooldown_s: float = 60.0
    _outcomes: deque[bool] = field(default_factory=deque)
    _opened_at: float = 0.0

    def __post_init__(self) -> None:
        self._outcomes = deque(maxlen=self.window)

    def allow(self) -> bool:
        if self._opened_at == 0:
            return True
        if time.monotonic() - self._opened_at >= self.cooldown_s:
            self._opened_at = 0.0
            self._outcomes.clear()
            return True
        return False

    def record(self, ok: bool) -> None:
        self._outcomes.append(ok)
        if len(self._outcomes) < self.min_samples:
            return
        failures = sum(1 for item in self._outcomes if not item)
        if failures / len(self._outcomes) >= self.failure_threshold:
            self._opened_at = time.monotonic()

    @property
    def is_open(self) -> bool:
        return not self.allow()

    @property
    def failure_rate(self) -> float:
        if not self._outcomes:
            return 0.0
        return sum(1 for item in self._outcomes if not item) / len(self._outcomes)
