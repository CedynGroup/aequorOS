# shellcheck shell=bash
# Shared by the setup scripts in this directory and in backend/dashboard/cloud. Source it; do not run it.
# The entry (setup.sh) exports ROOT; a script run on its own derives it from its own location.
ROOT="${ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
T=/opt/aequoros-tools

fetch() { # fetch <url> <sha256> <dest>
  curl -fsSL --retry 3 "$1" -o "$3"
  echo "$2  $3" | sha256sum --check --strict
}

# Docker backs aequoros-kms (the local-kms emulator), which backend and dashboard tests both use, so both
# service scripts call this. Images that already ship Docker keep theirs.
ensure_docker() {
  command -v dockerd >/dev/null && return 0
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker.io
}

# Stops a service script that was run before the toolchain and the test user exist.
require_base() {
  if [ ! -f "$T/toolpath" ] || ! id aqs >/dev/null 2>&1; then
    echo "Run backend/scripts/cloud/setup.sh first: it installs the toolchain and test user this script needs." >&2
    exit 1
  fi
}

# link_wrapper <executable>: put a wrapper on the PATH.
link_wrapper() { ln -sf "$(readlink -f "$1")" "/usr/local/bin/$(basename "$1")"; }
