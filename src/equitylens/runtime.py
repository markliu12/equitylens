"""装配层：把所有零件接起来。

放在 package 根目录而不是 engine/ 里，是为了让依赖方向保持单向：
engine 不知道 llm 和 tools 的存在，装配的复杂度全部收在这一层。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from equitylens.config import Settings, get_settings
from equitylens.db.store import SqliteStore
from equitylens.engine.budget import BudgetConfig, BudgetGuard
from equitylens.engine.events import EventBus
from equitylens.engine.graph import Graph
from equitylens.engine.runner import GraphRunner
from equitylens.engine.services import Services
from equitylens.graph.research_graph import GRAPH_VERSION, build_graph
from equitylens.llm.client import LLMClient, OpenAICompatClient
from equitylens.llm.offline import OfflineLLMClient
from equitylens.llm.router import ModelRouter
from equitylens.llm.structured import call_structured
from equitylens.tools.base import ToolRegistry
from equitylens.tools.cache import DataCache, NullCache, cache_key
from equitylens.tools.providers.base import MarketDataProvider
from equitylens.tools.providers.fixture import FixtureProvider
from equitylens.tools.ratelimit import NullLimiter, TokenBucket
from equitylens.tools.research import resolve_entity, retrieve_evidence


@dataclass
class AppRuntime:
    settings: Settings
    store: SqliteStore
    bus: EventBus
    provider: MarketDataProvider
    cache: Any
    tools: ToolRegistry
    graph: Graph
    llm_client: LLMClient
    runner: GraphRunner
    graph_version: str = GRAPH_VERSION

    async def aclose(self) -> None:
        await self.store.close()
        if self.cache is not None:
            await self.cache.close()
        closer = getattr(self.llm_client, "aclose", None)
        if closer is not None:
            await closer()


def build_provider(settings: Settings) -> MarketDataProvider:
    # 真实数据源（akshare / 巨潮）作为可选扩展位；离线默认 fixture
    return FixtureProvider()


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.is_offline:
        return OfflineLLMClient()
    return OpenAICompatClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout_s=settings.llm_timeout_s,
    )


def build_runtime(settings: Settings | None = None) -> AppRuntime:
    settings = settings or get_settings()
    store = SqliteStore(settings.resolve(settings.db_path))
    bus = EventBus(store)
    provider = build_provider(settings)
    cache = NullCache() if settings.is_offline else DataCache(
        settings.resolve(settings.cache_dir) / "cache.db"
    )
    limiter = NullLimiter() if settings.is_offline else TokenBucket(settings.data_rate_limit_qps)
    llm_client = build_llm_client(settings)
    graph = build_graph()
    llm_semaphore = asyncio.Semaphore(max(1, settings.llm_max_concurrency))
    artifact_dir = settings.resolve(settings.artifact_dir)

    registry = ToolRegistry()

    async def resolve_tool(*, query: str) -> dict[str, Any] | None:
        key = cache_key("resolve", query)
        cached = await cache.get(key)
        if cached is not None:
            return cached
        await limiter.acquire()
        entity = await resolve_entity(provider, query)
        payload = entity.to_dict() if entity else None
        if payload is not None:
            await cache.set(key, payload, ttl_s=7 * 24 * 3600)
        return payload

    async def retrieve_tool(*, code: str, focus: str, as_of: str | None = None) -> dict[str, Any]:
        key = cache_key("retrieve", code, focus, as_of or "")
        cached = await cache.get(key)
        if cached is not None:
            return cached
        await limiter.acquire()
        payload = await retrieve_evidence(provider, code=code, focus=focus, as_of=as_of)
        await cache.set(key, payload, ttl_s=6 * 3600)
        return payload

    registry.register(
        "resolve_entity", resolve_tool, description="把用户问题解析成标的（名称/代码/行业）"
    )
    registry.register(
        "retrieve_evidence",
        retrieve_tool,
        description="按研究维度取数，返回证据、事实与数据缺口",
    )

    def services_factory(run_id: str, budget: BudgetGuard) -> Services:
        router = ModelRouter(
            client=llm_client,
            models=settings.models,
            budget=budget,
            max_tokens=2048,
        )

        async def complete_structured(
            *,
            role: str,
            messages: list[dict[str, str]],
            output_model: type[Any],
            node_id: str,
            temperature: float = 0.2,
        ) -> tuple[Any, dict[str, Any]]:
            async with llm_semaphore:
                return await call_structured(
                    router,
                    role=role,
                    messages=messages,
                    output_model=output_model,
                    node_id=node_id,
                    temperature=temperature,
                    max_repair=2,
                )

        async def emit(event_type: str, *, node_id: str | None = None, **payload: Any) -> Any:
            return await bus.publish(run_id, event_type, node_id=node_id, **payload)

        return Services(
            schema=graph.schema,
            complete_structured=complete_structured,
            invoke_tool=registry.invoke,
            emit=emit,
            budget=budget,
            cache=cache,
            settings=settings,
            extras={"artifact_dir": str(artifact_dir), "provider": provider.name},
        )

    def budget_factory() -> BudgetGuard:
        return BudgetGuard(
            BudgetConfig(
                max_cost_cny=settings.budget_max_cost_cny,
                max_tokens=settings.budget_max_tokens,
                max_calls=settings.budget_max_llm_calls,
            )
        )

    runner = GraphRunner(
        graph=graph,
        store=store,
        bus=bus,
        services_factory=services_factory,
        budget_factory=budget_factory,
        graph_version=GRAPH_VERSION,
        config_hash=settings.config_hash(),
    )

    return AppRuntime(
        settings=settings,
        store=store,
        bus=bus,
        provider=provider,
        cache=cache,
        tools=registry,
        graph=graph,
        llm_client=llm_client,
        runner=runner,
    )
