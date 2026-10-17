"""运行相关接口。

设计上把"查询"和"指令"分开：
- 查询走快照（state 里的 final_report / node statuses），一次读到底，无副作用；
- 指令（创建/取消）只操作 RunManager，不直接碰图。
这样即使未来把执行挪到独立进程，查询接口也几乎不用改。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from equitylens.api.manager import RunManager
from equitylens.api.sse import event_stream
from equitylens.engine.types import RunStatus
from equitylens.report.renderer import to_html, to_markdown
from equitylens.runtime import AppRuntime

router = APIRouter(prefix="/runs", tags=["runs"])


class CreateRunRequest(BaseModel):
    question: str = Field(min_length=2, max_length=200)
    as_of: str = Field(default="", max_length=10)


def _runtime(request: Request) -> AppRuntime:
    return request.app.state.runtime


def _manager(request: Request) -> RunManager:
    return request.app.state.manager


async def _snapshot(request: Request, run_id: str) -> dict[str, Any] | None:
    snapshot = await _runtime(request).store.load_latest(run_id)
    return snapshot.state if snapshot else None


@router.post("", status_code=201)
async def create_run(
    payload: CreateRunRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    demo_invite_code: str | None = Header(default=None, alias="X-Demo-Invite-Code"),
) -> JSONResponse:
    settings = _runtime(request).settings
    if settings.app_mode == "demo":
        if not settings.demo_live_enabled:
            raise HTTPException(
                status_code=403,
                detail="演示环境已关闭实时调用，可在首页查看预生成报告",
            )
        if not settings.invite_codes:
            raise HTTPException(status_code=503, detail="演示环境未配置邀请码")
        if demo_invite_code not in settings.invite_codes:
            raise HTTPException(status_code=403, detail="邀请码无效")

    cache: dict[str, str] = request.app.state.idempotency
    if idempotency_key and idempotency_key in cache:
        run_id = cache[idempotency_key]
        return JSONResponse(
            {"run_id": run_id, "status": "QUEUED", "stream_url": f"/api/runs/{run_id}/events"},
            status_code=200,
        )

    result = await _manager(request).start(payload.question, as_of=payload.as_of)
    if result.get("error") == "daily_budget_exhausted":
        raise HTTPException(status_code=429, detail="今日演示预算已用尽，请明日再试")
    if idempotency_key:
        cache[idempotency_key] = result["run_id"]
    return JSONResponse(result, status_code=201)


@router.get("")
async def list_runs(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    runs = await _runtime(request).store.list_runs(limit=limit)
    return {"runs": runs, "count": len(runs)}


@router.get("/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    record = await runtime.store.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run 不存在")

    snapshot = await runtime.store.load_latest(run_id)
    state = snapshot.state if snapshot else {}
    executions = await runtime.store.node_records(run_id)
    report = state.get("final_report") or state.get("draft_report") or {}
    return {
        "run": record,
        "node_statuses": (snapshot.statuses if snapshot else {}),
        "executions": executions,
        "report": report,
        "audit": state.get("citation_audit") or {},
        "verdict": state.get("verdict") or {},
        "debate": state.get("debate_rounds") or [],
        "arguments": {
            "bull": state.get("bull_argument") or {},
            "bear": state.get("bear_argument") or {},
        },
        "analyst_reports": [
            {
                "role": item.get("role"),
                "stance": item.get("stance"),
                "confidence": item.get("confidence"),
                "claims": item.get("claims") or [],
                "gaps": item.get("gaps") or [],
            }
            for item in state.get("analyst_reports") or []
        ],
        "evidence_summary": state.get("evidence_summary") or {},
        "risk_review": state.get("risk_review") or {},
        "is_running": _manager(request).is_running(run_id),
        "graph": [
            {"id": node.id, "kind": node.kind, "label": node.label, "deps": list(node.deps)}
            for node in runtime.graph.nodes.values()
        ],
    }


@router.get("/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after: int = Query(default=0, ge=0),
) -> Any:
    from fastapi.responses import StreamingResponse

    runtime = _runtime(request)
    if await runtime.store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    cursor = after
    if last_event_id:
        try:
            cursor = max(cursor, int(last_event_id))
        except ValueError:
            cursor = after
    return StreamingResponse(
        event_stream(run_id, runtime.bus, runtime.store, last_event_id=cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 让 nginx/Caddy 不要缓冲，否则前端会"卡住"
        },
    )


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    result = await _manager(request).cancel(run_id)
    if not result.get("ok"):
        reason = result.get("reason")
        status = 404 if reason == "not_found" else 409
        raise HTTPException(status_code=status, detail=reason)
    return result


@router.get("/{run_id}/trace")
async def get_trace(run_id: str, request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    if await runtime.store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return {
        "executions": await runtime.store.node_records(run_id),
        "events": await runtime.store.list_events(run_id, limit=500),
    }


@router.get("/{run_id}/evidence")
async def get_evidence(run_id: str, request: Request) -> dict[str, Any]:
    state = await _snapshot(request, run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run 不存在或尚无快照")
    return {
        "evidence": state.get("evidence") or [],
        "gaps": state.get("evidence_gaps") or [],
        "summary": state.get("evidence_summary") or {},
        "audit": state.get("citation_audit") or {},
    }


@router.get("/{run_id}/report.md", response_class=PlainTextResponse)
async def get_report_markdown(run_id: str, request: Request) -> PlainTextResponse:
    state = await _snapshot(request, run_id)
    report = (state or {}).get("final_report") or {}
    if not report:
        raise HTTPException(status_code=404, detail="报告尚未生成")
    return PlainTextResponse(
        to_markdown(report, state.get("citation_audit")),
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/{run_id}/report.html", response_class=HTMLResponse)
async def get_report_html(run_id: str, request: Request) -> HTMLResponse:
    state = await _snapshot(request, run_id)
    report = (state or {}).get("final_report") or {}
    if not report:
        raise HTTPException(status_code=404, detail="报告尚未生成")
    return HTMLResponse(to_html(report, state.get("citation_audit")))


@router.get("/{run_id}/status")
async def get_status(run_id: str, request: Request) -> dict[str, Any]:
    record = await _runtime(request).store.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return {
        "run_id": run_id,
        "status": record["status"],
        "running": _manager(request).is_running(run_id),
        "terminal": record["status"]
        in {
            str(RunStatus.COMPLETED),
            str(RunStatus.PARTIAL),
            str(RunStatus.FAILED),
            str(RunStatus.CANCELLED),
        },
    }
