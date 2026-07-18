"""Contract tests for the cross-source counterparty deduplicator."""

from __future__ import annotations

from app.etl.contracts import MatchType
from app.etl.deduplication.counterparty_matcher import (
    CounterpartyMatcher,
    blocking_key,
    compute_signals,
)
from app.etl.models import CounterpartyMatchingModel, HumanOverride
from tests.etl._factories import counterparty

ACME_VARIANTS = [
    counterparty(
        "C1",
        "ACME TRADING LTD",
        source="t24",
        national_id="GHA-000111",
        country="GH",
        counterparty_type="CORPORATE",
    ),
    counterparty(
        "C2",
        "Acme Trading Limited",
        source="upload",
        national_id="GHA-000111",
        country="GH",
        counterparty_type="CORPORATE",
    ),
    counterparty(
        "C3", "ACME TRADING CO. LTD", source="los", country="GH", counterparty_type="CORPORATE"
    ),
]
DISTINCT = counterparty(
    "C9", "Kwame Mensah", source="t24", country="GH", counterparty_type="RETAIL_INDIVIDUAL"
)


def test_acme_variants_collapse_to_one_linkage() -> None:
    matcher = CounterpartyMatcher()
    linkages = matcher.link([*ACME_VARIANTS, DISTINCT])

    assert len(linkages) == 1
    link = linkages[0]
    assert link.match_type is MatchType.CROSS_SOURCE
    # All three ACME rows are linked; the distinct individual is not swept in.
    assert set(link.linked_source_ids) == {"C1", "C2", "C3"}
    assert "C9" not in link.linked_source_ids
    assert link.canonical_winner_id in {"C1", "C2", "C3"}


def test_linkage_carries_per_signal_scores_and_confidence() -> None:
    link = CounterpartyMatcher().link(ACME_VARIANTS)[0]
    # Multiple signals are present (fuzzy + phonetic at least), none singly authoritative.
    assert link.signals["token_sort_ratio"] > 0.8
    assert link.signals["phonetic_metaphone"] > 0.0
    assert 0.0 <= link.combined_confidence <= 1.0
    # Provenance rides on every linkage.
    assert link.provenance.operation_ref == "counterparty_matcher/v1"
    assert link.provenance.confidence == link.combined_confidence


def test_high_confidence_pairs_auto_confirm_low_ones_go_to_review() -> None:
    strong = CounterpartyMatcher().link(ACME_VARIANTS)
    assert strong[0].auto_confirmed is True

    # Two only-loosely-similar names should either not link, or link below auto-confirm.
    weak_recs = [
        counterparty("W1", "Danfo Logistics", source="t24"),
        counterparty("W2", "Danso Holdings", source="upload"),
    ]
    weak = CounterpartyMatcher(auto_confirm_threshold=0.95, review_floor=0.4).link(weak_recs)
    assert all(link.auto_confirmed is False for link in weak)


def test_no_linkage_when_fewer_than_two_records() -> None:
    assert CounterpartyMatcher().link([ACME_VARIANTS[0]]) == []


def test_national_id_signal_encodes_negative_evidence() -> None:
    same = compute_signals(
        counterparty("A", "John Doe", national_id="GHA-1"),
        counterparty("B", "Jon Doe", national_id="GHA-1"),
    )
    differ = compute_signals(
        counterparty("A", "John Doe", national_id="GHA-1"),
        counterparty("B", "Jon Doe", national_id="GHA-2"),
    )
    missing = compute_signals(
        counterparty("A", "John Doe"),
        counterparty("B", "Jon Doe"),
    )
    assert same["national_id"] == 1.0
    assert differ["national_id"] == -1.0
    assert missing["national_id"] == 0.0


def test_blocking_key_groups_phonetic_variants() -> None:
    assert blocking_key(ACME_VARIANTS[0]) == blocking_key(ACME_VARIANTS[1])


def test_human_override_forces_non_match() -> None:
    # Pin C1|C2 as "not a match" via the model's override registry; the pair key is the
    # sorted "id|id" string the matcher uses.
    model = CounterpartyMatchingModel()
    model.set_override(
        "C1|C2", HumanOverride(decided_by="analyst", decision=False, reason="distinct entities")
    )
    matcher = CounterpartyMatcher(model=model)
    linkages = matcher.link([ACME_VARIANTS[0], ACME_VARIANTS[1]])
    # With the only candidate pair overridden to non-match, no cluster forms.
    assert linkages == []


def test_fitted_model_stamps_model_id_on_provenance() -> None:
    # Train a tiny model so provenance carries the model id/version (governed ML op).
    positives = [
        compute_signals(ACME_VARIANTS[0], ACME_VARIANTS[1]),
        compute_signals(ACME_VARIANTS[0], ACME_VARIANTS[2]),
    ]
    negatives = [
        compute_signals(ACME_VARIANTS[0], DISTINCT),
        compute_signals(ACME_VARIANTS[1], DISTINCT),
    ]
    model = CounterpartyMatchingModel().fit([*positives, *negatives], [1, 1, 0, 0])
    link = CounterpartyMatcher(model=model, review_floor=0.4).link(ACME_VARIANTS)[0]
    assert link.provenance.model_id == "counterparty_matching_model"
    assert link.provenance.model_version == "1.0.0"
