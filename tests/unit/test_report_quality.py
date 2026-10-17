"""报告质量门禁。

这些断言锁的不是"能跑通"，而是"读起来像不像一份研究报告"。
每一条都对应一个真实出现过的问题：

1. 核心结论是明细章节的摘录（结构性重复）——因为终裁直接搬运了分析师 claims；
2. 结论写成"指标 + 数值"，没有判断——因为生成逻辑只会把 facts 拼成一句话；
3. 多空分歧只是给结论加了"从风险角度看"前缀，本质仍是复述；
4. 摘要用"综合三方分析，多方论据更完整"这类空话收尾。

这四类问题在代码层面都能跑通、schema 都合法、引用覆盖率都很好看，
所以只有靠这类"内容形态断言"才能拦住。任何一条回退，CI 立刻红。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from equitylens.config import Settings
from equitylens.runtime import build_runtime

#: 核心结论里必须出现的判断性表述。复述类结论（"营收为 X 亿元"）不含任何一个。
JUDGEMENT_MARKERS = (
    "来源",
    "驱动",
    "背离",
    "意味着",
    "说明",
    "高于",
    "低于",
    "时间差",
    "支撑",
    "压力",
    "不可持续",
    "取决于",
    "空间",
    "矛盾",
    "掩盖",
    "透支",
    "相反",
    "高估",
    "切换",
    "动能",
    "更接近",
    "基准",
)

#: 单纯转述指标的句式：主语 + "为" + 数字
_MERE_METRIC = re.compile(r"^[^，。]{0,24}为\s*[-+]?[\d,.]+\s*[%亿元万倍]")

_PUNCT = re.compile(r"[\s，。；：、！？,.;:!?（）()「」\[\]\"'“”‘’—\-/·]")


def _norm(text: str) -> str:
    return _PUNCT.sub("", str(text))[:28]


def _sections(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("title")): item for item in report.get("sections") or []}


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """跑一次完整的离线链路，所有断言共享同一份产出。"""
    root = tmp_path_factory.mktemp("quality")
    settings = Settings(
        app_mode="offline",
        db_path=str(root / "quality.db"),
        cache_dir=str(root / "cache"),
        artifact_dir=str(root / "artifacts"),
    )

    async def _run() -> dict[str, Any]:
        runtime = build_runtime(settings)
        try:
            outcome = await runtime.runner.run(
                "run_quality",
                question="分析贵州茅台最近一期经营情况与行业景气度",
                as_of="2026-09-30",
            )
            return dict(outcome.state.get("final_report") or {})
        finally:
            await runtime.aclose()

    result = asyncio.run(_run())
    assert result, "未能产出最终报告"
    return result


def test_required_sections_present(report: dict[str, Any]) -> None:
    titles = set(_sections(report))
    required = {"核心结论", "基本面分析", "行业景气与竞争格局", "舆情与事件驱动"}
    missing = required - titles
    assert not missing, f"报告缺少必需章节: {missing}（实际章节: {sorted(titles)}）"


def test_no_claim_repeats_across_sections(report: dict[str, Any]) -> None:
    """同一句话不允许出现在两个章节里。

    这是本次改造最核心的约束：旧实现把终裁"采纳"的结论直接当作核心结论，
    而那些结论本身就是分析师 claims，于是核心结论必然等于基本面章节的前三条。
    """
    seen: dict[str, str] = {}
    for section in report.get("sections") or []:
        title = str(section.get("title"))
        for claim in section.get("claims") or []:
            key = _norm(claim.get("text") or "")
            if not key:
                continue
            assert key not in seen, (
                f"同一结论同时出现在「{seen[key]}」和「{title}」: {str(claim.get('text'))[:40]}"
            )
            seen[key] = title


def test_core_conclusions_are_judgements(report: dict[str, Any]) -> None:
    """核心结论必须是判断，不能是指标转述。"""
    sections = _sections(report)
    core = sections.get("核心结论")
    assert core is not None, "缺少核心结论章节"
    claims = core.get("claims") or []
    assert claims, "核心结论为空"

    for claim in claims:
        text = str(claim.get("text") or "")
        assert not _MERE_METRIC.match(text), f"核心结论是指标转述而非判断: {text[:50]}"
        assert any(marker in text for marker in JUDGEMENT_MARKERS), (
            f"核心结论缺少判断性表述（应说明变化方向、来源或含义）: {text[:50]}"
        )


def test_core_conclusions_cross_source(report: dict[str, Any]) -> None:
    """每条核心结论至少由两个独立证据交叉支撑。

    只有一个来源的判断仍然可以写进明细章节，但不该进核心结论——
    读者无法从单点信息推出它的重要性。
    """
    core = _sections(report).get("核心结论") or {}
    for claim in core.get("claims") or []:
        ids = list(claim.get("evidence_ids") or [])
        assert len(ids) >= 2, (
            f"核心结论只挂了 {len(ids)} 个来源，不构成交叉判断: {str(claim.get('text'))[:50]}"
        )


def test_factual_claims_carry_evidence(report: dict[str, Any]) -> None:
    """标为"事实"的结论必须有引用；没有证据就必须降级成"观点"。"""
    for section in report.get("sections") or []:
        for claim in section.get("claims") or []:
            if str(claim.get("strength")) == "事实":
                assert claim.get("evidence_ids"), (
                    f"事实性结论缺少证据: {str(claim.get('text'))[:50]}"
                )


def test_debate_section_is_rebuttal_not_echo(report: dict[str, Any]) -> None:
    """多空分歧要讲清"对方的判断在什么条件下不成立"，不能是结论加前缀复述。"""
    section = _sections(report).get("多空分歧")
    if not section:
        pytest.skip("本期没有触发分歧章节")
    markers = ("但", "不能", "不足以", "高估", "如果", "前提", "掩盖", "透支", "先于")
    for claim in section.get("claims") or []:
        text = str(claim.get("text") or "")
        assert any(marker in text for marker in markers), (
            f"多空分歧缺少反驳结构，疑似复述明细结论: {text[:50]}"
        )


def test_summary_is_substantive(report: dict[str, Any]) -> None:
    """摘要要给出状态判断，不能用"综合几方分析"收尾。"""
    summary = str(report.get("summary") or "")
    assert summary, "摘要为空"
    assert "综合三方分析" not in summary, "摘要仍是空话模板"
    assert any(
        marker in summary for marker in ("同比", "增速", "个百分点", "倍", "走高", "回落")
    ), f"摘要缺少可核验的数量描述: {summary[:50]}"


def test_flags_recorded_in_meta(report: dict[str, Any]) -> None:
    """判断标签要落进 meta，便于前端标注与事后归因。"""
    meta = report.get("meta") or {}
    assert "flags" in meta, "报告 meta 缺少判断标签，无法回溯结论依据"
    assert isinstance(meta["flags"], list)
