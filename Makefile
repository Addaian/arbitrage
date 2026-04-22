.PHONY: help install sync test test-unit test-integration cov lint format typecheck check up down db-shell clean precommit-install paper-run paper-dry review preflight-live live-dry live-run live-switch paper-switch

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
	@echo "  paper-run        - start the scheduler against Alpaca paper (blocks)"
	@echo "  paper-dry        - one-shot dry-run cycle against Alpaca paper"
	@echo "  review           - print the daily-review dashboard from Postgres"
	@echo "  preflight-live   - run pre-live gate (Wave 20)"
	@echo "  live-dry         - one-shot dry-run cycle against Alpaca LIVE"
	@echo "  live-run         - one live cycle (persisted) — intended as systemd entrypoint"
	@echo "  live-switch      - paper -> live systemd flip (requires sudo)"
	@echo "  paper-switch     - live -> paper revert (requires sudo)"

install sync:
	uv sync --extra dev

# Everything below assumes `sync` has been run. If you hit "command not found"
# errors for pytest/mypy/ruff, run `make sync` first.

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

# --- Paper-run operations (Wave 9) --------------------------------------

paper-run:
	uv run python -m quant.live.scheduler --broker alpaca-paper --persist

paper-dry:
	uv run python -m quant.live.runner --broker alpaca-paper --dry-run

review:
	uv run python scripts/review.py

# --- Live-trading operations (Wave 20) ----------------------------------

# Sanity-check every gate before flipping systemd to real-money mode.
# Refuses to pass unless QUANT_ENV=live, creds are live, killswitch
# clear, paper PnL recent, and live Alpaca account has >=$100 equity.
preflight-live:
	uv run python scripts/preflight_live.py

# One-shot live cycle, for smoke-testing immediately after the flip.
# Per PRD §6.1 all risk limits still apply.
live-dry:
	uv run python -m quant.live.runner --broker alpaca-live --dry-run

live-run:
	uv run python -m quant.live.runner --broker alpaca-live --persist

# Operator's paper→live flip. Requires sudo (systemd unit edits).
live-switch:
	@echo "Paper -> live flip. Pre-flight first:"
	$(MAKE) preflight-live
	@echo
	@echo "Pre-flight OK. Swapping systemd units:"
	sudo systemctl disable --now quant-runner.timer
	sudo systemctl enable --now quant-runner-live.service
	sudo systemctl status --no-pager quant-runner-live.service

# Revert live -> paper, e.g. after failing Gate 4.
paper-switch:
	sudo systemctl disable --now quant-runner-live.service
	sudo systemctl enable --now quant-runner.timer
	sudo systemctl status --no-pager quant-runner.timer
