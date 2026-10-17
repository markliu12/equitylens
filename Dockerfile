# ---- Stage 1: build frontend ----
FROM node:22-slim AS web-builder
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="EquityLens"
LABEL org.opencontainers.image.description="Multi-agent equity research workbench"
LABEL org.opencontainers.image.source="https://github.com/markliu12/equitylens"
LABEL org.opencontainers.image.license="MIT"

WORKDIR /app

# 系统依赖最小化：sqlite3 仅用于排障（容器内查 DB）
RUN apt-get update && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用 Docker layer 缓存）
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -e ".[dev]"

# 再拷源码和构建产物
COPY src/ ./src/
COPY --from=web-builder /web/dist ./web/dist

# 运行时数据卷
VOLUME ["/app/data"]
ENV DB_PATH=data/equitylens.db \
    CACHE_DIR=data/cache \
    ARTIFACT_DIR=data/artifacts \
    APP_MODE=offline

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/healthz')" || exit 1

CMD ["uvicorn", "equitylens.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
