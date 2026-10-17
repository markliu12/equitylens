from __future__ import annotations

from equitylens.report.citations import audit_report


def _evidence(evidence_id: str, snippet: str, published: str = "2026-08-01", tier: int = 1) -> dict:
    return {
        "id": evidence_id,
        "snippet": snippet,
        "published_at": published,
        "tier": tier,
        "title": "t",
        "url": "u",
        "source_name": "s",
        "kind": "k",
    }


def _report(claims: list[dict], evidence: list[dict]) -> dict:
    return {
        "sections": [{"title": "S", "claims": claims}],
        "evidence": evidence,
    }


def test_full_coverage_and_quote_hit() -> None:
    snippet = "公司实现营业总收入 1,741.0 亿元，同比增长 15.6%。"
    evidence = [_evidence("ev_1", snippet)]
    report = _report(
        [{"text": "收入增长 15.6%", "evidence_ids": ["ev_1"], "strength": "事实", "quote": "同比增长 15.6%"}],
        evidence,
    )
    audit = audit_report(report, {"ev_1": evidence[0]}, "2026-09-30")
    assert audit.coverage == 1.0
    assert audit.quote_hit_rate == 1.0
    assert audit.to_dict()["passed"] is True


def test_missing_evidence_on_fact_claim_is_flagged() -> None:
    report = _report([{"text": "无依据的断言", "evidence_ids": [], "strength": "事实"}], [])
    audit = audit_report(report, {}, "2026-09-30")
    assert audit.coverage == 0.0
    assert any(issue.code == "missing_evidence" for issue in audit.issues)


def test_opinion_without_evidence_is_allowed() -> None:
    report = _report([{"text": "个人观点", "evidence_ids": [], "strength": "观点"}], [])
    audit = audit_report(report, {}, "2026-09-30")
    assert audit.issues == []
    assert audit.total_claims == 1


def test_unknown_evidence_id_is_flagged() -> None:
    report = _report([{"text": "x", "evidence_ids": ["ev_ghost"], "strength": "事实"}], [])
    audit = audit_report(report, {}, "2026-09-30")
    assert "ev_ghost" in audit.unknown_ids
    assert audit.coverage == 0.0


def test_quote_not_in_snippet_is_flagged() -> None:
    evidence = [_evidence("ev_1", "公告披露了分红方案。")]
    report = _report(
        [{"text": "x", "evidence_ids": ["ev_1"], "strength": "事实", "quote": "这句话原文里没有"}],
        evidence,
    )
    audit = audit_report(report, {"ev_1": evidence[0]}, "2026-09-30")
    assert audit.quote_hit_rate == 0.0
    assert any(issue.code == "quote_mismatch" for issue in audit.issues)


def test_future_leak_is_detected() -> None:
    evidence = [_evidence("ev_1", "未来的新闻", published="2026-10-15")]
    report = _report([{"text": "x", "evidence_ids": ["ev_1"], "strength": "事实"}], evidence)
    audit = audit_report(report, {"ev_1": evidence[0]}, "2026-09-30")
    assert "ev_1" in audit.future_leaks


def test_quote_matching_ignores_punctuation_and_space() -> None:
    evidence = [_evidence("ev_1", "实现营业总收入 1,741.0 亿元，同比增长 15.6%。")]
    report = _report(
        [
            {
                "text": "x",
                "evidence_ids": ["ev_1"],
                "strength": "事实",
                "quote": "实现营业总收入1741.0亿元 同比增长15.6%",
            }
        ],
        evidence,
    )
    audit = audit_report(report, {"ev_1": evidence[0]}, "2026-09-30")
    assert audit.quote_hit_rate == 1.0
