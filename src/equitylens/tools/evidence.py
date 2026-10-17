"""证据链：把"一句话结论"钉到"一段可核验的原文"上。

`evidence_id` 用 (规范化 url + 规范化片段) 做哈希，产生两个好处：
1. 同一条新闻从两个渠道抓到，id 相同 → 自动去重，不会在报告里挂两遍；
2. id 稳定 → 报告里的引用可以在重启、换机器后仍然对得上，审计才有意义。

tier 是证据强度分级（1 最强）：交易所/公司公告 > 财报与行情数据 > 主流财经媒体 > 其他。
它不只用于展示，还参与辩论打分的 `evidence` 维度，避免"媒体报道"压过"公司公告"。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[，。；：、“”‘’（）《》！？,.;:!?\"'()\[\]·—\-…]+")


def normalize_text(text: str) -> str:
    return _PUNCT.sub("", _SPACE.sub("", text or "")).lower()


def make_evidence_id(url: str, snippet: str) -> str:
    payload = f"{(url or '').strip().lower()}|{normalize_text(snippet)}"
    return "ev_" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


TIER_LABELS = {
    1: "公司公告",
    2: "财报/行情",
    3: "财经媒体",
    4: "其他来源",
}


@dataclass(frozen=True)
class Evidence:
    id: str
    source_name: str
    tier: int
    title: str
    url: str
    published_at: str | None
    snippet: str
    kind: str = "other"

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.tier, "其他来源")

    def to_ref(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceStore:
    """一次运行内的证据集合。key 是 id，天然去重。"""

    _items: dict[str, Evidence] = field(default_factory=dict)

    def add(self, evidence: Evidence) -> Evidence:
        self._items.setdefault(evidence.id, evidence)
        return evidence

    def add_many(self, items: list[Evidence]) -> None:
        for item in items:
            self.add(item)

    def get(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def __contains__(self, evidence_id: object) -> bool:
        return evidence_id in self._items

    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> list[Evidence]:
        return sorted(
            self._items.values(),
            key=lambda item: (item.tier, -(len(item.published_at or ""))),
        )

    def refs(self) -> list[dict[str, Any]]:
        return [item.to_ref() for item in self.all()]

    def ids(self) -> set[str]:
        return set(self._items)

    def subset(self, evidence_ids: list[str]) -> list[Evidence]:
        return [self._items[i] for i in evidence_ids if i in self._items]


def build_evidence(
    *,
    title: str,
    snippet: str,
    url: str,
    published_at: str | None,
    tier: int,
    source_name: str,
    kind: str = "other",
) -> Evidence:
    return Evidence(
        id=make_evidence_id(url, snippet),
        source_name=source_name,
        tier=tier,
        title=title,
        url=url,
        published_at=published_at,
        snippet=snippet,
        kind=kind,
    )
