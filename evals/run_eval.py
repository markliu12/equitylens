"""评测运行器：对题集逐题跑完整研究流水线，输出每题一行 JSONL 指标。

用法：
    python evals/run_eval.py --label baseline                 # 离线题集（CI 可用）
    python evals/run_eval.py --label exp-a --live             # 全量 25 题（需 API key）
    python evals/run_eval.py --label quick --limit 2          # 调试子集

指标全部是确定性的（不依赖第二个模型当裁判）：
    coverage        引用覆盖率（事实型结论挂了证据的比例）
    quote_hit_rate  quote 命中原文的比例
    future_leaks    前视偏差条数（必须为 0）
    passed          引用审计门禁
    n_claims        结论总数（防止"少写结论刷覆盖率"）
    status          COMPLETED / PARTIAL / FAILED
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from equitylens.config import Settings  # noqa: E402
from equitylens.runtime import build_runtime  # noqa: E402


def load_questions(path: Path, *, offline_only: bool, limit: int | None) -> list[dict[str, Any]]:
    questions = yaml.safe_load(path.read_text(encoding="utf-8"))
    if offline_only:
        questions = [q for q in questions if q.get("offline")]
    if limit:
        questions = questions[:limit]
    return questions


async def eval_one(question: dict[str, Any], *, live: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            app_mode="dev" if live else "offline",
            db_path=str(Path(tmp) / "eval.db"),
            cache_dir=str(Path(tmp) / "cache"),
            artifact_dir=str(Path(tmp) / "artifacts"),
        )
        runtime = build_runtime(settings)
        try:
            outcome = await runtime.runner.run(
                f"eval_{question['id']}",
                question=question["question"],
                as_of=question["as_of"],
            )
        finally:
            await runtime.aclose()

    state = outcome.state
    audit = state.get("citation_audit") or {}
    report = state.get("final_report") or {}
    n_claims = sum(len(s.get("claims", [])) for s in report.get("sections", []))
    return {
        "id": question["id"],
        "tags": question["tags"],
        "status": str(outcome.status),
        "coverage": audit.get("coverage", 0.0),
        "quote_hit_rate": audit.get("quote_hit_rate", 0.0),
        "future_leaks": len(audit.get("future_leaks", [])),
        "passed": bool(audit.get("passed", False)),
        "n_claims": n_claims,
        "n_evidence": len(report.get("evidence", [])),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="EquityLens 评测运行器")
    parser.add_argument("--label", required=True, help="本次评测的标签，结果写入 evals/results/<label>.jsonl")
    parser.add_argument("--live", action="store_true", help="跑全量题集（需要真实模型与数据源）")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题（调试用）")
    parser.add_argument("--questions", default=str(Path(__file__).parent / "questions.yaml"))
    args = parser.parse_args()

    questions = load_questions(Path(args.questions), offline_only=not args.live, limit=args.limit)
    if not questions:
        print("没有可跑的题目", file=sys.stderr)
        return 1

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.label}.jsonl"

    with out_path.open("w", encoding="utf-8") as fh:
        for q in questions:
            result = await eval_one(q, live=args.live)
            fh.write(json.dumps(result, ensure_ascii=False) + "\n")
            fh.flush()
            print(
                f"[{result['id']}] {result['status']} "
                f"coverage={result['coverage']:.2f} hit={result['quote_hit_rate']:.2f} "
                f"leaks={result['future_leaks']} passed={result['passed']}"
            )

    print(f"\n结果已写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
