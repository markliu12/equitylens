from __future__ import annotations

import time

from equitylens.engine.checkpoint import MemoryStore
from equitylens.engine.graph import GraphBuilder
from equitylens.engine.node import FanOut, NodeContext
from equitylens.engine.scheduler import Scheduler
from equitylens.engine.state import StateSchema
from equitylens.engine.types import FailureKind, NodePolicy, OnFailure, Patch, RunStatus
from equitylens.errors import NodeError
from tests.conftest import FuncNode, SleepNode, make_services, slow_policy


def test_diamond_dag_runs_all_nodes(schema: StateSchema, store: MemoryStore) -> None:
    async def _emit(_ctx: NodeContext) -> Patch:
        return Patch(channels={"items": [1]})

    builder = GraphBuilder(schema)
    builder.add(
        FuncNode("start", _emit, writes=("items",), policy=slow_policy()),
        FuncNode("left", _emit, deps=("start",), writes=("items",), policy=slow_policy()),
        FuncNode("right", _emit, deps=("start",), writes=("items",), policy=slow_policy()),
        FuncNode("join", _emit, deps=("left", "right"), writes=("items",), policy=slow_policy()),
    )
    graph = builder.build()
    services, _ = make_services(schema)

    import asyncio

    outcome = asyncio.run(Scheduler(graph, services, store).run("run_1"))
    assert outcome.status is RunStatus.COMPLETED
    assert set(outcome.node_statuses.values()) == {"SUCCEEDED"}
    assert len(outcome.state["items"]) == 4


def test_fanout_is_actually_parallel(schema: StateSchema, store: MemoryStore) -> None:
    """4 个 0.15s 的子任务，串行需要 0.6s；并行必须显著更快。"""
    builder = GraphBuilder(schema)
    builder.add(
        SleepNode("pre", 0.0),
        FanOut(
            "fan",
            items=lambda _view: [0, 1, 2, 3],
            worker_factory=lambda item, index: SleepNode(f"fan[{index}]", 0.15),
            deps=("pre",),
            reads=("input",),
            writes=("result",),
            policy=NodePolicy(max_parallel=4, on_failure=OnFailure.SKIP),
        ),
    )
    graph = builder.build()
    services, _ = make_services(schema)

    import asyncio

    started = time.perf_counter()
    outcome = asyncio.run(Scheduler(graph, services, store).run("run_fan"))
    elapsed = time.perf_counter() - started

    assert outcome.status is RunStatus.COMPLETED
    assert elapsed < 0.6 * 0.6, f"并行度不足，耗时 {elapsed:.2f}s"


def test_abort_policy_stops_run(schema: StateSchema, store: MemoryStore) -> None:
    async def _boom(_ctx: NodeContext) -> Patch:
        raise NodeError(FailureKind.UNKNOWN, "节点炸了")

    async def _ok(_ctx: NodeContext) -> Patch:
        return Patch(channels={"result": "ok"})

    builder = GraphBuilder(schema)
    builder.add(
        FuncNode("a", _ok, writes=("result",), policy=slow_policy()),
        FuncNode("b", _boom, deps=("a",), writes=("result",), policy=slow_policy(OnFailure.ABORT)),
        FuncNode("c", _ok, deps=("b",), writes=("result",), policy=slow_policy()),
    )
    services, _ = make_services(schema)

    import asyncio

    outcome = asyncio.run(Scheduler(builder.build(), services, store).run("run_abort"))
    assert outcome.status is RunStatus.FAILED
    assert outcome.node_statuses["b"] == "FAILED"
    assert outcome.node_statuses["c"] == "PENDING"
    assert "b" in outcome.failures


def test_skip_policy_lets_downstream_continue(schema: StateSchema, store: MemoryStore) -> None:
    async def _boom(_ctx: NodeContext) -> Patch:
        raise NodeError(FailureKind.PROVIDER_DOWN, "数据源不可用")

    async def _ok(_ctx: NodeContext) -> Patch:
        return Patch(channels={"result": "ok"})

    builder = GraphBuilder(schema)
    builder.add(
        FuncNode("a", _boom, writes=("result",), policy=slow_policy(OnFailure.SKIP)),
        FuncNode("b", _ok, deps=("a",), writes=("result",), policy=slow_policy()),
    )
    services, _ = make_services(schema)

    import asyncio

    outcome = asyncio.run(Scheduler(builder.build(), services, store).run("run_skip"))
    assert outcome.status is RunStatus.PARTIAL
    assert outcome.node_statuses["a"] == "SKIPPED"
    assert outcome.node_statuses["b"] == "SUCCEEDED"
    assert outcome.state["result"] == "ok"


def test_degrade_policy_uses_fallback(schema: StateSchema, store: MemoryStore) -> None:
    async def _boom(_ctx: NodeContext) -> Patch:
        error = NodeError(FailureKind.SCHEMA, "模型输出不合法")
        error.fallback = {"result": {"degraded": True}}
        raise error

    async def _ok(_ctx: NodeContext) -> Patch:
        return Patch(channels={"result": "downstream-ok"})

    builder = GraphBuilder(schema)
    builder.add(
        FuncNode("a", _boom, writes=("result",), policy=slow_policy(OnFailure.DEGRADE)),
        FuncNode("b", _ok, deps=("a",), writes=("result",), policy=slow_policy()),
    )
    services, _ = make_services(schema)

    import asyncio

    outcome = asyncio.run(Scheduler(builder.build(), services, store).run("run_degrade"))
    assert outcome.status is RunStatus.PARTIAL
    assert outcome.node_statuses["a"] == "SUCCEEDED"
    assert outcome.state["result"] == "downstream-ok"


def test_retry_succeeds_on_second_attempt(schema: StateSchema, store: MemoryStore) -> None:
    calls = {"n": 0}

    async def _flaky(_ctx: NodeContext) -> Patch:
        calls["n"] += 1
        if calls["n"] == 1:
            raise NodeError(FailureKind.TRANSIENT, "网络抖动")
        return Patch(channels={"result": "ok"})

    builder = GraphBuilder(schema)
    builder.add(
        FuncNode(
            "a",
            _flaky,
            writes=("result",),
            policy=NodePolicy(timeout_s=5, max_attempts=3, retryable=frozenset({FailureKind.TRANSIENT})),
        )
    )
    services, emitted = make_services(schema)

    import asyncio

    outcome = asyncio.run(Scheduler(builder.build(), services, store).run("run_retry"))
    assert outcome.status is RunStatus.COMPLETED
    assert calls["n"] == 2
    assert any(event[0] == "node.retried" for event in emitted)


def test_retry_stops_on_non_retryable_failure(schema: StateSchema, store: MemoryStore) -> None:
    calls = {"n": 0}

    async def _policy_error(_ctx: NodeContext) -> Patch:
        calls["n"] += 1
        raise NodeError(FailureKind.POLICY, "合规拦截")

    builder = GraphBuilder(schema)
    builder.add(
        FuncNode(
            "a",
            _policy_error,
            writes=("result",),
            policy=NodePolicy(timeout_s=5, max_attempts=3, on_failure=OnFailure.SKIP),
        )
    )
    services, _ = make_services(schema)

    import asyncio

    outcome = asyncio.run(Scheduler(builder.build(), services, store).run("run_policy"))
    assert outcome.status is RunStatus.PARTIAL
    assert calls["n"] == 1
