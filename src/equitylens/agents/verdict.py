"""终裁：把辩论结果收敛成"哪些结论进报告"。

这里刻意不只是"挑出分析师说得最好的三条"，而是重新做一次**交叉判断**：
单维度结论（营收增长多少）在终裁阶段要升级成跨维度结论（增长来自份额还是景气）。
理由是报告的"核心结论"如果只是明细章节的摘录，读者读完会觉得"那我自己看明细就行"，
整份报告也就失去了存在的理由。
"""

from __future__ import annotations

from equitylens.agents.base import BaseAgent, derived_view, evidence_brief
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
            reads=(
                "entity",
                "analyst_reports",
                "debate_rounds",
                "evidence",
                "research_data",
                "bull_argument",
                "bear_argument",
            ),
            writes=("verdict",),
            policy=NodePolicy(timeout_s=120, max_attempts=2, concurrency="llm"),
            label="终裁",
        )

    def build_context(self, ctx: NodeContext) -> dict:
        rounds = list(ctx.view.get("debate_rounds") or [])
        return {
            "role": self.role,
            "entity": ctx.view.get("entity") or {},
            # 交叉结论必须能挂到跨维度的证据上，所以终裁也要看到证据清单
            "evidence": evidence_brief(list(ctx.view.get("evidence") or []), limit=16),
            **derived_view(ctx.view),
            # 多空论点单独给出：反方视角要保留原始表述，不能被"终裁总结"磨平
            "bull": {
                "thesis": (ctx.view.get("bull_argument") or {}).get("thesis"),
                "points": ((ctx.view.get("bull_argument") or {}).get("points") or [])[:3],
            },
            "bear": {
                "thesis": (ctx.view.get("bear_argument") or {}).get("thesis"),
                "points": ((ctx.view.get("bear_argument") or {}).get("points") or [])[:3],
            },
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
