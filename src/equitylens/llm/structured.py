"""结构化输出：schema 校验 + 带错误信息的自动修复。

模型"不按 schema 输出"的三种典型情况，处理成本完全不同：
- 多了 ```json 围栏 / 尾部逗号 → 本地清洗即可，别浪费一次调用；
- 字段类型写错（"confidence": "高"） → 把字段路径和期望类型回灌，模型通常能改对；
- 干脆换了结构 → 重试大概率还是错，及时抛 SCHEMA 让节点走降级。

`repair_rate` 会被记录下来：它是 prompt 质量最灵敏的指标，比"看起来对不对"可靠。
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from equitylens.errors import SchemaValidationError
from equitylens.llm.router import ModelRouter

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def clean_json_text(text: str) -> str:
    cleaned = _FENCE.sub("", text.strip())
    cleaned = _TRAILING_COMMA.sub(r"\1", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    return cleaned


def field_errors(error: ValidationError) -> str:
    lines: list[str] = []
    for item in error.errors()[:8]:
        path = ".".join(str(part) for part in item.get("loc", ())) or "<root>"
        lines.append(f"- 字段 `{path}`: {item.get('msg')}")
    return "\n".join(lines)


def repair_instruction(error: ValidationError, model: type[BaseModel]) -> str:
    fields = ", ".join(model.model_fields.keys())
    return (
        "上一次输出没有通过 JSON Schema 校验，错误如下：\n"
        f"{field_errors(error)}\n\n"
        f"请修正后重新输出**完整**的 JSON 对象，只使用这些字段：{fields}。"
        "不要输出解释文字、不要用 markdown 代码块包裹。"
    )


async def call_structured[T: BaseModel](
    router: ModelRouter,
    *,
    role: str,
    messages: list[dict[str, str]],
    output_model: type[T],
    node_id: str = "",
    temperature: float = 0.2,
    max_repair: int = 2,
    max_tokens: int | None = None,
) -> tuple[T, dict[str, Any]]:
    """返回 (校验后的对象, 累计用量)。用量跨修复尝试累加，成本核算才准确。"""
    conversation = list(messages)
    usage: dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_cny": 0.0,
        "calls": 0,
        "model": "",
    }
    last_error: ValidationError | None = None

    for attempt in range(max_repair + 1):
        result = await router.complete(
            role=role,
            messages=conversation,
            json_mode=True,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        usage["prompt_tokens"] += result.prompt_tokens
        usage["completion_tokens"] += result.completion_tokens
        usage["calls"] += 1
        usage["model"] = result.model

        try:
            payload = json.loads(clean_json_text(result.text))
        except json.JSONDecodeError as exc:
            last_error = None
            if attempt >= max_repair:
                raise SchemaValidationError(
                    f"{node_id or role} 输出不是合法 JSON: {exc}"
                ) from exc
            conversation = [
                *conversation,
                {"role": "assistant", "content": result.text[:2000]},
                {
                    "role": "user",
                    "content": (
                        "你上一次的输出不是合法 JSON，解析错误："
                        f"{exc}。请只输出一个合法的 JSON 对象，不要任何额外文字。"
                    ),
                },
            ]
            continue

        try:
            obj = output_model.model_validate(payload)
        except ValidationError as exc:
            last_error = exc
            if attempt >= max_repair:
                break
            conversation = [
                *conversation,
                {"role": "assistant", "content": result.text[:2000]},
                {"role": "user", "content": repair_instruction(exc, output_model)},
            ]
            continue

        if attempt > 0:
            usage["repairs"] = attempt
        return obj, usage

    assert last_error is not None
    raise SchemaValidationError(
        f"{node_id or role} 连续 {max_repair + 1} 次未通过 {output_model.__name__} 校验：\n"
        f"{field_errors(last_error)}"
    )
