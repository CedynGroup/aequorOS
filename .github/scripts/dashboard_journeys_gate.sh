#!/usr/bin/env bash
# The required check may skip journeys only after successful change detection.
set -euo pipefail
printf '%s\n' "${RESULTS:?RESULTS is not set}" | jq .
if ! jq -e '
  .changes.result == "success" and (
    (.changes.outputs.journeys == "true" and .journeys.result == "success") or
    (.changes.outputs.journeys == "false" and .journeys.result == "skipped")
  )
' <<<"$RESULTS" >/dev/null; then
  echo "::error::Journey shards failed, were cancelled, or lack a verified change decision."
  exit 1
fi
