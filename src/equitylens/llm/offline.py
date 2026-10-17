"""离线确定性模型。

存在的理由不是为了"假跑"：
1. CI 与单测必须零外网、零费用、零随机——LLM 的采样不确定性会让断言变成玄学；
2. 演示可以断网进行，不会因为现场网络/额度问题翻车；
3. 它倒逼 agent 的输入上下文必须是**结构化、自解释**的（离线端只能靠这些字段拼答案），
   间接提升了在线 prompt 的质量。

实现方式：agent 把上下文序列化成 JSON 作为最后一条 user 消息，这里按 role 分派，
用确定性规则从证据里拼出合法输出。输出仍会走同一套 Pydantic 校验，schema 一旦漂移
测试立刻红——这是它最有价值的地方。
"""

from __future__ import annotations

import json
import re
from typing import Any

from equitylens.engine.types import FailureKind
from equitylens.errors import NodeError
from equitylens.llm.client import LLMResult
from equitylens.llm.tokens import estimate_tokens


class OfflineLLMClient:
    name = "offline"

    def __init__(self, *, model: str = "offline") -> None:
        self.model = model
        self.calls: list[str] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        json_mode: bool = True,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        role: str = "",
    ) -> LLMResult:
        context = _extract_context(messages)
        self.calls.append(role)
        handler = _HANDLERS.get(role)
        if handler is None:
            raise NodeError(FailureKind.UNKNOWN, f"离线模型未实现角色: {role!r}")
        payload = handler(context)
        text = json.dumps(payload, ensure_ascii=False)
        prompt_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)
        return LLMResult(
            text=text,
            model=self.model or "offline",
            prompt_tokens=prompt_tokens,
            completion_tokens=estimate_tokens(text),
            latency_ms=0,
            raw={"offline": True},
        )


def _extract_context(messages: list[dict[str, str]]) -> dict[str, Any]:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content", "").strip()
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            parsed = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise NodeError(FailureKind.SCHEMA, "离线模型无法从消息中解析出 JSON 上下文")


# --------------------------------------------------------------------------- #
# 各角色的确定性产出
# --------------------------------------------------------------------------- #
def _claim(text: str, evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not evidence:
        return {"text": text, "evidence_ids": [], "strength": "观点"}
    snippet = (evidence.get("snippet") or "").strip()
    quote = snippet[:60] if snippet else (evidence.get("title") or "")[:60]
    return {
        "text": text,
        "evidence_ids": [evidence["id"]],
        "strength": "事实",
        "quote": quote or None,
    }


def _planner(ctx: dict[str, Any]) -> dict[str, Any]:
    entity = ctx.get("entity") or {}
    name = entity.get("name") or ctx.get("question") or "目标公司"
    industry = entity.get("industry") or "所属行业"
    return {
        "entity_name": name,
        "entity_code": entity.get("code") or "",
        "industry": industry,
        "sub_questions": [
            f"{name}最近一期经营与盈利表现如何？",
            f"{industry}当前的景气度与竞争格局如何？",
            f"{name}近期有哪些值得关注的公告与舆情事件？",
        ],
        "required_data": ["财务报表", "公司公告", "行业数据", "财经新闻"],
        "analysts_enabled": ["fundamental", "industry", "sentiment"],
        "est_budget_cny": 0.4,
    }


def _by_id(evidence: list[dict[str, Any]], evidence_id: str) -> dict[str, Any] | None:
    return next((item for item in evidence if item.get("id") == evidence_id), None)


#: 各维度优先使用的证据类型 —— 否则按 tier 排序时公告会淹没行业数据与新闻
KIND_PREF = {"fundamental": "financial", "industry": "industry", "sentiment": "news"}


def _pick(evidence: list[dict[str, Any]], role: str, limit: int = 4) -> list[dict[str, Any]]:
    preferred = KIND_PREF.get(role)
    primary = [item for item in evidence if item.get("kind") == preferred]
    return (primary or evidence)[:limit]


def _flag_set(ctx: dict[str, Any]) -> set[str]:
    return set((ctx.get("derived") or {}).get("flags") or [])


def _signal(ctx: dict[str, Any], key: str) -> float | None:
    value = (ctx.get("signals") or {}).get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _find_fact(ctx: dict[str, Any], *keywords: str) -> dict[str, Any] | None:
    facts: list[dict[str, Any]] = list(ctx.get("facts") or [])
    for fact in facts:
        name = str(fact.get("name") or "")
        if any(word in name for word in keywords):
            return dict(fact)
    return None


def _anchor(
    evidence: list[dict[str, Any]], *keywords: str, kinds: tuple[str, ...] = ()
) -> dict[str, Any] | None:
    """按关键词在证据里找锚点。

    找不到就返回 None——调用方必须显式决定是"降级成观点"还是"整条不写"，
    不允许默默挂一个不相干的证据上去（那会让引用覆盖率虚高）。
    """
    for item in evidence:
        if kinds and item.get("kind") not in kinds:
            continue
        blob = f"{item.get('title') or ''}{item.get('snippet') or ''}"
        if any(word in blob for word in keywords):
            return item
    return None


def _claim_from(
    text: str, *anchors: dict[str, Any] | None, strength: str = "事实"
) -> dict[str, Any]:
    """组装一条结论。多个锚点 = 这条结论由多个来源交叉支撑。"""
    ids: list[str] = []
    quote: str | None = None
    for item in anchors:
        if not item or not item.get("id"):
            continue
        if item["id"] not in ids:
            ids.append(item["id"])
        if quote is None:
            quote = (item.get("snippet") or "")[:60] or None
    return {
        "text": text,
        "evidence_ids": ids,
        "strength": strength if ids else "观点",
        "quote": quote if ids else None,
    }


def _analyst_common(
    ctx: dict[str, Any], role: str, stance: str, confidence: float
) -> dict[str, Any]:
    """兜底骨架：只在没有足够信号做判断时使用。

    注意它产出的是"事实陈述"而不是"判断"——这是刻意的降级：宁可给读者一条
    干巴巴但有出处的数字，也不要给一条听起来漂亮但推不出来的结论。
    """
    evidence = list(ctx.get("evidence") or [])
    claims: list[dict[str, Any]] = []
    for fact in (ctx.get("facts") or [])[:3]:
        evidence_ids = list(fact.get("evidence_ids") or [])
        anchor = _by_id(evidence, evidence_ids[0]) if evidence_ids else None
        period = fact.get("period") or ""
        text = f"{fact.get('name')}为 {fact.get('value')}" + (f"（{period}）" if period else "")
        claims.append(
            {
                "text": text,
                "evidence_ids": evidence_ids,
                "strength": "事实" if evidence_ids else "观点",
                "quote": ((anchor or {}).get("snippet") or "")[:60] or None,
            }
        )
    if not claims:
        for item in _pick(evidence, role):
            snippet = (item.get("snippet") or "").strip()
            claims.append(
                {
                    "text": f"{item.get('title') or '公开信息'}：{snippet[:44]}",
                    "evidence_ids": [item["id"]],
                    "strength": "事实",
                    "quote": snippet[:60] or None,
                }
            )
    if not claims:
        claims = [
            {
                "text": "本期可核验数据不足，暂无法给出方向性判断。",
                "evidence_ids": [],
                "strength": "观点",
            }
        ]
    return {
        "role": role,
        "stance": stance if claims and claims[0]["evidence_ids"] else "数据不足",
        "confidence": confidence if claims and claims[0]["evidence_ids"] else 0.3,
        "metrics": list(ctx.get("metrics") or []),
        "claims": claims[:5],
        "gaps": list(ctx.get("gaps") or []),
    }


def _analyst_fundamental(ctx: dict[str, Any]) -> dict[str, Any]:
    """基本面：只回答"数字说明了什么"，不重复行业与舆情维度。"""
    flags = _flag_set(ctx)
    evidence = list(ctx.get("evidence") or [])
    growth = _signal(ctx, "revenue_growth")
    q_growth = _signal(ctx, "q_revenue_growth")
    divergence = _signal(ctx, "growth_divergence")
    margin = _signal(ctx, "net_margin")
    margin_change = _signal(ctx, "margin_change")
    cash_ratio = _signal(ctx, "cash_to_profit")
    cash_flow = _signal(ctx, "cash_flow")
    net_profit = _signal(ctx, "net_profit")
    backlog = _signal(ctx, "contract_liability_growth")

    stance = "偏正面" if growth is not None and growth >= 0.08 else (
        "中性" if growth is not None and growth >= 0 else "偏负面"
    )
    payload = _analyst_common(ctx, "fundamental", stance, 0.72)
    claims: list[dict[str, Any]] = []

    revenue_fact = _find_fact(ctx, "营业总收入", "营业收入")
    revenue_anchor = _anchor(evidence, "营业总收入", "营业收入", kinds=("financial",))
    quarter_anchor = _anchor(evidence, "季度", "单季", kinds=("financial",))
    cash_anchor = _anchor(evidence, "现金流量净额", kinds=("financial",))
    margin_anchor = _anchor(evidence, "净利率", kinds=("financial",))

    if revenue_fact:
        period = revenue_fact.get("period") or ""
        suffix = f"（{period}）" if period else ""
        value = revenue_fact.get("value")
        if growth is not None and q_growth is not None and "growth_divergence" in flags:
            claims.append(
                _claim_from(
                    f"全年营收 {value}{suffix}同比 {growth:+.1%}，但最新单季同比 {q_growth:+.1%}，"
                    f"两者相差 {abs(divergence or 0) * 100:.1f} 个百分点。"
                    f"高增与转负同时成立，说明增长节奏在年内已经切换，全年数字不代表当前状态",
                    revenue_anchor,
                    quarter_anchor,
                )
            )
        elif growth is not None:
            tone = "处于较快区间" if growth >= 0.10 else ("处于温和区间" if growth >= 0 else "为负")
            claims.append(
                _claim_from(
                    f"营收 {value}{suffix}同比 {growth:+.1%}，收入端增速{tone}",
                    revenue_anchor,
                )
            )
        else:
            claims.append(_claim_from(f"营收 {value}{suffix}", revenue_anchor))

    if cash_ratio is not None and cash_flow is not None and net_profit is not None:
        if cash_ratio >= 1.0:
            meaning = "利润的现金含量高于 1，收入确认有真实现金流支撑"
        else:
            meaning = "利润的现金含量低于 1，需核实应收与存货占用"
        claims.append(
            _claim_from(
                f"经营现金流 {cash_flow:g} 亿元 ÷ 净利润 {net_profit:g} 亿元"
                f" = {cash_ratio:.2f} 倍，{meaning}",
                cash_anchor,
            )
        )

    if margin is not None:
        if margin_change is not None and abs(margin_change) >= 0.002:
            if margin_change > 0 and "growth_decelerating" in flags:
                detail = "在收入增速回落的同时仍小幅扩张，利润端由效率改善而非规模驱动"
            else:
                direction = "提升" if margin_change > 0 else "下降"
                detail = f"同比{direction} {abs(margin_change) * 100:.1f} 个百分点"
            claims.append(
                _claim_from(f"销售净利率 {margin:.1%}，{detail}", margin_anchor)
            )
        else:
            claims.append(_claim_from(f"销售净利率 {margin:.1%}，与上年基本持平", margin_anchor))

    if (
        backlog is not None
        and "backlog_expanding" in flags
        and "growth_divergence" in flags
    ):
        claims.append(
            _claim_from(
                f"合同负债较年初增长 {backlog:.1%}，预收端仍在扩张。"
                f"预收增加与单季收入转负并存，更可能指向发货与确认节奏，而不是终端需求突然转弱",
                cash_anchor,
                quarter_anchor,
            )
        )

    if claims:
        payload["claims"] = claims[:5]
        payload["confidence"] = 0.72 if len(claims) >= 2 else 0.55
    payload["metrics"] = list(ctx.get("metrics") or [])
    return payload


def _analyst_industry(ctx: dict[str, Any]) -> dict[str, Any]:
    """行业：只回答"行业内公司处在什么位置"，不复述公司自己的财务数字。"""
    evidence = list(ctx.get("evidence") or [])
    industry = ctx.get("industry") or {}
    stage = industry.get("cycle_stage") or ""
    output_growth = _signal(ctx, "industry_output_growth")
    vs_industry = _signal(ctx, "growth_vs_industry")
    vs_peers = _signal(ctx, "growth_vs_peers")
    peer_growth = _signal(ctx, "peer_growth_median")
    peer_pe = _signal(ctx, "peer_pe_median")

    stance = "偏正面" if stage in {"扩张", "复苏"} else ("中性" if stage == "放缓" else "偏负面")
    payload = _analyst_common(ctx, "industry", stance, 0.65)
    claims: list[dict[str, Any]] = []

    output_anchor = _anchor(evidence, "产量", "行业需求", kinds=("industry",))
    peer_anchor = _anchor(evidence, "同业", "可比", "市盈率", kinds=("industry",))

    if output_growth is not None:
        if output_growth < 0:
            reading = "行业层面已经不提供增量，公司增长只能来自份额"
        else:
            reading = "行业层面仍有增量，公司可以分享景气"
        period = f"景气阶段定位为「{stage}」，" if stage else ""
        claims.append(
            _claim_from(
                f"行业产量同比 {output_growth:+.1%}，{period}量在"
                f"{'收缩' if output_growth < 0 else '扩张'}——{reading}",
                output_anchor,
            )
        )

    if vs_industry is not None and vs_peers is not None:
        peer_text = f"同业增速中位 {peer_growth:.1%}，" if peer_growth is not None else ""
        claims.append(
            _claim_from(
                f"公司增速高出行业产量 {vs_industry * 100:.1f} 个百分点"
                f"（{peer_text}超额 {vs_peers * 100:.1f} 个百分点）。"
                f"增长来源更接近份额提升而非行业景气上行，"
                f"因此可持续性要看竞争格局，而不是等需求周期回暖",
                output_anchor,
                peer_anchor,
            )
        )

    if peer_pe is not None:
        claims.append(
            _claim_from(
                f"同业市盈率中位约 {peer_pe:g} 倍，头部公司估值溢价收窄，"
                f"相对估值位置并没有随业绩同步抬升",
                peer_anchor,
            )
        )

    if claims:
        payload["claims"] = claims[:4]
        payload["confidence"] = 0.68
    payload["metrics"] = list(ctx.get("metrics") or [])
    return payload


def _analyst_sentiment(ctx: dict[str, Any]) -> dict[str, Any]:
    """舆情：只做事件归纳与可解释的情绪判断，不评价对股价的影响。"""
    news = list(ctx.get("news") or [])
    flags = _flag_set(ctx)
    if len(news) < 3:
        payload = _analyst_common(ctx, "sentiment", "数据不足", 0.3)
        payload["gaps"] = [*payload.get("gaps", []), "新闻样本少于 3 条，未给出情绪评分"]
        return payload

    net = _signal(ctx, "sentiment_net")
    positive = _signal(ctx, "news_positive") or 0.0
    negative = _signal(ctx, "news_negative") or 0.0
    claims: list[dict[str, Any]] = []

    if net is not None:
        if abs(net) < 0.10:
            tone = "接近中性，媒体层面尚未形成一致预期"
        elif net > 0:
            tone = "略偏正面"
        else:
            tone = "略偏负面"
        news_anchors = [n for n in news[:2] if n.get("id")]
        # 净值正好为 0 时 {net:+.2f} 会输出 "+0.00"，读起来像个笔误
        net_text = "0.00" if abs(net) < 0.005 else f"{net:+.2f}"
        claims.append(
            _claim_from(
                f"近期 {len(news)} 条报道中正面 {int(positive)} 条、负面 {int(negative)} 条，"
                f"情绪净值 {net_text}，{tone}",
                *news_anchors,
                strength="观点",
            )
        )

    negatives = [n for n in news if float(n.get("sentiment") or 0) < -0.2]
    if negatives:
        topics = "；".join(str(n.get("title") or "")[:20] for n in negatives[:2])
        claims.append(
            _claim_from(
                f"负面信息集中在「{topics}」，属于价格与渠道层面的结构性讨论，"
                f"针对的是经营质量问题而非报表结果",
                negatives[0],
                strength="观点",
            )
        )

    if "sentiment_lags_revenue_turn" in flags:
        quarter_anchor = _anchor(list(ctx.get("evidence") or []), "季度", kinds=("financial",))
        claims.append(
            _claim_from(
                "最新单季营收同比已经转负，但舆情仍以中性为主，两者存在时间差："
                "市场预期尚未反映经营节奏的变化，后续需要跟踪渠道端反馈是否跟上",
                news[0] if news else None,
                quarter_anchor,
                strength="观点",
            )
        )

    announcements = [
        item for item in (ctx.get("evidence") or []) if item.get("kind") == "announcement"
    ]
    if announcements:
        claims.append(
            _claim_from(
                f"同期公司公告以「{announcements[0].get('title')}」为代表，"
                f"属于主动披露的经营安排，不构成外部评价",
                announcements[0],
            )
        )

    return {
        "role": "sentiment",
        "stance": "中性" if net is not None and abs(net) < 0.10 else (
            "偏正面" if (net or 0) > 0 else "偏负面"
        ),
        "confidence": 0.6,
        "metrics": [
            {
                "name": "新闻情绪净值",
                "value": f"{(net or 0):+.2f}",
                "period": "近期样本",
                "evidence_ids": [n["id"] for n in news[:3] if n.get("id")],
            },
            {
                "name": "正/负面报道条数",
                "value": f"{int(positive)}/{int(negative)}",
                "period": "近期样本",
                "evidence_ids": [n["id"] for n in news[:3] if n.get("id")],
            },
        ],
        "claims": claims[:5],
        "gaps": list(ctx.get("gaps") or []),
    }


def _bearish_points(ctx: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """空方论点：针对多方的每一条支撑做定点反驳。

    旧实现是给分析师的结论统一加一句"从风险角度看，"前缀，本质仍是复述——
    读者会看到同一句话出现在行业章节和分歧章节里，只是前面多了五个字。
    真正的分歧必须说清"你那条判断在什么条件下不成立"。
    """
    flags = _flag_set(ctx)
    quarter = _anchor(evidence, "季度", kinds=("financial",))
    industry = _anchor(evidence, "产量", kinds=("industry",))
    cash = _anchor(evidence, "现金流量净额", kinds=("financial",))
    price = _anchor(evidence, "价格", "提价", kinds=("announcement",))
    negative_news = next(
        (item for item in evidence if item.get("kind") == "news" and "负" in str(item.get("title"))),
        None,
    )

    out: list[dict[str, Any]] = []
    if "growth_divergence" in flags:
        out.append(
            _claim_from(
                "多方用全年增速论证成长性，但最新单季营收同比已经转负。"
                "用过去十二个月的累计数字描述当下状态，会系统性高估当前动能",
                quarter,
                strength="观点",
            )
        )
    if "growth_above_industry" in flags:
        out.append(
            _claim_from(
                "份额提升发生在行业量收缩的背景下，这类份额通常靠价格折让或渠道压货换取。"
                "在缺少份额来源拆分的情况下，不能直接当作可持续的竞争优势",
                industry,
                price,
                strength="观点",
            )
        )
    if "cash_quality_high" in flags:
        out.append(
            _claim_from(
                "现金流覆盖率高，但在收入增速回落的当期，它也可能来自回款节奏前置。"
                "单期现金含量不足以证明长期盈利质量",
                cash,
                strength="观点",
            )
        )
    if "price_up_in_contracting_industry" in flags:
        out.append(
            _claim_from(
                "行业缩量期提价，如果渠道利润同时被压缩，提价会先表现为库存上升而不是收入增长",
                price,
                industry,
                strength="观点",
            )
        )
    if negative_news is not None:
        out.append(
            _claim_from(
                "负面信息集中在价格体系与渠道端，这类问题通常先于报表数据显现，"
                "属于领先指标而非噪音",
                negative_news,
                strength="观点",
            )
        )
    return out[:4]


def _debate_common(ctx: dict[str, Any], side: str) -> dict[str, Any]:
    evidence = list(ctx.get("evidence") or [])
    reports = ctx.get("reports") or []
    picked = evidence[:3]

    if side == "bull":
        claims: list[dict[str, Any]] = []
        for report in reports:
            if report.get("role") == "fundamental":
                claims.extend(list(report.get("claims") or []))
        points = [dict(item) for item in claims[:3] if item.get("text")]
        thesis = "多方认为：收入与利润的绝对水平仍在改善，且现金流提供了独立于损益表的验证。"
    else:
        points = _bearish_points(ctx, evidence)
        thesis = (
            "空方认为：累计增速掩盖了最近一期的边际变化，"
            "而增长来源与外部环境都不支持把当前增速线性外推。"
        )

    if not points:
        for item in picked[:2]:
            points.append(
                {
                    "text": f"{item.get('title') or '公开信息'}：{(item.get('snippet') or '')[:44]}",
                    "evidence_ids": [item["id"]],
                    "strength": "事实",
                    "quote": (item.get("snippet") or "")[:60] or None,
                }
            )
    if not points:
        anchor = ctx.get("entity") or {}
        points = [
            {
                "text": f"{anchor.get('name', '公司')}的公开信息尚不足以支撑强判断。",
                "evidence_ids": [],
                "strength": "观点",
            }
        ]

    opponent = ctx.get("opponent") or {}
    rebuttals: list[dict[str, Any]] = []
    for point in (opponent.get("points") or [])[:2]:
        text = str(point.get("text") or "")
        if not text:
            continue
        rebuttals.append(
            {
                "text": f"对方论点「{text[:24]}」所依赖的前提在本期数据中未得到验证。",
                "evidence_ids": [picked[-1]["id"]] if picked else [],
                "strength": "观点",
            }
        )

    return {
        "side": side,
        "thesis": thesis,
        "points": points[:3],
        "rebuttals": rebuttals,
        "new_evidence_ids": [e["id"] for e in picked if e.get("id")],
        "_reports_seen": len(reports),
    }


def _bull(ctx: dict[str, Any]) -> dict[str, Any]:
    return _debate_common(ctx, "bull")


def _bear(ctx: dict[str, Any]) -> dict[str, Any]:
    return _debate_common(ctx, "bear")


def _clamp(value: int) -> int:
    return max(1, min(5, value))


def _debate_judge(ctx: dict[str, Any]) -> dict[str, Any]:
    round_no = int(ctx.get("round") or 1)
    bull = ctx.get("bull") or {}
    bear = ctx.get("bear") or {}
    bull_ev = len([p for p in bull.get("points") or [] if p.get("evidence_ids")])
    bear_ev = len([p for p in bear.get("points") or [] if p.get("evidence_ids")])
    bull_scores = {
        "logic": 4,
        "evidence": _clamp(2 + bull_ev),
        "novelty": _clamp(4 - round_no + 1),
        "rebuttal": _clamp(3 if bull.get("rebuttals") else 1),
    }
    bear_scores = {
        "logic": 4,
        "evidence": _clamp(2 + bear_ev),
        "novelty": _clamp(4 - round_no + 1),
        "rebuttal": _clamp(3 if bear.get("rebuttals") else 1),
    }
    bull_total = _weighted(bull_scores)
    bear_total = _weighted(bear_scores)
    leader = "bull" if bull_total - bear_total > 0.2 else ("bear" if bear_total - bull_total > 0.2 else "tie")
    new_info = {1: 1.0, 2: 0.18, 3: 0.08}.get(round_no, 0.05)
    return {
        "round": round_no,
        "bull": bull_scores,
        "bear": bear_scores,
        "new_info_ratio": new_info,
        "leader": leader,
        "unresolved": ["行业景气持续性"] if round_no >= 2 else [],
        "note": f"第 {round_no} 轮：证据覆盖仍是决定性因素。",
    }


def _weighted(scores: dict[str, int]) -> float:
    return (
        scores["logic"] * 0.30
        + scores["evidence"] * 0.35
        + scores["novelty"] * 0.15
        + scores["rebuttal"] * 0.20
    )


def _derive_conclusions(
    ctx: dict[str, Any], evidence: list[dict[str, Any]], flags: set[str]
) -> list[dict[str, Any]]:
    """交叉判断：核心结论的唯一来源。

    每条结论至少挂两个不同维度的证据——这是"交叉"的硬性含义。
    只有一个来源的判断仍然可以写进明细章节，但不进核心结论，
    因为读者无法从单点信息推出它的重要性。
    """
    get = lambda key: _signal(ctx, key)  # noqa: E731
    revenue_anchor = _anchor(evidence, "营业总收入", "营业收入", kinds=("financial",))
    quarter_anchor = _anchor(evidence, "季度", kinds=("financial",))
    industry_anchor = _anchor(evidence, "产量", kinds=("industry",))
    peer_anchor = _anchor(evidence, "同业", "市盈率", kinds=("industry",))
    cash_anchor = _anchor(evidence, "现金流量净额", kinds=("financial",))
    price_anchor = _anchor(evidence, "价格", "提价", kinds=("announcement",))
    news_anchors = [item for item in evidence if item.get("kind") == "news"][:2]

    out: list[dict[str, Any]] = []
    growth = get("revenue_growth")
    q_growth = get("q_revenue_growth")
    divergence = get("growth_divergence")
    vs_industry = get("growth_vs_industry")
    vs_peers = get("growth_vs_peers")
    cash_ratio = get("cash_to_profit")
    price_hike = get("price_hike_pct")
    margin_change = get("margin_change")

    # 1) 增长来源：份额还是景气 —— 这是整份报告最需要回答的问题
    output_growth = get("industry_output_growth")
    if (
        "growth_above_industry" in flags
        and "industry_contracting" in flags
        and growth is not None
        and vs_industry is not None
        and output_growth is not None
    ):
        out.append(
            _claim_from(
                f"公司增速与行业走势相反：行业产量同比 {output_growth:+.1%}，"
                f"公司营收同比 {growth:+.1%}，高出 {vs_industry * 100:.1f} 个百分点。"
                f"这组关系说明增长的主要来源是份额提升，而不是行业景气回暖；"
                f"因此后续该盯的是竞争格局有没有变化，而不是等需求周期",
                revenue_anchor,
                industry_anchor,
            )
        )
    elif "growth_above_peers" in flags and vs_peers is not None:
        out.append(
            _claim_from(
                f"公司增速高出同业中位 {vs_peers * 100:.1f} 个百分点，"
                f"相对优势明显，但同业数据为可比公司中位数，口径差异需要核实",
                revenue_anchor,
                peer_anchor,
            )
        )

    # 2) 增长节奏：全年与单季背离
    if (
        "growth_divergence" in flags
        and divergence is not None
        and growth is not None
        and q_growth is not None
    ):
        out.append(
            _claim_from(
                f"全年增速与最新单季方向相反，相差 {abs(divergence) * 100:.1f} 个百分点"
                f"（全年 {growth:+.1%} vs 单季 {q_growth:+.1%}）。"
                f"单季数据比全年数据更接近当前真实状态，"
                f"解读这份报告时应以单季为基准，否则会高估当前动能",
                revenue_anchor,
                quarter_anchor,
            )
        )

    # 3) 利润质量：现金含量
    if "cash_quality_high" in flags and cash_ratio is not None:
        out.append(
            _claim_from(
                f"经营现金流是净利润的 {cash_ratio:.2f} 倍，利润有真实现金流入支撑。"
                f"这一点排除了「靠放宽信用政策冲收入」这一常见风险，"
                f"也让前述增长结论的可信度更高",
                cash_anchor,
                revenue_anchor,
            )
        )
    elif "cash_quality_low" in flags and cash_ratio is not None:
        out.append(
            _claim_from(
                f"经营现金流仅为净利润的 {cash_ratio:.2f} 倍，利润的现金含量偏低。"
                f"在收入仍在增长的情况下，这通常指向应收或存货占用上升，需要下一期报表验证",
                cash_anchor,
            )
        )

    # 4) 价格与量的背离
    if "price_up_in_contracting_industry" in flags and price_hike is not None:
        out.append(
            _claim_from(
                f"行业产量负增长的背景下公司上调出厂价约 {price_hike:.0%}，价格与量走向相反。"
                f"提价能否被渠道消化是后续最需要验证的变量——"
                f"它既可能意味着定价权仍在，也可能加剧渠道库存压力",
                price_anchor,
                industry_anchor,
            )
        )

    # 5) 舆情与经营的时间差
    if "sentiment_lags_revenue_turn" in flags:
        out.append(
            _claim_from(
                "舆情整体仍以中性为主，而单季收入已经转负，两者存在时间差。"
                "市场预期尚未反映经营节奏的变化，若下一期数据延续，"
                "舆情存在滞后修正的可能",
                quarter_anchor,
                *news_anchors,
                strength="观点",
            )
        )

    # 6) 利润率逆势扩张
    if "margin_expanding" in flags and "growth_decelerating" in flags and margin_change is not None:
        out.append(
            _claim_from(
                f"收入增速放缓的同时净利率仍提升 {margin_change * 100:.1f} 个百分点，"
                f"说明利润端由效率而非规模驱动。这种结构在需求走弱时更抗压，"
                f"但也意味着费用继续压缩的空间已经有限",
                cash_anchor,
                revenue_anchor,
            )
        )

    return out


def _summary_text(ctx: dict[str, Any], flags: set[str]) -> str:
    """摘要必须是实质判断，不是"综合三方分析"这类空话。"""
    get = lambda key: _signal(ctx, key)  # noqa: E731
    entity = (ctx.get("entity") or {}).get("name") or "标的公司"
    growth = get("revenue_growth")
    q_growth = get("q_revenue_growth")
    output_growth = get("industry_output_growth")
    vs_peers = get("growth_vs_peers")
    cash_ratio = get("cash_to_profit")

    parts: list[str] = []
    if growth is not None:
        parts.append(f"{entity}当期营收同比 {growth:+.1%}")
    if output_growth is not None and growth is not None:
        source = (
            "增长主要由份额提升驱动，而非行业景气"
            if output_growth < 0 <= growth
            else "增长与行业景气方向一致"
        )
        parts.append(f"行业产量同比 {output_growth:+.1%}，{source}")
    if "growth_divergence" in flags and q_growth is not None:
        parts.append(
            f"但最新单季同比 {q_growth:+.1%}，与全年增速背离，动能在年内已经切换"
        )
    if cash_ratio is not None:
        parts.append(
            f"经营现金流为净利润的 {cash_ratio:.2f} 倍，利润质量"
            + ("未见恶化" if cash_ratio >= 1.0 else "需要进一步核实")
        )
    if vs_peers is not None and "growth_above_peers" in flags:
        parts.append(f"增速较同业中位高出 {vs_peers * 100:.1f} 个百分点")
    if not parts:
        return str((ctx.get("verdict") or {}).get("summary") or "本期报告已生成。")
    return "；".join(parts) + "。"


def _verdict(ctx: dict[str, Any]) -> dict[str, Any]:
    flags = _flag_set(ctx)
    evidence = list(ctx.get("evidence") or [])
    conclusions = _derive_conclusions(ctx, evidence, flags)

    # 核心结论不是"采纳谁"，而是交叉判断；反方视角单独保留，不混进结论里。
    positive = bool({"growth_above_industry", "growth_above_peers", "cash_quality_high"} & flags)
    opposite = (ctx.get("bear") if positive else ctx.get("bull")) or {}
    rejected: list[dict[str, Any]] = []
    seen = {str(item.get("text") or "") for item in conclusions}
    for point in opposite.get("points") or []:
        text = str(point.get("text") or "")
        if not text or text in seen or not point.get("evidence_ids"):
            continue
        seen.add(text)
        rejected.append(point)

    if not conclusions:
        # 派生信号不足时退回到"挑分析师结论"，并在 gaps 里说明为什么降级
        for report in ctx.get("reports") or []:
            if report.get("stance") == "数据不足":
                continue
            for claim in list(report.get("claims") or [])[:2]:
                if claim.get("evidence_ids") and str(claim.get("text")) not in seen:
                    seen.add(str(claim.get("text")))
                    conclusions.append(claim)

    questions: list[str] = []
    if "growth_divergence" in flags:
        questions.append("下一期单季营收能否恢复同比正增长，还是延续负增长")
    if "backlog_expanding" in flags:
        questions.append("合同负债的增量能否在下一季度转化为收入确认")
    if "price_up_in_contracting_industry" in flags:
        questions.append("提价后渠道批价与库存周期的变化")
    if "growth_above_industry" in flags:
        questions.append("份额提升是否来自竞品退出，还是短期促销带来的透支")
    questions.extend(list(ctx.get("open_questions") or []))

    return {
        "summary": _summary_text(ctx, flags),
        "adopted": conclusions[:6],
        "rejected": rejected[:4],
        "confidence": 0.72 if len(conclusions) >= 3 else 0.5,
        "open_questions": questions[:5],
    }


def _risk(ctx: dict[str, Any]) -> dict[str, Any]:
    from equitylens.policy import scan_text

    texts = [str(item.get("text") or "") for item in ctx.get("claims") or []]
    violations = []
    for text in texts:
        for hit in scan_text(text):
            violations.append({"type": hit.type, "quote": hit.quote, "fix": hit.fix})
    return {
        "passed": not violations,
        "violations": violations[:5],
        "required_edits": [v["fix"] for v in violations[:5]],
        "notes": "未发现买卖建议、目标价或收益承诺类表述。" if not violations else "存在需修改的表述。",
    }


_PUNCT = re.compile(r"[\s，。；：、！？,.;:!?（）()「」\[\]\"'“”‘’—\-/·]")


def _norm_key(text: str) -> str:
    """跨章节去重用的归一化键（去标点后取前 28 字）。

    不用全等比较：模板和模型经常只在句末差一个句号，全等会漏掉这类重复。
    取前缀是权衡——太长抓不到改写，太短会误杀两条本来不同的结论。
    """
    return _PUNCT.sub("", str(text))[:28]


def _editor(ctx: dict[str, Any]) -> dict[str, Any]:
    entity = ctx.get("entity") or {}
    verdict = ctx.get("verdict") or {}
    evidence = list(ctx.get("evidence") or [])
    reports = {r.get("role"): r for r in ctx.get("reports") or []}
    flags = _flag_set(ctx)
    fundamental = reports.get("fundamental") or {}
    industry = reports.get("industry") or {}
    sentiment = reports.get("sentiment") or {}

    used: set[str] = set()

    def section(
        title: str, claims: list[dict[str, Any]] | None, narrative: str = ""
    ) -> dict[str, Any]:
        cleaned: list[dict[str, Any]] = []
        for claim in claims or []:
            text = str(claim.get("text") or "").strip()
            if not text:
                continue
            key = _norm_key(text)
            if not key or key in used:
                # 同一句话只允许在整份报告里出现一次。
                # 之前的实现只在"本节内"去重，导致核心结论和基本面章节结构性地重复——
                # 那不是研究报告的结构，那是把同一段话抄了两遍。
                continue
            used.add(key)
            cleaned.append(
                {
                    "text": text,
                    "evidence_ids": list(claim.get("evidence_ids") or []),
                    "strength": claim.get("strength") or "事实",
                    "quote": claim.get("quote"),
                }
            )
        return {"title": title, "narrative": narrative, "claims": cleaned[:5]}

    sections = [
        section(
            "核心结论",
            list(verdict.get("adopted") or []),
            "以下结论由基本面、行业、舆情三个维度的数据交叉得出，每条至少对应两个独立来源；"
            "明细事实另列于后文，不在此重复。",
        ),
        section("基本面分析", list(fundamental.get("claims") or [])),
        section("行业景气与竞争格局", list(industry.get("claims") or [])),
        section("舆情与事件驱动", list(sentiment.get("claims") or [])),
        section(
            "多空分歧",
            list(verdict.get("rejected") or []),
            "以下论点未被纳入核心结论，列示以保留反方视角。",
        ),
    ]

    gaps: list[str] = []
    for report in reports.values():
        gaps.extend(list(report.get("gaps") or []))
    gaps.extend(list(ctx.get("data_gaps") or []))
    gaps = list(dict.fromkeys(str(item) for item in gaps if item))

    occupied = sum(len(item["claims"]) for item in sections)
    if occupied == 0:
        sections = [section("数据缺口", [])]
    elif gaps:
        sections.append(
            section("数据缺口与后续核实", [], "本期未能取到或未能验证的部分：\n- " + "\n- ".join(gaps[:4]))
        )
    sections = [item for item in sections if item["claims"] or item["narrative"]]

    metrics: list[dict[str, Any]] = []
    seen_metrics: set[str] = set()
    for metric in list(fundamental.get("metrics") or []) + list(industry.get("metrics") or []):
        name = str(metric.get("name") or "")
        if not name or name in seen_metrics:
            continue
        seen_metrics.add(name)
        metrics.append(metric)
        if len(metrics) >= 6:
            break

    risks: list[str] = []
    if "growth_divergence" in flags:
        risks.append("单季与全年增速背离，若趋势延续，用全年增速描述当前状态会高估经营动能。")
    if "growth_above_industry" in flags:
        risks.append("增长依赖份额提升，竞品价格策略或渠道政策的变化会直接影响这一逻辑。")
    if "price_up_in_contracting_industry" in flags:
        risks.append("行业缩量期提价，若渠道未能消化，可能转化为库存与批价压力。")
    if "cash_quality_low" in flags:
        risks.append("经营现金流低于净利润，需关注应收账款与存货占用。")
    if "sentiment_lags_revenue_turn" in flags:
        risks.append("舆情仍偏中性而经营已转弱，二者收敛时可能带来情绪面的补跌。")
    risks.extend(
        [
            "AI 生成的结论可能存在事实偏差，所有数据需回溯原始公告核实。",
            "行业景气判断依赖第三方统计口径，不同来源可能存在差异。",
        ]
    )

    return {
        "title": f"{entity.get('name', '目标公司')}研究报告（{ctx.get('as_of', '')}）",
        "entity_name": entity.get("name") or "目标公司",
        "entity_code": entity.get("code") or "",
        "industry": entity.get("industry") or "",
        "as_of": str(ctx.get("as_of") or ""),
        "summary": str(verdict.get("summary") or "本期报告已生成。"),
        "sections": sections,
        "key_metrics": metrics,
        "risks": risks[:6],
        "open_questions": list(verdict.get("open_questions") or []) + gaps[:2],
        "disclaimer": ctx.get("disclaimer")
        or "本报告由 AI 多智能体系统基于公开信息自动生成，仅供研究参考，不构成投资建议。",
        "evidence": evidence,
        "generated_at": str(ctx.get("as_of") or ""),
        "meta": {
            "offline": True,
            "evidence_count": len(evidence),
            "flags": sorted(flags),
        },
    }


_HANDLERS = {
    "planner": _planner,
    "analyst.fundamental": _analyst_fundamental,
    "analyst.industry": _analyst_industry,
    "analyst.sentiment": _analyst_sentiment,
    "debate.bull": _bull,
    "debate.bear": _bear,
    "debate.judge": _debate_judge,
    "verdict": _verdict,
    "risk": _risk,
    "editor": _editor,
}
