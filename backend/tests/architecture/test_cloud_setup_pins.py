"""The cloud setup script installs the same MinIO and OpenBao builds that CI verifies.

``backend/scripts/cloud/setup.sh`` pins MinIO and OpenBao so a cloud container tests
against the binaries CI does. The composite actions under ``.github/actions`` hold the
CI side of each pin. Changing a release or a SHA256 in only one place lets the two
environments drift, so these tests read both sides and compare them.
"""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
SETUP_SCRIPT = REPOSITORY / "backend" / "scripts" / "cloud" / "setup.sh"
MINIO_ACTION = REPOSITORY / ".github" / "actions" / "setup-minio" / "action.yml"
OPENBAO_ACTION = REPOSITORY / ".github" / "actions" / "setup-openbao" / "action.yml"

SHA256 = r"[0-9a-f]{64}"


def _single(pattern: str, text: str, source: Path) -> str:
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    assert len(matches) == 1, (
        f"{source.relative_to(REPOSITORY)}: expected one match for {pattern!r}, found {matches}"
    )
    return matches[0]


def _script_pin(name: str) -> str:
    return _single(rf"^{name}=(\S+)$", SETUP_SCRIPT.read_text(encoding="utf-8"), SETUP_SCRIPT)


def _action_sha256(action: Path) -> str:
    return _single(rf'echo "({SHA256})  ', action.read_text(encoding="utf-8"), action)


def test_minio_release_matches_ci() -> None:
    ci = _single(
        r"minio/releases/download/(RELEASE\.[0-9TZ-]+)/minio\.linux-amd64\.\1",
        MINIO_ACTION.read_text(encoding="utf-8"),
        MINIO_ACTION,
    )
    assert _script_pin("MINIO_RELEASE") == ci


def test_minio_sha256_matches_ci() -> None:
    assert _script_pin("MINIO_SHA256") == _action_sha256(MINIO_ACTION)


def test_openbao_version_matches_ci() -> None:
    ci = _single(
        r"openbao/releases/download/(v[0-9][^/]*)/openbao_[^/\s]+_linux_amd64\.tar\.gz",
        OPENBAO_ACTION.read_text(encoding="utf-8"),
        OPENBAO_ACTION,
    )
    assert _script_pin("OPENBAO_VERSION") == ci


def test_openbao_sha256_matches_ci() -> None:
    assert _script_pin("OPENBAO_SHA256") == _action_sha256(OPENBAO_ACTION)
