.PHONY: help install lint typecheck test test-unit test-contract test-integration test-e2e
.PHONY: run-backend run-web import-map bootstrap docker-build docker-up docker-down clean

help:
	@echo "Marga V2X Platform"
	@echo ""
	@echo "Setup:"
	@echo "  make bootstrap        - Full local setup (venv, deps, DB, map)"
	@echo "  make install           - Install Python dependencies"
	@echo ""
	@echo "Quality:"
	@echo "  make lint              - Run ruff linter + formatter"
	@echo "  make typecheck         - Run mypy type checking"
	@echo "  make test              - Run all tests"
	@echo "  make test-unit         - Run unit tests only"
	@echo "  make test-contract     - Run contract tests only"
	@echo "  make test-integration  - Run integration tests only"
	@echo ""
	@echo "Run:"
	@echo "  make run-backend       - Start backend services"
	@echo "  make run-web           - Start web dashboard"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build      - Build all containers"
	@echo "  make docker-up         - Start all services via compose"
	@echo "  make docker-down       - Stop all services"
	@echo ""
	@echo "Data:"
	@echo "  make import-map REGION=sample-city  - Import OSM region"

PYTHON ?= python3
VENV := .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

install: $(VENV)/bin/activate
	$(PIP) install -e ".[dev]"

bootstrap: install
	@echo "Running database migrations..."
	$(VENV)/bin/alembic upgrade head || echo "DB not available, skipping migrations"
	@echo "Bootstrap complete."

lint:
	$(VENV)/bin/ruff check packages services tests
	$(VENV)/bin/ruff format --check packages services tests

typecheck:
	$(VENV)/bin/mypy packages/ services/

test:
	$(VENV)/bin/pytest packages/geo/tests packages/schemas/tests services/gateway/tests services/world_state/tests tests/ -v --tb=short

test-unit:
	$(VENV)/bin/pytest tests/ -v --tb=short -m "not integration and not e2e and not contract"

test-contract:
	$(VENV)/bin/pytest tests/contract/ -v --tb=short

test-integration:
	$(VENV)/bin/pytest tests/integration/ -v --tb=short

test-e2e:
	$(VENV)/bin/pytest tests/e2e/ -v --tb=short

run-backend:
	$(VENV)/bin/uvicorn services.gateway.app.main:app --host 0.0.0.0 --port 8000 --reload

run-web:
	@echo "Frontend not yet configured"

import-map:
	@echo "Map import not yet configured for REGION=$(REGION)"

docker-build:
	docker compose -f infra/compose/docker-compose.yml build

docker-up:
	docker compose -f infra/compose/docker-compose.yml up -d

docker-down:
	docker compose -f infra/compose/docker-compose.yml down

clean:
	rm -rf $(VENV) .mypy_cache .pytest_cache .ruff_cache __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
