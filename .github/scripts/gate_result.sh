#!/usr/bin/env bash
# Aggregate a workflow's `needs` results into one gate verdict.
#
# Why a script rather than inline `jq` in the workflow: dashboard.yml and
# web.yml share the verdict rules, and the rules carry a load-bearing
# distinction that must not drift — a failed or cancelled dependency fails the
# gate on every event, a skipped dependency is acceptable on a pull request
# (that is what makes path filtering below the trigger compatible with a
# required check), and a skipped dependency FAILS the gate on a merge_group
# entry. The queued merge result is what lands on main, so its checks must
# actually run there; a gate that accepts `skipped` on the queue is what let
# the Dashboard CI checks (lint, unit tests, coverage, build) first run on
# main and fail the unit-coverage ratchet after a green queue.
#
# Usage (from a gate job step):
#   env:
#     GATE_LABEL: Dashboard          # human name used in messages
#     GATE_EVENT: ${{ github.event_name }}
#     GATE_NEEDS: ${{ toJSON(needs) }}
#   run: .github/scripts/gate_result.sh
set -euo pipefail

LABEL="${GATE_LABEL:?GATE_LABEL is required}"
EVENT="${GATE_EVENT:?GATE_EVENT is required}"
RESULTS="${GATE_NEEDS:?GATE_NEEDS is required}"

NOT_PASSED="$(printf '%s' "$RESULTS" \
  | jq -r 'to_entries[] | select(.value.result != "success") | "  \(.key): \(.value.result)"')"
if [ -n "$NOT_PASSED" ]; then
  printf '%s jobs that did not pass:\n%s\n' "$LABEL" "$NOT_PASSED"
fi

FAILED="$(printf '%s' "$RESULTS" \
  | jq -r 'to_entries[] | select(.value.result == "failure" or .value.result == "cancelled") | .key')"
if [ -n "$FAILED" ]; then
  echo "::error::These ${LABEL} jobs did not pass:${NOT_PASSED//$'\n'/%0A}" 1>&2
  exit 1
fi

if [ "$EVENT" = "merge_group" ] && [ -n "$NOT_PASSED" ]; then
  echo "::error::A merge-queue entry cannot skip ${LABEL} jobs; the checks that guard main must run on the queue:${NOT_PASSED//$'\n'/%0A}" 1>&2
  exit 1
fi

echo "Every ${LABEL} job passed or was skipped."
