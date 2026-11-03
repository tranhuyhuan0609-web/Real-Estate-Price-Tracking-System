.PHONY: help up down test lint migrate seed clean

help:
	@echo "Real Estate Price Tracking System commands:"
	@echo "  make up       - Start all Docker infrastructure and worker services"
	@echo "  make down     - Stop all Docker services"
	@echo "  make test     - Run test suite with pytest"
	@echo "  make lint     - Run code linter and formatter checks"
	@echo "  make migrate  - Run database migrations with Alembic"
	@echo "  make clean    - Clean up temporary python caches"

up:
	docker compose up -d --build

down:
	docker compose down

test:
	pytest tests/ -v

lint:
	ruff check src/ tests/
	mypy src/

migrate:
	alembic upgrade head

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +

