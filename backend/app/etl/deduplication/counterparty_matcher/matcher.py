"""Cross-source counterparty deduplicator (multi-signal + RandomForest).

Links records that denote the same real-world counterparty across sources (T24 +
spreadsheet upload + LOS), per the build brief. It never destroys source records: it
emits :class:`LinkageRecord` s (winner + subsumed ids + per-signal scores + combined
confidence). Pairs above a high threshold are ``auto_confirmed``; the rest are linked
but left for human review.

Pipeline: block candidate pairs (phonetic key) → score each pair through
:mod:`signals` → probability from :class:`CounterpartyMatchingModel` → union-find the
above-floor edges into clusters → one linkage per multi-member cluster. The model is
consulted per pair, so a registered human override on a pair id wins outright.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from app.etl.contracts import (
    Deduplicator,
    ETLOperationType,
    ETLProvenance,
    LinkageRecord,
    MatchType,
)
from app.etl.deduplication._fields import get_field, normalize_name, record_id
from app.etl.deduplication.counterparty_matcher.signals import blocking_key, compute_signals
from app.etl.models.counterparty_matching_model import (
    MODEL_ID,
    MODEL_VERSION,
    CounterpartyMatchingModel,
)

if TYPE_CHECKING:
    from app.domain.ingestion.contracts import RawRecord

_OPERATION_REF = "counterparty_matcher/v1"


class _UnionFind:
    """Minimal union-find over record ids for transitive-closure clustering."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression.
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


class CounterpartyMatcher(Deduplicator):
    """Deduplicator producing :class:`MatchType.CROSS_SOURCE` linkages."""

    match_type = MatchType.CROSS_SOURCE

    def __init__(
        self,
        model: CounterpartyMatchingModel | None = None,
        *,
        auto_confirm_threshold: float = 0.90,
        review_floor: float = 0.55,
    ) -> None:
        if not 0.0 <= review_floor <= auto_confirm_threshold <= 1.0:
            msg = "Require 0 <= review_floor <= auto_confirm_threshold <= 1."
            raise ValueError(msg)
        self.model = model or CounterpartyMatchingModel()
        self.auto_confirm_threshold = auto_confirm_threshold
        self.review_floor = review_floor

    def link(self, records: list[RawRecord]) -> list[LinkageRecord]:
        counterparties = [r for r in records if r.entity_type == "counterparty"]
        if len(counterparties) < 2:
            return []

        by_id = {record_id(r): r for r in counterparties}

        # -- block, then score candidate pairs within each block --
        blocks: dict[str, list[str]] = defaultdict(list)
        for r in counterparties:
            blocks[blocking_key(r)].append(record_id(r))

        uf = _UnionFind()
        # edges: (id_a, id_b) -> (probability, signals) for above-floor pairs.
        edges: dict[tuple[str, str], tuple[float, dict[str, float]]] = {}
        for members in blocks.values():
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    id_a, id_b = members[i], members[j]
                    rec_a, rec_b = by_id[id_a], by_id[id_b]
                    signals = compute_signals(rec_a, rec_b)
                    pair_key = _pair_key(id_a, id_b)
                    prediction = self.model.predict(signals, key=pair_key)
                    if prediction.probability >= self.review_floor:
                        edges[(id_a, id_b)] = (prediction.probability, signals)
                        uf.union(id_a, id_b)

        # -- assemble clusters --
        clusters: dict[str, list[str]] = defaultdict(list)
        for cid in by_id:
            clusters[uf.find(cid)].append(cid)

        linkages: list[LinkageRecord] = []
        for member_ids in clusters.values():
            if len(member_ids) < 2:
                continue
            linkages.append(self._build_linkage(member_ids, by_id, edges))
        return linkages

    def _build_linkage(
        self,
        member_ids: list[str],
        by_id: dict[str, RawRecord],
        edges: dict[tuple[str, str], tuple[float, dict[str, float]]],
    ) -> LinkageRecord:
        member_set = set(member_ids)
        # Edges internal to this cluster.
        internal = [
            (prob, sig)
            for (a, b), (prob, sig) in edges.items()
            if a in member_set and b in member_set
        ]
        probs = [prob for prob, _ in internal]
        # Conservative combined confidence: the weakest link that holds the cluster
        # together (a chain is only as strong as its weakest confirmed edge).
        combined = min(probs) if probs else 0.0
        signals = _average_signals([sig for _, sig in internal])

        winner = _select_winner(member_ids, by_id)
        auto_confirmed = combined >= self.auto_confirm_threshold

        provenance = ETLProvenance(
            operation_type=ETLOperationType.DEDUP_LINK,
            operation_ref=_OPERATION_REF,
            model_id=MODEL_ID if self.model.is_fitted else None,
            model_version=MODEL_VERSION if self.model.is_fitted else None,
            confidence=combined,
        )
        return LinkageRecord(
            match_type=MatchType.CROSS_SOURCE,
            canonical_winner_id=winner,
            linked_source_ids=tuple(sorted(member_ids)),
            signals=signals,
            combined_confidence=combined,
            provenance=provenance,
            auto_confirmed=auto_confirmed,
        )


def _pair_key(id_a: str, id_b: str) -> str:
    lo, hi = sorted((id_a, id_b))
    return f"{lo}|{hi}"


def _average_signals(signal_dicts: list[dict[str, float]]) -> dict[str, float]:
    if not signal_dicts:
        return {}
    keys = signal_dicts[0].keys()
    n = float(len(signal_dicts))
    return {k: round(sum(d.get(k, 0.0) for d in signal_dicts) / n, 6) for k in keys}


def _select_winner(member_ids: list[str], by_id: dict[str, RawRecord]) -> str:
    """Canonical winner = the most information-complete record.

    Ranked by count of populated identity/contact fields, then by longest name
    (more specific), then by id for determinism. The loser records are never dropped —
    they are the ``linked_source_ids`` preserved on the linkage.
    """
    concepts = ("name", "national_id", "address", "account_number", "country", "type")

    def completeness(cid: str) -> tuple[int, int, str]:
        rec = by_id[cid]
        populated = sum(1 for c in concepts if get_field(rec, c))
        name_len = len(normalize_name(get_field(rec, "name")))
        return (populated, name_len, cid)

    return max(member_ids, key=completeness)
