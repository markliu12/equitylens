"""预算与上下文装配。

线上跑多智能体的真实教训：成本不是"最后统计一下"，而是**调用前**就要拦住。
BudgetGuard.check() 在每次 LLM 调用之前执行，超预算直接抛 BUDGET 失败，
这样最坏情况是少一段分析，而不是账单失控。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from equitylens.engine.types import Usage
from equitylens.errors import BudgetExceeded


@dataclass(frozen=True)
class BudgetConfig:
    max_cost_cny: float = 1.2
    max_tokens: int = 180_000
    max_calls: int = 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_cost_cny": self.max_cost_cny,
            "max_tokens": self.max_tokens,
            # 字段名与 Settings 保持一致，避免前后端各叫一套
            "max_llm_calls": self.max_calls,
        }


#: 输入/输出价格（元 / 千 token）。价格会变，所以放成表并允许被 configs 覆盖。
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.002, 0.008),
    "deepseek-reasoner": (0.004, 0.016),
    "qwen-plus": (0.0008, 0.002),
    "qwen-turbo": (0.0003, 0.0006),
    "glm-4-flash": (0.0, 0.0),
    "offline": (0.0, 0.0),
}


@dataclass
class CostModel:
    prices: dict[str, tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_PRICES)
    )
    default: tuple[float, float] = (0.002, 0.008)

    def cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        key = next((k for k in self.prices if model.startswith(k)), None)
        price_in, price_out = self.prices.get(key, self.default) if key else self.default
        return round(
            prompt_tokens / 1000 * price_in + completion_tokens / 1000 * price_out, 8
        )


class BudgetGuard:
    """run 级预算账本。线程/协程安全靠"单事件循环内顺序调用"保证。"""

    def __init__(self, config: BudgetConfig | None = None, *, cost_model: CostModel | None = None):
        self.config = config or BudgetConfig()
        self.cost_model = cost_model or CostModel()
        self.usage = Usage()
        self._by_node: dict[str, Usage] = {}
        self._warned = False
        self.warnings: list[str] = []

    # ---------------------------------------------------------------- #
    def check(self, estimated_tokens: int = 1200, node_id: str = "") -> None:
        if self.usage.calls + 1 > self.config.max_calls:
            raise BudgetExceeded(
                f"LLM 调用次数已达上限 {self.config.max_calls}（节点 {node_id}）"
            )
        if self.usage.total_tokens + estimated_tokens > self.config.max_tokens:
            raise BudgetExceeded(
                f"token 预算不足：已用 {self.usage.total_tokens}，"
                f"预计再需 {estimated_tokens}，上限 {self.config.max_tokens}"
            )
        if self.usage.cost_cny >= self.config.max_cost_cny:
            raise BudgetExceeded(
                f"成本预算已耗尽：{self.usage.cost_cny:.4f} / {self.config.max_cost_cny:.2f} 元"
            )

    def charge(
        self,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        node_id: str = "",
    ) -> Usage:
        cost = self.cost_model.cost(model, prompt_tokens, completion_tokens)
        delta = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_cny=cost,
            calls=1,
            model=model,
        )
        self.usage = self.usage + delta
        node_usage = self._by_node.get(node_id, Usage())
        self._by_node[node_id] = node_usage + delta
        if (
            not self._warned
            and self.usage.cost_cny >= self.config.max_cost_cny * 0.8
        ):
            self._warned = True
            self.warnings.append(
                f"成本已达预算的 80%：{self.usage.cost_cny:.4f} / {self.config.max_cost_cny:.2f} 元"
            )
        return delta

    def node_usage(self, node_id: str) -> Usage:
        return self._by_node.get(node_id, Usage())

    def by_node(self) -> dict[str, dict[str, Any]]:
        return {node: usage.to_dict() for node, usage in self._by_node.items()}

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.usage.to_dict(),
            "limits": self.config.to_dict(),
            "by_node": self.by_node(),
            "warnings": self.warnings,
            "remaining_cny": round(
                max(0.0, self.config.max_cost_cny - self.usage.cost_cny), 6
            ),
            "remaining_tokens": max(0, self.config.max_tokens - self.usage.total_tokens),
        }


@dataclass(frozen=True)
class Section:
    """上下文的一段。priority 越小越重要，预算不足时从大到小丢。"""

    name: str
    text: str
    priority: int = 5
    truncatable: bool = True


class ContextAssembler:
    """按优先级把多段上下文压进预算。

    顺序固定为：系统指令 > 任务说明 > 输出 schema > 必需证据 > 上游结论 > 历史摘要。
    预算不够时**先砍可选长文**，永远保留任务和 schema——否则模型会开始瞎编字段。
    """

    def __init__(self, max_tokens: int = 6000) -> None:
        self.max_tokens = max_tokens

    def assemble(self, sections: list[Section]) -> tuple[str, dict[str, Any]]:
        from equitylens.llm.tokens import estimate_tokens

        ordered = sorted(sections, key=lambda s: (s.priority, -len(s.text)))
        kept: list[Section] = []
        used = 0
        dropped: list[str] = []
        truncated: list[str] = []
        for section in ordered:
            cost = estimate_tokens(section.text)
            if used + cost <= self.max_tokens:
                kept.append(section)
                used += cost
                continue
            if not section.truncatable:
                kept.append(section)
                used += cost
                continue
            remaining = self.max_tokens - used
            if remaining > 200:
                ratio = remaining / max(cost, 1)
                cut = max(200, int(len(section.text) * ratio * 0.9))
                kept.append(Section(section.name, section.text[:cut] + "\n…（已截断）", section.priority))
                used += estimate_tokens(kept[-1].text)
                truncated.append(section.name)
            else:
                dropped.append(section.name)
        # 恢复成人类可读顺序（优先级顺序反而更好读）
        text = "\n\n".join(f"### {s.name}\n{s.text}" for s in kept)
        stats = {
            "estimated_tokens": used,
            "budget": self.max_tokens,
            "dropped": dropped,
            "truncated": truncated,
        }
        return text, stats
