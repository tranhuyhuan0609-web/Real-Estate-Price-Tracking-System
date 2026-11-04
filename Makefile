# Requires uv (https://docs.astral.sh/uv/). All commands run from the repo root.
COMPOSE := docker compose --env-file .env -f docker/docker-compose.yml
RUN     := uv run --frozen

.PHONY: help install lock schema up up-monitoring backup-now telegram-chat-id telegram-test down logs ps sync-configs migrate test test-integration cov lint format typecheck check clean

help:
	@echo "install           Create .venv from uv.lock (dev tools + browser extra)"
	@echo "lock              Re-resolve uv.lock and export requirements.lock (Docker)"
	@echo "up / down         Start / stop the full stack (infra + workers)"
	@echo "logs / ps         Follow logs / list containers"
	@echo "sync-configs      Upsert configs/sites/*.yaml into crawler_configs"
	@echo "migrate           alembic upgrade head"
	@echo "schema            Regenerate sql/init.sql from migrations/ (after adding a migration)"
	@echo "up-monitoring     up + Prometheus (:16104) + Alertmanager -> Telegram (:16105)"
	@echo "backup-now        Dump Postgres and Mongo to ./backups right now"
	@echo "telegram-chat-id  Show the chat id of whoever messaged the alert bot"
	@echo "telegram-test     Send a test message to TELEGRAM_CHAT_ID"
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

up-monitoring: .env
	$(COMPOSE) --profile monitoring up -d --build --remove-orphans

backup-now:
	$(COMPOSE) run --rm pg-backup once
	$(COMPOSE) run --rm mongo-backup once

telegram-chat-id:
	$(RUN) realestate telegram-chat-id

telegram-test:
	$(RUN) realestate telegram-test

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

schema:
	$(RUN) python scripts/generate_init_sql.py

test:
	$(RUN) pytest

test-integration:
	$(RUN) pytest -m integration tests/integration

cov:
	$(RUN) pytest --cov --cov-report=term

lint:
	$(RUN) ruff check src tests migrations scripts
	$(RUN) ruff format --check src tests migrations scripts

format:
	$(RUN) ruff check --fix src tests migrations scripts
	$(RUN) ruff format src tests migrations scripts

typecheck:
	$(RUN) mypy

check: lint typecheck cov

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
	rm -rf .coverage htmlcov
