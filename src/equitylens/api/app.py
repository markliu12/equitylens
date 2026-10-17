"""FastAPI 应用工厂。

用工厂函数（而不是模块级 app）是为了让测试可以注入独立配置与临时数据库；
部署时用 `uvicorn equitylens.api.app:create_app --factory` 即可。
"""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from equitylens.api.manager import RunManager
from equitylens.api.routes import meta, runs
from equitylens.config import Settings, get_settings
from equitylens.runtime import build_runtime

PLACEHOLDER = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>EquityLens 研镜</title></head>
<body style="font-family:system-ui;max-width:720px;margin:80px auto;line-height:1.8">
<h1>EquityLens 研镜</h1>
<p>前端尚未构建。请在 <code>web/</code> 目录执行：</p>
<pre style="background:#f5f6f8;padding:14px;border-radius:8px">npm install
npm run build</pre>
<p>然后刷新本页；或直接用 API：<code>GET /api/meta/graph</code>、<code>POST /api/runs</code>。</p>
</body></html>"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    runtime = build_runtime(settings)
    manager = RunManager(runtime)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 进程重启后自动续跑，已完成节点不会重复调用模型（也就不会重复扣费）
        with suppress(Exception):
            await manager.recover_on_startup()
        yield
        for run_id in list(manager.tasks):
            task = manager.tasks[run_id]
            task.cancel()
        await runtime.aclose()

    app = FastAPI(
        title="EquityLens 研镜",
        description="可审计的多智能体行业研究工作台（手写 DAG 编排引擎）",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.state.manager = manager
    app.state.idempotency = {}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(runs.router, prefix="/api")
    app.include_router(meta.router, prefix="/api")

    dist = settings.resolve("web/dist")
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")
    else:

        @app.get("/", response_class=HTMLResponse)
        async def placeholder() -> HTMLResponse:
            return HTMLResponse(PLACEHOLDER)

    return app
