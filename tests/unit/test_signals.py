"""派生信号的单元测试。

这些阈值是业务判断口径（多少算"超额"、现金流覆盖多少算"高质量"），
改一个数字就会改变报告结论，所以必须锁住。

另一组断言更重要：**缺数据时不能填默认值**。
把缺失的现金流当成 0，会让 `cash_to_profit` 变成 0，进而生成一条
"利润没有现金支撑"的错误结论——听起来像是发现了风险，实际只是数据没取到。
"""

from __future__ import annotations

import pytest

from equitylens.tools.signals import (
    CASH_QUALITY_HIGH,
    DIVERGENCE_PP,
    GROWTH_ABOVE_INDUSTRY_PP,
    derive_signals,
    format_for_prompt,
)


def _fundamental(**overrides: object) -> dict:
    signals = {
        "revenue_growth": 0.156,
        "net_margin": 0.524,
        "net_margin_change": 0.003,
        "net_profit": 862.3,
        "cash_flow": 912.6,
        "q_revenue_growth": -0.012,
        "contract_liability_growth": 0.121,
        "price_hike_pct": 0.03,
    }
    signals.update(overrides)  # type: ignore[arg-type]
    return {"signals": signals, "metrics": [], "facts": []}


def _industry(**overrides: object) -> dict:
    meta = {
        "name": "白酒",
        "cycle_stage": "放缓",
        "output_growth": -0.032,
        "peers": [
            {"name": "五粮液", "pe_ratio": "18.4", "revenue_growth": "7.2%"},
            {"name": "泸州老窖", "pe_ratio": "17.1", "revenue_growth": "5.8%"},
            {"name": "山西汾酒", "pe_ratio": "22.6", "revenue_growth": "11.4%"},
        ],
    }
    meta.update(overrides)  # type: ignore[arg-type]
    return {"industry": meta, "metrics": []}


def _sentiment(*, positive: int = 2, negative: int = 2, neutral: int = 1) -> dict:
    news = []
    for index in range(positive):
        news.append({"id": f"p{index}", "title": "正面", "sentiment": 0.6})
    for index in range(negative):
        news.append({"id": f"n{index}", "title": "负面", "sentiment": -0.5})
    for index in range(neutral):
        news.append({"id": f"z{index}", "title": "中性", "sentiment": 0.05})
    return {"news": news}


def test_full_input_produces_expected_flags() -> None:
    payload = derive_signals(
        fundamental=_fundamental(), industry=_industry(), sentiment=_sentiment()
    )
    flags = set(payload["flags"])
    assert {
        "growth_above_industry",
        "growth_above_peers",
        "growth_divergence",
        "growth_decelerating",
        "cash_quality_high",
        "margin_expanding",
        "backlog_expanding",
        "price_up_in_contracting_industry",
        "industry_contracting",
        "sentiment_neutral",
        "sentiment_diverges_from_fundamental",
        "sentiment_lags_revenue_turn",
    } <= flags
    assert payload["unavailable"] == []


def test_derived_values_and_basis_are_recorded() -> None:
    payload = derive_signals(
        fundamental=_fundamental(), industry=_industry(), sentiment=_sentiment()
    )
    derived = payload["derived"]
    # 15.6% - (-3.2%) = 18.8 个百分点
    assert derived["growth_vs_industry"] == pytest.approx(0.188, abs=1e-4)
    # 单季 -1.2% 减全年 15.6% = -16.8 个百分点
    assert derived["growth_divergence"] == pytest.approx(-0.168, abs=1e-4)
    # 912.6 / 862.3
    assert derived["cash_to_profit"] == pytest.approx(1.0583, abs=1e-4)
    # 同业增速中位数
    assert derived["peer_growth_median"] == pytest.approx(0.072, abs=1e-4)

    for key in derived:
        assert payload["derived_basis"].get(key), f"{key} 缺少算式说明，报告无法解释这个数字"


def test_missing_cash_flow_does_not_fabricate_zero() -> None:
    """缺现金流时不能把 cash_to_profit 当成 0，否则会凭空生成"利润无现金支撑"的结论。"""
    fundamental = _fundamental()
    fundamental["signals"].pop("cash_flow")

    payload = derive_signals(
        fundamental=fundamental, industry=_industry(), sentiment=_sentiment()
    )
    assert "cash_to_profit" not in payload["derived"]
    assert "cash_to_profit" in payload["unavailable"]
    assert "cash_quality_high" not in payload["flags"]
    assert "cash_quality_low" not in payload["flags"]


def test_missing_industry_skips_excess_growth() -> None:
    payload = derive_signals(
        fundamental=_fundamental(), industry={"industry": {}, "metrics": []}, sentiment=_sentiment()
    )
    assert "growth_vs_industry" not in payload["derived"]
    assert "growth_above_industry" not in payload["flags"]
    # 但公司自身的节奏判断仍然成立
    assert "growth_divergence" in payload["flags"]


def test_threshold_boundary_for_excess_growth() -> None:
    """超额增速刚好等于阈值时应触发，稍低一档不应触发。"""
    base = {"output_growth": -0.0}
    at_threshold = derive_signals(
        fundamental=_fundamental(revenue_growth=GROWTH_ABOVE_INDUSTRY_PP),
        industry=_industry(**base),
        sentiment=_sentiment(),
    )
    assert "growth_above_industry" in at_threshold["flags"]

    below = derive_signals(
        fundamental=_fundamental(revenue_growth=GROWTH_ABOVE_INDUSTRY_PP - 0.01),
        industry=_industry(**base),
        sentiment=_sentiment(),
    )
    assert "growth_above_industry" not in below["flags"]


def test_divergence_threshold() -> None:
    """单季与全年的差距要超过阈值才算"背离"，否则只是正常波动。"""
    payload = derive_signals(
        fundamental=_fundamental(revenue_growth=0.10, q_revenue_growth=0.10 - DIVERGENCE_PP / 2),
        industry=_industry(),
        sentiment=_sentiment(),
    )
    assert "growth_divergence" not in payload["flags"]

    payload = derive_signals(
        fundamental=_fundamental(revenue_growth=0.10, q_revenue_growth=0.10 - DIVERGENCE_PP - 0.01),
        industry=_industry(),
        sentiment=_sentiment(),
    )
    assert "growth_divergence" in payload["flags"]


def test_cash_quality_threshold() -> None:
    payload = derive_signals(
        fundamental=_fundamental(cash_flow=862.3 * CASH_QUALITY_HIGH),
        industry=_industry(),
        sentiment=_sentiment(),
    )
    assert "cash_quality_high" in payload["flags"]

    payload = derive_signals(
        fundamental=_fundamental(cash_flow=862.3 * (CASH_QUALITY_HIGH - 0.01)),
        industry=_industry(),
        sentiment=_sentiment(),
    )
    assert "cash_quality_high" not in payload["flags"]


def test_no_news_marks_sentiment_unavailable() -> None:
    payload = derive_signals(
        fundamental=_fundamental(), industry=_industry(), sentiment={"news": []}
    )
    assert "sentiment_net" in payload["unavailable"]
    assert not any(flag.startswith("sentiment_") for flag in payload["flags"])


def test_format_for_prompt_warns_against_recomputing() -> None:
    payload = derive_signals(
        fundamental=_fundamental(), industry=_industry(), sentiment=_sentiment()
    )
    text = format_for_prompt(payload)
    assert "不要自行重算" in text
    assert "growth_vs_industry" in text
    # 算式要出现在 prompt 里，模型才能解释这个数字的来源
    assert "18.8" in text or "15.6" in text


def test_format_for_prompt_when_empty() -> None:
    text = format_for_prompt({"derived": {}, "flags": []})
    assert "不可用" in text
