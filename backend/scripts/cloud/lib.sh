# Shared by the aequoros-* wrappers. Source it; do not run it.
HERE="$(dirname "$(readlink -f "$0")")"
ROOT="${AEQUOROS_ROOT:-$(cd "$HERE/../../.." && pwd)}"
T=/opt/aequoros-tools

# Run a command as the unprivileged test user with the toolchain, services and CA bundle on its PATH.
as_aqs() {
  runuser -u aqs -- env HOME=/home/aqs PATH="$T/minio:$T/openbao:$(cat "$T/toolpath")" \
    AQS_POSTGRES_BIN=/usr/lib/postgresql/17/bin AQS_OPENBAO_BIN="$T/openbao/bao" \
    AQS_LOCAL_SERVICES=native PYTHONDONTWRITEBYTECODE=1 SSL_CERT_FILE="$T/ca-bundle.crt" NODE_EXTRA_CA_CERTS="$T/ca-bundle.crt" "$@"
}
