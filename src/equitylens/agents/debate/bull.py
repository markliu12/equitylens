"""多方辩手。"""

from __future__ import annotations

from equitylens.agents.base import BaseAgent, evidence_brief
from equitylens.agents.schemas import Argument
from equitylens.engine.node import NodeContext
from equitylens.engine.types import FailureKind, NodePolicy, OnFailure, Patch
from equitylens.errors import NodeError


class BullAgent(BaseAgent):
    role = "debate.bull"
    prompt_name = "bull"
    schema_name = "Argument"
    output_model = Argument

    def __init__(self) -> None:
        super().__init__(
            "debater_bull",
            deps=("analyst_merge",),
            reads=("entity", "evidence", "analyst_reports", "bear_argument"),
            writes=("bull_argument",),
            policy=NodePolicy(
                timeout_s=150, max_attempts=2, on_failure=OnFailure.DEGRADE, concurrency="llm"
            ),
            label="多方辩论",
        )

    def build_context(self, ctx: NodeContext) -> dict:
        round_no = int(ctx.extra.get("round") or 0) + 1
        opponent = ctx.view.get("bear_argument") if round_no > 1 else None
        return {
            "role": self.role,
            "round": round_no,
            "entity": ctx.view.get("entity") or {},
            "reports": [
                {"role": item.get("role"), "stance": item.get("stance"),
                 "claims": (item.get("claims") or [])[:3]}
                for item in ctx.view.get("analyst_reports") or []
            ],
            "evidence": evidence_brief(list(ctx.view.get("evidence") or []), limit=8),
            "opponent": opponent,
        }

    async def run(self, ctx: NodeContext) -> Patch:
        try:
            return await super().run(ctx)
        except NodeError as exc:
            if exc.kind is FailureKind.BUDGET:
                raise
            exc.fallback = {
                "bull_argument": {
                    "side": "bull",
                    "thesis": "多方因模型调用失败未能完成本轮论证。",
                    "points": [],
                    "rebuttals": [],
                    "new_evidence_ids": [],
                }
            }
            raise
