#!/bin/bash
# Backend services for the cloud container: PostgreSQL 17, MinIO, OpenBao, the unprivileged aqs user
# and the aequoros-services and aequoros-openbao wrappers. Needs toolchain.sh to have run (setup.sh does).
set -euo pipefail
. "$(dirname "$0")/setup-lib.sh"
[ -f "$T/toolpath" ] || { echo "Run backend/scripts/cloud/setup.sh first: it installs the toolchain this script needs." >&2; exit 1; }

MINIO_RELEASE=RELEASE.2025-09-07T16-13-09Z
MINIO_SHA256=7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f
OPENBAO_VERSION=v2.6.1
OPENBAO_SHA256=ca8d836eb3a5c80407e45e762300b64e7138c419e78826955f2e4ba4ce6d8a6b

mkdir -p "$T/minio" "$T/openbao"

# lsof backs local_services.py's native port-ownership checks; slim images do not ship it.
if ! command -v lsof >/dev/null; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends lsof
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

# initdb refuses root, so services and Playwright run as this user.
id aqs >/dev/null 2>&1 || useradd -m -s /bin/bash aqs
# The wrappers export this file as SSL_CERT_FILE, so it must always exist and be readable by aqs.
if [ -f /root/.ccr/ca-bundle.crt ]; then
  install -m 644 /root/.ccr/ca-bundle.crt "$T/ca-bundle.crt"
else
  install -m 644 /etc/ssl/certs/ca-certificates.crt "$T/ca-bundle.crt"
fi

runuser -u aqs -- test -x "$ROOT/backend" || echo "WARNING: user aqs cannot reach $ROOT, so the aequoros-* wrappers will fail. Use a checkout outside /root or run: chmod o+x each directory on the path." >&2

link_wrapper "$ROOT/backend/scripts/cloud/aequoros-services"
link_wrapper "$ROOT/backend/scripts/cloud/aequoros-openbao"
