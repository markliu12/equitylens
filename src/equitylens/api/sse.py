"""SSE 推送：先补历史，再订阅增量。

关键实现细节：**先订阅、再读历史**。
反过来（先读历史、再订阅）会在两次操作之间丢事件，前端看到的时间线会永久缺一段，
而且这种丢失只在"节点恰好在两个动作之间完成"时发生——本地很难复现。
先用 seq 去重，就能安全地重叠。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from equitylens.engine.events import Event, EventBus, EventType

TERMINAL_EVENTS = {
    str(EventType.RUN_COMPLETED),
    str(EventType.RUN_FAILED),
    str(EventType.RUN_CANCELLED),
}
HEARTBEAT_SECONDS = 15.0


async def event_stream(
    run_id: str,
    bus: EventBus,
    store: Any,
    *,
    last_event_id: int = 0,
    heartbeat_s: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    queue = bus.subscribe(run_id)
    cursor = last_event_id
    try:
        history = await store.list_events(run_id, after_seq=cursor)
        for item in history:
            cursor = max(cursor, int(item["seq"]))
            yield _render(item)

        run = await store.get_run(run_id) or {}
        if run.get("status") in {"COMPLETED", "FAILED", "PARTIAL", "CANCELLED"}:
            # 已经结束的 run：补完历史就关流，绝不挂在这里等心跳——
            # 否则前端每次打开历史页面都会卡 15 秒以上。
            if not history or history[-1]["type"] not in TERMINAL_EVENTS:
                yield _render(
                    {
                        "seq": cursor + 1,
                        "type": str(EventType.RUN_COMPLETED),
                        "run_id": run_id,
                        "node_id": None,
                        "payload": {"status": run.get("status"), "replayed": True},
                        "ts": 0,
                    }
                )
            return

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if event.seq <= cursor:
                continue
            cursor = event.seq
            yield _render(event.to_dict())
            if event.type in TERMINAL_EVENTS:
                return
    finally:
        bus.unsubscribe(run_id, queue)


def _render(item: dict[str, Any]) -> str:
    payload = dict(item.get("payload") or {})
    body = json.dumps(
        {
            "v": 1,
            "seq": item.get("seq"),
            "type": item.get("type"),
            "run_id": item.get("run_id"),
            "node_id": item.get("node_id"),
            "ts": item.get("ts"),
            **payload,
        },
        ensure_ascii=False,
        default=str,
    )
    return f"id: {item.get('seq')}\nevent: {item.get('type')}\ndata: {body}\n\n"


def render_event(event: Event) -> str:
    return event.sse()
