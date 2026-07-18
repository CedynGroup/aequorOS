"""Guard-alias resolver: canonical-concept view over a raw source record.

Preprocessing operates on the adapter's post-``extract`` :class:`RawRecord`, whose ``data``
dict carries *raw source column names* (``balance_ghs``, ``ccy``, ``customer_id``). The
regulatory-critical guard (``data_engine.md`` §12.5, :data:`REGULATORY_CRITICAL_FIELDS`),
however, is defined over *canonical concepts* (``balance``, ``currency``,
``counterparty_id``). This module bridges the two so the guard keys on the concept, not
the source spelling — *a value under ``balance_ghs`` is still guarded as ``balance``*.

Two things live here:

* :func:`resolve_concept` / :func:`canonical_view` — map a raw column (or a whole record)
  onto its canonical concept via an alias index that extends the deduplication field
  aliases (:mod:`app.etl.deduplication._fields`) with the regulatory-critical concepts.
* :func:`make_operation` — the single sanctioned/flagged op builder every deterministic
  preprocessor routes through. It enforces the §12.5 discipline centrally: a critical
  concept may be *rewritten* only by a value-preserving format normalization applied
  through a raw alias; a value-changing edit to a critical concept becomes a
  :class:`Disposition.FLAGGED` op (``after=None``) — never a silent modification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.etl.contracts import (
    REGULATORY_CRITICAL_FIELDS,
    Disposition,
    ETLOperation,
    ETLOperationType,
    ETLProvenance,
)
from app.etl.deduplication._fields import _ALIASES, _normalize_key

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.domain.ingestion.contracts import RawRecord


# Raw source columns -> canonical *regulatory-critical* concept. These take precedence
# over the general dedup aliases because the critical concept name (e.g. ``balance``,
# ``counterparty_id``) is exactly what the §12.5 guard keys on. Every value target here
# is a member of :data:`REGULATORY_CRITICAL_FIELDS`.
_CRITICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "balance": ("balance", "balance_ghs", "balance_ccy", "book_balance", "ledger_balance"),
    "gl_balance": ("gl_balance", "gl_amount", "ledger_amount"),
    "notional": ("notional", "notional_ccy", "notional_amount", "face_value"),
    "outstanding_amount": (
        "outstanding_amount",
        "outstanding",
        "outstanding_balance",
        "amount_outstanding",
    ),
    "principal_amount": (
        "principal_amount",
        "principal",
        "original_principal",
        "disbursed_amount",
    ),
    "interest_rate": (
        "interest_rate",
        "interest_rate_pct",
        "rate",
        "rate_pct",
        "coupon_rate",
        "nominal_rate",
        "yield",
    ),
    "rate_spread": ("rate_spread", "spread", "margin"),
    "counterparty_id": ("counterparty_id", "customer_id", "party_id", "cif", "cif_id"),
    "counterparty_reference": (
        "counterparty_reference",
        "counterparty_ref",
        "customer_reference",
    ),
    "product_id": ("product_id",),
    "regulatory_category": ("regulatory_category", "reg_category", "basel_category"),
    "currency": ("currency", "ccy", "currency_code"),
    "ifrs9_stage": ("ifrs9_stage", "stage", "ecl_stage", "impairment_stage"),
    "risk_weight": ("risk_weight", "risk_weight_pct", "rwa_weight"),
    "capital_amount": ("capital_amount", "capital", "capital_component_amount"),
}


def _build_reverse(alias_map: Mapping[str, tuple[str, ...]]) -> dict[str, str]:
    """Reverse a concept->aliases map into a normalized alias->concept index.

    The first concept to claim a normalized alias wins, so ordering the source maps
    (critical before general) makes critical concepts authoritative for the guard.
    """
    reverse: dict[str, str] = {}
    for concept, aliases in alias_map.items():
        for alias in (concept, *aliases):
            key = _normalize_key(alias)
            reverse.setdefault(key, concept)
    return reverse


# Critical aliases first (authoritative for the guard), then the general dedup aliases
# (name, country, address, ...) for non-critical concept resolution.
_REVERSE_INDEX: dict[str, str] = _build_reverse(_CRITICAL_ALIASES)
for _key, _concept in _build_reverse(_ALIASES).items():
    _REVERSE_INDEX.setdefault(_key, _concept)


def resolve_concept(source_field: str) -> str:
    """Map a raw source column onto its canonical concept (identity if unknown)."""
    return _REVERSE_INDEX.get(_normalize_key(source_field), source_field)


def is_regulatory_critical(source_field: str) -> bool:
    """True when ``source_field`` resolves to a regulatory-critical concept.

    This is the alias-aware guard key: ``is_regulatory_critical("balance_ghs")`` is True
    because the column resolves to the critical concept ``balance``.
    """
    return resolve_concept(source_field) in REGULATORY_CRITICAL_FIELDS


@dataclass(frozen=True)
class ResolvedField:
    """One raw column resolved onto its canonical concept."""

    concept: str
    source_field: str
    value: Any
    is_critical: bool


def canonical_view(record: RawRecord) -> dict[str, ResolvedField]:
    """Canonical-concept view of a record: concept -> the first raw column carrying it.

    Preprocessing and the guard read concepts through this view rather than raw source
    spellings, so a source that names its balance column ``balance_ghs`` and one that
    names it ``balance_ccy`` are cleaned and guarded identically.
    """
    view: dict[str, ResolvedField] = {}
    for source_field, value in record.data.items():
        concept = resolve_concept(source_field)
        if concept in view:  # first column to claim a concept wins (stable, deterministic)
            continue
        view[concept] = ResolvedField(
            concept=concept,
            source_field=source_field,
            value=value,
            is_critical=concept in REGULATORY_CRITICAL_FIELDS,
        )
    return view


def make_operation(  # noqa: PLR0913 - explicit keyword-only op fields; a params struct would obscure
    *,
    record_id: str,
    source_field: str,
    before: Any,
    after: Any,
    operation_type: ETLOperationType,
    operation_ref: str,
    value_preserving: bool,
    confidence: float | None = 1.0,
    model_id: str | None = None,
    model_version: str | None = None,
    flag_reason: str | None = None,
    lineage_input_ids: tuple[str, ...] = (),
) -> ETLOperation | None:
    """Build the §12.5-compliant op for a proposed field change, or ``None`` for a no-op.

    Discipline enforced centrally so no individual preprocessor can violate it:

    * **Non-critical concept** -> straight :class:`Disposition.SANCTIONED` rewrite.
    * **Critical concept, value-preserving, raw alias** (e.g. ``balance_ghs`` carrying a
      thousands-separated number that parses to the same :class:`~decimal.Decimal`) ->
      SANCTIONED rewrite. The op is stamped with the *source* column name, which is not
      itself a critical field, so the contract-level guard admits it.
    * **Critical concept, value-preserving, but the column IS the critical name** (a source
      that literally names its column ``balance``) -> ``None``: the cosmetic change is
      dropped rather than trip the hard contract guard; nothing regulatory is lost.
    * **Critical concept, value-changing** -> :class:`Disposition.FLAGGED` (``after=None``):
      a human must adjudicate; the value is never modified.
    """
    if after == before:
        return None
    concept = resolve_concept(source_field)
    critical = concept in REGULATORY_CRITICAL_FIELDS
    provenance = ETLProvenance(
        operation_type=operation_type,
        operation_ref=operation_ref,
        model_id=model_id,
        model_version=model_version,
        confidence=confidence,
    )

    if not critical:
        return ETLOperation(
            record_id=record_id,
            field_name=source_field,
            disposition=Disposition.SANCTIONED,
            before=before,
            after=after,
            provenance=provenance,
            lineage_input_ids=lineage_input_ids,
        )

    if value_preserving:
        if source_field in REGULATORY_CRITICAL_FIELDS:
            # Column literally named as the critical concept: a SANCTIONED rewrite would
            # trip the contract guard. The change is value-preserving (cosmetic), so drop it.
            return None
        return ETLOperation(
            record_id=record_id,
            field_name=source_field,
            disposition=Disposition.SANCTIONED,
            before=before,
            after=after,
            provenance=provenance,
            lineage_input_ids=lineage_input_ids,
        )

    reason = flag_reason or (
        f"{source_field!r} resolves to regulatory-critical concept {concept!r}; the proposed "
        f"normalization changes its value ({before!r} -> {after!r}) and must be reviewed, "
        f"not applied (data_engine.md §12.5)."
    )
    return ETLOperation(
        record_id=record_id,
        field_name=source_field,
        disposition=Disposition.FLAGGED,
        before=before,
        after=None,
        provenance=provenance,
        lineage_input_ids=lineage_input_ids,
        reason=reason,
    )
