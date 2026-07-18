"""Per-signal scorers for counterparty matching.

The build brief requires *multiple signals, none singly authoritative*: fuzzy string
similarity (rapidfuzz token metrics + a char-ngram TF-IDF cosine + edit distance),
phonetic agreement (Soundex / Metaphone / Double-Metaphone — three distinct encoders), national-id
exact match, address similarity, and account-number cross-reference. Each scorer returns
a value on the model's signal contract (:data:`SIGNAL_FEATURES`); the RandomForest (or
the deterministic fallback) combines them into one probability.

rapidfuzz and jellyfish are declared dependencies of this layer and are imported at
module scope; scikit-learn (for TF-IDF) is imported lazily so a broken install degrades
the TF-IDF signal to 0.0 rather than breaking the whole matcher.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import jellyfish
import numpy as np
from metaphone import doublemetaphone
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from app.etl.deduplication._fields import (
    digits_only,
    get_field,
    jaccard,
    normalize_name,
    token_bag,
)

if TYPE_CHECKING:
    from app.domain.ingestion.contracts import RawRecord


def _phonetic_agreement(name_a: str, name_b: str, encoder) -> float:  # noqa: ANN001
    """1.0 when *any* aligned token pair shares a phonetic code, else 0.0.

    Names are compared token-wise so "Acme Trading" vs "Akme Trading" agrees on the
    stable token even if one token diverges phonetically.
    """
    ta, tb = name_a.split(), name_b.split()
    if not ta or not tb:
        return 0.0
    codes_a = {encoder(t) for t in ta if t}
    codes_b = {encoder(t) for t in tb if t}
    codes_a.discard("")
    codes_b.discard("")
    if not codes_a or not codes_b:
        return 0.0
    overlap = codes_a & codes_b
    return len(overlap) / max(len(codes_a), len(codes_b))


def _dm_agreement(name_a: str, name_b: str) -> float:
    """Double-Metaphone token agreement: 1.0-scaled overlap of primary+secondary codes.

    ``doublemetaphone`` returns a ``(primary, secondary)`` pair per token; both codes are
    pooled so two names agree when any of their primary/alternate encodings coincide.
    """
    ta = [t for t in name_a.split() if t]
    tb = [t for t in name_b.split() if t]
    if not ta or not tb:
        return 0.0

    def _codes(tokens: list[str]) -> set[str]:
        out: set[str] = set()
        for token in tokens:
            out.update(code for code in doublemetaphone(token) if code)
        return out

    codes_a, codes_b = _codes(ta), _codes(tb)
    if not codes_a or not codes_b:
        return 0.0
    return len(codes_a & codes_b) / max(len(codes_a), len(codes_b))


def _tfidf_cosine(name_a: str, name_b: str) -> float:
    """Char-ngram TF-IDF cosine between two names, in [0, 1].

    A two-document fit is degenerate for IDF, so we fit on character 2–4 grams (which
    still discriminate shared substrings) and read the cosine off the L2-normalised
    rows. Degrades to 0.0 if scikit-learn is unavailable.
    """
    if not name_a or not name_b:
        return 0.0
    try:
        from sklearn.feature_extraction.text import (  # noqa: PLC0415 - lazy heavy import
            TfidfVectorizer,
        )
    except (ImportError, OSError):  # pragma: no cover - environment-dependent
        return 0.0
    try:
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        transformed: Any = vec.fit_transform([name_a, name_b])
    except ValueError:
        # Empty vocabulary (e.g. names shorter than the ngram window).
        return 0.0
    dense = np.asarray(transformed.toarray(), dtype=float)
    # Rows are already L2-normalised by TfidfVectorizer, so the dot product is the cosine.
    cosine = float(np.dot(dense[0], dense[1]))
    return max(0.0, min(1.0, cosine))


def _id_signal(a: str | None, b: str | None) -> float:
    """+1.0 both present and equal, -1.0 both present and differ, 0.0 if either missing.

    Encoding *negative evidence* lets the forest separate "contradicted hard identifier"
    from "unknown", which a plain 0/1 match flag cannot.
    """
    da, db = digits_only(a), digits_only(b)
    if not da or not db:
        # Fall back to raw string compare when the id is non-numeric.
        if not a or not b:
            return 0.0
        return 1.0 if a.strip().lower() == b.strip().lower() else -1.0
    return 1.0 if da == db else -1.0


def compute_signals(rec_a: RawRecord, rec_b: RawRecord) -> dict[str, float]:
    """Compute the full multi-signal feature vector for one candidate pair."""
    raw_a = get_field(rec_a, "name") or ""
    raw_b = get_field(rec_b, "name") or ""
    name_a = normalize_name(raw_a)
    name_b = normalize_name(raw_b)

    # -- fuzzy string signals (rapidfuzz operates on the folded comparison keys) --
    token_sort = fuzz.token_sort_ratio(name_a, name_b) / 100.0
    token_set = fuzz.token_set_ratio(name_a, name_b) / 100.0
    partial = fuzz.partial_ratio(name_a, name_b) / 100.0
    jaro_winkler = jellyfish.jaro_winkler_similarity(name_a, name_b) if name_a and name_b else 0.0
    lev_norm = Levenshtein.normalized_similarity(name_a, name_b) if name_a and name_b else 0.0
    tfidf = _tfidf_cosine(name_a, name_b)

    # -- phonetic signals (three distinct encoders) --
    soundex = _phonetic_agreement(name_a, name_b, jellyfish.soundex)
    metaphone = _phonetic_agreement(name_a, name_b, jellyfish.metaphone)
    double_metaphone = _dm_agreement(name_a, name_b)

    # -- hard identifiers + auxiliary signals --
    national_id = _id_signal(get_field(rec_a, "national_id"), get_field(rec_b, "national_id"))
    account = _id_signal(get_field(rec_a, "account_number"), get_field(rec_b, "account_number"))
    account_overlap = 1.0 if account > 0.0 else (0.0 if account == 0.0 else -1.0)
    address = jaccard(
        token_bag(get_field(rec_a, "address")), token_bag(get_field(rec_b, "address"))
    )

    country_a, country_b = get_field(rec_a, "country"), get_field(rec_b, "country")
    country_match = (
        1.0
        if (country_a and country_b and country_a.strip().lower() == country_b.strip().lower())
        else 0.0
    )
    type_a, type_b = get_field(rec_a, "type"), get_field(rec_b, "type")
    type_match = (
        1.0 if (type_a and type_b and type_a.strip().lower() == type_b.strip().lower()) else 0.0
    )

    return {
        "token_sort_ratio": float(token_sort),
        "token_set_ratio": float(token_set),
        "partial_ratio": float(partial),
        "jaro_winkler": float(jaro_winkler),
        "tfidf_cosine": float(tfidf),
        "levenshtein_norm": float(lev_norm),
        "phonetic_soundex": float(soundex),
        "phonetic_metaphone": float(metaphone),
        "phonetic_double_metaphone": float(double_metaphone),
        "national_id": float(national_id),
        "address_similarity": float(address),
        "account_overlap": float(account_overlap),
        "country_match": float(country_match),
        "type_match": float(type_match),
    }


def blocking_key(record: RawRecord) -> str:
    """A coarse phonetic block key so the matcher compares only plausible pairs.

    Uses the Soundex of the first significant name token; records without a name fall
    into a shared ``"__noname__"`` block. Blocking is a recall/scale trade-off — it
    never decides a match, only which pairs are scored.
    """
    name = normalize_name(get_field(record, "name"))
    first = name.split()[0] if name.split() else ""
    if not first:
        return "__noname__"
    code = jellyfish.soundex(first)
    return code or first[:3]
