#!/usr/bin/env bash
# Decide whether a workflow's expensive jobs need to run on THIS pull request.
#
# Why this exists rather than a `paths:` filter on the workflow: a required
# status check whose workflow is path-filtered never REPORTS on a pull request
# touching none of those paths, and GitHub then holds the pull request at
# "Expected — waiting for status" forever. The workflow therefore triggers on
# every pull request, and this script moves the filtering one level down, into
# an `if:` on each expensive job — so the workflow always reports, while a
# docs-only change still does not pay for Postgres.
#
# Usage:  changed_area.sh <output-name> <extended-regex over repo-relative paths>
#
# Fail-closed on purpose: anything this cannot determine — a non-pull_request
# event, a diff that will not compute, an empty change set — answers "true" and
# runs every gate. A change-detector that guesses "nothing changed" silently
# disables the entire suite, which is the failure this whole workflow exists to
# stop happening again.
set -euo pipefail

NAME="${1:?usage: changed_area.sh <output-name> <regex>}"
PATTERN="${2:?usage: changed_area.sh <output-name> <regex>}"

emit() {
  echo "${NAME}=$1" >> "${GITHUB_OUTPUT:?GITHUB_OUTPUT is not set}"
  echo "${NAME}=$1"
}

if [ "${GITHUB_EVENT_NAME:-}" != "pull_request" ]; then
  echo "Event is '${GITHUB_EVENT_NAME:-unknown}', not a pull request: every gate runs."
  emit true
  exit 0
fi

# On a pull_request event actions/checkout builds the merge commit, whose FIRST
# parent is the base branch tip — so `HEAD^1..HEAD` is exactly what this pull
# request adds, already rebased on the current base. Fall back to the recorded
# base SHA when the merge ref is unavailable.
BASE="${PR_BASE_SHA:-}"
if git rev-parse --verify --quiet 'HEAD^2' >/dev/null 2>&1; then
  BASE="$(git rev-parse 'HEAD^1')"
fi

if [ -z "${BASE}" ]; then
  echo "::warning::No base revision to diff against; running every gate."
  emit true
  exit 0
fi

if ! CHANGED="$(git diff --name-only "${BASE}" HEAD 2>&1)"; then
  echo "::warning::Could not diff ${BASE}..HEAD (${CHANGED}); running every gate."
  emit true
  exit 0
fi

if [ -z "${CHANGED}" ]; then
  echo "::warning::Diff against ${BASE} is empty; running every gate rather than none."
  emit true
  exit 0
fi

echo "Changed files (${BASE}..HEAD):"
printf '%s\n' "${CHANGED}" | sed 's/^/  /'
echo "Matching against: ${PATTERN}"

if printf '%s\n' "${CHANGED}" | grep -qE "${PATTERN}"; then
  emit true
else
  emit false
fi
