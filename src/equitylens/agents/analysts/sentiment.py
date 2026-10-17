"""舆情与事件分析师。"""

from __future__ import annotations

from typing import Any

from equitylens.agents.analysts.base import AnalystBase
from equitylens.engine.node import NodeContext


class SentimentAnalyst(AnalystBase):
    def __init__(self) -> None:
        super().__init__(
            node_id="analyst_sentiment",
            role="analyst.sentiment",
            prompt_name="sentiment",
            label="舆情分析",
        )
        self.focus = "sentiment"

    def extra_context(self, ctx: NodeContext, detail: dict[str, Any]) -> dict[str, Any]:
        return {"news": detail.get("news") or []}
