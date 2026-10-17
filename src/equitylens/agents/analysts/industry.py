"""行业分析师。"""

from __future__ import annotations

from typing import Any

from equitylens.agents.analysts.base import AnalystBase
from equitylens.engine.node import NodeContext


class IndustryAnalyst(AnalystBase):
    def __init__(self) -> None:
        super().__init__(
            node_id="analyst_industry",
            role="analyst.industry",
            prompt_name="industry",
            label="行业分析",
        )
        self.focus = "industry"

    def extra_context(self, ctx: NodeContext, detail: dict[str, Any]) -> dict[str, Any]:
        return {"industry": detail.get("industry") or {}}
