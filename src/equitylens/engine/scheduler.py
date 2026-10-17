"""调度器：依赖计数 + 就绪队列 + 分层信号量。

为什么不是"拓扑排序后逐层执行"：
分层执行会把同一层的节点攒齐才放行，最慢的那个决定整层节奏，并行度被浪费。
依赖计数允许跨层流水线：A 完成后它的下游立刻可跑，不必等同一层的 B。

失败传播有三种，语义各不相同：
- ABORT   关键路径失败，整个 run 失败（例如实体解析不出来，后面全是垃圾）
- SKIP    该节点放弃，下游用已有状态继续，最终 run 标记 PARTIAL
- DEGRADE 使用节点自带的降级产物（例如"数据不足"的合法对象），下游照常
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from equitylens.engine.checkpoint import NodeRecord, Snapshot
from equitylens.engine.events import EventType
from equitylens.engine.graph import Graph
from equitylens.engine.node import Node, NodeContext
from equitylens.engine.services import Services
from equitylens.engine.state import Blackboard
from equitylens.engine.types import (
    SATISFIED_STATUSES,
    TERMINAL_STATUSES,
    FailureKind,
    NodeResult,
    NodeStatus,
    OnFailure,
    Patch,
    RunStatus,
    Usage,
)
from equitylens.errors import EquityLensError, NodeError


class RunAborted(EquityLensError):
    def __init__(self, node_id: str, message: str) -> None:
        super().__init__(f"节点 {node_id} 失败导致运行中止: {message}")
        self.node_id = node_id


@dataclass
class RunOutcome:
    run_id: str
    status: RunStatus
    state: dict[str, Any]
    node_statuses: dict[str, str] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    error: str | None = None


class Scheduler:
    def __init__(
        self,
        graph: Graph,
        services: Services,
        store: Any = None,
        *,
        max_parallel: int = 8,
    ) -> None:
        self.graph = graph
        self.services = services
        self.store = store
        self.max_parallel = max_parallel
        self._snapshot_seq = 0

    # ------------------------------------------------------------------ #
    async def run(
        self,
        run_id: str,
        *,
        resume: bool = False,
        initial_state: dict[str, Any] | None = None,
    ) -> RunOutcome:
        bb, statuses, remaining, ready = await self._prepare(run_id, resume, initial_state)
        running: dict[asyncio.Task[NodeResult], str] = {}
        failures: dict[str, str] = {}
        partial = False

        try:
            while ready or running:
                while ready and len(running) < self.max_parallel:
                    node_id = ready.popleft()
                    if statuses.get(node_id) in TERMINAL_STATUSES:
                        continue
                    node = self.graph.nodes[node_id]
                    statuses[node_id] = NodeStatus.RUNNING
                    await self.services.emit(EventType.NODE_STARTED, node_id=node_id, label=node.label)
                    task = asyncio.create_task(self._execute(node, bb, run_id))
                    running[task] = node_id

                if not running:
                    break

                done, _ = await asyncio.wait(
                    running.keys(), return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    node_id = running.pop(task)
                    node = self.graph.nodes[node_id]
                    result = task.result()
                    statuses[node_id] = result.status

                    if result.patch.channels:
                        bb.apply(result.patch, node_id=node_id, writes=node.writes)

                    if result.status is NodeStatus.SUCCEEDED:
                        partial = partial or result.degraded
                        await self.services.emit(
                            EventType.NODE_COMPLETED,
                            node_id=node_id,
                            label=node.label,
                            attempts=result.attempts,
                            degraded=result.degraded,
                            usage=result.usage.to_dict(),
                            meta=dict(result.patch.meta),
                        )
                    elif result.status is NodeStatus.SKIPPED:
                        partial = True
                        failures[node_id] = result.error or "skipped"
                        await self.services.emit(
                            EventType.NODE_SKIPPED, node_id=node_id, reason=result.error
                        )
                    else:
                        failures[node_id] = result.error or "failed"
                        await self.services.emit(
                            EventType.NODE_FAILED,
                            node_id=node_id,
                            kind=str(result.error_kind),
                            error=result.error,
                            attempts=result.attempts,
                        )

                    await self._persist(run_id, bb, statuses, result)

                    if result.status is NodeStatus.FAILED and node.policy.on_failure is OnFailure.ABORT:
                        raise RunAborted(node_id, result.error or "unknown")

                    for downstream in self.graph.dependents.get(node_id, ()):
                        if statuses.get(downstream) in TERMINAL_STATUSES:
                            continue
                        remaining[downstream] = max(0, remaining[downstream] - 1)
                        if remaining[downstream] == 0:
                            ready.append(downstream)

            status = RunStatus.PARTIAL if (partial or failures) else RunStatus.COMPLETED
            return RunOutcome(
                run_id=run_id,
                status=status,
                state=bb.snapshot(),
                node_statuses={k: str(v) for k, v in statuses.items()},
                usage=self._usage_snapshot(),
                failures=failures,
            )
        except asyncio.CancelledError:
            for task in running:
                task.cancel()
            if running:
                await asyncio.gather(*running.keys(), return_exceptions=True)
            for node_id, status in statuses.items():
                if status is NodeStatus.RUNNING:
                    statuses[node_id] = NodeStatus.CANCELLED
            await self._persist(run_id, bb, statuses, None)
            raise
        except RunAborted as exc:
            for task in running:
                task.cancel()
            if running:
                await asyncio.gather(*running.keys(), return_exceptions=True)
            return RunOutcome(
                run_id=run_id,
                status=RunStatus.FAILED,
                state=bb.snapshot(),
                node_statuses={k: str(v) for k, v in statuses.items()},
                usage=self._usage_snapshot(),
                failures={**failures, exc.node_id: str(exc)},
                error=str(exc),
            )

    # ------------------------------------------------------------------ #
    async def _prepare(
        self,
        run_id: str,
        resume: bool,
        initial_state: dict[str, Any] | None = None,
    ) -> tuple[Blackboard, dict[str, NodeStatus], dict[str, int], deque[str]]:
        snapshot: Snapshot | None = None
        if resume and self.store is not None:
            snapshot = await self.store.load_latest(run_id)

        if snapshot is not None:
            bb = Blackboard(self.graph.schema, snapshot.state)
            statuses = {
                node_id: NodeStatus(value)
                for node_id, value in snapshot.statuses.items()
                if node_id in self.graph.nodes
            }
            self._snapshot_seq = snapshot.seq
        else:
            bb = Blackboard(self.graph.schema, initial_state or {})
            statuses = {node_id: NodeStatus.PENDING for node_id in self.graph.nodes}

        for node_id in self.graph.nodes:
            statuses.setdefault(node_id, NodeStatus.PENDING)

        # 恢复时把失败/取消的节点及其下游重置为 PENDING，否则它们会永远卡在终态：
        # 既不会被调度，下游又因为"依赖未满足"永远等不到，整个 run 静默停摆。
        reset = {
            node_id
            for node_id, status in statuses.items()
            if status in {NodeStatus.FAILED, NodeStatus.CANCELLED}
        }
        changed = True
        while changed:
            changed = False
            for node_id, node in self.graph.nodes.items():
                if node_id in reset:
                    continue
                if any(dep in reset for dep in node.deps):
                    reset.add(node_id)
                    changed = True
        for node_id in reset:
            statuses[node_id] = NodeStatus.PENDING

        remaining: dict[str, int] = {}
        for node_id, node in self.graph.nodes.items():
            if statuses[node_id] in SATISFIED_STATUSES:
                remaining[node_id] = 0
                continue
            remaining[node_id] = sum(
                1 for dep in node.deps if statuses.get(dep) not in SATISFIED_STATUSES
            )

        ready: deque[str] = deque(
            node_id
            for node_id in self.graph.order
            if statuses[node_id] not in TERMINAL_STATUSES and remaining[node_id] == 0
        )
        return bb, statuses, remaining, ready

    async def _execute(self, node: Node, bb: Blackboard, run_id: str) -> NodeResult:
        started = time.time()
        last_error: NodeError | None = None
        attempt = 0
        view = bb.view(node.reads) if node.reads else bb.view()

        while attempt < max(1, node.policy.max_attempts):
            attempt += 1
            ctx = NodeContext(
                run_id=run_id,
                node_id=node.id,
                attempt=attempt,
                view=view,
                services=self.services,
            )
            try:
                async with asyncio.timeout(node.policy.timeout_s):
                    patch = await node.run(ctx)
                usage = _usage_from_meta(patch.meta)
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.SUCCEEDED,
                    patch=patch,
                    usage=usage,
                    attempts=attempt,
                    degraded=bool(patch.meta.get("degraded")),
                    started_at=started, finished_at=time.time(),
                )
            except TimeoutError:
                last_error = NodeError(
                    FailureKind.TIMEOUT, f"节点 {node.id} 超时（{node.policy.timeout_s}s）"
                )
            except NodeError as exc:
                last_error = exc
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = NodeError(FailureKind.UNKNOWN, f"{type(exc).__name__}: {exc}")
                break

            can_retry = (
                last_error.retryable
                and attempt < node.policy.max_attempts
                and last_error.kind in node.policy.retryable
            )
            if not can_retry:
                break
            delay = min(0.4 * 2 ** (attempt - 1), 4.0)
            await self.services.emit(
                EventType.NODE_RETRIED,
                node_id=node.id,
                attempt=attempt,
                kind=str(last_error.kind),
                delay=delay,
            )
            await asyncio.sleep(delay)

        assert last_error is not None
        usage = Usage()
        if node.policy.on_failure is OnFailure.DEGRADE and last_error.fallback is not None:
            payload = last_error.fallback
            patch = payload if isinstance(payload, Patch) else Patch(channels=dict(payload))
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.SUCCEEDED,
                patch=patch,
                usage=usage,
                attempts=attempt,
                error_kind=last_error.kind,
                error=str(last_error),
                degraded=True,
                started_at=started, finished_at=time.time(),
            )
        if node.policy.on_failure is OnFailure.SKIP:
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.SKIPPED,
                patch=Patch.empty(),
                usage=usage,
                attempts=attempt,
                error_kind=last_error.kind,
                error=str(last_error),
                started_at=started, finished_at=time.time(),
            )
        return NodeResult(
            node_id=node.id,
            status=NodeStatus.FAILED,
            patch=Patch.empty(),
            usage=usage,
            attempts=attempt,
            error_kind=last_error.kind,
            error=str(last_error),
            started_at=started, finished_at=time.time(),
        )

    async def _persist(
        self,
        run_id: str,
        bb: Blackboard,
        statuses: dict[str, NodeStatus],
        result: NodeResult | None,
    ) -> None:
        if self.store is None:
            return
        if result is not None:
            await self.store.save_node(
                NodeRecord(
                    run_id=run_id,
                    node_id=result.node_id,
                    status=result.status,
                    attempts=result.attempts,
                    usage=result.usage.to_dict(),
                    error_kind=str(result.error_kind) if result.error_kind else None,
                    error=result.error,
                    degraded=result.degraded,
                    started_at=result.started_at,
                    finished_at=result.finished_at or time.time(),
                    patch_keys=tuple(sorted(result.patch.channels)),
                )
            )
        self._snapshot_seq += 1
        await self.store.save_snapshot(
            Snapshot(
                run_id=run_id,
                seq=self._snapshot_seq,
                state=bb.snapshot(),
                statuses={k: str(v) for k, v in statuses.items()},
            )
        )

    def _usage_snapshot(self) -> dict[str, Any]:
        budget = self.services.budget
        return budget.snapshot() if budget is not None else {}


def _usage_from_meta(meta: dict[str, Any]) -> Usage:
    raw = meta.get("usage") or {}
    if not isinstance(raw, dict):
        return Usage()
    return Usage(
        prompt_tokens=int(raw.get("prompt_tokens") or 0),
        completion_tokens=int(raw.get("completion_tokens") or 0),
        cost_cny=float(raw.get("cost_cny") or 0.0),
        calls=int(raw.get("calls") or 0),
        model=str(raw.get("model") or ""),
    )
