"""空方辩手。"""

from __future__ import annotations

from equitylens.agents.base import BaseAgent, derived_view, evidence_brief
from equitylens.agents.schemas import Argument
from equitylens.engine.node import NodeContext
from equitylens.engine.types import FailureKind, NodePolicy, OnFailure, Patch
from equitylens.errors import NodeError


class BearAgent(BaseAgent):
    role = "debate.bear"
    prompt_name = "bear"
    schema_name = "Argument"
    output_model = Argument

    def __init__(self) -> None:
        super().__init__(
            "debater_bear",
            deps=("analyst_merge",),
            reads=("entity", "evidence", "analyst_reports", "bull_argument", "research_data"),
            writes=("bear_argument",),
            policy=NodePolicy(
                timeout_s=150, max_attempts=2, on_failure=OnFailure.DEGRADE, concurrency="llm"
            ),
            label="空方辩论",
        )

    def build_context(self, ctx: NodeContext) -> dict:
        round_no = int(ctx.extra.get("round") or 0) + 1
        opponent = ctx.view.get("bull_argument") if round_no > 1 else None
        return {
            "role": self.role,
            "round": round_no,
            "entity": ctx.view.get("entity") or {},
            "reports": [
                {"role": item.get("role"), "stance": item.get("stance"),
                 "claims": (item.get("claims") or [])[:3]}
                for item in ctx.view.get("analyst_reports") or []
            ],
            "evidence": evidence_brief(list(ctx.view.get("evidence") or []), limit=12),
            "opponent": opponent,
            **derived_view(ctx.view),
        }

    async def run(self, ctx: NodeContext) -> Patch:
        try:
            return await super().run(ctx)
        except NodeError as exc:
            if exc.kind is FailureKind.BUDGET:
                raise
            exc.fallback = {
                "bear_argument": {
                    "side": "bear",
                    "thesis": "空方因模型调用失败未能完成本轮论证。",
                    "points": [],
                    "rebuttals": [],
                    "new_evidence_ids": [],
                }
            }
            raise
