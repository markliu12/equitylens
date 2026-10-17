"""派生信号层：把原始数据交叉成可证伪的判断素材。

## 为什么需要单独一层

原始数据单独看都只是数字：营收增长 15.6%、行业产量 -3.2%、同业增速中位 6.5%。
把它们摆在一起仍然是三个数字，读者不会自动得出"份额驱动而非景气驱动"这个判断。
过去这层推导被留给了 LLM 自由发挥，带来两个后果：

1. 同一份数据两次运行会得到不同结论，无法回归测试；
2. 模型倾向于复述数字（"营业总收入为 1741 亿元"），因为复述最安全。

所以这里把"交叉推导"固定成确定性代码，只做算术和阈值判断，不生成一句话。
产出的 `flags` 是可以被单测锁定的**判断标签**，`derived` 是可以直接引用的数值，
`basis` 记录每个数字由哪两个原始字段算出来——报告里的每条结论因此可以反查到算式。

## 设计约定

- **缺数据就跳过**：任何一个输入缺失，对应派生项写进 `unavailable`，绝不填 0 或用默认值。
  这比"看起来完整"重要：0 会被当成真实数值参与后续判断。
- **只做可复现的算术**：不引入随机、不调用模型、不读外部状态。
- **阈值显式**：所有判断阈值集中在本模块顶部，调参时能一眼看到在改什么。
"""

from __future__ import annotations

import re
from statistics import median
from typing import Any

# --------------------------------------------------------------------------- #
# 判断阈值（集中在此，改动即改判断口径，需同步更新测试）
# --------------------------------------------------------------------------- #
GROWTH_ABOVE_INDUSTRY_PP = 0.05
GROWTH_ABOVE_PEERS_PP = 0.03
DIVERGENCE_PP = 0.10
CASH_QUALITY_HIGH = 1.0
CASH_QUALITY_LOW = 0.8
MARGIN_CHANGE_PP = 0.002
SENTIMENT_NEUTRAL_BAND = 0.10
BACKLOG_EXPANDING = 0.05

_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _pct(value: Any) -> float | None:
    """只解析百分比：'7.2%' → 0.072；已经是小数的原样返回；解析不了返回 None。

    刻意不接受"19 倍"这类非百分比字符串——把倍数当百分比是静默错误，比返回 None 危险。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str) or "%" not in value:
        return None
    match = _NUM.search(value.replace(",", ""))
    return float(match.group()) / 100.0 if match else None


def _number(value: Any) -> float | None:
    """解析不带百分号的数值（市盈率倍数等）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None
    match = _NUM.search(value.replace(",", ""))
    return float(match.group()) if match else None


def _median(values: list[float]) -> float | None:
    return median(values) if values else None


def derive_signals(
    *,
    fundamental: dict[str, Any],
    industry: dict[str, Any],
    sentiment: dict[str, Any],
) -> dict[str, Any]:
    """把三个维度的原始信号交叉成派生数值 + 判断标签。"""
    signals = dict(fundamental.get("signals") or {})
    industry_meta = dict(industry.get("industry") or {})
    news = list(sentiment.get("news") or [])

    derived: dict[str, float] = {}
    basis: dict[str, str] = {}
    unavailable: list[str] = []
    flags: list[str] = []

    def put(key: str, value: float | None, source: str) -> float | None:
        if value is None:
            unavailable.append(key)
            return None
        derived[key] = round(float(value), 4)
        basis[key] = source
        return derived[key]

    # ---- 1. 增速：公司 vs 行业 vs 同业 -------------------------------- #
    revenue_growth = _pct(signals.get("revenue_growth"))
    output_growth = _pct(industry_meta.get("output_growth"))
    if output_growth is None:
        for metric in industry.get("metrics") or []:
            if "产量" in str(metric.get("name") or "") or "行业增速" in str(
                metric.get("name") or ""
            ):
                output_growth = _pct(metric.get("value"))
                if output_growth is not None:
                    break

    peers = list(industry_meta.get("peers") or [])
    peer_growth = _median(
        [v for v in (_pct(p.get("revenue_growth")) for p in peers) if v is not None]
    )
    peer_pe = _median(
        [v for v in (_number(p.get("pe_ratio")) for p in peers) if v is not None]
    )
    put("peer_growth_median", peer_growth, "同业可比公司 revenue_growth 中位数")
    put("peer_pe_median", peer_pe, "同业可比公司 pe_ratio 中位数")
    put("industry_output_growth", output_growth, "行业产量同比")

    if revenue_growth is not None and output_growth is not None:
        excess = revenue_growth - output_growth
        put(
            "growth_vs_industry",
            excess,
            f"公司营收增速 {revenue_growth:.1%} - 行业产量增速 {output_growth:.1%}",
        )
        if excess >= GROWTH_ABOVE_INDUSTRY_PP:
            flags.append("growth_above_industry")
    else:
        unavailable.append("growth_vs_industry")
    if revenue_growth is not None and peer_growth is not None:
        excess = revenue_growth - peer_growth
        put(
            "growth_vs_peers",
            excess,
            f"公司营收增速 {revenue_growth:.1%} - 同业增速中位 {peer_growth:.1%}",
        )
        if excess >= GROWTH_ABOVE_PEERS_PP:
            flags.append("growth_above_peers")
    else:
        unavailable.append("growth_vs_peers")

    # ---- 2. 增长节奏：全年 vs 单季 ------------------------------------ #
    q_growth = _pct(signals.get("q_revenue_growth"))
    if revenue_growth is not None and q_growth is not None:
        gap = q_growth - revenue_growth
        put(
            "growth_divergence",
            gap,
            f"单季营收同比 {q_growth:.1%} - 全年营收增速 {revenue_growth:.1%}",
        )
        if abs(gap) >= DIVERGENCE_PP:
            flags.append("growth_divergence")
        if gap < 0:
            flags.append("growth_decelerating")
        elif gap > 0:
            flags.append("growth_accelerating")
    else:
        unavailable.append("growth_divergence")

    # ---- 3. 利润质量：现金流 / 净利润 --------------------------------- #
    cash_flow = _number(signals.get("cash_flow"))
    net_profit = _number(signals.get("net_profit"))
    if cash_flow is not None and net_profit not in (None, 0):
        ratio = cash_flow / float(net_profit)
        put(
            "cash_to_profit",
            ratio,
            f"经营现金流 {cash_flow:g} 亿 / 净利润 {float(net_profit):g} 亿",
        )
        if ratio >= CASH_QUALITY_HIGH:
            flags.append("cash_quality_high")
        elif ratio < CASH_QUALITY_LOW:
            flags.append("cash_quality_low")
    else:
        # 关键：不填 0。把缺失的现金流当成 0，会得出"利润没有现金支撑"这种
        # 听起来像发现、实际只是没取到数的结论。
        unavailable.append("cash_to_profit")

    # ---- 4. 利润率方向 ------------------------------------------------- #
    margin_change = _pct(signals.get("net_margin_change"))
    if margin_change is not None:
        put("margin_change", margin_change, "销售净利率同比变化（比例，非百分点）")
        if margin_change > MARGIN_CHANGE_PP:
            flags.append("margin_expanding")
        elif margin_change < -MARGIN_CHANGE_PP:
            flags.append("margin_contracting")
    else:
        unavailable.append("margin_change")

    # ---- 5. 预收/合同负债：订单能见度 --------------------------------- #
    backlog = _pct(signals.get("contract_liability_growth"))
    if backlog is not None:
        put("contract_liability_growth", backlog, "合同负债较年初增速")
        if backlog >= BACKLOG_EXPANDING:
            flags.append("backlog_expanding")
    else:
        unavailable.append("contract_liability_growth")

    # ---- 6. 价格与量的背离 -------------------------------------------- #
    price_hike = _pct(signals.get("price_hike_pct"))
    put("price_hike_pct", price_hike, "出厂价上调幅度")
    if price_hike is not None and price_hike > 0 and (output_growth or 0) < 0:
        flags.append("price_up_in_contracting_industry")
    if output_growth is not None and output_growth < 0:
        flags.append("industry_contracting")

    # ---- 7. 舆情：情绪净值与基本面背离 -------------------------------- #
    scores = [float(item.get("sentiment") or 0.0) for item in news]
    positive = sum(1 for s in scores if s > 0.2)
    negative = sum(1 for s in scores if s < -0.2)
    if scores:
        net = (positive - negative) / len(scores)
        put("sentiment_net", net, f"(正面 {positive} - 负面 {negative}) / 样本 {len(scores)}")
        put("news_positive", positive, "正面报道条数")
        put("news_negative", negative, "负面报道条数")
        if abs(net) < SENTIMENT_NEUTRAL_BAND:
            flags.append("sentiment_neutral")
        elif net < 0:
            flags.append("sentiment_negative")
        else:
            flags.append("sentiment_positive")
        if revenue_growth is not None and revenue_growth > 0.10 and net <= 0:
            flags.append("sentiment_diverges_from_fundamental")
        if q_growth is not None and q_growth < 0 and net >= 0:
            # 单季收入已经转负，舆情仍非负面：市场预期尚未反映经营变化。
            # 这是一个"时间差"判断，既不是利好也不是利空，只是提示后续需要验证。
            flags.append("sentiment_lags_revenue_turn")
    else:
        unavailable.append("sentiment_net")

    return {
        "derived": derived,
        "derived_basis": basis,
        "unavailable": sorted(set(unavailable)),
        "flags": flags,
        #: 原样回传少量原始信号，供离线模型直接引用而不必再猜路径
        "raw": {
            key: signals[key]
            for key in ("revenue_growth", "net_margin", "q_revenue_growth")
            if key in signals
        },
        "cycle_stage": industry_meta.get("cycle_stage"),
        "industry_name": industry_meta.get("name"),
    }


def format_for_prompt(payload: dict[str, Any]) -> str:
    """把派生信号压成一段可直接塞进 prompt 的紧凑文本。

    在线模型最容易犯的错是"自己重算"和"复述原始数字"。这里显式给出算式，
    并在结尾要求模型优先使用这些结论，目的是把它的自由发挥限制在"解读"而非"计算"。
    """
    derived = payload.get("derived") or {}
    basis = payload.get("derived_basis") or {}
    flags = payload.get("flags") or []
    if not derived and not flags:
        return "（派生信号不可用：原始数据不足）"
    lines = ["已由确定性代码算好，直接引用，不要自行重算："]
    for key, value in derived.items():
        lines.append(f"- {key} = {value}（{basis.get(key, '')}）")
    if flags:
        lines.append("已识别的判断标签（可作为结论骨架）：" + "、".join(flags))
    return "\n".join(lines)
