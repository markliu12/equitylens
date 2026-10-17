"""模型路由：降级链 + 熔断 + 调用前预算检查。

三个能力对应三种线上事故：
1. 某个模型限流/抽风 → 换备用模型，而不是整个 run 挂掉；
2. 某个 provider 持续故障 → 熔断 60s，别把超时时间浪费在必然失败的请求上；
3. 成本失控 → 在**发起请求前**拒绝，而不是月底看账单。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from equitylens.engine.budget import BudgetGuard
from equitylens.engine.retry import CircuitBreaker
from equitylens.engine.types import FailureKind
from equitylens.errors import BudgetExceeded, NodeError
from equitylens.llm.client import LLMClient, LLMResult
from equitylens.llm.tokens import estimate_messages


@dataclass
class ModelRouter:
    client: LLMClient
    models: list[str]
    budget: BudgetGuard
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)
    max_tokens: int = 2048
    last_used_model: str = ""

    def _breaker(self, model: str) -> CircuitBreaker:
        if model not in self.breakers:
            self.breakers[model] = CircuitBreaker()
        return self.breakers[model]

    async def complete(
        self,
        *,
        role: str,
        messages: list[dict[str, str]],
        json_mode: bool = True,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResult:
        budget = self.budget
        estimate = estimate_messages(messages) + (max_tokens or self.max_tokens)
        budget.check(estimated_tokens=estimate, node_id=role)

        errors: list[str] = []
        for model in self.models:
            breaker = self._breaker(model)
            if not breaker.allow():
                errors.append(f"{model}: 熔断中")
                continue
            try:
                result = await self.client.complete(
                    messages,
                    model=model,
                    json_mode=json_mode,
                    temperature=temperature,
                    max_tokens=max_tokens or self.max_tokens,
                    role=role,
                )
            except BudgetExceeded:
                raise
            except NodeError as exc:
                breaker.record(False)
                errors.append(f"{model}: [{exc.kind}] {exc}")
                if exc.kind is FailureKind.POLICY:
                    # 鉴权/合规失败换模型也没用，直接暴露
                    raise
                continue
            except Exception as exc:
                breaker.record(False)
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
                continue
            breaker.record(True)
            budget.charge(
                model=result.model or model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                node_id=role,
            )
            self.last_used_model = result.model or model
            if budget.warnings:
                await _maybe_warn(budget)
            return result
        raise NodeError(
            FailureKind.PROVIDER_DOWN,
            f"所有模型均不可用（{role}）：" + " | ".join(errors[-3:]),
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "models": self.models,
            "last_used": self.last_used_model,
            "breakers": {
                model: {
                    "open": breaker.is_open,
                    "failure_rate": round(breaker.failure_rate, 3),
                }
                for model, breaker in self.breakers.items()
            },
        }


async def _maybe_warn(budget: BudgetGuard) -> None:
    # 预算告警由上层 emit 负责，这里只做去重占位，避免重复刷事件
    budget.warnings.clear()
