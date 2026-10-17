"""基本面分析师。"""

from __future__ import annotations

from typing import Any

from equitylens.agents.analysts.base import AnalystBase
from equitylens.engine.node import NodeContext


class FundamentalAnalyst(AnalystBase):
    def __init__(self) -> None:
        super().__init__(
            node_id="analyst_fundamental",
            role="analyst.fundamental",
            prompt_name="fundamental",
            label="基本面分析",
        )
        self.focus = "fundamental"

    def extra_context(self, ctx: NodeContext, detail: dict[str, Any]) -> dict[str, Any]:
        return {"signals": detail.get("signals") or {}}
