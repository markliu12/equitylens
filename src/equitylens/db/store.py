"""SQLite 存储（运行记录 / 节点执行 / 快照 / 事件）。

为什么是裸 aiosqlite 而不是 ORM：
- 引擎侧的状态已经是纯 dict，ORM 在这里只增加映射成本，不带来任何类型收益；
- 并发写只有"节点完成时落一次"，用一把 asyncio.Lock + WAL 就够了，
  不需要连接池和 session 生命周期管理。
代价是 SQL 要自己写——所以 schema 只用一次（DDL 显式、无迁移），够用即可。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from equitylens.engine.checkpoint import NodeRecord, Snapshot
from equitylens.engine.types import RunStatus

DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    question TEXT NOT NULL DEFAULT '',
    as_of TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'QUEUED',
    graph_version TEXT NOT NULL DEFAULT '',
    config_hash TEXT NOT NULL DEFAULT '',
    usage_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS node_executions (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    usage_json TEXT NOT NULL DEFAULT '{}',
    error_kind TEXT,
    error TEXT,
    degraded INTEGER NOT NULL DEFAULT 0,
    started_at REAL NOT NULL DEFAULT 0,
    finished_at REAL NOT NULL DEFAULT 0,
    patch_keys TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, node_id, attempt)
);

CREATE TABLE IF NOT EXISTS checkpoints (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    statuses_json TEXT NOT NULL,
    event_seq INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    node_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    ts REAL NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS artifacts (
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
"""


class SqliteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Any = None
        self._lock = asyncio.Lock()

    async def _connection(self) -> Any:
        if self._conn is None:
            import aiosqlite

            self._conn = await aiosqlite.connect(self.path)
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
            await self._conn.executescript(DDL)
            await self._conn.commit()
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ #
    async def create_run(
        self,
        run_id: str,
        *,
        question: str,
        as_of: str,
        graph_version: str = "",
        config_hash: str = "",
    ) -> None:
        conn = await self._connection()
        now = time.time()
        async with self._lock:
            await conn.execute(
                """INSERT OR REPLACE INTO runs
                   (run_id, question, as_of, status, graph_version, config_hash,
                    usage_json, error, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, '{}', NULL, ?, ?)""",
                (run_id, question, as_of, str(RunStatus.QUEUED), graph_version, config_hash, now, now),
            )
            await conn.commit()

    async def mark_run(
        self,
        run_id: str,
        status: RunStatus,
        *,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        conn = await self._connection()
        async with self._lock:
            if usage is not None:
                await conn.execute(
                    "UPDATE runs SET status=?, usage_json=?, error=?, updated_at=? WHERE run_id=?",
                    (str(status), json.dumps(usage, ensure_ascii=False), error, time.time(), run_id),
                )
            else:
                await conn.execute(
                    "UPDATE runs SET status=?, error=?, updated_at=? WHERE run_id=?",
                    (str(status), error, time.time(), run_id),
                )
            await conn.commit()

    async def save_node(self, record: NodeRecord) -> None:
        conn = await self._connection()
        async with self._lock:
            await conn.execute(
                """INSERT OR REPLACE INTO node_executions
                   (run_id, node_id, attempt, status, usage_json, error_kind, error,
                    degraded, started_at, finished_at, patch_keys)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.run_id,
                    record.node_id,
                    record.attempts,
                    str(record.status),
                    json.dumps(record.usage, ensure_ascii=False),
                    record.error_kind,
                    record.error,
                    1 if record.degraded else 0,
                    record.started_at,
                    record.finished_at,
                    ",".join(record.patch_keys),
                ),
            )
            await conn.commit()

    async def save_snapshot(self, snapshot: Snapshot) -> None:
        conn = await self._connection()
        async with self._lock:
            await conn.execute(
                """INSERT OR REPLACE INTO checkpoints
                   (run_id, seq, state_json, statuses_json, event_seq, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.run_id,
                    snapshot.seq,
                    json.dumps(snapshot.state, ensure_ascii=False),
                    json.dumps(snapshot.statuses, ensure_ascii=False),
                    snapshot.event_seq,
                    time.time(),
                ),
            )
            await conn.commit()

    async def load_latest(self, run_id: str) -> Snapshot | None:
        conn = await self._connection()
        cursor = await conn.execute(
            "SELECT run_id, seq, state_json, statuses_json, event_seq FROM checkpoints "
            "WHERE run_id=? ORDER BY seq DESC LIMIT 1",
            (run_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return Snapshot(
            run_id=row[0],
            seq=int(row[1]),
            state=json.loads(row[2]),
            statuses=json.loads(row[3]),
            event_seq=int(row[4]),
        )

    async def append_event(self, event: Any) -> int:
        conn = await self._connection()
        async with self._lock:
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM events WHERE run_id=?", (event.run_id,)
            )
            row = await cursor.fetchone()
            await cursor.close()
            seq = int(row[0]) + 1
            await conn.execute(
                "INSERT INTO events (run_id, seq, type, node_id, payload_json, ts) VALUES (?,?,?,?,?,?)",
                (
                    event.run_id,
                    seq,
                    event.type,
                    event.node_id,
                    json.dumps(event.payload, ensure_ascii=False, default=str),
                    event.ts,
                ),
            )
            await conn.commit()
        return seq

    async def list_events(
        self, run_id: str, after_seq: int = 0, limit: int = 2000
    ) -> list[dict[str, Any]]:
        conn = await self._connection()
        cursor = await conn.execute(
            "SELECT run_id, seq, type, node_id, payload_json, ts FROM events "
            "WHERE run_id=? AND seq>? ORDER BY seq ASC LIMIT ?",
            (run_id, after_seq, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {
                "seq": int(row[1]),
                "run_id": row[0],
                "type": row[2],
                "node_id": row[3],
                "payload": json.loads(row[4]),
                "ts": row[5],
            }
            for row in rows
        ]

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        conn = await self._connection()
        cursor = await conn.execute(
            "SELECT run_id, question, as_of, status, graph_version, config_hash, "
            "usage_json, error, created_at, updated_at FROM runs WHERE run_id=?",
            (run_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "question": row[1],
            "as_of": row[2],
            "status": row[3],
            "graph_version": row[4],
            "config_hash": row[5],
            "usage": json.loads(row[6] or "{}"),
            "error": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }

    async def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = await self._connection()
        cursor = await conn.execute(
            "SELECT run_id, question, as_of, status, created_at, usage_json FROM runs "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {
                "run_id": row[0],
                "question": row[1],
                "as_of": row[2],
                "status": row[3],
                "created_at": row[4],
                "usage": json.loads(row[5] or "{}"),
            }
            for row in rows
        ]

    async def usage_cost_since(self, since: float) -> float:
        """Return the recorded LLM cost of runs touched since ``since``.

        The manager uses this at admission time for the demo's daily budget.
        Costs are stored as JSON to keep the run table portable, so summing in
        Python also avoids relying on SQLite's optional JSON extension.
        """
        conn = await self._connection()
        cursor = await conn.execute(
            "SELECT usage_json FROM runs WHERE updated_at >= ?", (since,)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        total = 0.0
        for (usage_json,) in rows:
            try:
                total += float((json.loads(usage_json or "{}") or {}).get("cost_cny", 0.0))
            except (TypeError, ValueError, json.JSONDecodeError):
                # Budget protection fails closed: a corrupt historic record
                # should never permit extra paid requests.
                return float("inf")
        return total

    async def node_records(self, run_id: str) -> list[dict[str, Any]]:
        conn = await self._connection()
        cursor = await conn.execute(
            "SELECT node_id, attempt, status, usage_json, error_kind, error, degraded, "
            "started_at, finished_at, patch_keys FROM node_executions "
            "WHERE run_id=? ORDER BY started_at ASC",
            (run_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {
                "node_id": row[0],
                "attempt": row[1],
                "status": row[2],
                "usage": json.loads(row[3] or "{}"),
                "error_kind": row[4],
                "error": row[5],
                "degraded": bool(row[6]),
                "started_at": row[7],
                "finished_at": row[8],
                "patch_keys": row[9].split(",") if row[9] else [],
            }
            for row in rows
        ]

    async def save_artifact(self, run_id: str, kind: str, path: str, sha256: str = "") -> None:
        conn = await self._connection()
        async with self._lock:
            await conn.execute(
                """INSERT OR REPLACE INTO artifacts (run_id, kind, path, sha256, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, kind, path, sha256, time.time()),
            )
            await conn.commit()

    async def recoverable_runs(self) -> list[str]:
        conn = await self._connection()
        cursor = await conn.execute(
            "SELECT run_id FROM runs WHERE status IN ('RUNNING','QUEUED') ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [row[0] for row in rows]
