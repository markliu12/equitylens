"""节点依赖的外部能力集合。

节点不应该直接 new 客户端、直接读全局配置。所有外部依赖通过 Services 注入，
好处：单测可以替换任何一项；循环依赖也被打断（engine 不 import llm/tools）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class StructuredCompleter(Protocol):
    async def __call__(
        self,
        *,
        role: str,
        messages: list[dict[str, str]],
        output_model: type[Any],
        node_id: str,
        temperature: float = 0.2,
    ) -> tuple[Any, dict[str, Any]]: ...


class ToolInvoker(Protocol):
    async def __call__(self, name: str, /, **kwargs: Any) -> Any: ...


class EventEmitter(Protocol):
    async def __call__(self, event_type: str, *, node_id: str | None = None, **payload: Any) -> Any: ...


@dataclass
class Services:
    """运行期服务容器。

    schema 放在这里是因为 LoopNode 需要在局部 overlay 上复用同一套 reducer，
    否则"循环体内部的状态合并"会和主状态产生两套语义。
    """

    schema: Any
    complete_structured: StructuredCompleter
    invoke_tool: ToolInvoker
    emit: EventEmitter
    budget: Any
    cache: Any = None
    settings: Any = None
    extras: dict[str, Any] | None = None
