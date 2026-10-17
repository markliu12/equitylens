from __future__ import annotations

from typing import Any

import pytest

from equitylens.engine.graph import GraphBuilder
from equitylens.engine.node import NodeContext
from equitylens.engine.state import StateSchema
from equitylens.engine.types import NodePolicy, Patch
from equitylens.errors import GraphDefinitionError
from tests.conftest import FuncNode


def _noop(node_id: str, **kwargs: Any) -> FuncNode:
    async def _run(_ctx: NodeContext) -> Patch:
        return Patch(channels={"result": node_id})

    return FuncNode(node_id, _run, writes=("result",), policy=NodePolicy(max_attempts=1), **kwargs)


def test_toposort_respects_dependencies(schema: StateSchema) -> None:
    builder = GraphBuilder(schema)
    builder.add(_noop("a"), _noop("b", deps=("a",)), _noop("c", deps=("a",)), _noop("d", deps=("b", "c")))
    graph = builder.build()
    order = list(graph.order)
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_cycle_is_detected(schema: StateSchema) -> None:
    builder = GraphBuilder(schema)
    builder.add(_noop("a"), _noop("b", deps=("a",)), _noop("c", deps=("b",)))
    builder.edge("c", "a")
    with pytest.raises(GraphDefinitionError, match="环"):
        builder.build()


def test_unknown_dependency_is_detected(schema: StateSchema) -> None:
    builder = GraphBuilder(schema)
    builder.add(_noop("a", deps=("ghost",)))
    with pytest.raises(GraphDefinitionError, match="不存在"):
        builder.build()


def test_undeclared_channel_is_detected(schema: StateSchema) -> None:
    async def _run(_ctx: NodeContext) -> Patch:
        return Patch.empty()

    builder = GraphBuilder(schema)
    builder.add(FuncNode("a", _run, writes=("nope",)))
    with pytest.raises(GraphDefinitionError, match="未声明"):
        builder.build()


def test_chain_builds_edges(schema: StateSchema) -> None:
    builder = GraphBuilder(schema)
    builder.add(_noop("a"), _noop("b"), _noop("c"))
    builder.chain("a", "b", "c")
    graph = builder.build()
    assert graph.nodes["b"].deps == ("a",)
    assert graph.nodes["c"].deps == ("b",)


def test_dependents_index(schema: StateSchema) -> None:
    builder = GraphBuilder(schema)
    builder.add(_noop("a"), _noop("b", deps=("a",)), _noop("c", deps=("a",)))
    graph = builder.build()
    assert set(graph.dependents["a"]) == {"b", "c"}


def test_graph_version_is_stable(schema: StateSchema) -> None:
    def build() -> str:
        builder = GraphBuilder(schema)
        builder.add(_noop("a"), _noop("b", deps=("a",)))
        return builder.build().version

    assert build() == build()
