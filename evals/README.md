# 评测（evals）

目标：把"报告质量"从感觉变成可回归的数字。

## 组成

- `questions.yaml` — 25 题评测集，覆盖白酒 / 新能源 / 医药 / 银行 / 半导体等行业。
  `offline: true` 的 5 题用合成 fixture 即可跑，是 CI 门禁；其余 20 题需真实数据源。
- `run_eval.py` — 逐题跑完整流水线，产出 `results/<label>.jsonl`（每题一行的确定性指标）。
- `ab_compare.py` — 配对 bootstrap 对比两次评测，指标差的 95% CI 不含 0 即判显著。

## 用法

```bash
# 离线门禁子集（零费用，CI 用）
python evals/run_eval.py --label baseline

# 全量 25 题（需要 LLM_API_KEY 与真实数据源）
python evals/run_eval.py --label exp-prompt-v2 --live

# A/B 对比
python evals/ab_compare.py evals/results/baseline.jsonl evals/results/exp-prompt-v2.jsonl
```

## 指标口径

| 指标 | 含义 | 门禁 |
|---|---|---|
| coverage | 事实型结论挂证据的比例 | ≥ 0.9，且不得显著劣化 |
| quote_hit_rate | quote 命中原文比例 | ≥ 0.95，且不得显著劣化 |
| future_leaks | 前视偏差条数 | 必须 = 0 |
| n_claims | 结论总数 | 防"少写结论刷覆盖率"的对冲指标 |

后续计划：接入 RAGAS 风格的模型裁判指标作为补充（不替代确定性门禁）。
