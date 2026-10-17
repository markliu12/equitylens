"""节点抽象：六类节点覆盖了当前所有编排需求。

- ToolNode      纯数据/计算，无 LLM
- AgentNode     单次结构化 LLM 调用
- JudgeNode     带 rubric 的打分/裁决（仍是 AgentNode，只是语义更明确）
- FanOut        运行时按列表展开 N 份并发执行
- Aggregate     fan-in，带"法定人数"（min_success_ratio）
- LoopNode      有界循环（辩论、合规回修）

一个刻意的取舍：FanOut 在节点内部展开，而不是运行时动态往图里加节点。
动态改图会让 checkpoint 的状态一致性变得很难验证（恢复时图结构和崩溃时不一致），
而并发度、失败合并这些收益在节点内部一样能拿到。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from equitylens.engine.services import Services
from equitylens.engine.state import StateView, fingerprint
from equitylens.engine.types import NodePolicy, Patch


@dataclass
class NodeContext:
    run_id: str
    node_id: str
    attempt: int
    view: StateView
    services: Services
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def emit(self) -> Any:
        return self.services.emit


class Node(ABC):
    """节点基类。`reads/writes` 是显式契约，构建期即可校验拼写与越权。"""

    kind = "node"

    def __init__(
        self,
        node_id: str,
        *,
        deps: Sequence[str] = (),
        reads: Sequence[str] = (),
        writes: Sequence[str] = (),
        policy: NodePolicy | None = None,
        label: str = "",
    ) -> None:
        self.id = node_id
        self.deps = tuple(deps)
        self.reads = tuple(reads)
        self.writes = tuple(writes)
        self.policy = policy or NodePolicy()
        self.label = label or node_id

    @abstractmethod
    async def run(self, ctx: NodeContext) -> Patch: ...

    def fingerprint(self, view: StateView, prompt_version: str = "") -> str:
        return fingerprint(self.id, self.kind, prompt_version, view.to_dict())

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} {self.id}>"


class ToolNode(Node):
    """无 LLM 的确定性节点：数据获取、校验、渲染等。"""

    kind = "tool"

    async def invoke(self, ctx: NodeContext) -> Patch:
        raise NotImplementedError

    async def run(self, ctx: NodeContext) -> Patch:
        return await self.invoke(ctx)


class AgentNode(Node):
    """一次结构化 LLM 调用 → 一个 Pydantic 对象 → 一个 Patch。"""

    kind = "agent"
    role: str = "agent"
    output_model: type[Any] | None = None
    prompt_version: str = "v1"
    temperature: float = 0.2

    def build_messages(self, ctx: NodeContext) -> list[dict[str, str]]:
        raise NotImplementedError

    async def run(self, ctx: NodeContext) -> Patch:
        if self.output_model is None:
            raise ValueError(f"{self.id} 未声明 output_model")
        messages = self.build_messages(ctx)
        output, usage = await ctx.services.complete_structured(
            role=self.role,
            messages=messages,
            output_model=self.output_model,
            node_id=self.id,
            temperature=self.temperature,
        )
        # 状态里只存可 JSON 序列化的数据：checkpoint 直接落库，不需要额外转换层
        value = output.model_dump(mode="json") if hasattr(output, "model_dump") else output
        return Patch(
            channels={self.writes[0]: value} if len(self.writes) == 1 else self._pack(value),
            meta={"usage": usage, "role": self.role, "label": self.label},
        )

    def _pack(self, output: Any) -> dict[str, Any]:
        raise NotImplementedError(f"{self.id} 声明了多个写通道，需要覆写 _pack()")

    def fingerprint_with_prompt(self, view: StateView) -> str:
        return self.fingerprint(view, self.prompt_version)


class JudgeNode(AgentNode):
    """打分/裁决节点。语义上仍是 AgentNode，单独建模是为了在 trace 里区分。"""

    kind = "judge"
    role = "judge"
    temperature = 0.0


class FanOut(Node):
    """把列表通道展开成 N 个并发子任务。

    `worker_factory(item, index) -> Node` 返回的子节点 id 会加上 `[i]` 后缀，
    这样 trace 里每一项都是独立可观测的。
    """

    kind = "fanout"

    def __init__(
        self,
        node_id: str,
        *,
        items: Callable[[StateView], list[Any]],
        worker_factory: Callable[[Any, int], Node],
        deps: Sequence[str] = (),
        reads: Sequence[str] = (),
        writes: Sequence[str] = (),
        policy: NodePolicy | None = None,
        label: str = "",
    ) -> None:
        super().__init__(
            node_id, deps=deps, reads=reads, writes=writes, policy=policy, label=label
        )
        self._items = items
        self._worker_factory = worker_factory

    async def run(self, ctx: NodeContext) -> Patch:
        items = self._items(ctx.view)
        if not items:
            await ctx.emit("fanout.expanded", node_id=self.id, count=0)
            return Patch.empty()

        workers = [self._worker_factory(item, idx) for idx, item in enumerate(items)]
        await ctx.emit("fanout.expanded", node_id=self.id, count=len(workers))

        semaphore = asyncio.Semaphore(max(1, self.policy.max_parallel))

        async def _one(worker: Node, idx: int) -> tuple[Node, Patch | BaseException]:
            async with semaphore:
                local = replace(
                    ctx,
                    node_id=worker.id,
                    attempt=1,
                    extra={**ctx.extra, "item": items[idx], "index": idx},
                )
                try:
                    patch = await worker.run(local)
                    return worker, patch
                except BaseException as exc:
                    return worker, exc

        outcomes = await asyncio.gather(*(_one(w, i) for i, w in enumerate(workers)))

        merged: dict[str, Any] = {}
        usages: dict[str, Any] = {}
        oks = 0
        errors: list[str] = []
        schema = ctx.services.schema
        for worker, result in outcomes:
            if isinstance(result, BaseException):
                errors.append(f"{worker.id}: {result}")
                continue
            oks += 1
            usages = _merge_usage(usages, result.meta.get("usage"))
            for name, value in result.channels.items():
                if name not in self.writes:
                    continue
                if name in merged:
                    # 复用通道声明的 reducer，而不是按值类型猜测合并方式：
                    # 三个 Agent 各返回一个 dict 时，"append" 应该得到列表，
                    # 而"按 dict 合并"会把三份报告揉成一份——这是极易踩的坑。
                    merged[name] = schema.reducer(name)(merged[name], value)
                else:
                    merged[name] = value

        ratio = oks / len(workers)
        if errors:
            # 部分子任务失败不阻断整体，但必须可见：否则会变成"少了一块数据却没人知道"
            await ctx.emit(
                "fanout.partial",
                node_id=self.id,
                success_ratio=round(ratio, 3),
                errors=errors[:5],
            )
        if oks == 0:
            raise RuntimeError(f"FanOut {self.id} 全部子任务失败: {errors[:3]}")
        return Patch(
            channels=merged,
            meta={"usage": usages, "success_ratio": round(ratio, 3), "errors": errors[:5]},
        )


class Aggregate(Node):
    """fan-in：把列表通道压成一个对象，并给"法定人数"约束。

    比"忽略失败"更严格一点：成功比例低于阈值时显式标注 degraded，
    让最终报告能如实写出数据缺口，而不是假装一切正常。
    """

    kind = "aggregate"

    def __init__(
        self,
        node_id: str,
        *,
        source: str,
        target: str,
        reduce_fn: Callable[[list[Any], StateView], Any],
        deps: Sequence[str] = (),
        min_success_ratio: float = 0.6,
        labels: Callable[[list[Any]], dict[str, Any]] | None = None,
        policy: NodePolicy | None = None,
        label: str = "",
    ) -> None:
        super().__init__(
            node_id,
            deps=deps,
            reads=(source,),
            writes=(target,),
            policy=policy,
            label=label,
        )
        self.source = source
        self.target = target
        self._reduce = reduce_fn
        self.min_success_ratio = min_success_ratio
        self._labels = labels

    async def run(self, ctx: NodeContext) -> Patch:
        items = list(ctx.view.get(self.source) or [])
        degraded = not items
        value = self._reduce(items, ctx.view)
        meta: dict[str, Any] = {"count": len(items), "degraded": degraded}
        if self._labels:
            meta.update(self._labels(items))
        if degraded:
            meta["warning"] = f"{self.source} 为空，已按降级路径继续"
        return Patch(channels={self.target: value}, meta=meta)


class LoopNode(Node):
    """有界循环：辩论、合规回修。

    循环体在**局部 overlay** 上跑，只有整轮结束才提交一次 Patch。这样外部看到的
    状态变化是原子的，checkpoint 不会出现"辩论跑到一半"的中间态。
    """

    kind = "loop"

    def __init__(
        self,
        node_id: str,
        *,
        body: Sequence[Node],
        max_rounds: int = 3,
        until: Callable[[StateView, int], bool] | None = None,
        deps: Sequence[str] = (),
        reads: Sequence[str] = (),
        writes: Sequence[str] = (),
        policy: NodePolicy | None = None,
        label: str = "",
    ) -> None:
        super().__init__(
            node_id, deps=deps, reads=reads, writes=writes, policy=policy, label=label
        )
        self.body = list(body)
        self.max_rounds = max_rounds
        self.until = until

    async def run(self, ctx: NodeContext) -> Patch:
        schema = ctx.services.schema
        overlay: dict[str, Any] = {}
        rounds = 0
        for round_index in range(self.max_rounds):
            rounds = round_index + 1
            for node in self.body:
                local = replace(
                    ctx,
                    node_id=node.id,
                    attempt=1,
                    view=ctx.view.with_overlay(overlay),
                    extra={**ctx.extra, "round": round_index},
                )
                patch = await node.run(local)
                for name, value in patch.channels.items():
                    if name not in self.writes:
                        continue
                    current = overlay.get(name, ctx.view.get(name))
                    overlay[name] = schema.reducer(name)(current, value)
            await ctx.emit("loop.round", node_id=self.id, round=rounds)
            merged_view = ctx.view.with_overlay(overlay)
            if self.until is not None and self.until(merged_view, rounds):
                break
        return Patch(channels=overlay, meta={"rounds": rounds})


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _merge_usage(left: dict[str, Any], right: Any) -> dict[str, Any]:
    if not right:
        return left
    acc = dict(left)
    for key, value in dict(right).items():
        if key == "model":
            acc[key] = value or acc.get(key, "")
        else:
            acc[key] = (acc.get(key) or 0) + (value or 0)
    return acc


AsyncNodeFn = Callable[[NodeContext], Awaitable[Patch]]
