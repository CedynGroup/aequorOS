#!/bin/bash
# Entry point for a fresh Linux cloud container (root, Ubuntu 24.04). Idempotent.
# The environment's setup script calls only this file; each service owns its own script.
# Run directly with: bash backend/scripts/cloud/setup.sh
#
#   toolchain.sh                    pinned mise toolchain, the one workspace `pnpm install`, no-mistakes
#   setup-backend.sh                PostgreSQL 17, MinIO, OpenBao, the aqs user, aequoros-services/-openbao
#   backend/dashboard/cloud/setup.sh Docker, Playwright Chromium, aequoros-kms/-dashboard-e2e
#
# frontend/, console/ and packages/risk-service-api need no script: see README.md in this directory.
set -euo pipefail

export ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HERE="$ROOT/backend/scripts/cloud"

bash "$HERE/toolchain.sh"
bash "$HERE/setup-backend.sh"
bash "$ROOT/backend/dashboard/cloud/setup.sh"
echo "aequorOS cloud setup complete. See $HERE/README.md"
