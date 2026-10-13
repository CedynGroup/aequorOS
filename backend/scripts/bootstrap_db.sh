#!/usr/bin/env bash
# Local services create the database; this prepares roles and runs migrations.
set -euo pipefail
cd "$(dirname "$0")/.."
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://postgres:postgres@127.0.0.1:15432/risk_service}"
exec uv run python -m scripts.bootstrap_database
