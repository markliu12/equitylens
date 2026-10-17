from __future__ import annotations

import pytest

from equitylens.engine.budget import BudgetConfig, BudgetGuard, ContextAssembler, Section
from equitylens.engine.types import Usage
from equitylens.errors import BudgetExceeded


def test_check_rejects_when_calls_exhausted() -> None:
    guard = BudgetGuard(BudgetConfig(max_calls=1))
    guard.charge(model="deepseek-chat", prompt_tokens=10, completion_tokens=10)
    with pytest.raises(BudgetExceeded, match="调用次数"):
        guard.check()


def test_check_rejects_when_tokens_exhausted() -> None:
    guard = BudgetGuard(BudgetConfig(max_tokens=100))
    guard.charge(model="deepseek-chat", prompt_tokens=60, completion_tokens=40)
    with pytest.raises(BudgetExceeded, match="token"):
        guard.check(estimated_tokens=50)


def test_check_rejects_when_cost_exhausted() -> None:
    guard = BudgetGuard(BudgetConfig(max_cost_cny=0.001, max_calls=100, max_tokens=10**9))
    guard.charge(model="deepseek-chat", prompt_tokens=1000, completion_tokens=1000)
    with pytest.raises(BudgetExceeded, match="成本"):
        guard.check()


def test_cost_model_prices_by_model() -> None:
    guard = BudgetGuard(BudgetConfig())
    cheap = guard.charge(model="glm-4-flash", prompt_tokens=10000, completion_tokens=10000)
    expensive = guard.charge(model="deepseek-chat", prompt_tokens=10000, completion_tokens=10000)
    assert cheap.cost_cny < expensive.cost_cny


def test_usage_accumulates_and_tracks_nodes() -> None:
    guard = BudgetGuard(BudgetConfig())
    guard.charge(model="qwen-plus", prompt_tokens=100, completion_tokens=50, node_id="plan")
    guard.charge(model="qwen-plus", prompt_tokens=200, completion_tokens=80, node_id="plan")
    assert guard.usage.calls == 2
    assert guard.node_usage("plan").total_tokens == 430
    snapshot = guard.snapshot()
    assert snapshot["by_node"]["plan"]["calls"] == 2


def test_context_assembler_drops_low_priority_first() -> None:
    assembler = ContextAssembler(max_tokens=120)
    sections = [
        Section("任务", "必须保留的任务说明" * 3, priority=1, truncatable=False),
        Section("长文", "填充内容" * 200, priority=9),
    ]
    text, stats = assembler.assemble(sections)
    assert "任务" in text
    assert stats["dropped"] or stats["truncated"]
    assert stats["estimated_tokens"] <= 400


def test_usage_add_merges_model_name() -> None:
    left = Usage(prompt_tokens=10, model="a")
    right = Usage(prompt_tokens=5, model="")
    assert (left + right).model == "a"
    assert (left + right).total_tokens == 15
