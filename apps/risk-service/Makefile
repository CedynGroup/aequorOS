.PHONY: help sync dev test lint format typecheck check migrate revision smoke

HOST ?= 127.0.0.1
PORT ?= 8000
APP_FILE := app/main.py

help:
	@echo "Available targets:"
	@echo "  sync       Install dependencies with uv"
	@echo "  dev        Run FastAPI dev server on HOST:PORT"
	@echo "  test       Run pytest"
	@echo "  lint       Run Ruff checks"
	@echo "  format     Format Python files with Ruff"
	@echo "  typecheck  Run BasedPyright"
	@echo "  check      Run lint, typecheck, and tests"
	@echo "  migrate    Run Alembic migrations; requires DATABASE_URL"
	@echo "  revision   Create Alembic revision: make revision name=\"message\""
	@echo "  smoke      Run local smoke checks that do not require Postgres"

sync:
	uv sync

dev:
	uv run fastapi dev $(APP_FILE) --host $(HOST) --port $(PORT)

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .
	uv run ruff check . --fix

typecheck:
	uv run basedpyright

check: lint typecheck test

migrate:
	uv run alembic upgrade head

revision:
	@test -n "$(name)" || (echo 'Usage: make revision name="message"' && exit 1)
	uv run alembic revision --autogenerate -m "$(name)"

smoke: sync check
