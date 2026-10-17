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

#: 每个维度"自己的"证据类型。排序时本维度证据必须优先，否则会被别的维度挤掉。
FOCUS_KIND = {"fundamental": "financial", "industry": "industry", "sentiment": "news"}


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
        # 本维度证据必须优先。
        # 旧实现把全量证据按 (tier, 日期) 排序后取前 10 条，行业分析师拿到的
        # 全是公告和财报，同业对比那条被挤掉——"估值位置"这条结论因此永远挂不上
        # 证据，覆盖率平白掉几个点。注意 evidence_brief 内部会重新排序，
        # 所以不能"先拼再排"，必须分两次截断。
        own_kind = FOCUS_KIND.get(self.focus, "")
        own = [item for item in evidence if item.get("kind") == own_kind]
        rest = [item for item in evidence if item.get("kind") != own_kind]
        brief = evidence_brief(own, limit=8) + evidence_brief(rest, limit=4)
        context: dict[str, Any] = {
            "role": self.role,
            "entity": ctx.view.get("entity") or {},
            "as_of": ctx.view.get("as_of"),
            "evidence": brief,
            "facts": detail.get("facts") or [],
            "metrics": detail.get("metrics") or [],
            "gaps": gaps,
        }
        context.update(self.extra_context(ctx, detail))

        # 派生信号对所有分析师可见：交叉判断本来就跨维度，
        # 让它只属于"基本面"会导致三个分析师各自重算、结论互相打架。
        derived = (ctx.view.get("research_data") or {}).get("derived") or {}
        if derived:
            context["signals"] = {
                **(context.get("signals") or {}),
                **(derived.get("derived") or {}),
            }
            context["derived"] = {
                "flags": derived.get("flags") or [],
                "basis": derived.get("derived_basis") or {},
                "unavailable": derived.get("unavailable") or [],
            }
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
