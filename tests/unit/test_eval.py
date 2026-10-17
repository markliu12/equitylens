"""评测框架门禁：离线题集能跑出指标、bootstrap 数学正确。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))

from ab_compare import paired_bootstrap  # noqa: E402
from run_eval import eval_one, load_questions  # noqa: E402

QUESTIONS = ROOT / "evals" / "questions.yaml"


def test_question_set_shape():
    questions = load_questions(QUESTIONS, offline_only=False, limit=None)
    assert len(questions) == 25
    offline = [q for q in questions if q.get("offline")]
    assert len(offline) >= 5, "离线门禁子集至少 5 题"
    ids = [q["id"] for q in questions]
    assert len(set(ids)) == len(ids)


async def test_offline_eval_produces_gate_metrics():
    q = load_questions(QUESTIONS, offline_only=True, limit=1)[0]
    result = await eval_one(q, live=False)
    assert result["passed"] is True
    assert result["coverage"] >= 0.9
    assert result["quote_hit_rate"] >= 0.95
    assert result["future_leaks"] == 0
    assert result["n_claims"] > 0


def test_bootstrap_identical_samples_ci_contains_zero():
    diffs = [0.0] * 10
    mean, lo, hi = paired_bootstrap(diffs, n_boot=1000)
    assert mean == 0.0 and lo <= 0.0 <= hi


def test_bootstrap_clear_improvement_excludes_zero():
    diffs = [0.05, 0.1, 0.08, 0.02, 0.07, 0.04]
    mean, lo, hi = paired_bootstrap(diffs, n_boot=2000)
    assert mean > 0 and lo > 0, f"全为正的差异应判显著提升: {(mean, lo, hi)}"


def test_ab_compare_cli_end_to_end(tmp_path: Path):
    def row(qid: str, cov: float) -> str:
        return json.dumps(
            {"id": qid, "tags": [], "status": "COMPLETED", "coverage": cov,
             "quote_hit_rate": 1.0, "future_leaks": 0, "passed": True,
             "n_claims": 10, "n_evidence": 5}
        )

    baseline = tmp_path / "a.jsonl"
    candidate = tmp_path / "b.jsonl"
    baseline.write_text("\n".join(row(f"q{i:02d}", 0.9) for i in range(1, 6)), encoding="utf-8")
    candidate.write_text("\n".join(row(f"q{i:02d}", 0.98) for i in range(1, 6)), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(ROOT / "evals" / "ab_compare.py"), str(baseline), str(candidate)],
        capture_output=True,
        text=True,
        encoding="utf-8",  # Windows runner 默认 cp1252，必须显式指定
        errors="replace",
    )
    assert proc.returncode == 0
    assert "coverage" in proc.stdout
    assert "显著提升" in proc.stdout


@pytest.mark.parametrize("limit", [2])
def test_run_eval_cli_smoke(limit: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "evals" / "run_eval.py"), "--label", "smoke", "--limit", str(limit)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    out = ROOT / "evals" / "results" / "smoke.jsonl"
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == limit
    assert all(json.loads(line)["passed"] for line in lines)
    out.unlink()  # 冒烟产物不留进仓库
