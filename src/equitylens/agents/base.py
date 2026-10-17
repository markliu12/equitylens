"""Agent 基类：把"Pydantic 输出 + 证据上下文 + 提示词版本"固化下来。

统一的上下文格式（最后一条 user 消息是一个 JSON 对象）有两个副作用，都是刻意的：
1. 离线模型可以解析同一份 JSON 生成确定性输出 → CI 可测；
2. trace 里保存的上下文本身就是可读的结构化快照 → 复盘时不用猜模型当时看到了什么。
"""

from __future__ import annotations

import json
from typing import Any

from equitylens.engine.node import AgentNode, NodeContext
from equitylens.llm.prompts import prompt_version, render_system_prompt


def evidence_brief(evidence: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """证据压缩：只保留做判断必需的字段，并按证据等级优先。"""
    ordered = sorted(
        evidence,
        key=lambda item: (int(item.get("tier") or 4), str(item.get("published_at") or "")),
    )
    return [
        {
            "id": item.get("id"),
            "tier": item.get("tier"),
            "kind": item.get("kind"),
            "title": item.get("title"),
            "published_at": item.get("published_at"),
            "snippet": (item.get("snippet") or "")[:220],
        }
        for item in ordered[:limit]
    ]


class BaseAgent(AgentNode):
    prompt_name: str = ""
    schema_name: str = ""

    def system_prompt(self) -> str:
        schema = self.schema_name or (self.output_model.__name__ if self.output_model else "")
        return render_system_prompt(self.prompt_name, schema)

    def build_context(self, ctx: NodeContext) -> dict[str, Any]:
        raise NotImplementedError

    def build_messages(self, ctx: NodeContext) -> list[dict[str, str]]:
        context = self.build_context(ctx)
        context.setdefault("role", self.role)
        return [
            {"role": "system", "content": self.system_prompt()},
            {
                "role": "user",
                "content": json.dumps(context, ensure_ascii=False, default=str),
            },
        ]

    @property
    def version(self) -> str:
        return prompt_version(self.prompt_name) if self.prompt_name else "v0"

    def fingerprint_with_prompt(self, view: Any) -> str:
        return self.fingerprint(view, self.version)
