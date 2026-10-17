"""图构建与静态校验。

校验放在构建期而不是运行期：环、未知依赖、越权写通道这类问题，如果没有在启动时
拦住，会以"跑了一半卡死""状态莫名少字段"的形式出现在线上，排查成本高一个数量级。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from dataclasses import dataclass, field

from equitylens.engine.node import Node
from equitylens.engine.state import StateSchema
from equitylens.errors import GraphDefinitionError


@dataclass
class Graph:
    schema: StateSchema
    nodes: dict[str, Node]
    order: tuple[str, ...]
    versions: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.dependents: dict[str, tuple[str, ...]] = {
            node_id: tuple(
                other.id for other in self.nodes.values() if node_id in other.deps
            )
            for node_id in self.nodes
        }

    @property
    def version(self) -> str:
        import hashlib

        raw = "|".join(f"{nid}:{type(n).__name__}:{nid}" for nid, n in sorted(self.nodes.items()))
        return hashlib.sha1(raw.encode()).hexdigest()[:8]


class GraphBuilder:
    def __init__(self, schema: StateSchema) -> None:
        self._schema = schema
        self._nodes: dict[str, Node] = {}
        self._extra_edges: list[tuple[str, str]] = []

    def add(self, *nodes: Node) -> GraphBuilder:
        for node in nodes:
            if node.id in self._nodes:
                raise GraphDefinitionError(f"节点 id 重复: {node.id}")
            self._nodes[node.id] = node
        return self

    def edge(self, src: str, dst: str) -> GraphBuilder:
        """声明 dst 依赖 src。"""
        self._extra_edges.append((src, dst))
        return self

    def chain(self, *node_ids: str) -> GraphBuilder:
        for src, dst in itertools.pairwise(node_ids):
            self.edge(src, dst)
        return self

    # ------------------------------------------------------------------ #
    def validate(self) -> None:
        for node in self._nodes.values():
            for dep in node.deps:
                if dep not in self._nodes:
                    raise GraphDefinitionError(f"节点 {node.id} 依赖了不存在的节点 {dep}")
            for channel in (*node.reads, *node.writes):
                if channel not in self._schema:
                    raise GraphDefinitionError(f"节点 {node.id} 引用了未声明的状态通道 {channel}")
            if not node.writes and node.kind not in {"aggregate"}:
                # 允许只读节点（例如纯校验节点），但显式提示更利于排错
                pass
        extra = self._resolve_extra_edges()
        self._detect_cycle(extra)

    def _resolve_extra_edges(self) -> dict[str, tuple[str, ...]]:
        deps = {nid: tuple(n.deps) for nid, n in self._nodes.items()}
        for src, dst in self._extra_edges:
            if src not in self._nodes:
                raise GraphDefinitionError(f"edge 源节点不存在: {src}")
            if dst not in self._nodes:
                raise GraphDefinitionError(f"edge 目标节点不存在: {dst}")
            deps[dst] = (*deps[dst], src)
        for nid, node in self._nodes.items():
            node.deps = deps[nid]
        return deps

    def _detect_cycle(self, deps: dict[str, tuple[str, ...]]) -> None:
        state: dict[str, int] = {}  # 0=未访问 1=栈中 2=已完成
        stack: list[str] = []

        def dfs(node_id: str) -> None:
            state[node_id] = 1
            stack.append(node_id)
            for dep in deps.get(node_id, ()):
                mark = state.get(dep, 0)
                if mark == 0:
                    dfs(dep)
                elif mark == 1:
                    cycle = " → ".join([*stack[stack.index(dep) :], dep])
                    raise GraphDefinitionError(f"检测到环: {cycle}")
            stack.pop()
            state[node_id] = 2

        for node_id in deps:
            if state.get(node_id, 0) == 0:
                dfs(node_id)

    def toposort(self) -> tuple[str, ...]:
        deps = {nid: tuple(n.deps) for nid, n in self._nodes.items()}
        resolved: list[str] = []
        seen: set[str] = set()
        pending = dict(deps)
        while pending:
            progressed = False
            for nid in list(pending):
                if all(dep in seen for dep in pending[nid]):
                    resolved.append(nid)
                    seen.add(nid)
                    del pending[nid]
                    progressed = True
            if not progressed:  # pragma: no cover - validate 已拦
                raise GraphDefinitionError(f"无法拓扑排序: {sorted(pending)}")
        return tuple(resolved)

    def build(self) -> Graph:
        self.validate()
        return Graph(
            schema=self._schema,
            nodes=dict(self._nodes),
            order=self.toposort(),
        )

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "id": n.id,
                "kind": n.kind,
                "label": n.label,
                "deps": list(n.deps),
                "reads": list(n.reads),
                "writes": list(n.writes),
            }
            for n in self._nodes.values()
        ]


def iter_nodes(graph: Graph) -> Iterable[Node]:
    for node_id in graph.order:
        yield graph.nodes[node_id]
