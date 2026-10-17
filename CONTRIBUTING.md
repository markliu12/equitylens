# Contributing to EquityLens

Thanks for your interest in contributing! This guide covers the basics.

## Development setup

```bash
git clone https://github.com/markliu12/equitylens.git
cd equitylens
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Verify everything works:

```bash
pytest -q                    # 124 passed
ruff check src tests evals   # 0 errors
mypy                         # 0 errors (engine / schemas / llm / tools)
```

## Project structure

```
src/equitylens/
  engine/      DAG orchestration core (no LLM/tool deps — pure asyncio)
  llm/         OpenAI-compat client, model router, offline model, structured output
  agents/      9 agents: planner, retriever, 3 analysts, bull/bear/judge, verdict, risk, editor
  tools/       Provider protocol, fixture, akshare, evidence chain, derived signals, cache, limiter
  graph/       Research DAG assembly (single wiring point)
  report/      Citation audit + Markdown/HTML renderer
  api/         FastAPI + SSE + run manager
```

**Key constraint**: `engine/` must not import `llm/`, `tools/`, or `agents/`. All external
capabilities are injected via `Services`. This keeps the engine testable in isolation.

## Commit conventions

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(scope): add a new feature
fix(scope): fix a bug
test(scope): add tests
docs: update documentation
chore: maintenance tasks
ci: CI/CD changes
```

Scope is typically a module name (`engine`, `agents`, `tools`, `api`, `web`).

**Keep commits small and focused.** One commit = one concern. If you're adding a new agent
and its tests, that's two commits: `feat(agents): ...` then `test: ...`.

## How to add a new data provider

1. Implement `MarketDataProvider` protocol in `tools/providers/your_source.py`
2. Add it to the switch in `runtime.build_provider()`
3. Add contract tests in `tests/unit/test_your_source.py` (test field structure, not live data)
4. Update `.env.example` with the new `DATA_PROVIDER=your_source` option

## How to add a derived signal

Derived signals live in `tools/signals.py` and are the only place where cross-dimensional
arithmetic happens. If you find yourself asking the model to compute a ratio, it probably
belongs here instead.

1. Add the raw inputs to the fixture seed (`fixtures/seeds/data.py`) and to the provider contract
2. Compute the value in `derive_signals()`; put the formula into `basis` so the report can explain it
3. If it implies a judgment, add a flag with an explicit threshold at the top of the module
4. Add the corresponding sentence template in `llm/offline.py` (both analyst and verdict paths)
5. Add a unit test in `tests/unit/test_signals.py` — thresholds are business logic, lock them down

**Never fill a missing input with 0 or a default.** Record it in `unavailable` and skip the
derived item. A wrong zero is far worse than an honest gap.

## How to add a new agent

1. Create `agents/your_agent.py`, extending `BaseAgent`
2. Define its Pydantic output schema in `agents/schemas.py`
3. Register it in `graph/research_graph.py` (the single wiring point)
4. Add an offline handler in `llm/offline.py` (for CI reproducibility)
5. Write tests

## Pull requests

1. Create a feature branch: `git checkout -b feat/your-feature`
2. Make small, focused commits
3. Ensure `pytest -q` and `ruff check src tests` pass
4. Write a clear PR description: what changed, why, how to test
5. Link any related issues

## Changing prompts

Prompts are versioned by content hash (`llm/prompts.py`), and the version is written into the
run trace — so an A/B result can be attributed to a prompt change rather than guessed at.

Prompt edits that change output *shape* will be caught by `tests/unit/test_report_quality.py`.
That file asserts things like "no claim appears in two sections" and "core conclusions cite at
least two sources". If your change is intentionally breaking one of those properties, the test
is the place to argue about it — not a silent regression.

## Testing philosophy

- **Offline by default**: tests must not require network or API keys
- **Deterministic assertions**: no `sleep` hacks, no "usually passes" tests
- **Test what matters**: parallelism (timing assertions), recovery (model call counts),
  citation integrity (coverage/hit rates), not trivial getters/setters

## Questions?

Open a [discussion](https://github.com/markliu12/equitylens/discussions) or an issue.
