from __future__ import annotations

from equitylens.agents.debate.protocol import should_stop, summarize_rounds


def _round(round_no: int, bull: float, bear: float, ratio: float) -> dict:
    return {
        "round": round_no,
        "bull": {"total": bull},
        "bear": {"total": bear},
        "new_info_ratio": ratio,
        "leader": "bull" if bull > bear else "bear" if bear > bull else "tie",
        "unresolved": [],
    }


def test_stop_at_round_limit() -> None:
    rounds = [_round(1, 4.0, 3.0, 1.0), _round(2, 4.0, 3.0, 1.0), _round(3, 4.0, 3.0, 1.0)]
    stop, reason = should_stop(rounds, 3)
    assert stop and "轮数" in reason


def test_stop_when_new_info_is_low() -> None:
    rounds = [_round(1, 4.0, 3.0, 1.0), _round(2, 4.0, 3.0, 0.15)]
    stop, reason = should_stop(rounds, 3)
    assert stop and "复读" in reason


def test_stop_when_score_gap_converges() -> None:
    rounds = [_round(1, 4.0, 3.0, 0.9), _round(2, 4.05, 3.0, 0.8)]
    stop, reason = should_stop(rounds, 3)
    assert stop and "收敛" in reason


def test_continue_when_meaningful_new_information() -> None:
    rounds = [_round(1, 4.0, 3.0, 0.9), _round(2, 4.6, 2.4, 0.7)]
    stop, _ = should_stop(rounds, 3)
    assert not stop


def test_empty_rounds_do_not_stop() -> None:
    assert should_stop([], 3)[0] is False


def test_summarize_rounds_counts_wins() -> None:
    rounds = [_round(1, 4.2, 3.6, 0.9), _round(2, 3.4, 4.0, 0.5)]
    summary = summarize_rounds(rounds)
    assert summary["rounds"] == 2
    assert summary["bull_wins"] == 1
    assert summary["bear_wins"] == 1
    assert summary["last_leader"] == "bear"
