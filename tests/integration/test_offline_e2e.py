from __future__ import annotations

from pathlib import Path

import pytest

from equitylens.agents.schemas import ResearchReport
from equitylens.config import Settings
from equitylens.engine.types import RunStatus
from equitylens.runtime import build_runtime


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_mode="offline",
        db_path=str(tmp_path / "test.db"),
        cache_dir=str(tmp_path / "cache"),
        artifact_dir=str(tmp_path / "artifacts"),
    )


@pytest.mark.asyncio
async def test_offline_run_produces_auditable_report(settings: Settings, tmp_path: Path) -> None:
    """整个项目的生死线：离线状态下能产出一份通过 schema 校验、引用可核验的报告。"""
    runtime = build_runtime(settings)
    try:
        outcome = await runtime.runner.run(
            "run_e2e",
            question="分析贵州茅台最近一期经营情况与行业景气度",
            as_of="2026-09-30",
        )
    finally:
        await runtime.aclose()

    assert outcome.status in {RunStatus.COMPLETED, RunStatus.PARTIAL}

    state = outcome.state
    report = state["final_report"]
    parsed = ResearchReport.model_validate(report)
    assert parsed.sections, "报告至少要有一个章节"
    assert parsed.entity_name == "贵州茅台"
    assert parsed.disclaimer

    audit = state["citation_audit"]
    assert audit["coverage"] >= 0.9, f"引用覆盖率不足: {audit}"
    assert audit["quote_hit_rate"] >= 0.95, f"引用命中率不足: {audit}"
    assert audit["future_leaks"] == []
    assert audit["passed"] is True

    # 每条"事实"型结论都必须挂证据
    for section in parsed.sections:
        for claim in section.claims:
            if claim.strength.value == "事实":
                assert claim.evidence_ids, f"事实型结论缺少引用: {claim.text}"

    # 三个分析维度都要参与
    roles = {item.get("role") for item in state["analyst_reports"]}
    assert roles == {"fundamental", "industry", "sentiment"}

    # 辩论有界
    rounds = state["debate_rounds"]
    assert 1 <= len(rounds) <= 3

    # 产物落盘
    artifacts = tmp_path / "artifacts" / "run_e2e"
    assert (artifacts / "report.md").read_text(encoding="utf-8").startswith("# ")
    assert (artifacts / "report.html").exists()
    assert (artifacts / "citation_audit.json").exists()


@pytest.mark.asyncio
async def test_all_nodes_succeed_in_offline_mode(settings: Settings) -> None:
    runtime = build_runtime(settings)
    try:
        outcome = await runtime.runner.run("run_nodes", question="分析贵州茅台", as_of="2026-09-30")
    finally:
        await runtime.aclose()
    assert all(status == "SUCCEEDED" for status in outcome.node_statuses.values()), outcome.node_statuses


@pytest.mark.asyncio
async def test_report_evidence_covers_all_references(settings: Settings) -> None:
    runtime = build_runtime(settings)
    try:
        outcome = await runtime.runner.run("run_refs", question="分析贵州茅台", as_of="2026-09-30")
    finally:
        await runtime.aclose()
    report = outcome.state["final_report"]
    evidence_ids = {item["id"] for item in report["evidence"]}
    for section in report["sections"]:
        for claim in section["claims"]:
            for evidence_id in claim["evidence_ids"]:
                assert evidence_id in evidence_ids, f"报告引用了未附带的证据: {evidence_id}"


@pytest.mark.asyncio
async def test_events_are_recorded_with_monotonic_seq(settings: Settings) -> None:
    runtime = build_runtime(settings)
    try:
        await runtime.runner.run("run_events", question="分析贵州茅台", as_of="2026-09-30")
        events = await runtime.store.list_events("run_events")
    finally:
        await runtime.aclose()
    seqs = [event["seq"] for event in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    types = {event["type"] for event in events}
    assert "run.started" in types
    assert "node.completed" in types
    assert "report.ready" in types
