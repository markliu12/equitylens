"""终裁：把辩论结果收敛成"哪些结论进报告"。"""

from __future__ import annotations

from equitylens.agents.base import BaseAgent
from equitylens.agents.schemas import Verdict
from equitylens.engine.node import NodeContext
from equitylens.engine.types import NodePolicy


class VerdictAgent(BaseAgent):
    role = "verdict"
    prompt_name = "verdict"
    schema_name = "Verdict"
    output_model = Verdict
    temperature = 0.1

    def __init__(self) -> None:
        super().__init__(
            "verdict",
            deps=("debate",),
            reads=("entity", "analyst_reports", "debate_rounds", "evidence"),
            writes=("verdict",),
            policy=NodePolicy(timeout_s=120, max_attempts=2, concurrency="llm"),
            label="终裁",
        )

    def build_context(self, ctx: NodeContext) -> dict:
        rounds = list(ctx.view.get("debate_rounds") or [])
        return {
            "role": self.role,
            "entity": ctx.view.get("entity") or {},
            "reports": [
                {
                    "role": item.get("role"),
                    "stance": item.get("stance"),
                    "confidence": item.get("confidence"),
                    "claims": (item.get("claims") or [])[:3],
                    "gaps": item.get("gaps") or [],
                }
                for item in ctx.view.get("analyst_reports") or []
            ],
            "rounds": [
                {
                    "round": item.get("round"),
                    "leader": item.get("leader"),
                    "new_info_ratio": item.get("new_info_ratio"),
                    "unresolved": item.get("unresolved") or [],
                }
                for item in rounds
            ],
            "open_questions": sorted(
                {
                    gap
                    for item in ctx.view.get("analyst_reports") or []
                    for gap in (item.get("gaps") or [])
                }
            )[:5],
        }
