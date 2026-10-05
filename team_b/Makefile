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

# Runs once P1.1a creates both files; until then it only says so (and exits 0).
ifneq ($(and $(wildcard tests/integration/test_scenarios.py),$(wildcard scripts/scenario_report.py)),)
scenarios:
	$(PYTHON) -m pytest -q tests/integration/test_scenarios.py && $(PYTHON) scripts/scenario_report.py
else
scenarios:
	@echo no scenarios yet
endif

cov:
	$(PYTHON) -m pytest --cov=team_b --cov-report=term-missing -q

schemas:
	$(PYTHON) scripts/export_schemas.py
