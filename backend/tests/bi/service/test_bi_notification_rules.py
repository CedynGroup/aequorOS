"""The rules the notification ROUTES hold that a request cannot reach.

Three kinds of thing live here, all of them about the surface rather than about
the engines (which have their own suites in this directory):

* **the copy is total.** Every value in every vocabulary the database will store
  has a sentence, so no raw enum and no wire code can reach a reader. Proven by
  iterating the model's own tuples and the service's own reason constants, so a
  new value fails here rather than appearing on screen as ``data_scope_unsupported``.
* **the defence-in-depth branches.** The threshold pairing and the recipient cap
  are each stated three times — a request validator, a route check, a database
  CHECK — and the route's copy is the one that answers with a named error rather
  than a 500. The request validator makes those route branches unreachable
  through HTTP, so they are exercised directly here; without this they would be
  untested code that a future reader would delete as dead.
* **the parity pins.** The surface reads the institution's time zone and the
  recipient cap from its own code; both have an authority elsewhere, and a
  surface that displayed one zone while the delivery scan used another, or a cap
  the interface enforced at a different number from the worker's, would be worse
  than not stating them at all.

This module's other-suite cases live in ``test_bi_notification_rules_integration.py``.
"""

from __future__ import annotations

__all__ = ["_detail"]
from decimal import Decimal
from typing import cast

import pytest
from fastapi import HTTPException

from app.bi.api import manage_bi_notifications as feature
from app.bi.domain.catalogue import MeasureDef, catalogue
from app.bi.models.notifications import (
    ALERT_EVENT_STATES,
    ALERT_NOT_EVALUATED_REASONS,
    DELIVERY_STATUSES,
)
from app.bi.schemas.notifications import (
    BI_SUBSCRIPTION_MAX_RECIPIENTS,
    BiAlertUpsert,
)
from app.bi.service import subscriptions

LOANS = "loans.balance_rc"


def test_every_alert_state_has_a_sentence() -> None:
    """A state the database can store and the surface cannot name is a raw enum."""

    assert set(feature._STATE_COPY) == set(ALERT_EVENT_STATES)
    for sentence in feature._STATE_COPY.values():
        assert sentence and sentence[0].isupper()


def test_every_reason_an_evaluation_can_record_has_a_sentence() -> None:
    """Including the ones only Phase 4's data scopes will produce."""

    assert set(feature._EVENT_REASON_COPY) == set(ALERT_NOT_EVALUATED_REASONS)
    for reason in ALERT_NOT_EVALUATED_REASONS:
        assert reason not in feature._EVENT_REASON_COPY[reason], (
            f"the sentence for {reason} repeats the wire code"
        )


def test_every_delivery_status_has_a_sentence_and_a_refusal_is_not_an_error() -> None:
    """``denied`` must read as an outcome: a recipient was correctly sent nothing."""

    assert set(feature._DELIVERY_STATUS_COPY) == set(DELIVERY_STATUSES)
    denied = feature._DELIVERY_STATUS_COPY["denied"]
    assert "Not sent" in denied
    assert "access" in denied
    assert "error" not in denied.lower()
    assert "fail" not in denied.lower()
    # And the one that IS something to act on says so instead.
    assert feature._DELIVERY_STATUS_COPY["failed"] == "Could not be sent"


def test_every_reason_a_delivery_can_record_has_a_sentence() -> None:
    """Read from the delivery service's own constants, not from a copied list.

    ``subscriptions`` names each reason as a ``REASON_*`` module constant, so the
    set is derived rather than restated: a reason added there and not here would
    otherwise reach a reader as ``unsupported_artifact_format``.
    """

    declared = {
        value
        for name, value in vars(subscriptions).items()
        if name.startswith("REASON_") and isinstance(value, str)
    }
    assert declared, "the delivery service declares no reasons — this proves nothing"
    missing = sorted(declared - set(feature._DELIVERY_REASON_COPY))
    assert missing == [], f"delivery reasons with no sentence: {missing}"


def test_no_sentence_on_this_surface_names_a_currency_or_a_regulator() -> None:
    """Jurisdiction is data. A figure's unit comes from the catalogue's value type.

    The alert copy states figures, so this is the surface most likely to grow a
    ``GHS`` or a ``BoG``; the engines' own suite scans the calculation modules for
    the same thing.
    """

    forbidden = ("GHS", "cedi", "Cedi", "BoG", "Bank of Ghana", "Ghana", "en-GH")
    sentences = [
        *feature._STATE_COPY.values(),
        *feature._EVENT_REASON_COPY.values(),
        *feature._DELIVERY_STATUS_COPY.values(),
        *feature._DELIVERY_REASON_COPY.values(),
        feature.NEVER_EVALUATED,
        feature.VERDICT_WITHHELD,
        feature.ALERT_INACTIVE,
        feature.DELIVERY_NOTE_ATTACHED,
        feature.DELIVERY_NOTE_LINK,
        feature._EVENT_REASON_FALLBACK,
    ]
    for sentence in sentences:
        for token in forbidden:
            assert token not in sentence, f"{token!r} in {sentence!r}"


def _detail(exc: HTTPException) -> dict[str, object]:
    """The named refusal body, narrowed once so each assertion reads plainly."""

    detail = exc.detail
    assert isinstance(detail, dict)
    return cast("dict[str, object]", detail)


def _measure(measure_id: str) -> MeasureDef:
    member = catalogue().member(measure_id)
    assert isinstance(member, MeasureDef)
    return member


def _upsert(**overrides: object) -> BiAlertUpsert:
    """A request body built WITHOUT validation, to reach the route's own check.

    ``model_construct`` is the point: the request model refuses every mismatch
    below, so a validated body could never carry one to the route.
    """

    body: dict[str, object] = {
        "name": "Loan book above plan",
        "measure_id": LOANS,
        "direction": "above",
        "threshold_basis": "stated",
        "threshold": Decimal("1000000"),
        "notify_user_ids": [],
        "notify_emails": [],
        "is_active": True,
        "filters": [],
        "reason": "Exercise the route's own pairing check.",
    }
    body.update(overrides)
    return BiAlertUpsert.model_construct(None, **body)


def test_the_route_refuses_a_threshold_pairing_with_a_named_error() -> None:
    """The branch the request model makes unreachable over HTTP, exercised directly.

    ``model_construct`` skips validation on purpose: this is the statement of the
    rule that would answer if the request model's validator were ever relaxed,
    and without it the database's CHECK would answer instead — as a 500 rather
    than as something a surface can act on.
    """

    measure = _measure(LOANS)
    for label, payload in (
        ("a stated basis with no number", _upsert(threshold=None)),
        (
            "a governed basis carrying a number",
            _upsert(threshold_basis="governed_limit", threshold=Decimal("5")),
        ),
    ):
        with pytest.raises(HTTPException) as caught:
            feature._require_threshold_pairing(payload, measure)
        assert caught.value.status_code == 422, label
        assert _detail(caught.value)["error_code"] == "bi_alert_threshold_mismatch", label


def test_the_route_refuses_a_governed_basis_on_a_figure_with_no_governed_source() -> None:
    """Reachable over HTTP, and the only pairing rule the request model cannot make."""

    with pytest.raises(HTTPException) as caught:
        feature._require_threshold_pairing(
            _upsert(threshold_basis="governed_limit", threshold=None), _measure(LOANS)
        )
    assert _detail(caught.value)["error_code"] == "bi_alert_governed_limit_unavailable"


def test_a_stated_pairing_and_a_governed_one_are_both_accepted() -> None:
    """The guard must admit, or the rule above is only ever tested one way."""

    feature._require_threshold_pairing(_upsert(), _measure(LOANS))
    feature._require_threshold_pairing(
        _upsert(threshold_basis="governed_limit", threshold=None),
        _measure("engine.car_pct.crd.live"),
    )


def test_the_schemas_recipient_cap_is_the_delivery_services_own() -> None:
    """One number. The interface, the request model and the worker all read it.

    They are separate constants by design — the schema module depends on no
    service — so this is the pin that keeps them one number, and it is why the
    route resolves the cap from ``subscriptions.MAX_RECIPIENTS`` rather than from
    the schema.
    """

    assert BI_SUBSCRIPTION_MAX_RECIPIENTS == subscriptions.MAX_RECIPIENTS
