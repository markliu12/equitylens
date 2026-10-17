"""元信息接口：前端据此渲染能力、示例与配置摘要。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from equitylens.runtime import AppRuntime

router = APIRouter(tags=["meta"])

EXAMPLES = [
    {
        "question": "分析贵州茅台最近一期经营情况与行业景气度",
        "as_of": "2026-09-30",
        "tag": "公司研究",
    },
    {
        "question": "白酒行业的景气度与竞争格局如何？以贵州茅台为例",
        "as_of": "2026-09-30",
        "tag": "行业研究",
    },
    {
        "question": "贵州茅台近期有哪些重大公告与舆情事件值得关注？",
        "as_of": "2026-09-30",
        "tag": "事件驱动",
    },
]


def _runtime(request: Request) -> AppRuntime:
    return request.app.state.runtime


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    return {
        "ok": True,
        "mode": runtime.settings.app_mode,
        "models": runtime.settings.models,
        "provider": runtime.provider.name,
        "graph_version": runtime.graph_version,
    }


@router.get("/meta/models")
async def models(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    return {
        "models": runtime.settings.models,
        "offline": runtime.settings.is_offline,
        "config_hash": runtime.settings.config_hash(),
        "budget": {
            "max_cost_cny": runtime.settings.budget_max_cost_cny,
            "max_tokens": runtime.settings.budget_max_tokens,
            "max_llm_calls": runtime.settings.budget_max_llm_calls,
        },
        "demo_live_enabled": runtime.settings.demo_live_enabled,
        "settings": runtime.settings.redacted(),
    }


@router.get("/meta/graph")
async def graph(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    return {
        "version": runtime.graph_version,
        "nodes": [
            {
                "id": node.id,
                "kind": node.kind,
                "label": node.label,
                "deps": list(node.deps),
                "reads": list(node.reads),
                "writes": list(node.writes),
            }
            for node in runtime.graph.nodes.values()
        ],
        "order": list(runtime.graph.order),
    }


@router.get("/meta/examples")
async def examples() -> dict[str, Any]:
    return {"examples": EXAMPLES}


@router.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    cache_stats = await runtime.cache.stats() if runtime.cache is not None else {}
    return {
        "manager": _runtime(request) and request.app.state.manager.stats(),
        "cache": cache_stats,
        "mode": runtime.settings.app_mode,
    }
