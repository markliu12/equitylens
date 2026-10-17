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
pytest -q                    # 97 passed
ruff check src tests         # 0 errors
mypy src/equitylens/engine   # type check (advisory)
```

## Project structure

```
src/equitylens/
  engine/      DAG orchestration core (no LLM/tool deps — pure asyncio)
  llm/         OpenAI-compat client, model router, offline model, structured output
  agents/      9 agents: planner, retriever, 3 analysts, bull/bear/judge, verdict, risk, editor
  tools/       Provider protocol, fixture, akshare, evidence chain, cache, rate limiter
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

## Testing philosophy

- **Offline by default**: tests must not require network or API keys
- **Deterministic assertions**: no `sleep` hacks, no "usually passes" tests
- **Test what matters**: parallelism (timing assertions), recovery (model call counts),
  citation integrity (coverage/hit rates), not trivial getters/setters

## Questions?

Open a [discussion](https://github.com/markliu12/equitylens/discussions) or an issue.
