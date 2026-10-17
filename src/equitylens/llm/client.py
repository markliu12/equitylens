"""LLM 客户端（OpenAI 兼容协议）。

国内主流模型（DeepSeek / 通义 / 智谱 / Moonshot）都兼容这套协议，所以客户端只写一份，
换模型只改 base_url + model 名。刻意不用各家 SDK：依赖少、行为可预期、错误码统一。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from equitylens.engine.types import FailureKind
from equitylens.errors import NodeError


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def usage(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "model": self.model,
        }


@runtime_checkable
class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        json_mode: bool = True,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        role: str = "",
    ) -> LLMResult: ...


class OpenAICompatClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_s: float = 90.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        json_mode: bool = True,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        role: str = "",
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise NodeError(FailureKind.TIMEOUT, f"模型请求超时（{role}）: {exc}") from exc
        except httpx.HTTPError as exc:
            raise NodeError(FailureKind.TRANSIENT, f"模型请求失败（{role}）: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            raise NodeError(
                _classify_status(response.status_code),
                f"模型返回 {response.status_code}: {response.text[:300]}",
            )

        data = response.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise NodeError(
                FailureKind.TRANSIENT, f"模型响应结构异常: {str(data)[:200]}"
            ) from exc

        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            model=data.get("model", model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency_ms,
            raw={"id": data.get("id"), "finish_reason": _finish_reason(data)},
        )


def _finish_reason(data: dict[str, Any]) -> str:
    try:
        return str(data["choices"][0].get("finish_reason"))
    except (KeyError, IndexError, AttributeError):
        return ""


def _classify_status(status: int) -> FailureKind:
    if status == 429:
        return FailureKind.RATE_LIMIT
    if status >= 500:
        return FailureKind.TRANSIENT
    if status in {401, 403}:
        return FailureKind.POLICY
    if status == 408:
        return FailureKind.TIMEOUT
    if status == 400:
        # 常见于上下文超限 / json 模式不支持，属于可用降级或压缩处理的情况
        return FailureKind.SCHEMA
    return FailureKind.UNKNOWN
