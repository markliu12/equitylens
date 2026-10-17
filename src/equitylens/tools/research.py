"""研究取数：把原始数据源组合成"证据 + 事实 + 结构化数据"。

两个关键点：
1. **时点过滤**：`published_at > as_of` 的证据直接丢弃。否则回看历史时会把"未来才知道
   的消息"喂给模型，研究报告看起来无比准确，实际是不可复现的前视偏差。
2. **缺口显式化**：取不到的数据写进 `gaps`，而不是悄悄省略。报告里"数据缺口"这一节
   就是从这里来的——宁可承认不知道，也不要编。
"""

from __future__ import annotations

from typing import Any

from equitylens.tools.evidence import build_evidence
from equitylens.tools.providers.base import EntityRef, MarketDataProvider

FOCUSES = ("fundamental", "industry", "sentiment")


def _visible(item: dict[str, Any], as_of: str | None) -> bool:
    published = str(item.get("published_at") or "")
    if not as_of or not published:
        return True
    return published <= as_of


def _evidence_from(
    item: dict[str, Any], *, kind: str, source_default: str, tier_default: int
) -> dict[str, Any]:
    evidence = build_evidence(
        title=str(item.get("title") or ""),
        snippet=str(item.get("snippet") or ""),
        url=str(item.get("url") or ""),
        published_at=item.get("published_at"),
        tier=int(item.get("tier") or tier_default),
        source_name=str(item.get("source") or source_default),
        kind=kind,
    )
    return evidence.to_ref()


async def retrieve_evidence(
    provider: MarketDataProvider,
    *,
    code: str,
    focus: str,
    as_of: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    gaps: list[str] = []
    payload: dict[str, Any] = {"focus": focus}

    if focus == "fundamental":
        financials = await provider.financials(code)
        items = [item for item in financials.get("items") or [] if _visible(item, as_of)]
        for item in items[:limit]:
            ref = _evidence_from(
                item, kind="financial", source_default="示例数据集 · 财务数据", tier_default=2
            )
            evidence.append(ref)
            facts.append(
                {
                    "name": item.get("name"),
                    "value": item.get("value"),
                    "period": item.get("period"),
                    "evidence_ids": [ref["id"]],
                }
            )
            metrics.append(
                {
                    "name": item.get("name"),
                    "value": item.get("value"),
                    "period": item.get("period"),
                    "evidence_ids": [ref["id"]],
                }
            )
        announcements = [
            item for item in await provider.announcements(code, limit=limit) if _visible(item, as_of)
        ]
        for item in announcements[:4]:
            evidence.append(
                _evidence_from(
                    item, kind="announcement", source_default="示例数据集 · 公司公告", tier_default=1
                )
            )
        if not items:
            gaps.append("未能获取到可核验的财务数据")
        payload.update(
            {
                "signals": financials.get("signals") or {},
                "metrics": metrics,
                "facts": facts,
                "announcement_count": len(announcements),
            }
        )

    elif focus == "industry":
        industry = await provider.industry(code)
        key_to_id: dict[str, str] = {}
        for item in industry.get("items") or []:
            if not _visible(item, as_of):
                continue
            ref = _evidence_from(
                item, kind="industry", source_default="示例数据集 · 行业数据", tier_default=3
            )
            evidence.append(ref)
            key_to_id[str(item.get("key") or "")] = ref["id"]
        for metric in industry.get("metrics") or []:
            evidence_id = key_to_id.get(str(metric.get("evidence_key") or ""))
            metrics.append(
                {
                    "name": metric.get("name"),
                    "value": metric.get("value"),
                    "period": metric.get("period"),
                    "evidence_ids": [evidence_id] if evidence_id else [],
                }
            )
        payload.update(
            {
                "industry": {
                    "name": industry.get("name"),
                    "cycle_stage": industry.get("cycle_stage"),
                    "summary": industry.get("summary"),
                    "peers": industry.get("peers") or [],
                    "evidence_ids": [e["id"] for e in evidence[:1]],
                },
                "metrics": metrics,
                "facts": [],
            }
        )
        if not evidence:
            gaps.append("行业数据缺失，景气判断已降级")

    elif focus == "sentiment":
        news = [item for item in await provider.news(code, limit=limit) if _visible(item, as_of)]
        news_brief: list[dict[str, Any]] = []
        for item in news[:limit]:
            ref = _evidence_from(
                item, kind="news", source_default="示例数据集 · 财经资讯", tier_default=3
            )
            evidence.append(ref)
            news_brief.append(
                {
                    "id": ref["id"],
                    "title": item.get("title"),
                    "snippet": item.get("snippet"),
                    "published_at": item.get("published_at"),
                    "sentiment": item.get("sentiment", 0.0),
                }
            )
        announcements = [
            item for item in await provider.announcements(code, limit=2) if _visible(item, as_of)
        ]
        for item in announcements:
            evidence.append(
                _evidence_from(
                    item, kind="announcement", source_default="示例数据集 · 公司公告", tier_default=1
                )
            )
        payload.update({"news": news_brief, "facts": [], "metrics": []})
        if len(news) < 3:
            gaps.append(f"新闻样本仅 {len(news)} 条，情绪结论可靠性有限")

    else:
        raise ValueError(f"未知的研究维度: {focus}")

    payload["evidence"] = evidence
    payload["gaps"] = gaps
    return payload


async def collect_all(
    provider: MarketDataProvider,
    *,
    code: str,
    as_of: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """三个维度全量取数，供 CLI / 评测 / 测试使用。"""
    merged: dict[str, Any] = {
        "evidence": [],
        "facts": [],
        "metrics": [],
        "gaps": [],
        "news": [],
        "signals": {},
        "industry": {},
    }
    seen: set[str] = set()
    for focus in FOCUSES:
        part = await retrieve_evidence(provider, code=code, focus=focus, as_of=as_of, limit=limit)
        for item in part.get("evidence") or []:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            merged["evidence"].append(item)
        merged["facts"].extend(part.get("facts") or [])
        merged["metrics"].extend(part.get("metrics") or [])
        merged["gaps"].extend(part.get("gaps") or [])
        merged["news"].extend(part.get("news") or [])
        if part.get("signals"):
            merged["signals"].update(part["signals"])
        if part.get("industry"):
            merged["industry"].update(part["industry"])
    return merged


async def resolve_entity(provider: MarketDataProvider, query: str) -> EntityRef | None:
    return await provider.resolve_entity(query)
