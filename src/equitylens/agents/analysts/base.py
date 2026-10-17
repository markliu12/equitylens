"""分析师共用基类。

降级策略统一放在这里：任一分析维度失败，产出一份"数据不足"的合法报告，
而不是让整份研究缺一块。终稿会把 gaps 汇总到"数据缺口"章节——
让读者知道哪块没做，比假装做完了更重要。
"""

from __future__ import annotations

from typing import Any

from equitylens.agents.base import BaseAgent, evidence_brief
from equitylens.agents.schemas import AnalystReport
from equitylens.engine.node import NodeContext
from equitylens.engine.types import NodePolicy, OnFailure, Patch


class AnalystBase(BaseAgent):
    output_model = AnalystReport
    schema_name = "AnalystReport"
    focus = ""

    def __init__(self, *, node_id: str, role: str, prompt_name: str, label: str) -> None:
        super().__init__(
            node_id,
            deps=("evidence_merge",),
            reads=("entity", "as_of", "evidence", "research_data", "evidence_gaps"),
            writes=("analyst_reports",),
            policy=NodePolicy(
                timeout_s=150, max_attempts=2, on_failure=OnFailure.DEGRADE, concurrency="llm"
            ),
            label=label,
        )
        self.role = role
        self.prompt_name = prompt_name

    # ------------------------------------------------------------------ #
    def detail(self, ctx: NodeContext) -> dict[str, Any]:
        research = ctx.view.get("research_data") or {}
        return research.get(self.focus) or {}

    def extra_context(self, ctx: NodeContext, detail: dict[str, Any]) -> dict[str, Any]:
        return {}

    def build_context(self, ctx: NodeContext) -> dict[str, Any]:
        detail = self.detail(ctx)
        evidence = list(ctx.view.get("evidence") or [])
        gaps = list(ctx.view.get("evidence_gaps") or []) + list(detail.get("gaps") or [])
        context: dict[str, Any] = {
            "role": self.role,
            "entity": ctx.view.get("entity") or {},
            "as_of": ctx.view.get("as_of"),
            "evidence": evidence_brief(evidence, limit=10),
            "facts": detail.get("facts") or [],
            "metrics": detail.get("metrics") or [],
            "gaps": gaps,
        }
        context.update(self.extra_context(ctx, detail))
        return context

    async def run(self, ctx: NodeContext) -> Patch:
        from equitylens.errors import NodeError

        try:
            return await super().run(ctx)
        except NodeError as exc:
            # 预算耗尽不再降级（否则会变成"悄悄降级直到全空"），直接向上冒泡
            from equitylens.engine.types import FailureKind

            if exc.kind is FailureKind.BUDGET:
                raise
            exc.fallback = {
                "analyst_reports": [
                    {
                        "role": self.focus,
                        "stance": "数据不足",
                        "confidence": 0.2,
                        "metrics": [],
                        "claims": [
                            {
                                "text": f"{self.label}因模型调用失败已降级，本维度未产出结论。",
                                "evidence_ids": [],
                                "strength": "观点",
                            }
                        ],
                        "gaps": [f"{self.label}降级：{exc}"],
                    }
                ]
            }
            raise
