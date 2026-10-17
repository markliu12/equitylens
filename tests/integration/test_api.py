from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from equitylens.api.app import create_app
from equitylens.config import Settings


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        app_mode="offline",
        db_path=str(tmp_path / "api.db"),
        cache_dir=str(tmp_path / "cache"),
        artifact_dir=str(tmp_path / "artifacts"),
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def _wait(client: TestClient, run_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/runs/{run_id}/status").json()
        if status["terminal"]:
            return status
        time.sleep(0.1)
    raise AssertionError("运行超时")


def test_healthz(client: TestClient) -> None:
    payload = client.get("/api/healthz").json()
    assert payload["ok"] is True
    assert payload["mode"] == "offline"
    assert payload["graph_version"] == "v1"


def test_create_run_and_fetch_detail(client: TestClient) -> None:
    created = client.post("/api/runs", json={"question": "分析贵州茅台", "as_of": "2026-09-30"})
    assert created.status_code == 201
    run_id = created.json()["run_id"]

    _wait(client, run_id)
    detail = client.get(f"/api/runs/{run_id}").json()

    assert detail["run"]["status"] in {"COMPLETED", "PARTIAL"}
    assert detail["report"]["sections"]
    assert detail["audit"]["coverage"] >= 0.9
    assert len(detail["graph"]) == 13
    assert detail["analyst_reports"]
    assert detail["debate"]
    assert set(detail["arguments"]) == {"bull", "bear"}


def test_idempotency_key_returns_same_run(client: TestClient) -> None:
    headers = {"Idempotency-Key": "abc-123"}
    first = client.post("/api/runs", json={"question": "分析贵州茅台"}, headers=headers)
    second = client.post("/api/runs", json={"question": "分析贵州茅台"}, headers=headers)
    assert first.json()["run_id"] == second.json()["run_id"]


def test_validation_rejects_short_question(client: TestClient) -> None:
    assert client.post("/api/runs", json={"question": "x"}).status_code == 422


def test_demo_live_run_requires_valid_invite(tmp_path: Path) -> None:
    settings = Settings(
        app_mode="demo",
        demo_live_enabled=True,
        demo_invite_codes="portfolio-demo",
        db_path=str(tmp_path / "api.db"),
        cache_dir=str(tmp_path / "cache"),
        artifact_dir=str(tmp_path / "artifacts"),
    )
    with TestClient(create_app(settings)) as demo_client:
        payload = {"question": "分析贵州茅台"}
        assert demo_client.post("/api/runs", json=payload).status_code == 403
        assert (
            demo_client.post(
                "/api/runs", json=payload, headers={"X-Demo-Invite-Code": "wrong"}
            ).status_code
            == 403
        )


def test_demo_rejects_run_that_cannot_fit_daily_budget(tmp_path: Path) -> None:
    settings = Settings(
        app_mode="demo",
        demo_live_enabled=True,
        demo_invite_codes="portfolio-demo",
        daily_budget_cny=0.5,
        budget_max_cost_cny=0.6,
        db_path=str(tmp_path / "api.db"),
        cache_dir=str(tmp_path / "cache"),
        artifact_dir=str(tmp_path / "artifacts"),
    )
    with TestClient(create_app(settings)) as demo_client:
        response = demo_client.post(
            "/api/runs",
            json={"question": "分析贵州茅台"},
            headers={"X-Demo-Invite-Code": "portfolio-demo"},
        )
    assert response.status_code == 429


def test_unknown_run_returns_404(client: TestClient) -> None:
    assert client.get("/api/runs/run_missing").status_code == 404
    assert client.get("/api/runs/run_missing/events").status_code == 404


def test_report_downloads(client: TestClient) -> None:
    run_id = client.post("/api/runs", json={"question": "分析贵州茅台"}).json()["run_id"]
    _wait(client, run_id)
    markdown = client.get(f"/api/runs/{run_id}/report.md")
    assert markdown.status_code == 200
    assert "参考来源" in markdown.text
    html = client.get(f"/api/runs/{run_id}/report.html")
    assert html.status_code == 200
    assert "免责" in html.text or "不构成" in html.text


def test_evidence_endpoint(client: TestClient) -> None:
    run_id = client.post("/api/runs", json={"question": "分析贵州茅台"}).json()["run_id"]
    _wait(client, run_id)
    payload = client.get(f"/api/runs/{run_id}/evidence").json()
    assert payload["evidence"]
    assert payload["audit"]["passed"] is True


def test_sse_replay_returns_events(client: TestClient) -> None:
    run_id = client.post("/api/runs", json={"question": "分析贵州茅台"}).json()["run_id"]
    _wait(client, run_id)
    response = client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "0"})
    assert response.status_code == 200
    body = response.text
    assert "event: run.started" in body
    assert "event: report.ready" in body
    # 续传：跳过前面的事件
    tail = client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "9999"})
    assert tail.status_code == 200


def test_meta_endpoints(client: TestClient) -> None:
    models = client.get("/api/meta/models").json()
    assert models["offline"] is True
    graph = client.get("/api/meta/graph").json()
    assert len(graph["nodes"]) == 13
    assert client.get("/api/meta/examples").json()["examples"]
