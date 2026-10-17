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


def derived_view(view: Any) -> dict[str, Any]:
    """把派生信号压成"原始 + 派生"一张扁平表，供需要做交叉判断的角色使用。

    辩论双方和终裁都要用同一份派生结果。各自拼一遍的结果是：某天给
    signals.py 加一个字段，只改了两处，第三处悄悄没有——判断口径就分叉了。
    """
    derived = (view.get("research_data") or {}).get("derived") or {}
    if not derived:
        return {}
    return {
        "signals": {
            **(derived.get("raw") or {}),
            **(derived.get("derived") or {}),
        },
        "derived": {
            "flags": derived.get("flags") or [],
            "basis": derived.get("derived_basis") or {},
            "unavailable": derived.get("unavailable") or [],
        },
    }


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
