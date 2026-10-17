"""研究主编：把散落的结构化结论组织成一份可读、可溯源的报告。"""

from __future__ import annotations

from typing import Any

from equitylens.agents.base import BaseAgent
from equitylens.agents.schemas import ResearchReport
from equitylens.engine.node import NodeContext
from equitylens.engine.types import NodePolicy
from equitylens.policy import DISCLAIMER
from equitylens.tools.evidence import TIER_LABELS


class EditorAgent(BaseAgent):
    role = "editor"
    prompt_name = "editor"
    schema_name = "ResearchReport"
    output_model = ResearchReport
    temperature = 0.3

    def __init__(self) -> None:
        super().__init__(
            "editor_draft",
            deps=("verdict",),
            reads=(
                "entity",
                "as_of",
                "analyst_reports",
                "verdict",
                "evidence",
                "evidence_gaps",
                "research_data",
            ),
            writes=("draft_report",),
            policy=NodePolicy(timeout_s=180, max_attempts=2, concurrency="llm"),
            label="报告撰写",
        )

    def build_context(self, ctx: NodeContext) -> dict[str, Any]:
        evidence = list(ctx.view.get("evidence") or [])
        refs = [
            {
                "id": item.get("id"),
                "source_name": item.get("source_name"),
                "tier": item.get("tier"),
                "tier_label": TIER_LABELS.get(int(item.get("tier") or 4), "其他"),
                "title": item.get("title"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
                "snippet": (item.get("snippet") or "")[:240],
                "kind": item.get("kind"),
            }
            for item in sorted(
                evidence, key=lambda x: (int(x.get("tier") or 4), str(x.get("published_at") or ""))
            )[:16]
        ]
        return {
            "role": self.role,
            "entity": ctx.view.get("entity") or {},
            "as_of": ctx.view.get("as_of"),
            "reports": [
                {
                    "role": item.get("role"),
                    "stance": item.get("stance"),
                    "confidence": item.get("confidence"),
                    "claims": item.get("claims") or [],
                    "metrics": item.get("metrics") or [],
                    "gaps": item.get("gaps") or [],
                }
                for item in ctx.view.get("analyst_reports") or []
            ],
            "verdict": ctx.view.get("verdict") or {},
            "derived": {
                "flags": ((ctx.view.get("research_data") or {}).get("derived") or {}).get("flags")
                or [],
            },
            "evidence": refs,
            "data_gaps": list(ctx.view.get("evidence_gaps") or []),
            "disclaimer": DISCLAIMER,
        }
