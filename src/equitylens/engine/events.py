"""事件流：一次运行的所有可观测事实。

两个用途共用一份数据：
1. SSE 实时推给前端（节点时间线、辩论回合、预算告警）
2. 落库形成 trace（可以直接展示"每个节点花了多少 token、重试了几次"）

`seq` 由存储层自增分配，是 run 内单调的——这让 SSE 的 Last-Event-ID 断线续传
变成一次简单的 `WHERE seq > ?` 查询，不需要前端自己记状态。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    RUN_RESUMED = "run.resumed"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"

    NODE_QUEUED = "node.queued"
    NODE_STARTED = "node.started"
    NODE_RETRIED = "node.retried"
    NODE_COMPLETED = "node.completed"
    NODE_FAILED = "node.failed"
    NODE_SKIPPED = "node.skipped"
    NODE_DELTA = "node.delta"

    FANOUT_EXPANDED = "fanout.expanded"
    LOOP_ROUND = "loop.round"
    DEBATE_TURN = "debate.turn"
    DEBATE_SCORED = "debate.scored"

    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_CACHE_HIT = "tool.cache_hit"

    BUDGET_WARNING = "budget.warning"
    BUDGET_EXCEEDED = "budget.exceeded"
    CHECKPOINT_SAVED = "checkpoint.saved"
    REPORT_READY = "report.ready"
    HEARTBEAT = "heartbeat"


@dataclass
class Event:
    seq: int = 0
    run_id: str = ""
    type: str = ""
    node_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    v: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def sse(self) -> str:
        import json

        data = json.dumps(
            {
                "v": self.v,
                "seq": self.seq,
                "run_id": self.run_id,
                "node_id": self.node_id,
                "ts": self.ts,
                **self.payload,
            },
            ensure_ascii=False,
        )
        return f"id: {self.seq}\nevent: {self.type}\ndata: {data}\n\n"


class EventBus:
    """发布-订阅。写库成功后才推给订阅者，保证"前端看到的 ⊆ 库里有的"。"""

    def __init__(self, store: Any | None = None, *, queue_size: int = 512) -> None:
        self._store = store
        self._subscribers: dict[str, list[asyncio.Queue[Event]]] = {}
        self._queue_size = queue_size

    def subscribe(self, run_id: str) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[Event]) -> None:
        subs = self._subscribers.get(run_id, [])
        if queue in subs:
            subs.remove(queue)
        if not subs:
            self._subscribers.pop(run_id, None)

    async def publish(
        self,
        run_id: str,
        event_type: str | EventType,
        *,
        node_id: str | None = None,
        **payload: Any,
    ) -> Event:
        event = Event(run_id=run_id, type=str(event_type), node_id=node_id, payload=payload)
        if self._store is not None:
            event.seq = await self._store.append_event(event)
        for queue in list(self._subscribers.get(run_id, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 订阅者太慢：丢增量事件，但保留终态事件，避免前端看到半截状态
                if event.type in {str(EventType.NODE_DELTA), str(EventType.HEARTBEAT)}:
                    continue
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    pass
        return event
