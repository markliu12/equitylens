"""数据缓存：同一标的同一天的研究，不应该把外部接口重复打一遍。

TTL 按数据性质分级（行情 1 天 / 财报 30 天 / 新闻 6 小时），因为这些数据的"新鲜度"
语义本来就不同——统一 1 小时缓存会让财报数据反复重抓，统一 30 天又会让新闻过时。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

TTL_PROFILE = {
    "financials": 30 * 24 * 3600,
    "announcements": 24 * 3600,
    "industry": 24 * 3600,
    "news": 6 * 3600,
    "resolve": 7 * 24 * 3600,
}


class NullCache:
    async def get(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any, ttl_s: int = 3600) -> None:
        return None

    async def close(self) -> None:
        return None

    async def stats(self) -> dict[str, Any]:
        return {"hits": 0, "misses": 0}


class DataCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Any = None
        self._hits = 0
        self._misses = 0

    async def _connection(self) -> Any:
        if self._conn is None:
            import aiosqlite

            self._conn = await aiosqlite.connect(self.path)
            await self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            await self._conn.commit()
        return self._conn

    async def get(self, key: str) -> Any | None:
        conn = await self._connection()
        cursor = await conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None or row[1] < time.time():
            self._misses += 1
            return None
        self._hits += 1
        return json.loads(row[0])

    async def set(self, key: str, value: Any, ttl_s: int = 3600) -> None:
        conn = await self._connection()
        await conn.execute(
            "INSERT OR REPLACE INTO cache(key, value, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), time.time() + ttl_s),
        )
        await conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
        }


def cache_key(*parts: Any) -> str:
    import hashlib

    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
