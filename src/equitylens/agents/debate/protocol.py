"""辩论协议：终止条件与打分口径。

为什么必须有终止条件：
多智能体辩论最常见的失败不是"辩得不好"，而是"停不下来"——双方不断重复已说过的
论点，每一轮都在烧钱。这里用三个信号组合判断：
1. 轮数硬上限（兜底）；
2. 新增信息比例过低（说明在复读）；
3. 领先方两轮之间得分差变化很小（说明已收敛）。

三个条件任一满足即停，并记录是哪一条触发的——这个记录本身就是很好的调试素材。
"""

from __future__ import annotations

from typing import Any

from equitylens.agents.schemas import RoundScore

NEW_INFO_FLOOR = 0.2
CONVERGENCE_DELTA = 0.3


def _score_gap(round_dict: dict[str, Any]) -> float:
    bull = round_dict.get("bull") or {}
    bear = round_dict.get("bear") or {}
    bull_total = bull.get("total")
    bear_total = bear.get("total")
    if bull_total is None or bear_total is None:
        # 容错：模型没给 total 时按加权公式现算
        bull_total = _weighted(bull)
        bear_total = _weighted(bear)
    return float(bull_total) - float(bear_total)


def _weighted(scores: dict[str, Any]) -> float:
    return (
        float(scores.get("logic") or 3) * 0.30
        + float(scores.get("evidence") or 3) * 0.35
        + float(scores.get("novelty") or 3) * 0.15
        + float(scores.get("rebuttal") or 3) * 0.20
    )


def should_stop(rounds: list[dict[str, Any]], max_rounds: int) -> tuple[bool, str]:
    if not rounds:
        return False, ""
    if len(rounds) >= max_rounds:
        return True, "达到轮数上限"
    last = rounds[-1]
    ratio = float(last.get("new_info_ratio") or 0.0)
    if ratio < NEW_INFO_FLOOR:
        return True, f"新增信息比例 {ratio:.2f} < {NEW_INFO_FLOOR}，判定为复读"
    if len(rounds) >= 2:
        prev_gap = _score_gap(rounds[-2])
        last_gap = _score_gap(last)
        if abs(last_gap - prev_gap) < CONVERGENCE_DELTA:
            return True, f"两轮得分差变化 {abs(last_gap - prev_gap):.2f} < {CONVERGENCE_DELTA}，判定收敛"
    return False, ""


def summarize_rounds(rounds: list[dict[str, Any]]) -> dict[str, Any]:

    bull_wins = bear_wins = ties = 0
    for item in rounds:
        leader = item.get("leader") or "tie"
        if leader == "bull":
            bull_wins += 1
        elif leader == "bear":
            bear_wins += 1
        else:
            ties += 1
    last = rounds[-1] if rounds else {}
    return {
        "rounds": len(rounds),
        "bull_wins": bull_wins,
        "bear_wins": bear_wins,
        "ties": ties,
        "last_leader": last.get("leader") or "tie",
        "last_new_info_ratio": last.get("new_info_ratio"),
        "unresolved": last.get("unresolved") or [],
    }


def validate_round(payload: dict[str, Any]) -> RoundScore:
    return RoundScore.model_validate(payload)
