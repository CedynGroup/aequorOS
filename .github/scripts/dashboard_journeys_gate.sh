#!/usr/bin/env bash
# The required check may skip journeys only after successful change detection;
# queued merge results skip detection and always run the journey shards.
set -euo pipefail
printf '%s\n' "${RESULTS:?RESULTS is not set}" | jq .
if ! jq -e '
  (
    (.changes.result == "skipped" and .journeys.result == "success") or
    (.changes.result == "success" and (
      (.changes.outputs.journeys == "true" and .journeys.result == "success") or
      (.changes.outputs.journeys == "false" and .journeys.result == "skipped")
    ))
  )
' <<<"$RESULTS" >/dev/null; then
  NOT_PASSED="$(jq -r 'to_entries[] | select(.value.result != "success") | "  \(.key): \(.value.result)"' <<<"$RESULTS")"
  echo "::error::These dashboard journey jobs did not pass:${NOT_PASSED//$'\n'/%0A}"
  exit 1
fi
