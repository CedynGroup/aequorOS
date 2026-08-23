"""One vocabulary for the operational conditions this platform must not hide.

The backend already emits a great deal: audit events for calculation and
regulatory-run failures, `regulatory_submission_events` rows for filing
outcomes, `worker_heartbeats` and the `jobs` table for the worker,
`StorageHealth` for the object store, `OutcomeDetail` codes for blocked
calculations. What it lacked was a *vocabulary* — a stable set of condition
codes emitted as structured fields, so that "how often are we blocking
calculations" is a query rather than a grep through prose messages.

This module supplies that, and nothing else. Deliberately:

* **No new dependency.** The logger is the existing loguru instance configured
  in :mod:`app.core.logging`, already JSON-serialised to stdout with the
  request id patched onto every record. There is no metrics library in this
  project and this module does not introduce one — it emits fields a log
  pipeline can aggregate.
* **No parallel store.** Where an authoritative signal already exists (an audit
  event, a DB row, a readiness check), :data:`CONDITION_SOURCES` records where
  it lives instead of duplicating it. A second, divergent copy of "did this
  regulatory run fail" would be worse than none.
* **Never raises.** :func:`emit` swallows its own failures. These calls sit in
  authorization denials and exception handlers, and an observability bug must
  never convert a clean 403 into a 500.
* **Never carries a secret.** Call sites pass identifiers and reason codes.
  Passwords, tokens, credential material, full request bodies and raw vendor
  payloads must not be passed; :func:`emit` drops a small set of obviously
  dangerous field names as a backstop, but the real control is the call site.

Every record carries ``condition`` (a :class:`Condition` value) and
``severity``, alongside ``request_id`` from the logging patcher.
"""

from __future__ import annotations

import contextlib
from enum import StrEnum
from typing import Any, Final

from loguru import logger

# Field names never worth writing to a log, whatever a caller passes.
_FORBIDDEN_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "api_key",
        "authorization",
        "credential",
        "private_key",
        "database_url",
    }
)

_MAX_VALUE_CHARS: Final[int] = 512


class Condition(StrEnum):
    """The operational conditions that must be visible in production.

    Values are stable, dotted and greppable. Renaming one breaks every alert
    built on it, so treat these as a published interface.
    """

    CALCULATION_FAILED = "calculation.failed"
    CALCULATION_BLOCKED = "calculation.blocked"
    RECONCILIATION_FAILED = "reconciliation.failed"
    MARKET_DATA_STALE = "market_data.stale"
    DATA_MISSING = "data.missing"
    REGULATORY_RUN_FAILED = "regulatory_run.failed"
    PACKAGE_FAILED = "reporting.package_failed"
    SUBMISSION_FAILED = "reporting.submission_failed"
    WORKER_STARVED = "worker.starved"
    STORAGE_FAILED = "storage.failed"
    AUTH_ANOMALY = "auth.anomaly"
    SSRF_BLOCKED = "egress.blocked"
    AUTHORIZATION_DENIED = "authz.denied"
    CROSS_TENANT_ATTEMPT = "tenant.cross_access_attempt"


Severity = str  # "info" | "warning" | "error"

_LEVELS: Final[dict[str, str]] = {"info": "INFO", "warning": "WARNING", "error": "ERROR"}


#: Where each condition's *authoritative* record lives, for whoever has to
#: answer "is this the only place this is recorded?" during an incident. The
#: structured log emitted by this module is an operational signal; for the
#: conditions that name a table below, the table is the evidence.
CONDITION_SOURCES: Final[dict[Condition, str]] = {
    Condition.CALCULATION_FAILED: (
        "audit_events(event_type='calculation_run.failed') + calculation_runs.status"
    ),
    Condition.CALCULATION_BLOCKED: (
        "app.domain.authority.outcomes.OutcomeDetail.code, returned on the module payload"
    ),
    Condition.RECONCILIATION_FAILED: (
        "audit_events(event_type='reconciliation.balance_sheet_identity'), written by the "
        "DERIVATION plane (app/services/reconciliation.py::record_check, which commits its "
        "own row on a block). The FILING plane deliberately writes no audit row — it runs "
        "inside caller transactions holding pending writes — so for a refused package, "
        "approval, certification, transmission or official run this log line (emitted from "
        "app/services/filing_reconciliation.py, carrying purpose= and plane=) plus the 409 "
        "itself are the record"
    ),
    Condition.MARKET_DATA_STALE: (
        "SourceAttribution.stale on every market-data view; desk determination flags"
    ),
    Condition.DATA_MISSING: "OutcomeState.MISSING_REQUIRED_INPUT; ingestion_batches counters",
    Condition.REGULATORY_RUN_FAILED: (
        "audit_events(event_type='regulatory_run.failed') + regulatory_runs.status"
    ),
    Condition.PACKAGE_FAILED: (
        "this log line only, from the single package-mint site "
        "(app/services/regulatory_reporting/generation.py::generate_package). A refusal "
        "writes no audit event and no row BY DESIGN — the package never comes into "
        "existence, so there is nothing to attach one to; the log line is the record"
    ),
    Condition.SUBMISSION_FAILED: (
        "regulatory_submission_events + audit_events('regulatory_package.submission_*'); "
        "a transport/network failure on the channel is NOT recorded as a submission event"
    ),
    Condition.WORKER_STARVED: "worker_heartbeats + jobs; /operator/v1/worker-health; /health/ready",
    Condition.STORAGE_FAILED: "storage hash-chained access log; /health/ready checks.storage",
    Condition.AUTH_ANOMALY: (
        "failed_login_attempts / locked_until on the principal's table — 'users' for a "
        "bank user, 'operator_users' for a staff account (the emission carries "
        "plane='operator') — plus this log line on lockout (app/services/auth_throttle.py)"
    ),
    Condition.SSRF_BLOCKED: (
        "this log line only, emitted from OutboundTargetBlocked.__init__ so every block "
        "is covered (app/core/outbound.py)"
    ),
    Condition.AUTHORIZATION_DENIED: "this log line only, from the gates in app/api/deps.py",
    Condition.CROSS_TENANT_ATTEMPT: "this log line only, from app/api/deps.py",
}


def _scrub(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop dangerous field names and bound the size of every value."""
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if key.lower() in _FORBIDDEN_FIELDS or value is None:
            continue
        if isinstance(value, str) and len(value) > _MAX_VALUE_CHARS:
            clean[key] = value[:_MAX_VALUE_CHARS] + "...[truncated]"
        else:
            clean[key] = value
    return clean


def emit(
    condition: Condition, summary: str, *, severity: Severity = "warning", **fields: Any
) -> None:
    """Record an operational condition as a structured log event.

    ``summary`` is a short human sentence; everything queryable belongs in
    ``fields``. Never raises: an observability failure must not change the
    behaviour of the code path that reported the condition.
    """
    # Suppressed on purpose: these calls sit inside authorization denials and
    # exception handlers, where a logging failure must not become a 500.
    with contextlib.suppress(Exception):
        logger.bind(condition=condition.value, severity=severity, **_scrub(fields)).log(
            _LEVELS.get(severity, "WARNING"), summary
        )


def authorization_denied(*, reason: str, **fields: Any) -> None:
    """A request was refused by a role, module or impersonation gate.

    Previously silent: every denial in ``app/api/deps.py`` raised a bare 403
    with no log, no audit row and no counter, so a misconfigured role or a
    probing client left no trace at all.
    """
    emit(
        Condition.AUTHORIZATION_DENIED,
        "Authorization denied",
        severity="warning",
        reason=reason,
        **fields,
    )


def cross_tenant_attempt(*, reason: str, **fields: Any) -> None:
    """A principal referenced a tenant it is not scoped to.

    RLS makes this condition structurally quiet — a cross-tenant read matches
    zero rows and surfaces as an ordinary 404 — so it has to be reported
    explicitly at the boundary that detects it, or it is invisible.
    """
    emit(
        Condition.CROSS_TENANT_ATTEMPT,
        "Cross-tenant access attempt",
        severity="error",
        reason=reason,
        **fields,
    )


def auth_anomaly(*, reason: str, **fields: Any) -> None:
    """A repeated-failure lockout, a token-reuse detection, or similar."""
    emit(
        Condition.AUTH_ANOMALY,
        "Authentication anomaly",
        severity="warning",
        reason=reason,
        **fields,
    )


def package_failed(*, reason: str, severity: Severity = "warning", **fields: Any) -> None:
    """A regulatory package could not be generated.

    Previously silent: the mint site refused with a bare ``HTTPException`` — no
    log, no audit event, no row — so a bank hitting a wall on the last day of a
    filing window left no trace an operator could find. There is deliberately no
    persistent record: the refusal means no package row exists to attach one to,
    which is exactly why this condition needs a log line of its own.

    ``reason`` is the refusal's own error code where it has one
    (``template_pending``, ``return_not_eligible``, ``no_stress_scenarios`` …),
    otherwise a code derived from the refusal. Unexpected failures pass
    ``severity="error"``.
    """
    emit(
        Condition.PACKAGE_FAILED,
        "Regulatory package generation failed",
        severity=severity,
        reason=reason,
        **fields,
    )


def ssrf_blocked(*, reason: str, field: str, **fields: Any) -> None:
    """The egress guard refused a tenant-supplied outbound target."""
    emit(
        Condition.SSRF_BLOCKED,
        "Outbound target blocked by the egress guard",
        severity="warning",
        reason=reason,
        field=field,
        **fields,
    )
