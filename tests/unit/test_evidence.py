from __future__ import annotations

from equitylens.tools.evidence import EvidenceStore, build_evidence, make_evidence_id


def test_evidence_id_is_stable_across_punctuation_and_space() -> None:
    first = make_evidence_id("https://a.example/x", "公司实现营收 1,741 亿元，同比增长 15.6%")
    second = make_evidence_id("https://a.example/x ", "公司实现营收1741亿元同比增长15.6%")
    assert first == second


def test_evidence_id_differs_by_url() -> None:
    assert make_evidence_id("https://a/1", "同样内容") != make_evidence_id("https://a/2", "同样内容")


def test_store_dedupes_same_content_from_different_channels() -> None:
    store = EvidenceStore()
    first = build_evidence(
        title="标题 A",
        snippet="同一条新闻内容",
        url="https://a.example/news",
        published_at="2026-09-01",
        tier=3,
        source_name="渠道 A",
    )
    second = build_evidence(
        title="标题 B",
        snippet="同一条新闻内容",
        url="https://a.example/news",
        published_at="2026-09-01",
        tier=3,
        source_name="渠道 B",
    )
    store.add(first)
    store.add(second)
    assert len(store) == 1
    assert store.all()[0].source_name == "渠道 A"


def test_store_sorts_by_tier() -> None:
    store = EvidenceStore()
    store.add(build_evidence(title="媒体", snippet="x", url="u1", published_at=None, tier=3, source_name="m"))
    store.add(build_evidence(title="公告", snippet="y", url="u2", published_at=None, tier=1, source_name="c"))
    assert [item.tier for item in store.all()] == [1, 3]


def test_refs_are_json_serializable() -> None:
    import json

    evidence = build_evidence(
        title="t", snippet="s", url="u", published_at="2026-01-01", tier=2, source_name="x"
    )
    assert json.loads(json.dumps(evidence.to_ref(), ensure_ascii=False))["id"] == evidence.id
