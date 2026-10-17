"""配对 bootstrap A/B 对比：两次评测结果按题配对，给出指标差的置信区间。

为什么用配对 bootstrap 而不是 t 检验：
- 25 题样本量小，指标分布明显非正态（coverage 顶在 1.0）；
- 按题配对消除了"题目难度"这个混杂变量，只比较同一题上两次运行的差异。

用法：
    python evals/ab_compare.py evals/results/baseline.jsonl evals/results/exp-a.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

METRICS = ["coverage", "quote_hit_rate", "future_leaks", "n_claims"]

# 同 run_eval：Windows 管道默认 cp1252，中文输出会直接抛 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def load_results(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["id"]] = row
    return rows


def paired_bootstrap(
    diffs: list[float], *, n_boot: int = 10_000, seed: int = 42
) -> tuple[float, float, float]:
    """返回 (均值差, 95% CI 下界, 上界)。CI 不含 0 即差异显著。"""
    if not diffs:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(
        sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot)
    )
    mean = sum(diffs) / n
    return (mean, means[int(0.025 * n_boot)], means[int(0.975 * n_boot)])


def main() -> int:
    parser = argparse.ArgumentParser(description="配对 bootstrap A/B 对比")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()

    a = load_results(args.baseline)
    b = load_results(args.candidate)
    common = sorted(set(a) & set(b))
    if not common:
        print("两次评测没有共同题目，无法配对", file=sys.stderr)
        return 1
    print(f"配对题目数: {len(common)}（baseline={args.baseline.name}, candidate={args.candidate.name}）\n")

    print(f"{'metric':<16}{'mean_diff':>10}{'95% CI':>24}  判定")
    significant = False
    for metric in METRICS:
        diffs = [float(b[qid].get(metric, 0)) - float(a[qid].get(metric, 0)) for qid in common]
        mean, lo, hi = paired_bootstrap(diffs)
        sig = (lo > 0) or (hi < 0)
        significant = significant or sig
        verdict = "显著劣化" if hi < 0 else ("显著提升" if lo > 0 else "无显著差异")
        print(f"{metric:<16}{mean:>+10.3f}   [{lo:>+7.3f}, {hi:>+7.3f}]  {verdict}")

    # 门禁规则：coverage / quote_hit_rate 显著下降，或 future_leaks 显著上升，判回归
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
