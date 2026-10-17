# 部署说明（无 Docker）

单进程 + 单端口：FastAPI 同时托管 API 与前端静态产物（`web/dist`）。
这样做的好处是没有 CORS、没有 Node 运行时、没有容器层，出问题只需看一个日志。

## 1. 本地（Windows / macOS）

```bash
python -m venv .venv && .venv\Scripts\activate      # macOS: source .venv/bin/activate
pip install -e ".[dev]"
cd web && npm install && npm run build && cd ..
uvicorn equitylens.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

开发前端时用 Vite 热更新（已配好 `/api` 代理）：

```bash
cd web && npm run dev     # http://localhost:5173
```

## 2. 服务器（Ubuntu 24.04，2C2G 足够）

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv caddy
sudo useradd -r -s /bin/false equitylens
sudo mkdir -p /opt/equitylens /var/lib/equitylens
sudo chown -R equitylens:equitylens /opt/equitylens /var/lib/equitylens

# 上传代码（前端产物请在本地构建后一并上传，服务器不必装 Node）
cd /opt/equitylens
python3.12 -m venv .venv
.venv/bin/pip install -e .

sudo cp deploy/equitylens.service /etc/systemd/system/
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo cp .env.example /etc/equitylens.env    # 再编辑：APP_MODE / LLM_API_KEY / DB_PATH
sudo systemctl daemon-reload && sudo systemctl enable --now equitylens
sudo systemctl reload caddy
```

## 3. 三个必须注意的运维点

1. **单 worker**：`RunManager` 的任务表在内存，`--workers 4` 会让状态分裂。要横向扩展，
   先按 `docs/architecture.md` 第 7 节把任务提交抽成 `JobQueue`。
2. **SSE 不能被反代缓冲**：Caddy 需要 `flush_interval -1`，nginx 需要 `X-Accel-Buffering: no`
   （响应头里已经带了）。否则前端会"卡住不动"。
3. **数据放在 `/var/lib/equitylens`**：SQLite 用 WAL 模式，备份要带上 `-wal`/`-shm` 或直接
   `sqlite3 xxx.db ".backup backup.db"`；`artifacts/` 目录里是报告与审计 JSON，也要备份。

## 4. 成本防护清单（公开 Demo 必做）

| 层 | 做法 | 环境变量 |
|---|---|---|
| 1 | 默认只读回放，匿名用户零调用 | `APP_MODE=demo` |
| 2 | 实时调用需邀请码（请求头 `X-Demo-Invite-Code`） | `DEMO_LIVE_ENABLED=true` + `DEMO_INVITE_CODES` |
| 3 | 单次运行预算硬上限 | `BUDGET_MAX_COST_CNY=1.2` |
| 4 | 单日总预算（按每次运行最大成本预留，避免并发绕过） | `DAILY_BUDGET_CNY=10` |
| 5 | 一键止血 | `DEMO_LIVE_ENABLED=false` + 重启 |

> 关键原则：预算检查发生在**调用之前**（`BudgetGuard.check`），而不是事后统计。

开启实时 Demo 时，先在 `/etc/equitylens.env` 设置一个不公开的邀请码：

```bash
APP_MODE=demo
DEMO_LIVE_ENABLED=true
DEMO_INVITE_CODES=replace-with-a-long-random-code
```

调用接口时带上该邀请码：

```bash
curl -X POST https://example.com/api/runs \
  -H "Content-Type: application/json" \
  -H "X-Demo-Invite-Code: replace-with-a-long-random-code" \
  -d '{"question":"分析贵州茅台"}'
```

## 5. 健康检查

```bash
curl -s http://127.0.0.1:8000/api/healthz
# {"ok":true,"mode":"offline","models":["offline"],"provider":"fixture","graph_version":"v1"}
```

`/api/metrics` 会返回活跃任务数与缓存命中率；`/docs` 是自动生成的 OpenAPI 文档。
