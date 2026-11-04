# Requires uv (https://docs.astral.sh/uv/). All commands run from the repo root.
COMPOSE := docker compose --env-file .env -f docker/docker-compose.yml
RUN     := uv run --frozen

.PHONY: help install lock up down logs ps sync-configs migrate test test-integration cov lint format typecheck check clean

help:
	@echo "install           Create .venv from uv.lock (dev tools + browser extra)"
	@echo "lock              Re-resolve uv.lock and export requirements.lock (Docker)"
	@echo "up / down         Start / stop the full stack (infra + workers)"
	@echo "logs / ps         Follow logs / list containers"
	@echo "sync-configs      Upsert configs/sites/*.yaml into crawler_configs"
	@echo "migrate           alembic upgrade head"
	@echo "test              Unit + contract tests"
	@echo "test-integration  Integration tests (needs Docker)"
	@echo "cov               Tests with coverage report"
	@echo "lint / format     ruff check / ruff format + fix"
	@echo "typecheck         mypy"
	@echo "check             lint + typecheck + test (what CI runs)"

install:
	uv sync --frozen --extra browser

lock:
	uv lock
	uv export --frozen --no-dev --no-emit-project --no-header -o requirements.lock

.env:
	cp .env.example .env

up: .env
	$(COMPOSE) up -d --build --remove-orphans

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

sync-configs:
	$(RUN) realestate sync-configs

migrate:
	$(RUN) alembic upgrade head

test:
	$(RUN) pytest

test-integration:
	$(RUN) pytest -m integration tests/integration

cov:
	$(RUN) pytest --cov --cov-report=term-missing

lint:
	$(RUN) ruff check src tests migrations

format:
	$(RUN) ruff check --fix src tests migrations
	$(RUN) ruff format src tests migrations

typecheck:
	$(RUN) mypy

check: lint typecheck test

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
	rm -rf .coverage htmlcov
