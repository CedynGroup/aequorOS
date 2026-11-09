#!/bin/bash
# Dashboard pieces of the cloud container: Docker (backs the local-kms emulator), the Playwright Chromium
# that matches the dashboard's @playwright/test pin, and the aequoros-kms and aequoros-dashboard-e2e
# wrappers. Needs the toolchain and the aqs user from backend/scripts/cloud/setup.sh, which calls this.
# Unit tests and the production build need nothing from here: see backend/scripts/cloud/README.md.
set -euo pipefail
. "$(dirname "$0")/../../scripts/cloud/setup-lib.sh"
require_base

# Docker backs aequoros-kms (the local-kms emulator). Images that already ship Docker keep theirs.
if ! command -v dockerd >/dev/null; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker.io
fi

# Browser and its system libraries, matching the dashboard's exact @playwright/test pin (as CI does).
(cd "$ROOT/backend/dashboard" && PATH="$(cat "$T/toolpath")" PLAYWRIGHT_BROWSERS_PATH=/opt/pw-dashboard \
  node node_modules/@playwright/test/cli.js install --with-deps chromium)
chmod -R a+rX /opt/pw-dashboard

link_wrapper "$ROOT/backend/dashboard/cloud/aequoros-kms"
link_wrapper "$ROOT/backend/dashboard/cloud/aequoros-dashboard-e2e"
