# Use the project's virtualenv when present (Windows, then POSIX); otherwise whatever `python` is on PATH.
PYTHON ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,$(if $(wildcard .venv/bin/python),.venv/bin/python,python))

.PHONY: install test lint fmt run scenarios cov schemas

install:
	$(PYTHON) -m pip install -e . -r requirements-dev.txt

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy

fmt:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

run:
	$(PYTHON) -m uvicorn team_b.api.app:app --app-dir src --port 8010

# Not a pytest marker selection: an empty selection would exit with code 5.
scenarios:
	$(PYTHON) -m pytest -q tests/integration/test_scenarios.py && $(PYTHON) scripts/scenario_report.py

cov:
	$(PYTHON) -m pytest --cov=team_b --cov-report=term-missing -q

schemas:
	$(PYTHON) scripts/export_schemas.py
