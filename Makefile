.PHONY: help install lint format typecheck test test-unit test-contract test-integration test-e2e check
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
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy
UVICORN := $(VENV)/bin/uvicorn
ALEMBIC := $(VENV)/bin/alembic

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

install: $(VENV)/bin/activate
	$(PIP) install -e ".[dev]" -e ./packages/schemas -e ./tools/osm-import \
		-e "./packages/persistence[dev]" -e "./packages/observability[fastapi]"

bootstrap: install
	@echo "Running database migrations..."
	$(ALEMBIC) -c packages/persistence/alembic.ini upgrade head || echo "DB not available, skipping migrations"
	@echo "Bootstrap complete."

lint:
	$(RUFF) check packages/schemas/marga_schemas packages/persistence packages/observability \
		services/hazards services/trust services/messaging services/alerts services/gateway \
		tests/contract/test_schemas.py tests/unit/test_alerts.py tests/unit/test_hazard_fusion.py \
		tests/unit/test_messaging.py tests/unit/test_persistence.py tests/unit/test_trust.py

format:
	$(RUFF) format --check packages/schemas/marga_schemas packages/persistence packages/observability \
		services/hazards services/trust services/messaging services/alerts services/gateway \
		tests/contract/test_schemas.py tests/unit/test_alerts.py tests/unit/test_hazard_fusion.py \
		tests/unit/test_messaging.py tests/unit/test_persistence.py tests/unit/test_trust.py

typecheck:
	$(MYPY) packages/schemas/marga_schemas packages/persistence packages/observability \
		services/hazards services/trust services/messaging services/alerts services/gateway

test:
	$(PYTEST) tests/ -q --tb=short

test-unit:
	$(PYTEST) tests/unit -q --tb=short

test-contract:
	$(PYTEST) tests/contract -q --tb=short

test-integration:
	$(PYTEST) tests/integration -q --tb=short

check: lint format typecheck test

test-e2e:
	$(VENV)/bin/pytest tests/e2e/ -v --tb=short

run-backend:
	$(UVICORN) services.gateway.app:app --host 0.0.0.0 --port 8000 --reload

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
