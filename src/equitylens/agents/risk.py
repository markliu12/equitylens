"""合规风控节点。

它是"研究"与"荐股"之间的硬闸门。注意它同时用了两条独立的检查路径：
1. LLM 判断语义（能否构成操作性建议）；
2. 确定性词表扫描（policy.scan_text）。
只靠 LLM 会有漏检和幻觉通过，只靠词表会误伤"公司否认了市场传言"这类表述。
两条都过才算 passed。
"""

from __future__ import annotations

from typing import Any

from equitylens.agents.base import BaseAgent
from equitylens.agents.schemas import RiskReview
from equitylens.engine.node import NodeContext
from equitylens.engine.types import NodePolicy, Patch
from equitylens.policy import scan_many


class ComplianceAgent(BaseAgent):
    role = "risk"
    prompt_name = "risk"
    schema_name = "RiskReview"
    output_model = RiskReview
    temperature = 0.0

    def __init__(self) -> None:
        super().__init__(
            "compliance",
            deps=("fact_check",),
            reads=("draft_report", "as_of"),
            writes=("risk_review",),
            policy=NodePolicy(timeout_s=90, max_attempts=2, concurrency="llm"),
            label="合规风控",
        )

    def _claim_texts(self, ctx: NodeContext) -> list[str]:
        report = ctx.view.get("draft_report") or {}
        texts: list[str] = [str(report.get("summary") or "")]
        for section in report.get("sections") or []:
            texts.append(str(section.get("narrative") or ""))
            for claim in section.get("claims") or []:
                texts.append(str(claim.get("text") or ""))
        return [text for text in texts if text]

    def build_context(self, ctx: NodeContext) -> dict:
        report = ctx.view.get("draft_report") or {}
        return {
            "role": self.role,
            "as_of": ctx.view.get("as_of"),
            "summary": report.get("summary"),
            "claims": [{"text": text} for text in self._claim_texts(ctx)],
            "claim_count": len(self._claim_texts(ctx)),
        }

    async def run(self, ctx: NodeContext) -> Patch:
        patch = await super().run(ctx)
        review: dict[str, Any] = patch.channels.get("risk_review") or {}
        deterministic = scan_many(self._claim_texts(ctx))
        if deterministic:
            violations = list(review.get("violations") or [])
            existing = {v.get("quote") for v in violations}
            for hit in deterministic:
                if hit.quote in existing:
                    continue
                violations.append({"type": hit.type, "quote": hit.quote, "fix": hit.fix})
            review["violations"] = violations[:8]
            review["passed"] = False
            review["required_edits"] = list(
                dict.fromkeys([*(review.get("required_edits") or []), *[v["fix"] for v in violations]])
            )
            patch = Patch(
                channels={"risk_review": review},
                meta={**patch.meta, "deterministic_hits": len(deterministic)},
            )
        return patch
