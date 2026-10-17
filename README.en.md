# EquityLens

> **An equity research system where every claim can be traced back to its source sentence.**

Give it a research question. Nine agent roles run across 14 orchestration nodes and produce a
Chinese-language equity research report in which every figure and every judgment is clickable —
down to the exact sentence in the underlying filing.

[![CI](https://github.com/markliu12/equitylens/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/markliu12/equitylens/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-124%20passed-brightgreen)](#quality)
[![coverage](https://img.shields.io/badge/coverage-80%25-yellowgreen)](#quality)
[![python](https://img.shields.io/badge/python-3.12%2B-blue)](#quick-start)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[See a real output](https://markliu12.github.io/equitylens/examples/maotai-2026q3/report.html) · [Quick start](#quick-start) · [Docs](https://markliu12.github.io/equitylens/)

---

## The problem

The blocker for LLM-generated research isn't fluency — it's **trust**. A report you cannot
verify is a report you cannot use.

| Concern | What this project does |
|---|---|
| "Did it make that number up?" | Any claim marked `fact` must carry an `evidence_id`, and its `quote` must appear verbatim in the source. Three deterministic checks, no model involved |
| "Did it use information that didn't exist yet?" | Evidence published after the report's `as_of` date is dropped and flagged in the audit. Look-ahead bias prevention is the default, not an option |
| "If it crashes halfway, is my spend gone?" | A checkpoint is written after every node. On resume, succeeded nodes are skipped — measured: **zero** LLM calls replayed for completed nodes |
| "Will I get a surprise bill?" | `BudgetGuard` checks cost / token / call limits **before** every LLM call and aborts on breach |
| "The agents disagree. Which one do I trust?" | The debate loop has explicit termination conditions, and the verdict node produces **cross-dimensional judgments**, not the most quotable sentence |
| "Will it give me investment advice?" | A compliance node blocks buy/sell calls, price targets, and return promises, then re-audits after revision |

---

## Quick start

### Docker

```bash
docker compose up --build          # serves http://127.0.0.1:8000

docker compose run --rm equitylens \
  python -m equitylens.cli run "分析贵州茅台" --offline
```

Without compose:

```bash
docker build -t equitylens .
docker run --rm equitylens python -m equitylens.cli run "分析贵州茅台" --offline
```

### Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
equitylens doctor
equitylens run "分析贵州茅台最近一期经营情况与行业景气度" --offline
```

The default `offline` mode uses a bundled fixture dataset plus a deterministic offline model:
**no network, no API key, no cost**. Artifacts land in `data/artifacts/<run_id>/`
(`report.md`, `report.html`, `citation_audit.json`).

To use a real model and real market data:

```bash
cp .env.example .env
# APP_MODE=dev
# LLM_API_KEY=sk-xxx        # DeepSeek / Qwen / GLM — any OpenAI-compatible endpoint
# DATA_PROVIDER=akshare     # pip install akshare
```

Per-run cost ceiling defaults to `BUDGET_MAX_COST_CNY=1.2`, with a fallback chain
`deepseek-chat → qwen-plus → glm-4-flash` and a sliding-window circuit breaker.

> The repository itself is Chinese-first (A-share market, Chinese filings, Chinese prompts).
> English is provided so you can evaluate the architecture; the generated reports are Chinese.

---

## Design highlights

**Content-hashed evidence ids.** `evidence_id = "ev_" + sha1(normalized_url + normalized_quote)[:10]`.
The same article fetched from two channels collapses into one id, and references survive restarts,
machine changes, and cross-run comparison.

**A deterministic derived-signal layer.** `tools/signals.py` crosses raw inputs into arithmetic
facts before any model sees them:

| Derived quantity | Formula | What it answers |
|---|---|---|
| `growth_vs_industry` | company revenue growth − industry output growth | Is growth share-driven or cycle-driven? |
| `growth_divergence` | quarterly YoY − full-year YoY | Has momentum already turned? |
| `cash_to_profit` | operating cash flow ÷ net income | Is the profit backed by cash? |

Each value ships with its `basis` (the actual formula), and the prompt instructs the model to
**cite, not recompute**. Models recomputing financial ratios is a leading source of hallucination;
moving it into deterministic code makes runs reproducible and unit-testable.

**Core conclusions must be cross-dimensional.** Each one has to cite **at least two independent
sources**. This is enforced by tests, not by prompt hope.

**The engine knows nothing about the business.** `engine/` is ten modules of pure asyncio
orchestration — dependency-count scheduling, cross-layer pipelining, separated semaphores for
LLM/tool/domain, Patch + reducer merge semantics, checkpointing, event streaming — with all
external capability injected through `Services`.

---

## Quality

```bash
pytest -q --cov=src/equitylens        # 124 passed, 80% coverage
ruff check src tests evals            # 0 errors
mypy                                  # 0 errors
python evals/run_eval.py --label baseline   # offline subset: coverage 1.00, hit 1.00, leaks 0
```

Beyond the usual engine tests (DAG execution, cycle detection, parallel speedup, resume with zero
replayed calls, SSE resume, idempotency), `tests/unit/test_report_quality.py` locks **content shape**:

- no claim may appear in two sections;
- core conclusions must contain judgment markers, not bare metric restatement;
- each core conclusion cites ≥ 2 sources;
- `fact` claims must carry evidence;
- the "bull vs bear" section must contain rebuttal structure, not an echo with a prefix;
- the summary must contain verifiable quantities.

These catch errors that pass schema validation and still show a 100% citation-coverage score.

---

## Layout

```
src/equitylens/
├─ engine/     Orchestration core — no LLM or tool dependencies
├─ llm/        OpenAI-compatible client, model router, structured output repair, offline model
├─ agents/     Nine roles: planner, retriever, 3 analysts, bull/bear/judge, verdict, risk, editor
├─ tools/      Provider protocol, fixture, akshare, evidence chain, derived signals, cache, limiter
├─ graph/      The single wiring point for the research DAG
├─ report/     Citation audit + Markdown/HTML rendering
└─ api/        FastAPI + SSE + run management
web/           Vite + React + TS, no UI framework, hand-written CSS
evals/         25-question eval set, deterministic metrics, paired bootstrap A/B
```

---

## Scope

**Not investment advice.** The system produces research summaries only; the compliance node blocks
advisory language. The bundled dataset is synthetic — do not cite it as real disclosure.

MIT licensed.

<sub>[中文说明](README.md) · [Contributing](CONTRIBUTING.md) · [Docs](https://markliu12.github.io/equitylens/) · [Data sources](https://markliu12.github.io/equitylens/data-sources/)</sub>
