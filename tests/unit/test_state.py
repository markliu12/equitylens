from __future__ import annotations

import pytest

from equitylens.engine.state import Blackboard, StateSchema, append, last
from equitylens.engine.types import Patch
from equitylens.errors import GraphDefinitionError


def test_append_handles_single_object_as_first_value(schema: StateSchema) -> None:
    """回归测试：append 的 old 可能是单个 dict（FanOut 首次合并时的原始值）。

    直接 list(dict) 会得到键名列表，不会报错，但下游拿到的全是字符串。
    """
    board = Blackboard(schema)
    board.apply(Patch(channels={"items": {"name": "a"}}), node_id="n1", writes=["items"])
    board.apply(Patch(channels={"items": {"name": "b"}}), node_id="n1", writes=["items"])
    assert board.read("items") == [{"name": "a"}, {"name": "b"}]


def test_append_unique_dedupes_by_key(schema: StateSchema) -> None:
    board = Blackboard(schema)
    payload = [{"id": "ev_1", "text": "x"}, {"id": "ev_2", "text": "y"}]
    board.apply(Patch(channels={"unique": payload}), node_id="n1", writes=["unique"])
    board.apply(
        Patch(channels={"unique": [{"id": "ev_1", "text": "重复"}, {"id": "ev_3"}]}),
        node_id="n1",
        writes=["unique"],
    )
    assert [item["id"] for item in board.read("unique")] == ["ev_1", "ev_2", "ev_3"]


def test_write_to_undeclared_channel_is_rejected(schema: StateSchema) -> None:
    board = Blackboard(schema)
    with pytest.raises(GraphDefinitionError):
        board.apply(Patch(channels={"result": 1}), node_id="n1", writes=["items"])


def test_view_is_isolated_from_board(schema: StateSchema) -> None:
    board = Blackboard(schema)
    board.apply(Patch(channels={"result": {"value": 1}}), node_id="n1", writes=["result"])
    view = board.view(["result"])
    view.to_dict()["result"]["value"] = 999
    assert board.read("result") == {"value": 1}


def test_snapshot_is_json_serializable(schema: StateSchema) -> None:
    import json

    board = Blackboard(schema)
    board.apply(
        Patch(channels={"items": [{"a": 1}], "meta": {"k": "v"}}),
        node_id="n1",
        writes=["items", "meta"],
    )
    snapshot = board.snapshot()
    assert json.loads(json.dumps(snapshot, ensure_ascii=False)) == snapshot


def test_restore_from_snapshot(schema: StateSchema) -> None:
    board = Blackboard(schema)
    board.apply(Patch(channels={"result": {"a": 1}}), node_id="n1", writes=["result"])
    restored = Blackboard(schema, board.snapshot())
    assert restored.read("result") == {"a": 1}


def test_overlay_view_does_not_mutate_base(schema: StateSchema) -> None:
    board = Blackboard(schema)
    board.apply(Patch(channels={"result": {"step": 1}}), node_id="n1", writes=["result"])
    overlay_view = board.view().with_overlay({"result": {"step": 2}})
    assert overlay_view.get("result") == {"step": 2}
    assert board.read("result") == {"step": 1}


def test_sum_reducer_via_usage() -> None:
    from equitylens.engine.state import sum_usage
    from equitylens.engine.types import Usage

    first = sum_usage(None, Usage(prompt_tokens=10, completion_tokens=5, cost_cny=0.001, calls=1))
    second = sum_usage(first, Usage(prompt_tokens=20, completion_tokens=10, cost_cny=0.002, calls=1))
    assert second["prompt_tokens"] == 30
    assert second["calls"] == 2


def test_last_reducer_overwrites(schema: StateSchema) -> None:
    assert last({"a": 1}, {"b": 2}) == {"b": 2}
    assert append(None, {"a": 1}) == [{"a": 1}]
