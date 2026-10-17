"""GraphRunner：把 store / event bus / services / scheduler 串起来的薄编排层。

它只负责三件事：建 run、发 run 级事件、把调度结果落库。
真正的执行逻辑全在 Scheduler 里，这样两者都可以单独测试。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from equitylens.engine.budget import BudgetGuard
from equitylens.engine.checkpoint import CheckpointStore
from equitylens.engine.events import EventBus, EventType
from equitylens.engine.graph import Graph
from equitylens.engine.scheduler import RunOutcome, Scheduler
from equitylens.engine.services import Services
from equitylens.engine.types import RunStatus

ServicesFactory = Callable[[str, BudgetGuard], Services]


@dataclass
class GraphRunner:
    graph: Graph
    store: CheckpointStore
    bus: EventBus
    services_factory: ServicesFactory
    budget_factory: Callable[[], BudgetGuard]
    max_parallel: int = 8
    graph_version: str = "v1"
    config_hash: str = ""

    async def run(
        self,
        run_id: str,
        *,
        question: str,
        as_of: str,
        resume: bool = False,
        config_hash: str | None = None,
    ) -> RunOutcome:
        budget = self.budget_factory()
        services = self.services_factory(run_id, budget)

        if not resume:
            await self.store.create_run(
                run_id,
                question=question,
                as_of=as_of,
                graph_version=self.graph_version,
                config_hash=config_hash or self.config_hash,
            )
            await self.bus.publish(
                run_id,
                EventType.RUN_QUEUED,
                question=question,
                as_of=as_of,
                graph_version=self.graph_version,
                nodes=[n.label for n in self.graph.nodes.values()],
            )

        await self.store.mark_run(run_id, RunStatus.RUNNING)
        await self.bus.publish(
            run_id,
            EventType.RUN_RESUMED if resume else EventType.RUN_STARTED,
            as_of=as_of,
            question=question,
            budget=budget.config.to_dict(),
        )

        started = time.time()
        scheduler = Scheduler(self.graph, services, self.store, max_parallel=self.max_parallel)
        try:
            outcome = await scheduler.run(
                run_id,
                resume=resume,
                initial_state={"question": question, "as_of": as_of},
            )
        except Exception as exc:
            await self.store.mark_run(
                run_id, RunStatus.FAILED, usage=budget.snapshot(), error=str(exc)
            )
            await self.bus.publish(run_id, EventType.RUN_FAILED, error=str(exc))
            raise

        outcome.usage = budget.snapshot()
        await self.store.mark_run(run_id, outcome.status, usage=outcome.usage, error=outcome.error)

        if outcome.status in {RunStatus.COMPLETED, RunStatus.PARTIAL}:
            await self.bus.publish(
                run_id,
                EventType.RUN_COMPLETED,
                status=str(outcome.status),
                elapsed_s=round(time.time() - started, 2),
                usage=outcome.usage,
                failures=outcome.failures,
            )
        else:
            await self.bus.publish(
                run_id,
                EventType.RUN_FAILED,
                status=str(outcome.status),
                error=outcome.error,
                failures=outcome.failures,
            )
        return outcome

    async def cancel(self, run_id: str) -> None:
        await self.store.mark_run(run_id, RunStatus.CANCELLED)
        await self.bus.publish(run_id, EventType.RUN_CANCELLED)

    async def recover(self, run_id: str, *, question: str = "", as_of: str = "") -> Any:
        """从最后一次快照续跑。已完成的节点不会重跑，因此不会重复产生 LLM 费用。"""
        record = await self.store.get_run(run_id)
        question = question or (record or {}).get("question", "")
        as_of = as_of or (record or {}).get("as_of", "")
        return await self.run(run_id, question=question, as_of=as_of, resume=True)
