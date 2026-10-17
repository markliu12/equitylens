"""AkshareProvider 契约测试。

设计原则：不依赖外网，不依赖 akshare 已安装。
测试覆盖三类：
1. 纯转换函数（_extract_code / _estimate_sentiment / _df_to_dict / _first_match）
2. build_provider 的降级行为（akshare 不可用时落回 fixture）
3. AkshareProvider 的懒加载（akshare 不在时抛清晰的 ImportError）
"""

from __future__ import annotations

import pytest

from equitylens.config import Settings
from equitylens.runtime import build_provider
from equitylens.tools.providers.akshare import (
    _df_to_dict,
    _estimate_sentiment,
    _extract_code,
    _first_match,
    _format_amount,
    _safe_str,
)
from equitylens.tools.providers.fixture import FixtureProvider


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #
class TestExtractCode:
    def test_extracts_sh_code(self) -> None:
        assert _extract_code("分析贵州茅台 600519") == "600519"

    def test_extracts_sz_code(self) -> None:
        assert _extract_code("000858 五粮液") == "000858"

    def test_extracts_gem_code(self) -> None:
        assert _extract_code("300750 宁德时代") == "300750"

    def test_returns_empty_for_name_only(self) -> None:
        assert _extract_code("分析贵州茅台最近一期经营情况") == ""

    def test_returns_empty_for_empty(self) -> None:
        assert _extract_code("") == ""


class TestEstimateSentiment:
    def test_positive_words_raise_score(self) -> None:
        assert _estimate_sentiment("营收增长超预期，创新高") > 0

    def test_negative_words_lower_score(self) -> None:
        assert _estimate_sentiment("利润下降，存在退市风险") < 0

    def test_neutral_text_is_zero(self) -> None:
        assert _estimate_sentiment("公司发布公告") == 0.0

    def test_clamps_to_range(self) -> None:
        # 很多正面词也不会超过 1.0
        text = "增长提升改善利好突破创新高回暖超预期增长提升改善利好突破"
        assert _estimate_sentiment(text) <= 1.0


class TestSafeStr:
    def test_strips_nan(self) -> None:
        assert _safe_str(float("nan")) == ""

    def test_strips_none_string(self) -> None:
        assert _safe_str("none") == ""

    def test_keeps_normal_text(self) -> None:
        assert _safe_str("1,741.0 亿元") == "1,741.0 亿元"

    def test_none_returns_empty(self) -> None:
        assert _safe_str(None) == ""


class TestFormatAmount:
    def test_formats_with_unit(self) -> None:
        assert _format_amount("1741.0", "亿元") == "1741.0 亿元"

    def test_empty_stays_empty(self) -> None:
        assert _format_amount(None) == ""


class TestFirstMatch:
    def test_finds_exact(self) -> None:
        assert _first_match(["标题", "日期"], ["标题", "title"]) == "标题"

    def test_falls_through_to_second(self) -> None:
        assert _first_match(["title", "date"], ["标题", "title"]) == "title"

    def test_returns_none_for_no_match(self) -> None:
        assert _first_match(["a", "b"], ["c", "d"]) is None


class TestDfToDict:
    def test_empty_returns_empty(self) -> None:
        assert _df_to_dict(None) == {}

    def test_converts_two_column_df(self) -> None:
        from typing import ClassVar

        class FakeDF:
            empty: ClassVar[bool] = False
            columns: ClassVar[list[str]] = ["item", "value"]

            def iterrows(self):
                return iter([
                    (0, {"item": "名称", "value": "贵州茅台"}),
                    (1, {"item": "行业", "value": "白酒"}),
                ])

        result = _df_to_dict(FakeDF())
        assert result["名称"] == "贵州茅台"
        assert result["行业"] == "白酒"


# --------------------------------------------------------------------------- #
# build_provider 降级行为
# --------------------------------------------------------------------------- #
class TestBuildProviderFallback:
    """akshare 在 CI 环境不可用，build_provider 应该降级到 fixture。"""

    def test_offline_always_fixture(self) -> None:
        settings = Settings(app_mode="offline", data_provider="akshare")
        provider = build_provider(settings)
        assert provider.name == "fixture"
        assert isinstance(provider, FixtureProvider)

    def test_fixture_provider(self) -> None:
        settings = Settings(app_mode="dev", data_provider="fixture")
        provider = build_provider(settings)
        assert provider.name == "fixture"

    def test_akshare_unavailable_falls_back(self) -> None:
        """CI 环境没有 akshare，应降级到 fixture 并不报错。"""
        settings = Settings(app_mode="dev", data_provider="akshare")
        provider = build_provider(settings)
        # akshare 在 CI 不装 → 降级到 fixture
        assert provider.name == "fixture"

    def test_unknown_provider_falls_back(self) -> None:
        settings = Settings(app_mode="dev", data_provider="nonexistent")
        provider = build_provider(settings)
        assert provider.name == "fixture"


# --------------------------------------------------------------------------- #
# AkshareProvider 懒加载
# --------------------------------------------------------------------------- #
class TestAkshareLazyImport:
    """akshare 未安装时，AkshareProvider 应抛清晰的 ImportError。"""

    def test_raises_importerror_when_not_installed(self) -> None:
        # 如果 akshare 真的装了，这个测试会 skip
        try:
            import akshare  # noqa: F401
        except ImportError:
            # akshare 未安装，验证懒加载行为
            from equitylens.tools.providers.akshare import AkshareProvider

            with pytest.raises(ImportError, match="akshare 未安装"):
                AkshareProvider()
        else:
            pytest.skip("akshare 已安装，跳过懒加载测试")
