# aequorOS

AequorOS is a bank risk-review workspace for canonical financial data, scenario forecasts,
liquidity and capital analysis, findings, decisions, and committee reports.

## Run the MVP locally

Requirements: Docker, `mise`, `uv`, `pnpm`, and the Postgres `psql` client.

From the repository root, start Postgres and object storage, then bootstrap the database:

```bash
docker compose -f apps/risk-service/docker-compose.yml up -d
mise run risk-service:bootstrap-db
```

Start the API in one terminal:

```bash
DATABASE_URL=postgresql+psycopg://risk_service_app:risk_service_app@localhost:15432/risk_service \
  CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173 \
  RISK_STORAGE_BACKEND=s3 \
  RISK_S3_BUCKET=risk-local \
  RISK_S3_REGION=us-east-1 \
  RISK_S3_ENDPOINT_URL=http://localhost:9000 \
  RISK_S3_ACCESS_KEY_ID=minioadmin \
  RISK_S3_SECRET_ACCESS_KEY=minioadmin \
  RISK_S3_FORCE_PATH_STYLE=true \
  HOST=127.0.0.1 \
  PORT=8003 \
  mise run risk-service:dev
```

Restore the pristine narrative demo portfolio:

```bash
RISK_DEMO_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:15432/risk_service \
  mise run risk-service:reset-demo
```

Then start the web console in another terminal:

```bash
VITE_RISK_API_BASE_URL=http://127.0.0.1:8003/api/v1 \
  pnpm --filter @aequoros/aequoros-web dev
```

Open `http://127.0.0.1:5173/cases`. See the [risk-service setup](apps/risk-service/README.md),
the [web console guide](apps/aequoros-web/README.md), and the
[ten-minute demo playbook](docs/demo-playbook.md) for the full presenter journey.

The reset command is idempotent and replaces only the fixed demo tenant in one transaction. It
refuses to operate if that tenant identifier belongs to an organization with an unexpected name,
and a failed reset leaves the previous portfolio intact.
