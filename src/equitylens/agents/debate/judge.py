"""单轮辩论裁判。"""

from __future__ import annotations

from equitylens.agents.base import BaseAgent, evidence_brief
from equitylens.agents.schemas import RoundScore
from equitylens.engine.node import NodeContext
from equitylens.engine.types import FailureKind, NodePolicy, OnFailure, Patch
from equitylens.errors import NodeError


class RoundJudge(BaseAgent):
    role = "debate.judge"
    prompt_name = "judge"
    schema_name = "RoundScore"
    output_model = RoundScore
    temperature = 0.0

    def __init__(self) -> None:
        super().__init__(
            "round_judge",
            deps=("analyst_merge",),
            reads=("bull_argument", "bear_argument", "evidence"),
            writes=("debate_rounds",),
            policy=NodePolicy(
                timeout_s=90, max_attempts=2, on_failure=OnFailure.DEGRADE, concurrency="llm"
            ),
            label="辩论裁判",
        )

    def build_context(self, ctx: NodeContext) -> dict:
        return {
            "role": self.role,
            "round": int(ctx.extra.get("round") or 0) + 1,
            "bull": ctx.view.get("bull_argument") or {},
            "bear": ctx.view.get("bear_argument") or {},
            "evidence": evidence_brief(list(ctx.view.get("evidence") or []), limit=8),
        }

    async def run(self, ctx: NodeContext) -> Patch:
        try:
            return await super().run(ctx)
        except NodeError as exc:
            if exc.kind is FailureKind.BUDGET:
                raise
            round_no = int(ctx.extra.get("round") or 0) + 1
            # 规则兜底：裁判不可用时，用"引用条数"这种确定性指标给出分数
            bull = ctx.view.get("bull_argument") or {}
            bear = ctx.view.get("bear_argument") or {}
            bull_ev = len([p for p in bull.get("points") or [] if p.get("evidence_ids")])
            bear_ev = len([p for p in bear.get("points") or [] if p.get("evidence_ids")])
            exc.fallback = {
                "debate_rounds": {
                    "round": round_no,
                    "bull": {"logic": 3, "evidence": min(5, 2 + bull_ev), "novelty": 3, "rebuttal": 3},
                    "bear": {"logic": 3, "evidence": min(5, 2 + bear_ev), "novelty": 3, "rebuttal": 3},
                    "new_info_ratio": 0.2,
                    "leader": "tie",
                    "unresolved": ["裁判降级：使用确定性规则打分"],
                    "note": "裁判模型不可用，已降级为规则打分",
                }
            }
            raise
