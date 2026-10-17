"""引擎的基础值对象。

这里只放"没有行为、只有语义"的结构：状态枚举、失败分类、补丁、预算用量。
把它们与执行逻辑分开，是为了让 `state.py` / `scheduler.py` 不产生循环依赖，
同时让这些类型可以被 API 层直接序列化给前端。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NodeStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


#: 视作"依赖已满足"的终态。SKIPPED 也算满足，否则上游可选节点跳过会卡死下游。
SATISFIED_STATUSES: frozenset[NodeStatus] = frozenset(
    {NodeStatus.SUCCEEDED, NodeStatus.SKIPPED}
)
TERMINAL_STATUSES: frozenset[NodeStatus] = frozenset(
    {
        NodeStatus.SUCCEEDED,
        NodeStatus.FAILED,
        NodeStatus.SKIPPED,
        NodeStatus.CANCELLED,
    }
)


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"  # 有节点降级/失败，但报告仍可交付
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FailureKind(StrEnum):
    TRANSIENT = "TRANSIENT"  # 网络抖动，可重试
    RATE_LIMIT = "RATE_LIMIT"  # 限流，可退避重试
    TIMEOUT = "TIMEOUT"  # 超时，可重试
    SCHEMA = "SCHEMA"  # 结构化输出不合法，可带错误信息重试
    PROVIDER_DOWN = "PROVIDER_DOWN"  # 数据源不可用，应换源
    BUDGET = "BUDGET"  # 预算耗尽，不可重试
    POLICY = "POLICY"  # 合规拦截，不可重试
    UNKNOWN = "UNKNOWN"


class OnFailure(StrEnum):
    """节点失败后的传播策略。"""

    ABORT = "abort"  # 整个 run 失败（用于关键路径节点）
    SKIP = "skip"  # 该节点及下游标记 SKIPPED，run 降级为 PARTIAL
    DEGRADE = "degrade"  # 使用降级产物，下游继续，run 标记 PARTIAL


@dataclass(frozen=True, slots=True)
class Usage:
    """一次 LLM 调用的计量，用于预算与成本报表。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: float = 0.0
    calls: int = 0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cost_cny=round(self.cost_cny + other.cost_cny, 6),
            calls=self.calls + other.calls,
            model=self.model or other.model,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_cny": self.cost_cny,
            "calls": self.calls,
            "model": self.model,
        }


@dataclass(frozen=True, slots=True)
class Patch:
    """节点对状态的唯一写入方式。

    channels 里只能出现节点 `writes` 声明过的通道，由 Blackboard 强制校验。
    """

    channels: Mapping[str, Any] = field(default_factory=dict)
    meta: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def empty() -> Patch:
        return Patch(channels={}, meta={})


@dataclass(frozen=True, slots=True)
class NodePolicy:
    timeout_s: float = 120.0
    max_attempts: int = 3
    on_failure: OnFailure = OnFailure.ABORT
    concurrency: str = "tool"  # llm | tool | domain，决定用哪把并发信号量
    max_parallel: int = 4  # FanOut/LoopNode 内部并发上限
    retryable: frozenset[FailureKind] = frozenset(
        {
            FailureKind.TRANSIENT,
            FailureKind.RATE_LIMIT,
            FailureKind.TIMEOUT,
            FailureKind.SCHEMA,
        }
    )


@dataclass(frozen=True, slots=True)
class NodeResult:
    """节点执行结果（内部使用，便于 scheduler 统一处理）。"""

    node_id: str
    status: NodeStatus
    patch: Patch
    usage: Usage = field(default_factory=Usage)
    attempts: int = 1
    error_kind: FailureKind | None = None
    error: str | None = None
    degraded: bool = False
    started_at: float = 0.0
    finished_at: float = 0.0
