"""取数节点：一条子问题 → 一个研究维度的证据集合。

它是纯确定性节点（无 LLM），因此失败策略是 SKIP：少一个维度的证据不应让整份报告
挂掉，但必须在 run 上留下 PARTIAL 标记和 gaps 记录。
"""

from __future__ import annotations

from equitylens.engine.node import NodeContext, ToolNode
from equitylens.engine.types import NodePolicy, OnFailure, Patch


class RetrieverNode(ToolNode):
    def __init__(self, node_id: str, *, focus: str, deps: tuple[str, ...] = ("plan",)) -> None:
        super().__init__(
            node_id,
            deps=deps,
            reads=("entity", "as_of"),
            writes=("evidence", "research_data", "evidence_gaps"),
            policy=NodePolicy(timeout_s=30, max_attempts=2, on_failure=OnFailure.SKIP),
            label=f"取数·{focus}",
        )
        self.focus = focus

    async def invoke(self, ctx: NodeContext) -> Patch:
        entity = ctx.view.get("entity") or {}
        data = await ctx.services.invoke_tool(
            "retrieve_evidence",
            code=str(entity.get("code") or ""),
            focus=self.focus,
            as_of=ctx.view.get("as_of"),
        )
        detail = {key: value for key, value in data.items() if key not in {"evidence", "gaps"}}
        return Patch(
            channels={
                "evidence": data.get("evidence") or [],
                "research_data": {self.focus: detail},
                "evidence_gaps": data.get("gaps") or [],
            },
            meta={"focus": self.focus, "evidence_count": len(data.get("evidence") or [])},
        )
