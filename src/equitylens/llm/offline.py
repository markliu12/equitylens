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


def _analyst_common(
    ctx: dict[str, Any], role: str, stance: str, confidence: float
) -> dict[str, Any]:
    """分析师共用逻辑：先 facts 转 Claim，facts 不够就用本维度证据兜底，再没有就写缺口。"""
    evidence = list(ctx.get("evidence") or [])
    claims: list[dict[str, Any]] = []
    for fact in (ctx.get("facts") or [])[:4]:
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
    signals = ctx.get("signals") or {}
    growth = float(signals.get("revenue_growth") or 0.0)
    margin = float(signals.get("net_margin") or 0.0)
    stance = "偏正面" if growth >= 0.08 else ("中性" if growth >= 0 else "偏负面")
    confidence = 0.72 if ctx.get("metrics") else 0.4
    payload = _analyst_common(ctx, "fundamental", stance, confidence)
    payload["metrics"] = list(ctx.get("metrics") or [])
    evidence = list(ctx.get("evidence") or [])
    margin_fact = next(
        (fact for fact in ctx.get("facts") or [] if "净利率" in str(fact.get("name") or "")),
        None,
    )
    anchor = None
    if margin_fact and margin_fact.get("evidence_ids"):
        anchor = _by_id(evidence, margin_fact["evidence_ids"][0])
    if margin and anchor:
        payload["claims"].append(
            {
                "text": (
                    f"净利率约 {margin:.1%}，"
                    f"{'盈利质量保持在较高水平' if margin > 0.2 else '处于行业常见区间'}。"
                ),
                "evidence_ids": [anchor["id"]],
                "strength": "事实",
                "quote": (anchor.get("snippet") or "")[:60] or None,
            }
        )
    return payload


def _analyst_industry(ctx: dict[str, Any]) -> dict[str, Any]:
    industry = ctx.get("industry") or {}
    stage = industry.get("cycle_stage") or "扩张"
    stance = "偏正面" if stage in {"扩张", "复苏"} else ("中性" if stage == "放缓" else "偏负面")
    payload = _analyst_common(ctx, "industry", stance, 0.65 if industry else 0.4)
    evidence = list(ctx.get("evidence") or [])
    if industry.get("summary"):
        anchor = None
        for evidence_id in industry.get("evidence_ids") or []:
            anchor = _by_id(evidence, str(evidence_id))
            if anchor:
                break
        if anchor is None:
            picked = _pick(evidence, "industry", limit=1)
            anchor = picked[0] if picked else None
        if anchor is not None:
            payload["claims"].insert(
                0,
                {
                    "text": str(industry["summary"]),
                    "evidence_ids": [anchor["id"]],
                    "strength": "事实",
                    "quote": (anchor.get("snippet") or "")[:60] or None,
                },
            )
    payload["metrics"] = list(industry.get("metrics") or [])
    return payload


def _analyst_sentiment(ctx: dict[str, Any]) -> dict[str, Any]:
    news = ctx.get("news") or []
    if len(news) < 3:
        payload = _analyst_common(ctx, "sentiment", "数据不足", 0.3)
        payload["gaps"] = [*payload.get("gaps", []), "新闻样本少于 3 条，未给出情绪评分"]
        return payload
    positive = sum(1 for item in news if (item.get("sentiment") or 0) > 0.2)
    negative = sum(1 for item in news if (item.get("sentiment") or 0) < -0.2)
    if positive > negative * 1.5:
        stance = "偏正面"
    elif negative > positive:
        stance = "偏负面"
    else:
        stance = "中性"

    def label(score: float) -> str:
        if score > 0.2:
            return "偏正面"
        if score < -0.2:
            return "偏负面"
        return "中性"

    claims = [
        {
            "text": f"{item.get('title')}（媒体倾向：{label(float(item.get('sentiment') or 0))}）",
            "evidence_ids": [item["id"]],
            "strength": "观点",
            "quote": (item.get("snippet") or "")[:60] or None,
        }
        for item in news[:4]
        if item.get("id")
    ]
    evidence = list(ctx.get("evidence") or [])
    announcements = [item for item in evidence if item.get("kind") == "announcement"]
    for item in announcements[:1]:
        claims.append(
            {
                "text": f"公司公告：{item.get('title')}",
                "evidence_ids": [item["id"]],
                "strength": "事实",
                "quote": (item.get("snippet") or "")[:60] or None,
            }
        )
    return {
        "role": "sentiment",
        "stance": stance,
        "confidence": 0.6,
        "metrics": [
            {
                "name": "新闻情绪净值",
                "value": f"{(positive - negative) / max(len(news), 1):+.2f}",
                "period": "近 30 日",
                "evidence_ids": [item["id"] for item in news[:3] if item.get("id")],
            },
            {
                "name": "正/负面报道条数",
                "value": f"{positive}/{negative}",
                "period": "近 30 日",
                "evidence_ids": [item["id"] for item in news[:3] if item.get("id")],
            },
        ],
        "claims": claims[:5],
        "gaps": list(ctx.get("gaps") or []),
    }


def _debate_common(ctx: dict[str, Any], side: str) -> dict[str, Any]:
    evidence = ctx.get("evidence") or []
    reports = ctx.get("reports") or []
    picked = evidence[:3]
    positive_roles = {"fundamental"} if side == "bull" else {"sentiment", "industry"}
    claims: list[dict[str, Any]] = []
    for report in reports:
        if report.get("role") in positive_roles:
            claims.extend(list(report.get("claims") or []))
    if not claims:
        for item in picked[:2]:
            claims.append(
                {
                    "text": f"{item.get('title') or '公开信息'}：{(item.get('snippet') or '')[:44]}",
                    "evidence_ids": [item["id"]],
                    "strength": "事实",
                    "quote": (item.get("snippet") or "")[:60] or None,
                }
            )
    if not claims:
        anchor = ctx.get("entity") or {}
        claims = [
            {
                "text": f"{anchor.get('name', '公司')}的公开信息尚不足以支撑强判断。",
                "evidence_ids": [],
                "strength": "观点",
            }
        ]
    points = []
    for claim in claims[:3]:
        item = dict(claim)
        if side == "bear":
            item["text"] = _bearify(item["text"])
        points.append(item)
    thesis = (
        f"{'多方' if side == 'bull' else '空方'}认为："
        + ("经营指标与行业景气提供支撑，证据链相对完整。"
           if side == "bull"
           else "增长质量与外部环境存在不确定性，需警惕预期差。")
    )
    opponent = ctx.get("opponent") or {}
    rebuttals = []
    for point in (opponent.get("points") or [])[:2]:
        rebuttals.append(
            {
                "text": f"对方论点「{str(point.get('text', ''))[:24]}」证据等级不足或存在时效问题。",
                "evidence_ids": [picked[-1]["id"]] if picked else [],
                "strength": "观点",
            }
        )
    return {
        "side": side,
        "thesis": thesis,
        "points": points,
        "rebuttals": rebuttals,
        "new_evidence_ids": [e["id"] for e in picked if e.get("id")],
        "_reports_seen": len(reports),
    }


def _bearify(text: str) -> str:
    mapping = {
        "同比增长": "增速较前期放缓，",
        "较高水平": "存在回落压力，",
        "提供支撑": "但持续性待验证，",
    }
    for key, prefix in mapping.items():
        if key in text:
            return prefix + text
    return "从风险角度看，" + text


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


def _verdict(ctx: dict[str, Any]) -> dict[str, Any]:
    rounds = ctx.get("rounds") or []
    leader = "tie"
    if rounds:
        last = rounds[-1]
        leader = last.get("leader") or "tie"
    preferred = "bull" if leader == "tie" else leader
    adopted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for report in ctx.get("reports") or []:
        if report.get("stance") == "数据不足":
            continue
        positive = report.get("stance") in {"偏正面", "中性"}
        for claim in list(report.get("claims") or [])[:3]:
            text = str(claim.get("text") or "")
            if not text or text in seen:
                continue
            # 核心结论优先放"有证据的事实"，观点类结论留到对应章节
            if not claim.get("evidence_ids") or claim.get("strength") == "观点":
                continue
            seen.add(text)
            (adopted if positive else rejected).append(claim)
    if preferred == "bear" and len(adopted) > 3:
        rejected.extend(adopted[3:])
        adopted = adopted[:3]
    summary = (
        "综合三方分析，"
        + ("多方论据的证据链更完整" if preferred == "bull" else "空方提出的风险点更值得关注")
        + (
            f"，辩论共进行 {len(rounds)} 轮，共识与分歧已分别列示。"
            if rounds
            else "，未形成有效辩论。"
        )
    )
    return {
        "summary": summary,
        "adopted": adopted[:6],
        "rejected": rejected[:4],
        "confidence": 0.62 if rounds else 0.35,
        "open_questions": list(ctx.get("open_questions") or ["行业景气度持续性", "下一期财报兑现度"]),
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


def _editor(ctx: dict[str, Any]) -> dict[str, Any]:
    entity = ctx.get("entity") or {}
    verdict = ctx.get("verdict") or {}
    evidence = list(ctx.get("evidence") or [])
    reports = {r.get("role"): r for r in ctx.get("reports") or []}
    fundamental = reports.get("fundamental") or {}
    industry = reports.get("industry") or {}
    sentiment = reports.get("sentiment") or {}

    sections: list[dict[str, Any]] = []

    def section(title: str, claims: list[dict[str, Any]], narrative: str = "") -> dict[str, Any]:
        cleaned = []
        # 只在本节内去重：同一结论同时出现在"核心结论"和明细章节是正常的研究报告结构，
        # 但一节之内重复就是硬伤。
        seen: set[str] = set()
        for claim in claims:
            text = claim.get("text")
            if not text or text in seen:
                continue
            seen.add(text)
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
        section("核心结论", list(verdict.get("adopted") or [])),
        section("基本面分析", list(fundamental.get("claims") or [])),
        section("行业景气与竞争格局", list(industry.get("claims") or [])),
        section("舆情与事件驱动", list(sentiment.get("claims") or [])),
        section(
            "多空分歧",
            list(verdict.get("rejected") or []),
            "上述论点未被采纳或需要更多证据支持，列示以保留反方视角。",
        ),
    ]
    sections = [s for s in sections if s["claims"]]
    if not sections:
        sections = [section("数据缺口", [])]

    metrics = list(fundamental.get("metrics") or [])[:4]
    gaps: list[str] = []
    for report in reports.values():
        gaps.extend(list(report.get("gaps") or []))
    risks = [
        "AI 生成的结论可能存在事实偏差，所有数据需回溯原始公告核实。",
        "行业景气判断依赖第三方统计口径，不同来源可能存在差异。",
    ]
    return {
        "title": f"{entity.get('name', '目标公司')}研究报告（{ctx.get('as_of', '')}）",
        "entity_name": entity.get("name") or "目标公司",
        "entity_code": entity.get("code") or "",
        "industry": entity.get("industry") or "",
        "as_of": str(ctx.get("as_of") or ""),
        "summary": str(verdict.get("summary") or "本期报告已生成。"),
        "sections": sections,
        "key_metrics": metrics,
        "risks": risks,
        "open_questions": list(verdict.get("open_questions") or []) + gaps[:2],
        "disclaimer": ctx.get("disclaimer")
        or "本报告由 AI 多智能体系统基于公开信息自动生成，仅供研究参考，不构成投资建议。",
        "evidence": evidence,
        "generated_at": str(ctx.get("as_of") or ""),
        "meta": {"offline": True, "evidence_count": len(evidence)},
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
