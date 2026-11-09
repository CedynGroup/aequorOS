#!/bin/bash
# Prepares a fresh Linux cloud container (root, Ubuntu 24.04) to develop and test aequorOS.
# Idempotent. Run from the environment's setup script, or directly: bash backend/scripts/cloud/setup.sh
#
# Installs the pinned toolchain through the repo's own mise.toml, then the services the full test
# suite needs and root-safe wrappers for them. See README.md in this directory.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HERE="$ROOT/backend/scripts/cloud"
T=/opt/aequoros-tools
MISE_VERSION=v2026.7.7
MISE_SHA256=429f71e7e989908bf975aafac9066329c16e2d8fc7cd8e74fdf21dd6300ffe7c
MINIO_RELEASE=RELEASE.2025-09-07T16-13-09Z
MINIO_SHA256=7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f
OPENBAO_VERSION=v2.6.1
OPENBAO_SHA256=ca8d836eb3a5c80407e45e762300b64e7138c419e78826955f2e4ba4ce6d8a6b
# Same version as .no-mistakes.yaml pins; the release publishes no checksum, so this
# hash pins the tarball that was reviewed when the version was pinned (re-pin both on a bump).
NO_MISTAKES_VERSION=v1.106.1
NO_MISTAKES_SHA256=fbf131356a278623a509b6b50ecb39c5375b1a82e7dcfc48e42d10e761416d1e

mkdir -p "$T/minio" "$T/openbao"

fetch() { # fetch <url> <sha256> <dest>
  curl -fsSL --retry 3 "$1" -o "$3"
  echo "$2  $3" | sha256sum --check --strict
}

# mise, readable by the unprivileged test user.
export MISE_DATA_DIR=/opt/mise
if [ ! -x /usr/local/bin/mise ]; then
  fetch "https://github.com/jdx/mise/releases/download/$MISE_VERSION/mise-$MISE_VERSION-linux-x64" "$MISE_SHA256" /usr/local/bin/mise
  chmod +x /usr/local/bin/mise
fi
(cd "$ROOT" && mise trust && mise run setup)
chmod -R a+rX /opt/mise
(cd "$ROOT" && mise exec -- printenv PATH) > "$T/toolpath"

# lsof backs local_services.py's native port-ownership checks; slim images do not ship it.
if ! command -v lsof >/dev/null; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends lsof
fi

# Docker backs aequoros-kms (the local-kms emulator). Images that already ship Docker keep theirs.
if ! command -v dockerd >/dev/null; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker.io
fi

# PostgreSQL 17 (CI parity); local_services.py starts its own cluster, so drop the packaged one.
if [ ! -x /usr/lib/postgresql/17/bin/postgres ]; then
  install -d /usr/share/postgresql-common/pgdg
  curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
  echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt noble-pgdg main" > /etc/apt/sources.list.d/pgdg.list
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends postgresql-17
  pg_dropcluster --stop 17 main || true
fi

# MinIO and OpenBao: the exact releases CI pins (.github/actions/setup-minio, setup-openbao).
[ -x "$T/minio/minio" ] || { fetch "https://github.com/minio/minio/releases/download/$MINIO_RELEASE/minio.linux-amd64.$MINIO_RELEASE" "$MINIO_SHA256" "$T/minio/minio"; chmod +x "$T/minio/minio"; }
if [ ! -x "$T/openbao/bao" ]; then
  fetch "https://github.com/openbao/openbao/releases/download/$OPENBAO_VERSION/openbao_${OPENBAO_VERSION#v}_linux_amd64.tar.gz" "$OPENBAO_SHA256" "$T/openbao/openbao.tar.gz"
  tar -xzf "$T/openbao/openbao.tar.gz" -C "$T/openbao" bao
fi

# no-mistakes: installed only (never run here); `no-mistakes init` stays a per-repository choice.
if [ ! -x /opt/no-mistakes/no-mistakes ]; then
  mkdir -p /opt/no-mistakes
  fetch "https://github.com/kunchenguid/no-mistakes/releases/download/$NO_MISTAKES_VERSION/no-mistakes-$NO_MISTAKES_VERSION-linux-amd64.tar.gz" "$NO_MISTAKES_SHA256" /opt/no-mistakes/no-mistakes.tar.gz
  tar -xzf /opt/no-mistakes/no-mistakes.tar.gz --no-same-owner -C /opt/no-mistakes no-mistakes
  chmod 755 /opt/no-mistakes/no-mistakes
fi
ln -sf /opt/no-mistakes/no-mistakes /usr/local/bin/no-mistakes

# initdb refuses root, so services and Playwright run as this user.
id aqs >/dev/null 2>&1 || useradd -m -s /bin/bash aqs
# The wrappers export this file as SSL_CERT_FILE, so it must always exist and be readable by aqs.
if [ -f /root/.ccr/ca-bundle.crt ]; then
  install -m 644 /root/.ccr/ca-bundle.crt "$T/ca-bundle.crt"
else
  install -m 644 /etc/ssl/certs/ca-certificates.crt "$T/ca-bundle.crt"
fi

# Browser and its system libraries, matching the dashboard's exact @playwright/test pin (as CI does).
(cd "$ROOT/backend/dashboard" && PATH="$(cat "$T/toolpath")" PLAYWRIGHT_BROWSERS_PATH=/opt/pw-dashboard \
  node node_modules/@playwright/test/cli.js install --with-deps chromium)
chmod -R a+rX /opt/pw-dashboard

for wrapper in aequoros-services aequoros-dashboard-e2e aequoros-kms aequoros-openbao; do
  ln -sf "$HERE/$wrapper" "/usr/local/bin/$wrapper"
done
# Sorts after nodejs.sh, which would otherwise put the image's older Node first.
rm -f /etc/profile.d/aequoros.sh
MISE_PATH="$(tr ':' '\n' < "$T/toolpath" | grep '^/opt/mise/' | paste -sd:)"
cat > /etc/profile.d/zz-aequoros.sh <<PROFILE
export MISE_DATA_DIR=/opt/mise
export PATH="$MISE_PATH:\$PATH"
PROFILE
echo "aequorOS cloud setup complete. See $HERE/README.md"
