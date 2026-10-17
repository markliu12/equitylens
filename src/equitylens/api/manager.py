"""运行管理器：任务注册、并发闸门、取消、启动恢复。

持久化在 SqliteStore（运行记录 / checkpoint / 事件全部落库，重启可恢复）；
内存里的 dict[run_id, Task] 只是 asyncio 任务句柄，句柄本身无法也不需持久化。
刻意没有引入 Celery/Redis：本项目的瓶颈是模型调用，不是调度吞吐；
引入消息队列后，checkpoint 语义、取消语义、事件顺序都要重做一遍，
收益却几乎为零。扩展路径写在 ADR 里。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from equitylens.engine.events import EventType
from equitylens.engine.types import RunStatus
from equitylens.runtime import AppRuntime


@dataclass
class RunManager:
    runtime: AppRuntime
    tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    _semaphore: asyncio.Semaphore | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _daily_reservations: dict[str, float] = field(default_factory=dict)

    @property
    def semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.runtime.settings.max_parallel_runs)
        return self._semaphore

    async def start(
        self,
        question: str,
        *,
        as_of: str = "",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        as_of = as_of or "2026-09-30"
        async with self._lock:
            if not await self._reserve_demo_budget(run_id):
                return {"error": "daily_budget_exhausted"}
            await self.runtime.store.create_run(
                run_id,
                question=question,
                as_of=as_of,
                graph_version=self.runtime.graph_version,
                config_hash=self.runtime.settings.config_hash(),
            )
            self.tasks[run_id] = asyncio.create_task(self._run(run_id, question, as_of))
        return {
            "run_id": run_id,
            "status": str(RunStatus.QUEUED),
            "stream_url": f"/api/runs/{run_id}/events",
        }

    async def _run(self, run_id: str, question: str, as_of: str) -> None:
        try:
            async with self.semaphore:
                await self.runtime.runner.run(run_id, question=question, as_of=as_of)
        except asyncio.CancelledError:
            # 取消是"正常终态"，必须落库，否则前端永远转圈
            await self.runtime.store.mark_run(run_id, RunStatus.CANCELLED)
            await self.runtime.bus.publish(run_id, EventType.RUN_CANCELLED)
            raise
        except Exception as exc:
            await self.runtime.bus.publish(run_id, EventType.RUN_FAILED, error=str(exc))
        finally:
            async with self._lock:
                self.tasks.pop(run_id, None)
                self._daily_reservations.pop(run_id, None)

    async def cancel(self, run_id: str) -> dict[str, Any]:
        task = self.tasks.get(run_id)
        record = await self.runtime.store.get_run(run_id)
        if record is None:
            return {"ok": False, "reason": "not_found"}
        if task is None or task.done():
            if record["status"] in {str(RunStatus.COMPLETED), str(RunStatus.PARTIAL)}:
                return {"ok": False, "reason": "already_finished"}
            return {"ok": False, "reason": "not_running"}
        task.cancel()
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=3)
        return {"ok": True, "run_id": run_id}

    async def recover_on_startup(self) -> list[str]:
        """进程重启后把处于 RUNNING/QUEUED 的 run 续跑，不重复消耗已完成节点。"""
        recovered: list[str] = []
        for run_id in await self.runtime.store.recoverable_runs():
            record = await self.runtime.store.get_run(run_id) or {}
            if run_id in self.tasks:
                continue
            async with self._lock:
                if not await self._reserve_demo_budget(run_id):
                    await self.runtime.store.mark_run(
                        run_id,
                        RunStatus.FAILED,
                        error="今日演示预算不足，无法恢复运行",
                    )
                    continue
                self.tasks[run_id] = asyncio.create_task(
                    self._resume(run_id, record.get("question", ""), record.get("as_of", ""))
                )
            recovered.append(run_id)
        return recovered

    async def _resume(self, run_id: str, question: str, as_of: str) -> None:
        try:
            async with self.semaphore:
                await self.runtime.runner.run(
                    run_id, question=question, as_of=as_of, resume=True
                )
        except asyncio.CancelledError:
            await self.runtime.store.mark_run(run_id, RunStatus.CANCELLED)
            raise
        except Exception as exc:
            await self.runtime.bus.publish(run_id, EventType.RUN_FAILED, error=str(exc))
        finally:
            async with self._lock:
                self.tasks.pop(run_id, None)
                self._daily_reservations.pop(run_id, None)

    async def _reserve_demo_budget(self, run_id: str) -> bool:
        """Reserve one run's worst-case spend before accepting a demo request.

        Reservations close the race where several concurrent requests all see
        the same remaining budget. Finished runs are charged by their recorded
        actual cost; active runs retain a conservative max-cost reservation.
        """
        settings = self.runtime.settings
        if settings.app_mode != "demo":
            return True
        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        spent = await self.runtime.store.usage_cost_since(day_start)
        reserved = sum(self._daily_reservations.values())
        reservation = settings.budget_max_cost_cny
        if spent + reserved + reservation > settings.daily_budget_cny:
            return False
        self._daily_reservations[run_id] = reservation
        return True

    def is_running(self, run_id: str) -> bool:
        task = self.tasks.get(run_id)
        return task is not None and not task.done()

    def stats(self) -> dict[str, Any]:
        return {
            "active": len([t for t in self.tasks.values() if not t.done()]),
            "tracked": len(self.tasks),
            "started_at": time.time(),
        }
