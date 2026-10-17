from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from equitylens.engine.budget import BudgetConfig, BudgetGuard
from equitylens.engine.checkpoint import MemoryStore
from equitylens.engine.node import NodeContext, ToolNode
from equitylens.engine.services import Services
from equitylens.engine.state import StateSchema, append, append_unique, last, merge_map
from equitylens.engine.types import NodePolicy, OnFailure, Patch


@pytest.fixture
def schema() -> StateSchema:
    s = StateSchema()
    s.channel("input", last, "")
    s.channel("items", append, [])
    s.channel("unique", append_unique("id"), [])
    s.channel("meta", merge_map, {})
    s.channel("result", last, {})
    s.channel("errors", append, [])
    return s


def make_services(
    schema: StateSchema,
    *,
    structured: Callable[..., Awaitable[Any]] | None = None,
    tools: Callable[..., Awaitable[Any]] | None = None,
    budget: BudgetGuard | None = None,
) -> tuple[Services, list[tuple[str, str | None, dict[str, Any]]]]:
    emitted: list[tuple[str, str | None, dict[str, Any]]] = []

    async def _emit(event_type: str, *, node_id: str | None = None, **payload: Any) -> None:
        emitted.append((event_type, node_id, payload))

    async def _structured(**kwargs: Any) -> tuple[Any, dict[str, Any]]:
        if structured is None:
            raise AssertionError("本测试不需要 LLM 调用")
        return await structured(**kwargs)

    async def _tools(name: str, **kwargs: Any) -> Any:
        if tools is None:
            raise AssertionError(f"本测试不需要工具调用: {name}")
        return await tools(name, **kwargs)

    services = Services(
        schema=schema,
        complete_structured=_structured,
        invoke_tool=_tools,
        emit=_emit,
        budget=budget or BudgetGuard(BudgetConfig()),
    )
    return services, emitted


class FuncNode(ToolNode):
    """把一段 async 函数包装成节点，测试里最省事的写法。"""

    def __init__(
        self,
        node_id: str,
        fn: Callable[[NodeContext], Awaitable[Patch]],
        *,
        deps: tuple[str, ...] = (),
        reads: tuple[str, ...] = (),
        writes: tuple[str, ...] = ("result",),
        policy: NodePolicy | None = None,
    ) -> None:
        super().__init__(
            node_id,
            deps=deps,
            reads=reads,
            writes=writes,
            policy=policy or NodePolicy(timeout_s=5, max_attempts=1),
            label=node_id,
        )
        self._fn = fn

    async def invoke(self, ctx: NodeContext) -> Patch:
        return await self._fn(ctx)


class SleepNode(FuncNode):
    def __init__(self, node_id: str, delay: float, *, deps: tuple[str, ...] = ()) -> None:
        async def _sleep(_ctx: NodeContext) -> Patch:
            import asyncio

            await asyncio.sleep(delay)
            return Patch(channels={"result": node_id})

        super().__init__(node_id, _sleep, deps=deps)


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


def slow_policy(on_failure: OnFailure = OnFailure.ABORT) -> NodePolicy:
    return NodePolicy(timeout_s=5, max_attempts=1, on_failure=on_failure)
