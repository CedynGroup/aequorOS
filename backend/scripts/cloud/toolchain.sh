#!/bin/bash
# Repo-wide toolchain for the cloud container: mise (checksum-pinned), the tools mise.toml pins, every
# workspace dependency (`mise run setup` runs one pnpm install over all projects), and no-mistakes.
# Called by setup.sh; safe to run alone.
set -euo pipefail
. "$(dirname "$0")/setup-lib.sh"

MISE_VERSION=v2026.7.7
MISE_SHA256=429f71e7e989908bf975aafac9066329c16e2d8fc7cd8e74fdf21dd6300ffe7c
# Same version as .no-mistakes.yaml pins; the release publishes no checksum, so this
# hash pins the tarball that was reviewed when the version was pinned (re-pin both on a bump).
NO_MISTAKES_VERSION=v1.106.1
NO_MISTAKES_SHA256=fbf131356a278623a509b6b50ecb39c5375b1a82e7dcfc48e42d10e761416d1e

mkdir -p "$T"

# mise, readable by the unprivileged test user.
export MISE_DATA_DIR=/opt/mise
if [ ! -x /usr/local/bin/mise ]; then
  fetch "https://github.com/jdx/mise/releases/download/$MISE_VERSION/mise-$MISE_VERSION-linux-x64" "$MISE_SHA256" /usr/local/bin/mise
  chmod +x /usr/local/bin/mise
fi
(cd "$ROOT" && mise trust && mise run setup)
chmod -R a+rX /opt/mise
(cd "$ROOT" && mise exec -- printenv PATH) > "$T/toolpath"

# no-mistakes: installed only (never run here); `no-mistakes init` stays a per-repository choice.
NM_DIR="/opt/no-mistakes/$NO_MISTAKES_VERSION"
if [ ! -x "$NM_DIR/no-mistakes" ]; then
  mkdir -p "$NM_DIR"
  fetch "https://github.com/kunchenguid/no-mistakes/releases/download/$NO_MISTAKES_VERSION/no-mistakes-$NO_MISTAKES_VERSION-linux-amd64.tar.gz" "$NO_MISTAKES_SHA256" "$NM_DIR/no-mistakes.tar.gz"
  tar -xzf "$NM_DIR/no-mistakes.tar.gz" --no-same-owner -C "$NM_DIR" no-mistakes
  chmod 755 "$NM_DIR/no-mistakes"
fi
ln -sfn "$NM_DIR/no-mistakes" /usr/local/bin/no-mistakes

# Sorts after nodejs.sh, which would otherwise put the image's older Node first.
rm -f /etc/profile.d/aequoros.sh
MISE_PATH="$(tr ':' '\n' < "$T/toolpath" | grep '^/opt/mise/' | paste -sd:)"
cat > /etc/profile.d/zz-aequoros.sh <<PROFILE
export MISE_DATA_DIR=/opt/mise
export PATH="$MISE_PATH:\$PATH"
PROFILE
