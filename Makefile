.PHONY: help install sync test test-unit test-integration cov lint format typecheck check up down db-shell clean precommit-install

help:
	@echo "Common targets:"
	@echo "  install          - uv sync with dev extras"
	@echo "  test             - run unit tests"
	@echo "  test-integration - run integration tests (requires docker compose up)"
	@echo "  cov              - unit tests with coverage report"
	@echo "  lint             - ruff check + format check"
	@echo "  format           - ruff format + autofix"
	@echo "  typecheck        - mypy strict on risk/execution/portfolio"
	@echo "  check            - lint + typecheck + test"
	@echo "  up / down        - start/stop local Postgres via docker compose"
	@echo "  precommit-install- install git hooks"

install sync:
	uv sync --extra dev

test test-unit:
	uv run pytest tests/unit -v

test-integration:
	uv run pytest tests/integration -v -m integration

cov:
	uv run pytest tests/unit --cov=src/quant --cov-report=term-missing --cov-report=html

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check . --fix
	uv run ruff format .

typecheck:
	uv run mypy src/quant/risk src/quant/execution src/quant/portfolio --strict

check: lint typecheck test

up:
	docker compose up -d
	@echo "Waiting for Postgres..."
	@until docker compose exec -T postgres pg_isready -U $${POSTGRES_USER:-quant} >/dev/null 2>&1; do sleep 1; done
	@echo "Postgres ready."

down:
	docker compose down

db-shell:
	docker compose exec postgres psql -U $${POSTGRES_USER:-quant} -d $${POSTGRES_DB:-quant}

precommit-install:
	uv run pre-commit install

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
