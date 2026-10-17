"""离线 fixture 数据源。

为什么它是"一等公民"而不是测试桩：
- CI 不能依赖外网；
- 演示不能赌现场网络；
- 真实数据源抖动时，它是最后一道降级（宁可报告标注"示例数据"，也不要整个功能不可用）。
"""

from __future__ import annotations

import copy
from typing import Any

from equitylens.fixtures.seeds.data import DEFAULT_CODE, SEEDS
from equitylens.tools.providers.base import EntityRef


class FixtureProvider:
    name = "fixture"

    def __init__(self, seeds: dict[str, dict] | None = None, default_code: str = DEFAULT_CODE):
        self._seeds = copy.deepcopy(seeds or SEEDS)
        self._default = default_code

    # ------------------------------------------------------------------ #
    def _seed(self, code: str) -> dict[str, Any]:
        return self._seeds.get(code) or self._seeds[self._default]

    async def resolve_entity(self, query: str) -> EntityRef | None:
        text = (query or "").strip()
        for code, seed in self._seeds.items():
            entity = seed["entity"]
            if code in text or entity["name"] in text:
                return EntityRef(
                    name=entity["name"],
                    code=entity["code"],
                    industry=entity.get("industry", ""),
                    exchange=entity.get("exchange", ""),
                    aliases=(entity["name"], code),
                )
        if not text:
            return None
        seed = self._seed(self._default)
        entity = seed["entity"]
        return EntityRef(
            name=entity["name"],
            code=entity["code"],
            industry=entity.get("industry", ""),
            exchange=entity.get("exchange", ""),
            aliases=(entity["name"], entity["code"]),
        )

    async def financials(self, code: str) -> dict[str, Any]:
        return copy.deepcopy(self._seed(code)["financials"])

    async def industry(self, code: str) -> dict[str, Any]:
        return copy.deepcopy(self._seed(code)["industry"])

    async def announcements(self, code: str, limit: int = 10) -> list[dict[str, Any]]:
        return copy.deepcopy(self._seed(code)["announcements"][:limit])

    async def news(self, code: str, limit: int = 10) -> list[dict[str, Any]]:
        return copy.deepcopy(self._seed(code)["news"][:limit])
