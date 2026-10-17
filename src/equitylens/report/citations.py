"""引用审计：把"看起来有引用"变成"真的能对上原文"。

三级校验，全部是确定性代码，不依赖模型：
1. 有结论无引用（且声明为"事实"）→ 记为无证据断言；
2. 引用 id 不在本次运行的证据集合里 → 记为悬空引用；
3. `quote` 归一化后必须能在证据原文里找到 → 记为引用不符；
额外做时点检查：证据发布时间晚于报告基准日 → 前视偏差（未来信息泄漏）。

报告里"引用覆盖率 ≥ 0.9、引用命中率 ≥ 0.95"的验收标准就来自这里，
所以它不是装饰性的 dashboard，而是可以卡住 CI 的门禁。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from equitylens.tools.evidence import normalize_text


@dataclass(frozen=True)
class CitationIssue:
    code: str
    claim: str
    detail: str


@dataclass
class CitationAudit:
    total_claims: int = 0
    grounded_claims: int = 0
    quotes_checked: int = 0
    quotes_hit: int = 0
    unknown_ids: list[str] = field(default_factory=list)
    future_leaks: list[str] = field(default_factory=list)
    issues: list[CitationIssue] = field(default_factory=list)
    by_tier: dict[str, int] = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        return round(self.grounded_claims / self.total_claims, 4) if self.total_claims else 0.0

    @property
    def quote_hit_rate(self) -> float:
        return round(self.quotes_hit / self.quotes_checked, 4) if self.quotes_checked else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_claims": self.total_claims,
            "grounded_claims": self.grounded_claims,
            "coverage": self.coverage,
            "quotes_checked": self.quotes_checked,
            "quotes_hit": self.quotes_hit,
            "quote_hit_rate": self.quote_hit_rate,
            "unknown_ids": self.unknown_ids,
            "future_leaks": self.future_leaks,
            "by_tier": self.by_tier,
            "issues": [
                {"code": item.code, "claim": item.claim, "detail": item.detail}
                for item in self.issues
            ],
            "passed": self.coverage >= 0.9 and self.quote_hit_rate >= 0.95,
        }


def audit_report(
    report: dict[str, Any],
    evidence_index: dict[str, dict[str, Any]],
    as_of: str | None = None,
) -> CitationAudit:
    audit = CitationAudit()
    tier_counts: dict[str, int] = {}

    sections = report.get("sections") or []
    for section in sections:
        for claim in section.get("claims") or []:
            audit.total_claims += 1
            text = str(claim.get("text") or "")
            evidence_ids = [str(i) for i in claim.get("evidence_ids") or []]
            strength = str(claim.get("strength") or "事实")

            valid_ids: list[str] = []
            for evidence_id in evidence_ids:
                evidence = evidence_index.get(evidence_id)
                if evidence is None:
                    audit.unknown_ids.append(evidence_id)
                    audit.issues.append(
                        CitationIssue("unknown_evidence", text[:60], f"引用不存在: {evidence_id}")
                    )
                    continue
                valid_ids.append(evidence_id)
                tier = str(evidence.get("tier") or 4)
                tier_counts[tier] = tier_counts.get(tier, 0) + 1
                published = str(evidence.get("published_at") or "")
                if as_of and published and published > as_of:
                    audit.future_leaks.append(evidence_id)
                    audit.issues.append(
                        CitationIssue(
                            "future_leak",
                            text[:60],
                            f"证据 {evidence_id} 发布于 {published}，晚于基准日 {as_of}",
                        )
                    )

            if not evidence_ids:
                if strength == "事实":
                    audit.issues.append(
                        CitationIssue("missing_evidence", text[:60], "事实性结论缺少证据引用")
                    )
                continue

            if not valid_ids:
                continue
            audit.grounded_claims += 1

            quote = claim.get("quote")
            if quote:
                audit.quotes_checked += 1
                needle = normalize_text(str(quote))
                hit = any(
                    needle and needle in normalize_text(str(evidence_index[i].get("snippet") or ""))
                    for i in valid_ids
                )
                if hit:
                    audit.quotes_hit += 1
                else:
                    audit.issues.append(
                        CitationIssue(
                            "quote_mismatch",
                            text[:60],
                            f"引用片段未在证据原文中找到: {str(quote)[:40]}",
                        )
                    )

    audit.by_tier = tier_counts
    return audit


def unsupported_claims(report: dict[str, Any]) -> list[str]:
    return [
        str(claim.get("text") or "")
        for section in report.get("sections") or []
        for claim in section.get("claims") or []
        if not claim.get("evidence_ids") and str(claim.get("strength")) == "事实"
    ]
