"""checkpoint：让"跑到一半崩了"变成可恢复事件，而不是重跑全流程。

三个设计决策值得说明：
1. **节点级而非步骤级**：每完成一个节点写一次快照（state < 200KB），代价小、
   恢复粒度够用；不做"每 token 存一次"那种过度设计。
2. **终态即事实**：恢复时已 SUCCEEDED 的节点直接跳过，LLM 调用次数为 0，
   所以恢复不会重复扣费。
3. **状态可序列化**：Blackboard.snapshot() 本身就是 dict，没有任何 ORM 映射成本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from equitylens.engine.types import NodeStatus, RunStatus, Usage


@dataclass(frozen=True)
class NodeRecord:
    run_id: str
    node_id: str
    status: NodeStatus
    attempts: int = 1
    usage: dict[str, Any] = field(default_factory=dict)
    error_kind: str | None = None
    error: str | None = None
    degraded: bool = False
    started_at: float = 0.0
    finished_at: float = 0.0
    patch_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    run_id: str
    seq: int
    state: dict[str, Any]
    statuses: dict[str, str]
    event_seq: int = 0


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    question: str
    as_of: str
    status: RunStatus
    graph_version: str = ""
    config_hash: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    error: str | None = None


@runtime_checkable
class CheckpointStore(Protocol):
    async def create_run(
        self,
        run_id: str,
        *,
        question: str,
        as_of: str,
        graph_version: str = "",
        config_hash: str = "",
    ) -> None: ...

    async def mark_run(
        self,
        run_id: str,
        status: RunStatus,
        *,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None: ...

    async def save_node(self, record: NodeRecord) -> None: ...

    async def save_snapshot(self, snapshot: Snapshot) -> None: ...

    async def load_latest(self, run_id: str) -> Snapshot | None: ...

    async def append_event(self, event: Any) -> int: ...

    async def list_events(
        self, run_id: str, after_seq: int = 0, limit: int = 2000
    ) -> list[dict[str, Any]]: ...

    async def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    async def list_runs(self, limit: int = 20) -> list[dict[str, Any]]: ...

    async def recoverable_runs(self) -> list[str]: ...


class MemoryStore:
    """测试与纯离线跑批用的内存实现，语义与 SqliteStore 对齐。"""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.nodes: list[NodeRecord] = []
        self.snapshots: dict[str, list[Snapshot]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self._seq: dict[str, int] = {}

    async def create_run(self, run_id: str, **kwargs: Any) -> None:
        import time

        self.runs[run_id] = {
            "run_id": run_id,
            **kwargs,
            "status": str(RunStatus.QUEUED),
            "usage": {},
            "created_at": time.time(),
            "updated_at": time.time(),
            "error": None,
        }
        self.events.setdefault(run_id, [])
        self._seq.setdefault(run_id, 0)

    async def mark_run(
        self,
        run_id: str,
        status: RunStatus,
        *,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        import time

        run = self.runs.setdefault(run_id, {"run_id": run_id})
        run["status"] = str(status)
        run["updated_at"] = time.time()
        if usage is not None:
            run["usage"] = usage
        if error is not None:
            run["error"] = error

    async def save_node(self, record: NodeRecord) -> None:
        self.nodes.append(record)

    async def save_snapshot(self, snapshot: Snapshot) -> None:
        self.snapshots.setdefault(snapshot.run_id, []).append(snapshot)

    async def load_latest(self, run_id: str) -> Snapshot | None:
        snaps = self.snapshots.get(run_id)
        return snaps[-1] if snaps else None

    async def append_event(self, event: Any) -> int:
        seq = self._seq.get(event.run_id, 0) + 1
        self._seq[event.run_id] = seq
        event.seq = seq
        self.events.setdefault(event.run_id, []).append(event.to_dict())
        return seq

    async def list_events(
        self, run_id: str, after_seq: int = 0, limit: int = 2000
    ) -> list[dict[str, Any]]:
        items = [e for e in self.events.get(run_id, []) if e["seq"] > after_seq]
        return items[:limit]

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    async def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        runs = sorted(self.runs.values(), key=lambda r: r.get("created_at", 0), reverse=True)
        return runs[:limit]

    async def recoverable_runs(self) -> list[str]:
        return [rid for rid, r in self.runs.items() if r.get("status") in {"RUNNING", "QUEUED"}]

    async def node_records(self, run_id: str) -> list[NodeRecord]:
        return [n for n in self.nodes if n.run_id == run_id]

    async def save_artifact(self, run_id: str, kind: str, path: str, sha256: str = "") -> None:
        self.runs.setdefault(run_id, {}).setdefault("artifacts", {})[kind] = {
            "path": path,
            "sha256": sha256,
        }


def usage_from_dict(data: dict[str, Any] | None) -> Usage:
    data = data or {}
    return Usage(
        prompt_tokens=int(data.get("prompt_tokens") or 0),
        completion_tokens=int(data.get("completion_tokens") or 0),
        cost_cny=float(data.get("cost_cny") or 0.0),
        calls=int(data.get("calls") or 0),
        model=str(data.get("model") or ""),
    )
