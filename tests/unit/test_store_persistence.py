"""运行记录持久化：换进程（新 SqliteStore 实例）后记录不丢。

README 曾声称"任务表在内存里"，实际运行记录早已落在 SQLite。
这组测试把"重启后运行记录可查询、可恢复"变成门禁断言。
"""

from __future__ import annotations

import pytest

from equitylens.db.store import SqliteStore
from equitylens.engine.types import RunStatus


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "runs.db"


async def test_run_record_survives_reopen(db_path):
    store = SqliteStore(db_path)
    await store.create_run("run_a", question="分析贵州茅台", as_of="2026-09-30")
    await store.mark_run("run_a", RunStatus.COMPLETED, usage={"cost_cny": 0.5})
    await store.close()

    # 模拟进程重启：同一个 db 文件，全新实例
    store2 = SqliteStore(db_path)
    record = await store2.get_run("run_a")
    assert record is not None
    assert record["question"] == "分析贵州茅台"
    assert record["status"] == str(RunStatus.COMPLETED)
    assert record["usage"]["cost_cny"] == 0.5
    runs = await store2.list_runs()
    assert [r["run_id"] for r in runs] == ["run_a"]
    await store2.close()


async def test_running_run_is_recoverable_after_reopen(db_path):
    store = SqliteStore(db_path)
    await store.create_run("run_b", question="q", as_of="2026-09-30")
    await store.mark_run("run_b", RunStatus.RUNNING)
    await store.create_run("run_c", question="q2", as_of="2026-09-30")
    await store.mark_run("run_c", RunStatus.COMPLETED)
    await store.close()

    store2 = SqliteStore(db_path)
    recoverable = await store2.recoverable_runs()
    assert recoverable == ["run_b"]  # COMPLETED 的不恢复
    await store2.close()


async def test_events_survive_reopen(db_path):
    from equitylens.engine.events import Event

    store = SqliteStore(db_path)
    await store.create_run("run_d", question="q", as_of="2026-09-30")
    await store.append_event(Event(run_id="run_d", type="RUN_STARTED", node_id=None, payload={}, ts=1.0))
    await store.append_event(Event(run_id="run_d", type="NODE_STARTED", node_id="n1", payload={}, ts=2.0))
    await store.close()

    store2 = SqliteStore(db_path)
    events = await store2.list_events("run_d")
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["node_id"] == "n1"
    # SSE 续传语义：after_seq 过滤仍然有效
    assert [e["seq"] for e in await store2.list_events("run_d", after_seq=1)] == [2]
    await store2.close()
