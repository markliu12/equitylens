"""结构化输出契约（单一事实源）。

所有 Agent 的输入输出都在这里定义。理由：
1. 模型输出必须可校验——没有 schema 的 LLM 输出等于字符串拼接；
2. 报告里的 `Claim.evidence_ids` 让"可审计"（能否溯源）和"可评测"（引用覆盖率）
   同时成立，是整个项目最重要的一个设计；
3. 嵌套深度刻意控制在 2 层以内，模型的 JSON 合法率会明显更高（实测修复率能降一半）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from equitylens.policy import DISCLAIMER


class Strength(StrEnum):
    FACT = "事实"
    OPINION = "观点"


class Stance(StrEnum):
    POSITIVE = "偏正面"
    NEUTRAL = "中性"
    NEGATIVE = "偏负面"
    INSUFFICIENT = "数据不足"


class Base(BaseModel):
    # 模型偶尔会多吐字段，宽容掉比整条失败更划算
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Claim(Base):
    """一条结论。有证据的标"事实"，没证据的必须标"观点"——不允许含糊。"""

    text: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list)
    strength: Strength = Strength.FACT
    #: 原文片段，用于确定性校验引用是否真的存在于证据里
    quote: str | None = Field(default=None, max_length=300)


class Metric(Base):
    name: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=60)
    period: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ResearchPlan(Base):
    entity_name: str
    entity_code: str = ""
    industry: str = ""
    #: 必填：Pydantic 不会校验"默认值"，给默认值会让缺失字段静默通过
    sub_questions: list[str] = Field(min_length=1, max_length=6)
    required_data: list[str] = Field(default_factory=list)
    analysts_enabled: list[str] = Field(default_factory=list)
    est_budget_cny: float = 0.4


class AnalystReport(Base):
    role: str
    stance: Stance = Stance.INSUFFICIENT
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    metrics: list[Metric] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class Argument(Base):
    side: Literal["bull", "bear"]
    thesis: str = Field(min_length=1, max_length=300)
    points: list[Claim] = Field(default_factory=list)
    rebuttals: list[Claim] = Field(default_factory=list)
    new_evidence_ids: list[str] = Field(default_factory=list)


class ScoreSet(Base):
    logic: int = Field(ge=1, le=5)
    evidence: int = Field(ge=1, le=5)
    novelty: int = Field(ge=1, le=5)
    rebuttal: int = Field(ge=1, le=5)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> float:
        return round(
            self.logic * 0.30 + self.evidence * 0.35 + self.novelty * 0.15 + self.rebuttal * 0.20,
            3,
        )


class RoundScore(Base):
    round: int = Field(ge=1, le=5)
    bull: ScoreSet
    bear: ScoreSet
    new_info_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    leader: Literal["bull", "bear", "tie"] = "tie"
    unresolved: list[str] = Field(default_factory=list)
    note: str = ""


class Verdict(Base):
    summary: str = Field(min_length=1, max_length=600)
    adopted: list[Claim] = Field(default_factory=list)
    rejected: list[Claim] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    open_questions: list[str] = Field(default_factory=list)


class RiskViolation(Base):
    type: str
    quote: str
    fix: str


class RiskReview(Base):
    passed: bool = True
    violations: list[RiskViolation] = Field(default_factory=list)
    required_edits: list[str] = Field(default_factory=list)
    notes: str = ""


class EvidenceRef(Base):
    id: str
    source_name: str = ""
    tier: int = Field(default=3, ge=1, le=4)
    title: str = ""
    url: str = ""
    published_at: str | None = None
    snippet: str = ""
    kind: str = ""


class ReportSection(Base):
    title: str
    narrative: str = ""
    claims: list[Claim] = Field(default_factory=list)


class ResearchReport(Base):
    title: str
    entity_name: str
    entity_code: str = ""
    industry: str = ""
    as_of: str
    summary: str
    sections: list[ReportSection] = Field(default_factory=list)
    key_metrics: list[Metric] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER
    evidence: list[EvidenceRef] = Field(default_factory=list)
    generated_at: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)

    def iter_claims(self) -> list[Claim]:
        claims: list[Claim] = []
        for section in self.sections:
            claims.extend(section.claims)
        return claims


#: 供 prompt 渲染的紧凑 schema 说明（不用完整 JSON Schema，省 token 且模型更好懂）
SCHEMA_HINTS: dict[str, str] = {
    "ResearchPlan": (
        "entity_name, entity_code, industry, sub_questions[1-6 条中文问题], "
        "required_data[], analysts_enabled[], est_budget_cny"
    ),
    "AnalystReport": (
        "role, stance(偏正面|中性|偏负面|数据不足), confidence(0-1), "
        "metrics[{name,value,period,evidence_ids}], claims[{text,evidence_ids,strength,quote}], gaps[]"
    ),
    "Argument": (
        "side(bull|bear), thesis, points[{text,evidence_ids,strength,quote}], "
        "rebuttals[], new_evidence_ids[]"
    ),
    "RoundScore": (
        "round, bull{logic,evidence,novelty,rebuttal}, bear{...}, "
        "new_info_ratio(0-1), leader(bull|bear|tie), unresolved[], note"
    ),
    "Verdict": "summary, adopted[{text,evidence_ids,...}], rejected[], confidence(0-1), open_questions[]",
    "RiskReview": "passed(bool), violations[{type,quote,fix}], required_edits[], notes",
    "ResearchReport": (
        "title, entity_name, entity_code, industry, as_of, summary, "
        "sections[{title,narrative,claims[{text,evidence_ids,strength,quote}]}], "
        "key_metrics[], risks[], open_questions[], disclaimer"
    ),
}
