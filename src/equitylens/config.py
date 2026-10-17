"""运行配置。

三档 APP_MODE 是刻意的分层：
- offline：fixture 数据 + 确定性模型，零外网零费用（CI / 断网演示）
- dev：真实模型；数据源由 Provider 决定（当前仅内置 fixture）
- demo：默认关闭实时调用，开启后用邀请码与预算护住额度
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_mode: Literal["offline", "dev", "demo"] = "offline"

    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_fallback_models: str = ""
    llm_timeout_s: float = 90.0
    llm_max_concurrency: int = 4

    budget_max_cost_cny: float = 1.2
    budget_max_tokens: int = 180_000
    budget_max_llm_calls: int = 60
    daily_budget_cny: float = 10.0

    db_path: str = "data/equitylens.db"
    cache_dir: str = "data/cache"
    artifact_dir: str = "data/artifacts"

    data_provider: str = "fixture"
    data_rate_limit_qps: float = 1.0

    demo_live_enabled: bool = False
    demo_invite_codes: str = ""

    max_parallel_runs: int = 2

    @property
    def is_offline(self) -> bool:
        return self.app_mode == "offline"

    @property
    def models(self) -> list[str]:
        chain = [self.llm_model]
        chain.extend(
            item.strip() for item in self.llm_fallback_models.split(",") if item.strip()
        )
        if self.is_offline:
            return ["offline"]
        return list(dict.fromkeys(chain))

    def resolve(self, relative: str) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def invite_codes(self) -> set[str]:
        return {item.strip() for item in self.demo_invite_codes.split(",") if item.strip()}

    def config_hash(self) -> str:
        """A/B 归因用：模型链 + 预算 + 模式，任何一项变动都改变指纹。"""
        raw = "|".join(
            [*self.models, str(self.budget_max_cost_cny), str(self.budget_max_tokens), self.app_mode]
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]

    def redacted(self) -> dict[str, object]:
        key = self.llm_api_key or ""
        return {
            "app_mode": self.app_mode,
            "models": self.models,
            "llm_base_url": self.llm_base_url,
            "llm_api_key": f"{key[:4]}***{key[-2:]}" if len(key) > 8 else ("未配置" if not key else "***"),
            "budget": {
                "max_cost_cny": self.budget_max_cost_cny,
                "max_tokens": self.budget_max_tokens,
                "max_llm_calls": self.budget_max_llm_calls,
            },
            "provider": self.data_provider,
            "demo_live_enabled": self.demo_live_enabled,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
