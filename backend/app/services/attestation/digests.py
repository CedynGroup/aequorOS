"""Attestation digests — what a signature actually binds to.

Three digests, each with a distinct job (docs/attestation_esignature.md §3.1):

``content_digest``
    A true content fingerprint of a package snapshot: canonical JSON with
    VOLATILE metadata (``generated_at``) removed, so two regenerations of
    identical figures produce an identical digest. This is what the existing
    ``regulatory_packages.snapshot_sha256`` cannot do — it embeds
    ``metadata.generated_at`` and therefore seals *a version*, not content
    (gap G13). ``snapshot_sha256`` is left untouched as the version seal.

``register_state_digest``
    The master-data analogue of an engine run's ``input_hash``, for the five
    ``LRT-*`` corporate packs which bind NO engine run (``source_runs = []``,
    gap G16). It pins which register rows — and which values — produced the
    pack, so a later mutation of a shareholding or outlet is detectable
    against a signed pack. Weaker than ``input_hash``: it proves state, not
    derivation (legal register L16).

``certification_digest``
    What every signer signs. Binds institution + return identity + the
    content digest + whichever provenance the class has. Both the PDF
    signature and the detached attestation cover this one value, so it is
    provable that every signer signed the same figures.

Canonicalisation is the engines' recipe EXACTLY (``regulatory_capital.py``
``_snapshot_hash`` and peers): ``sort_keys=True``, compact separators,
``ensure_ascii=True``, and deliberately **no** ``default=`` handler — every
value must already be JSON-native. ``generation.py:66`` passes
``default=str``; the attestation path does not inherit that laxity, because a
silent ``str()`` coercion would let two different values hash the same.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

ATTESTATION_SCHEMA = "aequoros-attestation-v2"
REGISTER_STATE_SCHEMA = "aequoros-register-state-v1"
SIGNATURE_PAYLOAD_SCHEMA = "aequoros-signature-v1"

type BindingClass = Literal["engine_run", "master_data"]

#: Snapshot ``metadata`` keys excluded from ``content_digest``. Only ever
#: extend this set — removing a key would change every historical digest.
VOLATILE_METADATA_KEYS: frozenset[str] = frozenset({"generated_at"})


class CanonicalisationError(ValueError):
    """A payload contained a value JSON cannot represent losslessly."""


def canonical_json(payload: Any) -> str:
    """The one canonical form. No ``default=`` — see the module docstring."""
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except TypeError as exc:  # a Decimal/date/UUID leaked in un-stringified
        raise CanonicalisationError(
            f"Attestation payloads must be JSON-native (pre-stringify decimals, "
            f"dates and UUIDs): {exc}"
        ) from exc


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_of(payload: Any) -> str:
    return sha256_hex(canonical_json(payload))


def strip_volatile_metadata(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``snapshot`` with volatile metadata keys removed.

    Non-mutating: the stored snapshot is immutable evidence and must never be
    touched.
    """
    stripped = dict(snapshot)
    metadata = stripped.get("metadata")
    if isinstance(metadata, Mapping):
        stripped["metadata"] = {
            key: value for key, value in metadata.items() if key not in VOLATILE_METADATA_KEYS
        }
    return stripped


def content_digest(snapshot: Mapping[str, Any]) -> str:
    """Content fingerprint of a package snapshot (volatile metadata excluded)."""
    return digest_of(strip_volatile_metadata(snapshot))


def register_state_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    """Pin the master-data register state that produced an ``LRT-*`` pack.

    Each row contributes ``{table, id, updated_at, values_digest}`` where
    ``values_digest`` covers the row's business fields only — never internal
    bookkeeping — so the digest changes if and only if reportable data changed.
    """
    entries = [
        {
            "table": str(row["table"]),
            "id": str(row["id"]),
            "updated_at": str(row["updated_at"]),
            "values_digest": digest_of(row["values"]),
        }
        for row in rows
    ]
    return digest_of(
        {
            "schema": REGISTER_STATE_SCHEMA,
            "rows": sorted(entries, key=canonical_json),
        }
    )


def certification_digest(  # noqa: PLR0913 - keyword-only by design; positional
    # args on eleven near-identical strings would be a correctness hazard
    *,
    organization_id: str,
    bank_id: str,
    package_id: str,
    package_version: int,
    return_code: str,
    reporting_date: str,
    basis: str,
    content_digest_value: str,
    binding_class: BindingClass,
    source_runs: Sequence[Mapping[str, Any]] | None = None,
    register_state_digest_value: str | None = None,
) -> str:
    """The value every signer signs.

    ``source_runs`` entries are taken verbatim as stored on the package
    (``{module, run_id, input_hash, engine_version}``) and sorted by their own
    canonical JSON, so DB return order cannot change the digest — the same
    value-based discipline the engine ``input_hash`` uses.
    """
    if binding_class == "engine_run":
        if not source_runs:
            raise ValueError(
                "binding_class 'engine_run' requires at least one source run; "
                "a package with no engine run must bind as 'master_data'."
            )
        if register_state_digest_value is not None:
            raise ValueError("engine_run bindings must not carry a register-state digest.")
    else:
        if source_runs:
            raise ValueError(
                "binding_class 'master_data' must not carry source runs; "
                "an engine-backed package must bind as 'engine_run'."
            )
        if not register_state_digest_value:
            raise ValueError(
                "binding_class 'master_data' requires a register-state digest — "
                "otherwise the signature would bind content with no provenance (G16)."
            )

    payload: dict[str, Any] = {
        "schema": ATTESTATION_SCHEMA,
        "organization_id": organization_id,
        "bank_id": bank_id,
        "package_id": package_id,
        "package_version": package_version,
        "return_code": return_code,
        "reporting_date": reporting_date,
        "basis": basis,
        "content_digest": content_digest_value,
        "binding_class": binding_class,
        "source_runs": sorted(
            ({str(k): _jsonable(v) for k, v in entry.items()} for entry in (source_runs or ())),
            key=canonical_json,
        ),
        "register_state_digest": register_state_digest_value,
    }
    return digest_of(payload)


def attestation_payload(  # noqa: PLR0913 - keyword-only envelope fields
    *,
    certification_digest_value: str,
    signer_id: str,
    signing_role: str,
    officer_title: str | None,
    statement: str,
    declared_at: str,
) -> dict[str, Any]:
    """The per-signature payload.

    ``statement`` is what-you-see-is-what-you-sign: the signer's cryptographic
    commitment covers the exact wording rendered on screen, not merely the
    numbers. Any change to that wording changes the signature.
    """
    return {
        "schema": SIGNATURE_PAYLOAD_SCHEMA,
        "certification_digest": certification_digest_value,
        "signer_id": signer_id,
        "signing_role": signing_role,
        "officer_title": officer_title,
        "statement": statement,
        "declared_at": declared_at,
    }


def _jsonable(value: Any) -> Any:
    """Pre-stringify the few non-JSON types that legitimately appear in
    persisted ``source_runs`` entries. Anything else raises in canonical_json,
    which is the intended fail-loud behaviour."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)
