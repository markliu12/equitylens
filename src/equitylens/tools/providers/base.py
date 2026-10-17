"""数据源协议。

抽象这一层的直接原因：akshare 的接口名一年能改三次，巨潮的字段口径也会变。
上层（分析师、证据链）只依赖这里的语义契约，换源时只需要新增一个实现 + 契约测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class EntityRef:
    name: str
    code: str
    industry: str = ""
    exchange: str = ""
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "code": self.code,
            "industry": self.industry,
            "exchange": self.exchange,
            "aliases": list(self.aliases),
        }


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str

    async def resolve_entity(self, query: str) -> EntityRef | None: ...

    async def financials(self, code: str) -> dict[str, Any]: ...

    async def industry(self, code: str) -> dict[str, Any]: ...

    async def announcements(self, code: str, limit: int = 10) -> list[dict[str, Any]]: ...

    async def news(self, code: str, limit: int = 10) -> list[dict[str, Any]]: ...
