"""研究 DAG 的装配点。

整个系统的"业务形状"只在这一个文件里：改流程只改这里，引擎和 agent 都不用动。
这也让"图结构"变成一个可以版本化的对象（graph_version 会写进 run 记录，
做 A/B 实验时能确认两次运行到底只是 prompt 变了，还是流程也变了）。

    resolve_entity → plan → retrieve(FanOut×N) → evidence_merge
      → analysts(FanOut×3) → analyst_merge
      → debate(LoopNode≤3: bull → bear → judge)
      → verdict → editor_draft → fact_check → compliance → revise → finalize
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from equitylens.agents.analysts.fundamental import FundamentalAnalyst
from equitylens.agents.analysts.industry import IndustryAnalyst
from equitylens.agents.analysts.sentiment import SentimentAnalyst
from equitylens.agents.debate.bear import BearAgent
from equitylens.agents.debate.bull import BullAgent
from equitylens.agents.debate.judge import RoundJudge
from equitylens.agents.debate.protocol import should_stop
from equitylens.agents.editor import EditorAgent
from equitylens.agents.planner import PlannerAgent
from equitylens.agents.retriever import RetrieverNode
from equitylens.agents.risk import ComplianceAgent
from equitylens.agents.verdict import VerdictAgent
from equitylens.engine.graph import Graph, GraphBuilder
from equitylens.engine.node import Aggregate, FanOut, NodeContext, ToolNode
from equitylens.engine.state import StateSchema, append, append_unique, last, merge_map, sum_usage
from equitylens.engine.types import FailureKind, NodePolicy, OnFailure, Patch
from equitylens.errors import NodeError
from equitylens.policy import DISCLAIMER, redact
from equitylens.report.citations import audit_report
from equitylens.report.renderer import to_html, to_markdown
from equitylens.tools.evidence import TIER_LABELS

GRAPH_VERSION = "v1"
MAX_DEBATE_ROUNDS = 3
FOCUSES = ("fundamental", "industry", "sentiment")


def build_schema() -> StateSchema:
    schema = StateSchema()
    schema.channel("question", last, "")
    schema.channel("as_of", last, "")
    schema.channel("entity", last, {})
    schema.channel("plan", last, {})
    schema.channel("evidence", append_unique("id"), [])
    schema.channel("evidence_gaps", append, [])
    schema.channel("research_data", merge_map, {})
    schema.channel("evidence_summary", last, {})
    schema.channel("analyst_reports", append, [])
    schema.channel("analyst_summary", last, {})
    schema.channel("bull_argument", last, None)
    schema.channel("bear_argument", last, None)
    schema.channel("debate_rounds", append, [])
    schema.channel("verdict", last, {})
    schema.channel("draft_report", last, {})
    schema.channel("citation_audit", last, {})
    schema.channel("risk_review", last, {})
    schema.channel("final_report", last, {})
    schema.channel("usage", sum_usage, {})
    return schema


# --------------------------------------------------------------------------- #
# 工具型节点
# --------------------------------------------------------------------------- #
class ResolveEntityNode(ToolNode):
    def __init__(self) -> None:
        super().__init__(
            "resolve_entity",
            deps=(),
            reads=("question",),
            writes=("entity",),
            policy=NodePolicy(timeout_s=15, max_attempts=2),
            label="识别标的",
        )

    async def invoke(self, ctx: NodeContext) -> Patch:
        question = str(ctx.view.get("question") or "")
        entity = await ctx.services.invoke_tool("resolve_entity", query=question)
        if not entity:
            raise NodeError(FailureKind.PROVIDER_DOWN, f"无法识别研究标的: {question[:40]}")
        return Patch(channels={"entity": entity}, meta={"entity": entity})


class FactCheckNode(ToolNode):
    def __init__(self) -> None:
        super().__init__(
            "fact_check",
            deps=("editor_draft",),
            reads=("draft_report", "evidence", "as_of"),
            writes=("citation_audit",),
            policy=NodePolicy(timeout_s=20, on_failure=OnFailure.SKIP),
            label="引用审计",
        )

    async def invoke(self, ctx: NodeContext) -> Patch:
        report = ctx.view.get("draft_report") or {}
        index = {str(item.get("id")): item for item in ctx.view.get("evidence") or []}
        result = audit_report(report, index, ctx.view.get("as_of"))
        return Patch(
            channels={"citation_audit": result.to_dict()},
            meta={"coverage": result.coverage, "quote_hit_rate": result.quote_hit_rate},
        )


class ReviseNode(ToolNode):
    """合规回修。

    刻意先做**确定性改写**而不是再来一次 LLM 重写：改写的目标是"删掉操作性表述"，
    这是规则能解决的；让模型重写反而可能引入新的越界表述，还得再校验一轮。
    """

    def __init__(self) -> None:
        super().__init__(
            "revise",
            deps=("compliance",),
            reads=("draft_report", "risk_review", "citation_audit", "evidence", "as_of"),
            writes=("final_report",),
            policy=NodePolicy(timeout_s=20, on_failure=OnFailure.SKIP),
            label="合规回修",
        )

    async def invoke(self, ctx: NodeContext) -> Patch:
        report = dict(ctx.view.get("draft_report") or {})
        review = ctx.view.get("risk_review") or {}
        audit = ctx.view.get("citation_audit") or {}
        changed = 0

        if not review.get("passed", True):
            for section in report.get("sections") or []:
                for claim in section.get("claims") or []:
                    original = str(claim.get("text") or "")
                    cleaned = redact(original)
                    if cleaned != original:
                        claim["text"] = cleaned
                        changed += 1
                if section.get("narrative"):
                    original = str(section["narrative"])
                    cleaned = redact(original)
                    if cleaned != original:
                        section["narrative"] = cleaned
                        changed += 1
            report["summary"] = redact(str(report.get("summary") or ""))
            violations = review.get("violations") or []
            if violations:
                report["risks"] = [
                    *[f"合规提示：{v.get('type')}（已改写）" for v in violations[:3]],
                    *(report.get("risks") or []),
                ]

        report["disclaimer"] = report.get("disclaimer") or DISCLAIMER
        report.setdefault("evidence", ctx.view.get("evidence") or [])
        report.setdefault("meta", {})
        report["meta"].update(
            {
                "revised": changed > 0,
                "revisions": changed,
                "risk_passed": bool(review.get("passed", True)),
                "citation_coverage": audit.get("coverage"),
                "quote_hit_rate": audit.get("quote_hit_rate"),
            }
        )
        return Patch(
            channels={"final_report": report},
            meta={"revisions": changed, "risk_passed": review.get("passed", True)},
        )


class FinalizeNode(ToolNode):
    def __init__(self) -> None:
        super().__init__(
            "finalize",
            deps=("revise",),
            reads=(
                "final_report",
                "citation_audit",
                "evidence",
                "evidence_summary",
                "analyst_summary",
                "entity",
            ),
            writes=("final_report",),
            policy=NodePolicy(timeout_s=30),
            label="产出报告",
        )

    async def invoke(self, ctx: NodeContext) -> Patch:
        report = dict(ctx.view.get("final_report") or {})
        entity = ctx.view.get("entity") or {}
        audit = ctx.view.get("citation_audit") or {}
        summary = ctx.view.get("evidence_summary") or {}
        analysts = ctx.view.get("analyst_summary") or {}

        report.setdefault("entity_name", entity.get("name") or "")
        report.setdefault("entity_code", entity.get("code") or "")
        report.setdefault("industry", entity.get("industry") or "")
        report.setdefault("as_of", str(ctx.view.get("as_of") or ""))

        # 报告附带的来源列表必须覆盖所有被引用的证据，否则读者会点到空编号。
        # 做法：先按引用顺序取全，再补高等级证据，最后统一截断。
        index = {str(item.get("id")): item for item in (ctx.view.get("evidence") or [])}
        referenced: list[str] = []
        for section in report.get("sections") or []:
            for claim in section.get("claims") or []:
                referenced.extend(str(i) for i in claim.get("evidence_ids") or [])
        ordered_ids = list(dict.fromkeys(referenced))
        extras = [eid for eid in index if eid not in ordered_ids]
        selected = (ordered_ids + extras)[:24]
        report["evidence"] = [index[eid] for eid in selected if eid in index]
        meta = dict(report.get("meta") or {})
        meta.update(
            {
                "evidence_count": summary.get("count") or len(report.get("evidence") or []),
                "evidence_by_tier": {
                    TIER_LABELS.get(int(k), k): v for k, v in (summary.get("by_tier") or {}).items()
                },
                "analyst_stances": analysts.get("stances") or {},
                "citation_audit": audit,
            }
        )
        report["meta"] = meta

        artifact_dir = (ctx.services.extras or {}).get("artifact_dir")
        if artifact_dir:
            target = Path(artifact_dir) / ctx.run_id
            target.mkdir(parents=True, exist_ok=True)
            (target / "report.md").write_text(to_markdown(report, audit), encoding="utf-8")
            (target / "report.html").write_text(to_html(report, audit), encoding="utf-8")
            (target / "citation_audit.json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            meta["artifacts"] = ["report.md", "report.html", "citation_audit.json"]

        await ctx.services.emit(
            "report.ready",
            node_id=self.id,
            coverage=audit.get("coverage"),
            title=report.get("title"),
            sections=len(report.get("sections") or []),
        )
        return Patch(channels={"final_report": report}, meta={"coverage": audit.get("coverage")})


# --------------------------------------------------------------------------- #
# 聚合函数
# --------------------------------------------------------------------------- #
def _summarize_evidence(items: list[Any], _view: Any) -> dict[str, Any]:
    tiers = Counter(str(item.get("tier")) for item in items)
    return {
        "count": len(items),
        "by_tier": dict(tiers),
        "sources": len({item.get("source_name") for item in items}),
        "kinds": dict(Counter(str(item.get("kind")) for item in items)),
    }


def _summarize_analysts(items: list[Any], _view: Any) -> dict[str, Any]:
    return {
        "count": len(items),
        "stances": {str(item.get("role")): item.get("stance") for item in items},
        "insufficient": [
            str(item.get("role")) for item in items if item.get("stance") == "数据不足"
        ],
        "confidence": {
            str(item.get("role")): item.get("confidence") for item in items
        },
    }


def _focus_for(question: str) -> str:
    if any(key in question for key in ("行业", "景气", "竞争", "格局", "同业", "份额")):
        return "industry"
    if any(key in question for key in ("舆情", "新闻", "事件", "公告", "市场", "情绪")):
        return "sentiment"
    return "fundamental"


def build_graph() -> Graph:
    schema = build_schema()
    builder = GraphBuilder(schema)

    retrieve = FanOut(
        "retrieve",
        items=lambda view: list((view.get("plan") or {}).get("sub_questions") or []),
        worker_factory=lambda item, index: RetrieverNode(
            f"retrieve[{index}]", focus=_focus_for(str(item)), deps=("plan",)
        ),
        deps=("plan",),
        reads=("plan", "entity", "as_of"),
        writes=("evidence", "research_data", "evidence_gaps"),
        policy=NodePolicy(max_parallel=3, on_failure=OnFailure.SKIP),
        label="并行取数",
    )

    evidence_merge = Aggregate(
        "evidence_merge",
        source="evidence",
        target="evidence_summary",
        reduce_fn=_summarize_evidence,
        deps=("retrieve",),
        min_success_ratio=0.5,
        policy=NodePolicy(on_failure=OnFailure.SKIP),
        label="证据合并",
    )

    analysts = FanOut(
        "analysts",
        items=lambda _view: ["fundamental", "industry", "sentiment"],
        worker_factory=lambda focus, _index: {
            "fundamental": FundamentalAnalyst,
            "industry": IndustryAnalyst,
            "sentiment": SentimentAnalyst,
        }[str(focus)](),
        deps=("evidence_merge",),
        reads=("entity", "as_of", "evidence", "research_data", "evidence_gaps"),
        writes=("analyst_reports",),
        policy=NodePolicy(max_parallel=3, on_failure=OnFailure.SKIP),
        label="并行分析",
    )

    analyst_merge = Aggregate(
        "analyst_merge",
        source="analyst_reports",
        target="analyst_summary",
        reduce_fn=_summarize_analysts,
        deps=("analysts",),
        min_success_ratio=0.5,
        policy=NodePolicy(on_failure=OnFailure.SKIP),
        label="分析汇总",
    )

    debate = _build_debate()

    builder.add(
        ResolveEntityNode(),
        PlannerAgent(),
        retrieve,
        evidence_merge,
        analysts,
        analyst_merge,
        debate,
        VerdictAgent(),
        EditorAgent(),
        FactCheckNode(),
        ComplianceAgent(),
        ReviseNode(),
        FinalizeNode(),
    )
    return builder.build()


def _build_debate() -> Any:
    from equitylens.engine.node import LoopNode

    return LoopNode(
        "debate",
        body=[BullAgent(), BearAgent(), RoundJudge()],
        max_rounds=MAX_DEBATE_ROUNDS,
        until=lambda view, _rounds: should_stop(
            list(view.get("debate_rounds") or []), MAX_DEBATE_ROUNDS
        )[0],
        deps=("analyst_merge",),
        reads=(
            "entity",
            "evidence",
            "analyst_reports",
            "bull_argument",
            "bear_argument",
            "debate_rounds",
        ),
        writes=("bull_argument", "bear_argument", "debate_rounds"),
        policy=NodePolicy(timeout_s=900, max_attempts=1, on_failure=OnFailure.SKIP),
        label="多空辩论",
    )
