.PHONY: help sync hooks precommit dev test test-postgres lint format typecheck check migrate bootstrap-db revision smoke

HOST ?= 127.0.0.1
PORT ?= 8000
APP_FILE := app/main.py

help:
	@echo "Available targets:"
	@echo "  sync       Install dependencies with uv"
	@echo "  hooks      Install pre-commit and commit-msg hooks"
	@echo "  precommit  Run pre-commit hooks against all files"
	@echo "  dev        Run FastAPI dev server on HOST:PORT"
	@echo "  test       Run pytest"
	@echo "  test-postgres Run pytest against local Docker Postgres"
	@echo "  lint       Run Ruff checks"
	@echo "  format     Format Python files with Ruff"
	@echo "  typecheck  Run BasedPyright"
	@echo "  check      Run lint, typecheck, and tests"
	@echo "  migrate    Run Alembic migrations; requires DATABASE_URL"
	@echo "  bootstrap-db Create local DB roles, run migrations, and grant app privileges"
	@echo "  revision   Create Alembic revision: make revision name=\"message\""
	@echo "  smoke      Run local smoke checks that do not require Postgres"

sync:
	uv sync

hooks:
	uv run pre-commit install --config ../../.pre-commit-config.yaml --install-hooks

precommit:
	uv run pre-commit run --all-files --config ../../.pre-commit-config.yaml

dev:
	uv run fastapi dev $(APP_FILE) --host $(HOST) --port $(PORT)

test:
	uv run pytest

test-postgres:
	TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:15432/risk_service uv run pytest

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

bootstrap-db:
	./scripts/bootstrap_db.sh

revision:
	@test -n "$(name)" || (echo 'Usage: make revision name="message"' && exit 1)
	uv run alembic revision --autogenerate -m "$(name)"

smoke: sync check
