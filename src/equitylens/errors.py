"""统一异常体系。

设计要点：异常必须携带"失败类型"（FailureKind），因为重试策略、熔断策略和
降级策略全都依赖失败类型做判断——把失败语义编码在异常里，比在上层用字符串
匹配错误消息可靠得多。
"""

from __future__ import annotations

from typing import Any

from equitylens.engine.types import FailureKind


class EquityLensError(Exception):
    """项目内所有可预期异常的基类。"""


class NodeError(EquityLensError):
    """节点执行失败。`kind` 决定是否重试、是否熔断。"""

    def __init__(
        self,
        kind: FailureKind,
        message: str,
        *,
        fallback: Any | None = None,
        provider: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.fallback = fallback
        self.provider = provider

    @property
    def retryable(self) -> bool:
        return self.kind in {
            FailureKind.TRANSIENT,
            FailureKind.RATE_LIMIT,
            FailureKind.TIMEOUT,
            FailureKind.SCHEMA,
        }


class BudgetExceeded(NodeError):
    def __init__(self, message: str) -> None:
        super().__init__(FailureKind.BUDGET, message)


class SchemaValidationError(NodeError):
    def __init__(self, message: str) -> None:
        super().__init__(FailureKind.SCHEMA, message)


class ProviderUnavailable(NodeError):
    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(FailureKind.PROVIDER_DOWN, message, provider=provider)


class GraphDefinitionError(EquityLensError):
    """图定义非法（环、未知依赖、写权限冲突等），属于编码错误，不可重试。"""


class PolicyViolation(NodeError):
    """合规/策略拦截。不可重试，必须人工或上游修正。"""

    def __init__(self, message: str) -> None:
        super().__init__(FailureKind.POLICY, message)
