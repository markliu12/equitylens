from __future__ import annotations

import asyncio

import pytest

from equitylens.engine.checkpoint import MemoryStore
from equitylens.engine.graph import GraphBuilder
from equitylens.engine.node import NodeContext
from equitylens.engine.scheduler import Scheduler
from equitylens.engine.state import StateSchema
from equitylens.engine.types import FailureKind, NodePolicy, Patch, RunStatus
from equitylens.errors import NodeError
from tests.conftest import FuncNode, make_services, slow_policy


def _build(schema: StateSchema, *, fail_on: str | None) -> GraphBuilder:
    calls: list[str] = []

    async def _ok(ctx: NodeContext) -> Patch:
        calls.append(ctx.node_id)
        return Patch(channels={"result": ctx.node_id, "items": [ctx.node_id]})

    async def _boom(ctx: NodeContext) -> Patch:
        calls.append(ctx.node_id)
        raise NodeError(FailureKind.TRANSIENT, "模拟崩溃")

    builder = GraphBuilder(schema)
    nodes = []
    for index in range(1, 5):
        node_id = f"n{index}"
        fn = _boom if node_id == fail_on else _ok
        policy = slow_policy() if node_id == fail_on else NodePolicy(timeout_s=5, max_attempts=1)
        nodes.append(
            FuncNode(
                node_id,
                fn,
                deps=(f"n{index - 1}",) if index > 1 else (),
                writes=("result", "items"),
                policy=policy,
            )
        )
    builder.add(*nodes)
    builder.calls = calls  # type: ignore[attr-defined]
    return builder


@pytest.mark.asyncio
async def test_resume_skips_finished_nodes(schema: StateSchema, store: MemoryStore) -> None:
    """崩在 n3，续跑时 n1/n2 不应再执行；最终状态要与一次跑到底完全一致。"""
    broken = _build(schema, fail_on="n3")
    services, _ = make_services(schema)
    first = await Scheduler(broken.build(), services, store).run(
        "run_resume", initial_state={"input": "q"}
    )
    assert first.status is RunStatus.FAILED
    assert first.node_statuses["n1"] == "SUCCEEDED"
    assert first.node_statuses["n2"] == "SUCCEEDED"

    healthy = _build(schema, fail_on=None)
    services2, _ = make_services(schema)
    resumed = await Scheduler(healthy.build(), services2, store).run("run_resume", resume=True)

    assert resumed.status is RunStatus.COMPLETED
    # 已完成的节点在恢复阶段没有被再次调用（不会重复扣费）
    assert healthy.calls == ["n3", "n4"]  # type: ignore[attr-defined]

    # 与"一次跑到底"的结果做对照
    healthy_fresh = _build(schema, fail_on=None)
    services3, _ = make_services(schema)
    fresh = await Scheduler(healthy_fresh.build(), services3, MemoryStore()).run(
        "run_fresh", initial_state={"input": "q"}
    )
    assert resumed.state["result"] == fresh.state["result"]
    assert resumed.state["items"] == fresh.state["items"]


@pytest.mark.asyncio
async def test_cancel_marks_run_cancelled(schema: StateSchema, store: MemoryStore) -> None:
    async def _slow(_ctx: NodeContext) -> Patch:
        await asyncio.sleep(5)
        return Patch(channels={"result": "never"})

    builder = GraphBuilder(schema)
    builder.add(FuncNode("slow", _slow, writes=("result",), policy=NodePolicy(timeout_s=10)))
    services, _ = make_services(schema)
    scheduler = Scheduler(builder.build(), services, store)

    task = asyncio.create_task(scheduler.run("run_cancel"))
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    snapshot = await store.load_latest("run_cancel")
    assert snapshot is not None
    assert snapshot.statuses["slow"] == "CANCELLED"


@pytest.mark.asyncio
async def test_checkpoint_saved_per_node(schema: StateSchema, store: MemoryStore) -> None:
    builder = _build(schema, fail_on=None)
    services, _ = make_services(schema)
    await Scheduler(builder.build(), services, store).run("run_ckpt")
    snapshots = store.snapshots["run_ckpt"]
    assert len(snapshots) == 4
    assert snapshots[-1].statuses["n4"] == "SUCCEEDED"
