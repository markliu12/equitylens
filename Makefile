# Windows 上没有 make 也能用，这里只是给习惯的人一个统一入口。
PY ?= python

.PHONY: install dev test lint fmt typecheck web serve run-offline doctor

install:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests

fmt:
	$(PY) -m ruff format src tests && $(PY) -m ruff check src tests --fix

typecheck:
	$(PY) -m mypy src/equitylens/engine src/equitylens/agents/schemas.py

web:
	cd web && npm install && npm run build

serve:
	$(PY) -m uvicorn equitylens.api.app:create_app --factory --host 127.0.0.1 --port 8000

run-offline:
	$(PY) -m equitylens.cli run "分析贵州茅台最近一期经营情况与行业景气度" --offline

doctor:
	$(PY) -m equitylens.cli doctor
