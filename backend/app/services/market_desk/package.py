"""Weekly rates package readiness: completeness, provenance, prior-publish deltas.

Supports the Research Desk wizard (Review inputs → Rates → Adjustments →
Confirm → Supervisor). Completeness is advisory for display and soft-gate
warnings; rates_qa_passed remains the hard submit/approve gate.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models import DeskDetermination, DeskObservation, DeskSourceCapture

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Must-have weekly rates series for the Ghana beachhead package (v1).
# Order is display order on the checklist.
REQUIRED_RATES_SERIES: tuple[str, ...] = (
    "GHS.MPR",
    "GHS.INTERBANK.ON",
    "GHS.TBILL.91.DISCOUNT",
    "GHS.TBILL.182.DISCOUNT",
    "GHS.TBILL.364.DISCOUNT",
    "GHS.GRR",
    # Prefer the dual-written alias; live FX tables also write GHS.FX.USDGHS.MID.
    "GHS.USDGHS.MID",
)

# Nice-to-have: shown on checklist but missing does not fail readiness soft flags.
# YIELD is dual-written from BoG INTEREST; REF is the weighted-median banner.
OPTIONAL_RATES_SERIES: tuple[str, ...] = (
    "GHS.TBILL.91.YIELD",
    "GHS.TBILL.91.INTEREST",
    "GHS.INTERBANK.WAVG",
    "GHS.FX.USDGHS.REF",
    "GHS.FX.USDGHS.MID",
)

# Soft staleness by series family (calendar days before COB).
# GRR is monthly (BoG table 21 + GSS); allow a full quarter lag before STALE.
_STALE_DAYS: dict[str, int] = {
    "GHS.MPR": 120,  # per-event
    "GHS.INTERBANK.ON": 5,
    "GHS.TBILL.": 14,
    "GHS.GRR": 100,  # monthly (BoG econ matrix / GSS)
    "GHS.USDGHS.": 5,
    "GHS.FX.": 5,
    "GHS.APR.": 75,
}


def _stale_limit(series_code: str) -> int:
    for prefix, days in sorted(_STALE_DAYS.items(), key=lambda kv: -len(kv[0])):
        if series_code.startswith(prefix) or series_code == prefix.rstrip("."):
            return days
    return 30


# Series that share a value under dual-write aliases (first found wins for display).
_SERIES_ALIASES: dict[str, tuple[str, ...]] = {
    "GHS.USDGHS.MID": ("GHS.USDGHS.MID", "GHS.FX.USDGHS.MID"),
    "GHS.FX.USDGHS.MID": ("GHS.FX.USDGHS.MID", "GHS.USDGHS.MID"),
    "GHS.TBILL.91.YIELD": ("GHS.TBILL.91.YIELD", "GHS.TBILL.91.INTEREST"),
}


def _latest_observation(
    db: Session, series_code: str, cob_date: date
) -> DeskObservation | None:
    codes = _SERIES_ALIASES.get(series_code, (series_code,))
    best: DeskObservation | None = None
    for code in codes:
        row = db.scalar(
            select(DeskObservation)
            .where(
                DeskObservation.series_code == code,
                DeskObservation.as_of_date <= cob_date,
                DeskObservation.superseded_by.is_(None),
            )
            .order_by(DeskObservation.as_of_date.desc())
            .limit(1)
        )
        if row is None:
            continue
        if best is None or row.as_of_date > best.as_of_date:
            best = row
    return best


def _provenance(db: Session, observation: DeskObservation | None) -> dict[str, Any]:
    if observation is None:
        return {"source": "missing", "capture_id": None, "entered_by": None}
    if observation.entered_by:
        return {
            "source": "manual",
            "capture_id": None,
            "entered_by": observation.entered_by,
            "quality_flags": list(observation.quality_flags or []),
            "attributes": dict(observation.attributes or {}),
        }
    if observation.capture_id is not None:
        capture = db.get(DeskSourceCapture, observation.capture_id)
        return {
            "source": "capture",
            "capture_id": str(observation.capture_id),
            "source_key": capture.source_key if capture else None,
            "source_url": capture.source_url if capture else None,
            "parser_version": capture.parser_version if capture else None,
            "capture_status": capture.status if capture else None,
            "quality_flags": list(observation.quality_flags or []),
            "attributes": dict(observation.attributes or {}),
            "entered_by": None,
        }
    return {
        "source": "unknown",
        "capture_id": None,
        "entered_by": None,
        "quality_flags": list(observation.quality_flags or []),
        "attributes": dict(observation.attributes or {}),
    }


def completeness_report(db: Session, *, cob_date: date) -> dict[str, Any]:
    """Expected weekly rates series vs current observations as-of COB."""
    items: list[dict[str, Any]] = []
    required_missing: list[str] = []
    required_stale: list[str] = []

    for series_code, required in (
        *[(c, True) for c in REQUIRED_RATES_SERIES],
        *[(c, False) for c in OPTIONAL_RATES_SERIES],
    ):
        obs = _latest_observation(db, series_code, cob_date)
        limit = _stale_limit(series_code)
        if obs is None:
            status = "missing"
            if required:
                required_missing.append(series_code)
            items.append(
                {
                    "series_code": series_code,
                    "required": required,
                    "status": status,
                    "as_of_date": None,
                    "value": None,
                    "unit": None,
                    "age_days": None,
                    "stale_limit_days": limit,
                    "provenance": _provenance(db, None),
                }
            )
            continue
        age = (cob_date - obs.as_of_date).days
        stale = age > limit
        status = "stale" if stale else "present"
        if required and stale:
            required_stale.append(series_code)
        items.append(
            {
                "series_code": series_code,
                "required": required,
                "status": status,
                "as_of_date": obs.as_of_date.isoformat(),
                "value": str(obs.value),
                "unit": obs.unit,
                "age_days": age,
                "stale_limit_days": limit,
                "provenance": _provenance(db, obs),
            }
        )

    required_present = sum(
        1 for i in items if i["required"] and i["status"] in ("present", "stale")
    )
    required_total = len(REQUIRED_RATES_SERIES)
    # Soft ready: all required series present (stale allowed with warning).
    ready = len(required_missing) == 0

    # Recent failed captures (desk morning view).
    fail_cutoff = cob_date - timedelta(days=14)
    failed_captures = list(
        db.scalars(
            select(DeskSourceCapture)
            .where(
                DeskSourceCapture.status == "failed",
                DeskSourceCapture.as_of_date >= fail_cutoff,
            )
            .order_by(DeskSourceCapture.captured_at.desc())
            .limit(20)
        )
    )

    return {
        "cob_date": cob_date.isoformat(),
        "items": items,
        "required_total": required_total,
        "required_present": required_present,
        "required_missing": required_missing,
        "required_stale": required_stale,
        "ready": ready,
        "failed_captures": [
            {
                "id": str(c.id),
                "source_key": c.source_key,
                "as_of_date": c.as_of_date.isoformat(),
                "captured_at": c.captured_at.isoformat() if c.captured_at else None,
                "parse_error": c.parse_error,
            }
            for c in failed_captures
        ],
    }


def _rate_value(entry: Any) -> str | None:
    if isinstance(entry, dict):
        value = entry.get("value")
        return str(value) if value is not None else None
    if entry is None:
        return None
    return str(entry)


def prior_published_package(
    db: Session, *, cob_date: date, exclude_id: Any = None
) -> DeskDetermination | None:
    """Most recent published determination with COB strictly before this one."""
    query = (
        select(DeskDetermination)
        .where(
            DeskDetermination.status == "published",
            DeskDetermination.cob_date < cob_date,
        )
        .order_by(
            DeskDetermination.cob_date.desc(), DeskDetermination.published_at.desc()
        )
        .limit(1)
    )
    if exclude_id is not None:
        query = query.where(DeskDetermination.id != exclude_id)
    return db.scalar(query)


def rates_wow_deltas(
    current: DeskDetermination, prior: DeskDetermination | None
) -> dict[str, Any]:
    """Week-over-week (package-over-package) deltas for published rate levels."""
    current_rates = (current.derived_values or {}).get("rates") or {}
    if not isinstance(current_rates, dict):
        current_rates = {}
    prior_rates: dict[str, Any] = {}
    if prior is not None:
        raw = (prior.derived_values or {}).get("rates") or {}
        if isinstance(raw, dict):
            prior_rates = raw

    codes = sorted(set(current_rates) | set(prior_rates))
    deltas: list[dict[str, Any]] = []
    for code in codes:
        cur_raw = _rate_value(current_rates.get(code))
        prior_raw = _rate_value(prior_rates.get(code))
        cur_f: float | None
        prior_f: float | None
        try:
            cur_f = float(cur_raw) if cur_raw is not None else None
        except (TypeError, ValueError):
            cur_f = None
        try:
            prior_f = float(prior_raw) if prior_raw is not None else None
        except (TypeError, ValueError):
            prior_f = None
        delta_pp = None
        if cur_f is not None and prior_f is not None:
            delta_pp = f"{(cur_f - prior_f):.6f}"
        deltas.append(
            {
                "series_code": code,
                "current": cur_raw,
                "prior": prior_raw,
                "delta_pp": delta_pp,
                "unit": (
                    current_rates.get(code, {}) or {}
                ).get("unit")
                if isinstance(current_rates.get(code), dict)
                else "pct",
            }
        )

    return {
        "prior_determination_id": str(prior.id) if prior else None,
        "prior_cob_date": prior.cob_date.isoformat() if prior else None,
        "prior_published_at": (
            prior.published_at.isoformat() if prior and prior.published_at else None
        ),
        "deltas": deltas,
    }


def input_provenance_rows(
    db: Session, determination: DeskDetermination
) -> list[dict[str, Any]]:
    """Per snapshot entry: value + capture/manual provenance for field review."""
    rows: list[dict[str, Any]] = []
    for entry in determination.input_snapshot or []:
        if not isinstance(entry, dict):
            continue
        series = str(entry.get("series_code") or "")
        as_of = entry.get("as_of_date")
        value = entry.get("value")
        obs = None
        if series and as_of:
            try:
                as_of_d = date.fromisoformat(str(as_of))
            except ValueError:
                as_of_d = determination.cob_date
            obs = db.scalar(
                select(DeskObservation)
                .where(
                    DeskObservation.series_code == series,
                    DeskObservation.as_of_date == as_of_d,
                    DeskObservation.superseded_by.is_(None),
                )
                .limit(1)
            )
        rows.append(
            {
                "series_code": series,
                "as_of_date": as_of,
                "value": value,
                "provenance": _provenance(db, obs),
            }
        )
    return rows


def build_package_view(db: Session, determination: DeskDetermination) -> dict[str, Any]:
    """Full package payload for the Research Desk wizard."""
    completeness = completeness_report(db, cob_date=determination.cob_date)
    prior = prior_published_package(
        db, cob_date=determination.cob_date, exclude_id=determination.id
    )
    return {
        "completeness": completeness,
        "week_over_week": rates_wow_deltas(determination, prior),
        "input_provenance": input_provenance_rows(db, determination),
        "rates_qa_passed": (determination.derived_values or {}).get("rates_qa_passed"),
        "curves_qa_passed": (determination.derived_values or {}).get("curves_qa_passed"),
        "package_digest": (determination.derived_values or {}).get("package_digest"),
    }
