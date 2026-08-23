"""Row-level cross-source position matching: the same real position, two systems.

Why this stage exists — and why it is not in ``run_etl``
-------------------------------------------------------
:func:`app.etl.run_etl` is pure and sees exactly ONE extraction batch. That is
deliberate and worth preserving. But a bank whose core banking book arrives twice
— once through a nightly direct-database sync, once through an API push — does
not deliver the two copies in one batch: at Sample Bank the two books arrived a
month apart. No single-batch pass can see that, so this matcher is invoked from
the out-of-band ``etl_dedup`` job (``app.services.etl_dedup_jobs``), which has
database access and can load the CURRENT canonical generation across every source
system at one as-of date.

The complement, not the replacement, of the book-level detector
--------------------------------------------------------------
``app.services.reconciliation.detect_source_overlap`` already answers, at the
aggregate level, *"are two source systems each carrying a book for this position
type, and how big is the duplication?"* — and ``app.services.system_of_record``
turns that into a named rule violation once a bank has declared its book of
record. Neither can say WHICH rows are the same position, and a withdrawal
performed without that evidence is a guess.

This matcher supplies exactly that missing evidence, and only for the position
types the book-level detector has already flagged as contested. The book-level
control owns materiality and the vocabulary; this one owns the rows.

Why ``source_reference`` cannot be the key
------------------------------------------
:class:`app.etl.deduplication.position_deduplicator.PositionDeduplicator` groups
on ``source_reference``, which is a per-source-system identifier: the same loan is
``AA.ARRANGEMENT/1181932`` in one feed and ``SBL-LOAN-010092`` in another, so it
never groups. Canonical supersession has the same blind spot by design — the
current-generation key on ``canonical_positions`` includes ``source_system``, so
two systems each hold a complete live book (which is CORRECT: a bank legitimately
splits its book, core banking for loans and treasury for securities).

DETECTION ONLY
--------------
Nothing here picks a winner, merges, retires or suppresses a position. Output is
:class:`LinkageRecord` evidence with :attr:`MatchType.CROSS_SOURCE`, preserving
every subsumed id. ``canonical_winner_id`` is required by the contract, so it
carries the lexically smallest member id as a STABLE GROUPING REPRESENTATIVE — it
is not a system-of-record determination, every linkage says so through the
``system_of_record_determined: 0.0`` signal, and ``auto_confirmed`` is always
False. Choosing an authoritative system is the register's job
(``app.services.system_of_record``); performing the withdrawal is a separate,
separately approved act.

What the matcher catches, in two tiers
--------------------------------------
**Tier 1 — shared source reference.** Two systems using the SAME arrangement
identifier for the same position type in the same currency. Zero inference: this
is the bank's own identifier appearing twice. The whole normalized reference must
be equal — no prefix stripping, no namespace guessing, because inventing an
equivalence between ``ACCOUNT/2782827`` and ``SBL-DEP-2782827`` is precisely the
source-semantics reasoning the ML-ETL layer is forbidden to do.

**Tier 2 — attribute fingerprint.** For rows tier 1 did not already pair: the
contract terms both systems must agree on if they describe the same deal —
resolved counterparty, product, currency, origination date, interest rate and
contractual maturity. The first five must be PRESENT: a missing one means "we
cannot tell", never "match", and the row is counted as unassessable instead.

``contractual_maturity`` is the deliberate exception — it must AGREE, and two
rows that both state no maturity agree. A demand or savings deposit has no
contractual maturity by definition, so requiring its presence would make this
tier permanently blind to the entire retail deposit book of every bank, which is
a class-level blind spot rather than an edge case. Measured on the primary at
BK-0PMD7Z5M / 2026-06-30 this is not a licence to over-match: the relaxation
turns 0 deposit matches into 80, ALL of them one-row-per-system and none
ambiguous, against a chance-collision expectation below 0.02 pairs. A pair that
rests on the joint absence carries ``maturity_stated: 0.0`` and a lower
confidence, because it is agreement on one fewer stated term. One system stating
a maturity where the other does not is NOT agreement — that is "cannot tell", and
those rows fall through to ``assessed_unmatched``.

``balance`` is deliberately NOT in the key (two extracts taken at different times
legitimately disagree on it) but IS reported as evidence on the linkage.

What it does NOT catch — stated, not silently missed
----------------------------------------------------
* **Different granularity.** One system's facility row against another's three
  drawdown rows share no origination/maturity/rate, so they do not pair; and a
  group that is not one-row-per-system is emitted as ``ambiguous`` (lower
  confidence, ``one_to_one: 0.0``) rather than as a confirmed pair. Nothing is
  aggregated or apportioned to force a match.
* **Books that agree on nothing.** Where two systems describe the same economic
  book with different references AND different contract terms, no row-level
  matcher can honestly pair them. The book-level detector remains the only
  truthful signal there, and this matcher reports zero rather than loosening its
  key until something appears.
* **Positions whose counterparty could not be resolved across systems**, and
  positions with any null tier-2 component. Both are COUNTED and reported in
  :class:`CrossSourceCoverage` so the gap is visible rather than implied.
* **Duplicates inside one source system** — that is ``PositionDeduplicator``'s
  job, and this matcher requires two distinct source systems in every group.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.etl.contracts import (
    ETLOperationType,
    ETLProvenance,
    LinkageRecord,
    MatchType,
)

_OPERATION_REF = "cross_source_position_matcher/v1"

#: Tier 1: the bank's own identifier, used by two systems for the same kind of
#: position in the same currency. Nothing is inferred, so the confidence is the
#: highest the layer expresses — while still short of 1.0, because two feeds
#: could in principle reuse an identifier string across unrelated namespaces.
SHARED_REFERENCE_CONFIDENCE = 0.99
#: Tier 2, one row per system, every contract term STATED and agreeing.
ATTRIBUTE_CONFIDENCE = 0.80
#: Tier 2, one row per system, agreeing on five stated terms while both sides
#: state no contractual maturity. Real agreement, one term thinner in evidence.
ATTRIBUTE_UNSTATED_MATURITY_CONFIDENCE = 0.70
#: Tier 2, more than one row on some side: the terms agree but the pairing is
#: ambiguous (a facility/drawdown split looks like this). Evidence, not a pair.
AMBIGUOUS_CONFIDENCE = 0.50

MATCH_SHARED_REFERENCE = "shared_source_reference"
MATCH_ATTRIBUTE_FINGERPRINT = "attribute_fingerprint"


@dataclass(frozen=True)
class CanonicalPositionRow:
    """One current-generation position + snapshot, flattened for matching.

    Source-agnostic by construction: the loader hands over canonical values, so
    this matcher never reaches into source-system semantics (``data_engine.md``
    §2.1). ``counterparty_key`` is a RESOLVED cross-source counterparty identity
    (see ``app.services.etl_dedup_jobs``), not a per-system counterparty id.
    """

    row_id: str
    source_system: str
    source_reference: str
    position_type: str
    currency: str
    counterparty_key: str | None = None
    product_code: str | None = None
    origination_date: date | None = None
    contractual_maturity: date | None = None
    interest_rate: Decimal | None = None
    balance: Decimal | None = None


@dataclass(frozen=True)
class CrossSourceCoverage:
    """What the matcher could and could not assess — the honesty ledger.

    Every row the matcher declined to pair is accounted for here, so a caller can
    never read "few matches" as "few duplicates" when the real answer is "we could
    not tell". ``considered`` is the population; the rest partition the misses.
    """

    considered: int = 0
    source_systems: tuple[str, ...] = ()
    position_types: tuple[str, ...] = ()
    matched_rows: int = 0
    #: The position types and source systems that actually appear in a linkage —
    #: NOT the population's. A finding that named every contested type when only
    #: the deposit book matched would overstate what the evidence supports.
    matched_position_types: tuple[str, ...] = ()
    matched_source_systems: tuple[str, ...] = ()
    #: Rows excluded from tier 2 because their counterparty had no cross-source
    #: resolved identity (the join dimension was unavailable, not absent).
    unresolved_counterparty: int = 0
    #: Rows excluded from tier 2 because a REQUIRED key component was null
    #: (counterparty, product, origination or rate). An unstated contractual
    #: maturity is not a gap — it is a matchable value, see ``_attribute_key``.
    incomplete_attributes: int = 0
    #: Rows that were fully assessable on both tiers and simply did not pair.
    assessed_unmatched: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "considered": self.considered,
            "source_systems": list(self.source_systems),
            "position_types": list(self.position_types),
            "matched_rows": self.matched_rows,
            "matched_position_types": list(self.matched_position_types),
            "matched_source_systems": list(self.matched_source_systems),
            "unresolved_counterparty": self.unresolved_counterparty,
            "incomplete_attributes": self.incomplete_attributes,
            "assessed_unmatched": self.assessed_unmatched,
        }


@dataclass(frozen=True)
class CrossSourceResult:
    """Linkage evidence plus the coverage ledger that qualifies it."""

    linkages: tuple[LinkageRecord, ...] = ()
    coverage: CrossSourceCoverage = field(default_factory=CrossSourceCoverage)

    @property
    def matched_row_ids(self) -> frozenset[str]:
        return frozenset(rid for link in self.linkages for rid in link.linked_source_ids)

    def by_match(self) -> dict[str, int]:
        """Linkage count per matching rule, for the report and the audit event."""
        counts: dict[str, int] = {MATCH_SHARED_REFERENCE: 0, MATCH_ATTRIBUTE_FINGERPRINT: 0}
        for link in self.linkages:
            key = (
                MATCH_SHARED_REFERENCE
                if link.signals.get(MATCH_SHARED_REFERENCE, 0.0) > 0.0
                else MATCH_ATTRIBUTE_FINGERPRINT
            )
            counts[key] += 1
        return counts


class CrossSourcePositionMatcher:
    """Links canonical positions that two source systems both carry.

    Deliberately NOT an :class:`app.etl.contracts.Deduplicator`: that ABC's
    ``link(records: list[RawRecord])`` is the contract of an in-batch stage over
    the adapter's post-extract shape, and this matcher spans batches over
    canonical rows. Widening the ABC to fit would let a future in-batch caller
    invoke a stage that cannot work on one batch. The OUTPUT contract is
    unchanged: :class:`LinkageRecord` with :attr:`MatchType.CROSS_SOURCE`.
    """

    match_type = MatchType.CROSS_SOURCE

    def link(self, rows: list[CanonicalPositionRow]) -> CrossSourceResult:
        """Match ``rows`` across source systems; never mutates or drops a row."""
        systems = {row.source_system for row in rows}
        types = {row.position_type for row in rows}
        base = CrossSourceCoverage(
            considered=len(rows),
            source_systems=tuple(sorted(systems)),
            position_types=tuple(sorted(types)),
        )
        if len(systems) < 2:
            # One source system carries the whole population: there is nothing
            # cross-source to find, and saying so is not the same as "clean".
            return CrossSourceResult(coverage=base)

        linkages: list[LinkageRecord] = []
        paired: set[str] = set()

        for group in self._groups(rows, key=_shared_reference_key).values():
            link = self._build(group, match=MATCH_SHARED_REFERENCE)
            if link is not None:
                linkages.append(link)
                paired.update(link.linked_source_ids)

        remaining = [row for row in rows if row.row_id not in paired]
        unresolved = sum(1 for row in remaining if row.counterparty_key is None)
        incomplete = sum(
            1
            for row in remaining
            if row.counterparty_key is not None and _attribute_key(row) is None
        )
        for group in self._groups(remaining, key=_attribute_key).values():
            link = self._build(group, match=MATCH_ATTRIBUTE_FINGERPRINT)
            if link is not None:
                linkages.append(link)
                paired.update(link.linked_source_ids)

        assessable = len(remaining) - unresolved - incomplete
        assessed_unmatched = assessable - sum(
            1 for row in remaining if row.row_id in paired
        )
        by_id = {row.row_id: row for row in rows}
        coverage = CrossSourceCoverage(
            considered=base.considered,
            source_systems=base.source_systems,
            position_types=base.position_types,
            matched_rows=len(paired),
            matched_position_types=tuple(
                sorted({by_id[rid].position_type for rid in paired})
            ),
            matched_source_systems=tuple(
                sorted({by_id[rid].source_system for rid in paired})
            ),
            unresolved_counterparty=unresolved,
            incomplete_attributes=incomplete,
            assessed_unmatched=max(assessed_unmatched, 0),
        )
        return CrossSourceResult(linkages=tuple(linkages), coverage=coverage)

    @staticmethod
    def _groups(
        rows: list[CanonicalPositionRow],
        *,
        key: Callable[[CanonicalPositionRow], tuple[str, ...] | None],
    ) -> dict[tuple[str, ...], list[CanonicalPositionRow]]:
        """Bucket rows by ``key``, keeping only buckets spanning ≥2 source systems."""
        buckets: dict[tuple[str, ...], list[CanonicalPositionRow]] = defaultdict(list)
        for row in rows:
            row_key = key(row)
            if row_key is not None:
                buckets[row_key].append(row)
        return {
            k: group
            for k, group in buckets.items()
            if len({row.source_system for row in group}) > 1
        }

    def _build(
        self, group: list[CanonicalPositionRow], *, match: str
    ) -> LinkageRecord | None:
        by_system: dict[str, list[CanonicalPositionRow]] = defaultdict(list)
        for row in group:
            by_system[row.source_system].append(row)
        if len(by_system) < 2:  # pragma: no cover - _groups already guarantees this
            return None

        one_to_one = all(len(members) == 1 for members in by_system.values())
        if match == MATCH_SHARED_REFERENCE:
            confidence = SHARED_REFERENCE_CONFIDENCE if one_to_one else AMBIGUOUS_CONFIDENCE
            signals = {
                MATCH_SHARED_REFERENCE: 1.0,
                "position_type_match": 1.0,
                "currency_match": 1.0,
            }
        else:
            maturity_stated = all(row.contractual_maturity is not None for row in group)
            stated_confidence = (
                ATTRIBUTE_CONFIDENCE if maturity_stated else ATTRIBUTE_UNSTATED_MATURITY_CONFIDENCE
            )
            confidence = stated_confidence if one_to_one else AMBIGUOUS_CONFIDENCE
            signals = {
                MATCH_ATTRIBUTE_FINGERPRINT: 1.0,
                "counterparty_match": 1.0,
                "product_match": 1.0,
                "currency_match": 1.0,
                "origination_match": 1.0,
                "rate_match": 1.0,
                "maturity_match": 1.0,
                # 0.0 = both sides state no maturity. The terms still agree; the
                # reviewer can see the pair rests on one fewer stated term.
                "maturity_stated": 1.0 if maturity_stated else 0.0,
            }
        signals["one_to_one"] = 1.0 if one_to_one else 0.0
        signals["distinct_source_systems"] = float(len(by_system))
        # Load-bearing: this linkage is EVIDENCE, never a resolution. No consumer
        # may read ``canonical_winner_id`` as the authoritative system.
        signals["system_of_record_determined"] = 0.0

        ids = tuple(sorted(row.row_id for row in group))
        return LinkageRecord(
            match_type=MatchType.CROSS_SOURCE,
            # A stable grouping representative, NOT a winner: lexically smallest
            # so the linkage is deterministic across runs. See the module
            # docstring — picking an authoritative system is the register's job.
            canonical_winner_id=ids[0],
            linked_source_ids=ids,
            signals=signals,
            combined_confidence=confidence,
            provenance=ETLProvenance(
                operation_type=ETLOperationType.DEDUP_LINK,
                operation_ref=_OPERATION_REF,
                confidence=confidence,
            ),
            # Never auto-confirmed: confirming a cross-source position linkage
            # would amount to naming a winner, which this layer must not do.
            auto_confirmed=False,
        )


def _shared_reference_key(row: CanonicalPositionRow) -> tuple[str, ...] | None:
    """Tier 1 key: the whole normalized reference + the two identity fields.

    ``position_type`` and ``currency`` join the key because they are identity —
    they never move over a position's life — so two rows that are the same
    position always agree on them, while an accidental identifier collision
    across unrelated namespaces usually does not.
    """
    reference = row.source_reference.strip().upper()
    if not reference:
        return None
    return (MATCH_SHARED_REFERENCE, reference, row.position_type, row.currency)


#: The tier-2 sentinel for "this row states no contractual maturity". It can
#: never collide with a stated date, so a row that states one and a row that does
#: not land in different buckets — which is the correct reading: one system
#: knowing a term the other does not is "cannot tell", not agreement.
_UNSTATED_MATURITY = "maturity:unstated"


def _attribute_key(row: CanonicalPositionRow) -> tuple[str, ...] | None:
    """Tier 2 key: the contract terms, or ``None`` when a REQUIRED one is unknown.

    All-or-nothing on the five required terms, on purpose: dropping a null
    component and matching on the rest would trade a known gap for an invented
    match, which is the failure mode this whole layer exists to prevent.
    Contractual maturity is the documented exception (see the module docstring)
    and enters through a sentinel rather than being dropped, so its absence is
    matched only against another absence.
    """
    required = (
        row.counterparty_key,
        row.product_code,
        row.origination_date.isoformat() if row.origination_date else None,
        _rate_key(row.interest_rate),
    )
    if any(part is None for part in required):
        return None
    maturity = (
        row.contractual_maturity.isoformat()
        if row.contractual_maturity is not None
        else _UNSTATED_MATURITY
    )
    return (
        MATCH_ATTRIBUTE_FINGERPRINT,
        row.position_type,
        row.currency,
        *(str(part) for part in required),
        maturity,
    )


def _rate_key(rate: Decimal | None) -> str | None:
    """Normalize a rate so ``0.1143`` and ``0.11430000`` are one key, not two."""
    if rate is None:
        return None
    return format(rate.normalize(), "f")
