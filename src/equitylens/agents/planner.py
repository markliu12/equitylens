"""研究规划节点。"""

from __future__ import annotations

from equitylens.agents.base import BaseAgent
from equitylens.agents.schemas import ResearchPlan
from equitylens.engine.node import NodeContext
from equitylens.engine.types import FailureKind, NodePolicy, OnFailure, Patch
from equitylens.errors import NodeError


class PlannerAgent(BaseAgent):
    role = "planner"
    prompt_name = "planner"
    schema_name = "ResearchPlan"
    output_model = ResearchPlan

    def __init__(self) -> None:
        super().__init__(
            "plan",
            deps=("resolve_entity",),
            reads=("question", "entity", "as_of"),
            writes=("plan",),
            policy=NodePolicy(timeout_s=60, on_failure=OnFailure.DEGRADE, concurrency="llm"),
            label="研究规划",
        )

    def build_context(self, ctx: NodeContext) -> dict:
        return {
            "role": self.role,
            "question": ctx.view.get("question"),
            "as_of": ctx.view.get("as_of"),
            "entity": ctx.view.get("entity") or {},
        }

    async def run(self, ctx: NodeContext) -> Patch:
        try:
            return await super().run(ctx)
        except NodeError as exc:
            if exc.kind is FailureKind.BUDGET:
                raise
            entity = ctx.view.get("entity") or {}
            name = entity.get("name") or "目标公司"
            # 降级计划：不阻断主流程，后续分析会显式标注"规划降级"
            exc.fallback = {
                "plan": {
                    "entity_name": name,
                    "entity_code": entity.get("code") or "",
                    "industry": entity.get("industry") or "",
                    "sub_questions": [
                        f"{name}最近一期经营与盈利表现如何？",
                        f"{name}所处行业的景气度如何？",
                        f"{name}近期有哪些重大公告？",
                    ],
                    "required_data": ["财务报表", "公司公告", "行业数据"],
                    "analysts_enabled": ["fundamental", "industry", "sentiment"],
                    "est_budget_cny": 0.4,
                }
            }
            raise
