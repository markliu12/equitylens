from __future__ import annotations

import json

import pytest

from equitylens.agents.schemas import ResearchPlan
from equitylens.errors import SchemaValidationError
from equitylens.llm.client import LLMResult
from equitylens.llm.structured import call_structured, clean_json_text


class FakeRouter:
    """按顺序返回预设文本，用来验证修复循环。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, *, role: str, messages: list[dict[str, str]], **kwargs) -> LLMResult:
        self.calls += 1
        text = self.responses.pop(0)
        return LLMResult(text=text, model="fake", prompt_tokens=10, completion_tokens=10)


VALID = json.dumps(
    {
        "entity_name": "贵州茅台",
        "entity_code": "600519",
        "industry": "白酒",
        "sub_questions": ["经营情况如何？"],
        "required_data": ["财报"],
        "analysts_enabled": ["fundamental"],
        "est_budget_cny": 0.4,
    },
    ensure_ascii=False,
)


def test_clean_json_text_strips_fences_and_trailing_commas() -> None:
    raw = '```json\n{"a": 1, "b": [1,2,],}\n```'
    assert json.loads(clean_json_text(raw)) == {"a": 1, "b": [1, 2]}


@pytest.mark.asyncio
async def test_repair_loop_fixes_invalid_schema() -> None:
    bad = '{"entity_name": "贵州茅台"}'
    router = FakeRouter([bad, VALID])
    obj, usage = await call_structured(
        router,  # type: ignore[arg-type]
        role="planner",
        messages=[{"role": "user", "content": "{}"}],
        output_model=ResearchPlan,
        node_id="plan",
    )
    assert obj.entity_name == "贵州茅台"
    assert router.calls == 2
    assert usage["calls"] == 2
    assert usage.get("repairs") == 1


@pytest.mark.asyncio
async def test_repair_loop_raises_after_exhausting_attempts() -> None:
    router = FakeRouter(["{}", "{}", "{}"])
    with pytest.raises(SchemaValidationError, match="未通过"):
        await call_structured(
            router,  # type: ignore[arg-type]
            role="planner",
            messages=[{"role": "user", "content": "{}"}],
            output_model=ResearchPlan,
            node_id="plan",
            max_repair=2,
        )
    assert router.calls == 3


@pytest.mark.asyncio
async def test_repair_loop_handles_non_json_output() -> None:
    router = FakeRouter(["这不是 JSON", VALID])
    obj, _ = await call_structured(
        router,  # type: ignore[arg-type]
        role="planner",
        messages=[{"role": "user", "content": "{}"}],
        output_model=ResearchPlan,
        node_id="plan",
    )
    assert obj.sub_questions
    assert router.calls == 2


@pytest.mark.asyncio
async def test_extra_fields_are_ignored() -> None:
    payload = json.loads(VALID)
    payload["unexpected"] = "多余字段"
    router = FakeRouter([json.dumps(payload, ensure_ascii=False)])
    obj, _ = await call_structured(
        router,  # type: ignore[arg-type]
        role="planner",
        messages=[{"role": "user", "content": "{}"}],
        output_model=ResearchPlan,
        node_id="plan",
    )
    assert obj.entity_name == "贵州茅台"
