"""Bank-facing error classification for the database-direct adapter.

Mirrors ``temenos_t24/errors.py`` and ``market_data/errors.py``: every failure
against a bank's core database — a rejected login, an unreachable replica, a
malformed row, an absent driver library — is classified into a
:class:`DbDirectErrorCode` with a pre-authored, bank-safe message template,
recommended actions, and an escalation severity.

A direct connection to a bank's core database is one of the most sensitive
integrations AequorOS operates: a leaked driver error can expose schema names,
table structures, host names, connection strings, and even fragments of SQL.
Raw DBAPI exception text, ODBC/JDBC SQLSTATEs, Oracle ``ORA-`` codes, host:port
coordinates, and connection strings NEVER appear on a bank-facing surface —
they travel only in :attr:`DatabaseDirectError.internal_detail`, which is logged
for AequorOS engineering. ``str(DatabaseDirectError)`` deliberately renders only
the bank-facing message so an accidentally surfaced exception cannot leak core
internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

type Severity = Literal["informational", "warning", "urgent"]


class DbDirectErrorCode(Enum):
    """The classified failure modes of a direct core-database integration."""

    CREDENTIAL_INVALID = "CREDENTIAL_INVALID"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    CREDENTIAL_REVOKED = "CREDENTIAL_REVOKED"
    INSUFFICIENT_PRIVILEGE = "INSUFFICIENT_PRIVILEGE"
    WRITE_ATTEMPT_BLOCKED = "WRITE_ATTEMPT_BLOCKED"
    TABLE_NOT_FOUND = "TABLE_NOT_FOUND"
    CORE_UNAVAILABLE = "CORE_UNAVAILABLE"
    REPLICA_UNAVAILABLE = "REPLICA_UNAVAILABLE"
    TLS_REQUIRED = "TLS_REQUIRED"
    MUTUAL_TLS_REQUIRED = "MUTUAL_TLS_REQUIRED"
    NETWORK_ERROR = "NETWORK_ERROR"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    RESPONSE_MALFORMED = "RESPONSE_MALFORMED"
    NO_DATA_RETURNED = "NO_DATA_RETURNED"
    DRIVER_UNAVAILABLE = "DRIVER_UNAVAILABLE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    WAREHOUSE_UNAVAILABLE = "WAREHOUSE_UNAVAILABLE"


@dataclass(frozen=True)
class BankFacingError:
    """A fully rendered, bank-safe error: classified code, actionable message,
    recommended actions, and escalation severity."""

    code: DbDirectErrorCode
    message: str
    actions: tuple[str, ...]
    severity: Severity


@dataclass(frozen=True)
class MessageTemplate:
    """Pre-authored bank-facing template — never a runtime-formatted DB error.

    Placeholders are limited to business-level parameters such as
    ``{database}`` (a friendly source-system label), ``{timestamp}``, and
    ``{table}`` (a canonical entity name, never a raw physical table name).
    """

    message: str
    actions: tuple[str, ...]
    severity: Severity


_DB = "{database}"

MESSAGE_TEMPLATES: dict[DbDirectErrorCode, MessageTemplate] = {
    DbDirectErrorCode.CREDENTIAL_INVALID: MessageTemplate(
        message=(
            f"AequorOS could not sign in to your {_DB} core banking database. The connection "
            "credentials were rejected — this usually means the read-only service account's "
            "password was rotated or its grants changed at your end. Please verify the AequorOS "
            "read-only account and update the credentials. AequorOS will use the last successful "
            "data pull (from {timestamp}) until this is resolved."
        ),
        actions=(
            "Update credentials",
            "View last successful pull",
            "Contact your core banking team",
        ),
        severity="urgent",
    ),
    DbDirectErrorCode.CREDENTIAL_EXPIRED: MessageTemplate(
        message=(
            f"The credentials for your {_DB} core database connection have expired. Please issue "
            "new credentials for the AequorOS read-only account and enter them in AequorOS. The "
            "last successful data pull (from {timestamp}) remains in use until new credentials "
            "are provided."
        ),
        actions=(
            "Rotate credentials",
            "View last successful pull",
            "Contact your core banking team",
        ),
        severity="urgent",
    ),
    DbDirectErrorCode.CREDENTIAL_REVOKED: MessageTemplate(
        message=(
            f"Access for the AequorOS read-only account on your {_DB} core database has been "
            "revoked. Scheduled data pulls are paused. If this was unintentional, please "
            "re-enable the account and enter fresh credentials in AequorOS."
        ),
        actions=("Re-authorize and update credentials", "Contact your core banking team"),
        severity="urgent",
    ),
    DbDirectErrorCode.INSUFFICIENT_PRIVILEGE: MessageTemplate(
        message=(
            f"The AequorOS read-only account is not permitted to read {{table}} from your {_DB} "
            "core database. Options: (1) grant SELECT on the relevant view to the AequorOS "
            "account, (2) remove {table} from the AequorOS data scope, (3) provide {table} via "
            "manual upload."
        ),
        actions=("Grant read access", "Remove from scope", "Use manual upload"),
        severity="warning",
    ),
    DbDirectErrorCode.WRITE_ATTEMPT_BLOCKED: MessageTemplate(
        message=(
            f"AequorOS halted a data pull from your {_DB} core database as a safety measure: the "
            "operation was not a read. AequorOS never writes to your core systems, so this pull "
            "was stopped and AequorOS support has been notified. Your existing data is unaffected."
        ),
        actions=("Contact AequorOS support", "View last successful pull"),
        severity="urgent",
    ),
    DbDirectErrorCode.TABLE_NOT_FOUND: MessageTemplate(
        message=(
            f"AequorOS could not find the table or view needed for {{table}} in your {_DB} core "
            "database. This can happen when a core banking release renames or relocates an "
            "object. AequorOS support has been notified; the last successful pull for {table} "
            "remains available."
        ),
        actions=(
            "View last successful pull",
            "Contact AequorOS support",
            "Use manual upload for this data",
        ),
        severity="warning",
    ),
    DbDirectErrorCode.CORE_UNAVAILABLE: MessageTemplate(
        message=(
            f"Your {_DB} core banking database is currently unreachable. AequorOS will retry "
            "automatically and continue using the last successful data pull (from {timestamp}) "
            "in the meantime. No action is needed unless the outage persists."
        ),
        actions=("View last successful pull", "Contact your core banking team if this persists"),
        severity="informational",
    ),
    DbDirectErrorCode.REPLICA_UNAVAILABLE: MessageTemplate(
        message=(
            f"The read-replica AequorOS uses for your {_DB} core database could not be reached, "
            "so a fresh pull could not be completed right now. AequorOS will retry and continue "
            "using the last successful pull (from {timestamp}) until the replica is available."
        ),
        actions=("View last successful pull", "Contact your core banking team"),
        severity="warning",
    ),
    DbDirectErrorCode.TLS_REQUIRED: MessageTemplate(
        message=(
            f"AequorOS requires an encrypted (TLS) connection to your {_DB} core database and "
            "could not establish one, so no pull was attempted. Please enable TLS on the "
            "read-only endpoint provided to AequorOS. Your existing data is unaffected."
        ),
        actions=("Enable TLS on the endpoint", "Contact your core banking team"),
        severity="urgent",
    ),
    DbDirectErrorCode.MUTUAL_TLS_REQUIRED: MessageTemplate(
        message=(
            f"Your {_DB} core database requires a client wallet for mutual TLS (mTLS) — the "
            "default for Oracle Autonomous Database. AequorOS reached the database over TLS, but "
            "the session was refused because no client wallet was presented. Upload the "
            "database's client wallet (the Client Credentials ZIP from its console) and its "
            "wallet password on this connection, then retry. Your existing data is unaffected."
        ),
        actions=("Upload the client wallet", "Contact your core banking team"),
        severity="urgent",
    ),
    DbDirectErrorCode.NETWORK_ERROR: MessageTemplate(
        message=(
            f"AequorOS could not reach your {_DB} core database due to a network problem. "
            "AequorOS will retry automatically and use the last successful data pull (from "
            "{timestamp}) in the meantime."
        ),
        actions=("View last successful pull", "Contact AequorOS support if this persists"),
        severity="informational",
    ),
    DbDirectErrorCode.QUERY_TIMEOUT: MessageTemplate(
        message=(
            f"A data pull from your {_DB} core database took longer than the allowed time and "
            "was stopped to avoid loading your systems. AequorOS will retry, and may narrow the "
            "pull window. The last successful pull (from {timestamp}) remains available."
        ),
        actions=("View last successful pull", "Review pull schedule", "Contact AequorOS support"),
        severity="warning",
    ),
    DbDirectErrorCode.RESPONSE_MALFORMED: MessageTemplate(
        message=(
            f"AequorOS received unexpected data from your {_DB} core database while reading "
            "{table} and could not process it safely. To avoid ingesting incorrect data, this "
            "pull was stopped. AequorOS support has been notified; the last successful pull for "
            "{table} remains in use."
        ),
        actions=("View last successful pull", "Contact AequorOS support"),
        severity="warning",
    ),
    DbDirectErrorCode.NO_DATA_RETURNED: MessageTemplate(
        message=(
            f"Your {_DB} core database returned no records for {{table}} for {{timestamp}}. If "
            "you expected data for this period, please check that {table} is populated for that "
            "date. The last successful pull for {table} remains in use."
        ),
        actions=(
            "View last successful pull",
            "Verify the source date in core",
            "Contact AequorOS support",
        ),
        severity="warning",
    ),
    DbDirectErrorCode.DRIVER_UNAVAILABLE: MessageTemplate(
        message=(
            f"AequorOS is not yet fully provisioned to connect to your {_DB} core database. "
            "AequorOS support has been notified and will complete the setup. Your existing data "
            "is unaffected."
        ),
        actions=("Contact AequorOS support", "View last successful pull"),
        severity="warning",
    ),
    DbDirectErrorCode.CONFIGURATION_ERROR: MessageTemplate(
        message=(
            f"The AequorOS connection to your {_DB} core database is not fully configured for "
            "{table}. AequorOS support has been notified and will complete the setup. Your "
            "existing data is unaffected."
        ),
        actions=("Contact AequorOS support", "View last successful pull"),
        severity="warning",
    ),
    DbDirectErrorCode.WAREHOUSE_UNAVAILABLE: MessageTemplate(
        message=(
            f"AequorOS connected to your {_DB} data warehouse, but the compute warehouse it was "
            "asked to run on could not be started (it may be suspended, resized, or the service "
            "role may lack permission to resume it). No pull was completed. Please confirm the "
            "warehouse name and that the AequorOS service role can use it. Your existing data is "
            "unaffected."
        ),
        actions=("Confirm the warehouse and role", "Contact your data platform team"),
        severity="warning",
    ),
}

_DEFAULT_DATABASE_LABEL = "your core banking system"
_NO_CACHE_TIMESTAMP = "not available"
_UNKNOWN_TABLE = "the requested data"


def render_bank_facing(code: DbDirectErrorCode, **params: str) -> BankFacingError:
    """Render the pre-authored template for ``code`` into a bank-facing error.

    ``params`` supplies the business-level placeholders the template uses
    (``database``, ``timestamp``, ``table``). Missing placeholders are filled
    with safe defaults so a partially-specified error can never ship with raw
    ``{placeholders}`` left in the text, and never accidentally interpolates a
    physical table name where a canonical label is expected.
    """
    template = MESSAGE_TEMPLATES[code]
    resolved = {
        "database": params.get("database", _DEFAULT_DATABASE_LABEL),
        "timestamp": params.get("timestamp", _NO_CACHE_TIMESTAMP),
        "table": params.get("table", _UNKNOWN_TABLE),
    }
    return BankFacingError(
        code=code,
        message=template.message.format(**resolved),
        actions=template.actions,
        severity=template.severity,
    )


class DatabaseDirectError(Exception):
    """A classified database-direct failure.

    ``bank_facing`` is the only part that may ever reach a bank-facing surface.
    ``internal_detail`` carries the raw DBAPI/ODBC/JDBC error text, SQLSTATE,
    ``ORA-`` code, host coordinates, or diagnostic context for AequorOS
    engineering logs and is NEVER shown to banks — ``str(error)`` deliberately
    renders only the bank-facing message so an accidentally surfaced exception
    cannot leak core internals.
    """

    def __init__(self, bank_facing: BankFacingError, internal_detail: str) -> None:
        super().__init__(bank_facing.message)
        self.bank_facing = bank_facing
        self.internal_detail = internal_detail

    @property
    def code(self) -> DbDirectErrorCode:
        return self.bank_facing.code


def classify_dbapi_error(
    exc: BaseException,
    *,
    database: str,
    table: str | None = None,
    timestamp: str | None = None,
) -> DatabaseDirectError:
    """Best-effort classification of a raw DBAPI exception into a bank-safe error.

    Classification is heuristic and driver-agnostic: it inspects the SQLSTATE
    (when the DBAPI exposes one) and the lowercased message text for well-known
    signals shared across Oracle, SQL Server, and standard ODBC/JDBC drivers.
    Anything unrecognized falls back to ``CORE_UNAVAILABLE`` (informational,
    retry-and-use-cache) rather than surfacing an unclassified raw error. The
    raw text is preserved only in :attr:`DatabaseDirectError.internal_detail`.
    """
    text = str(exc).lower()
    sqlstate = _sqlstate_of(exc)
    params = {"database": database}
    if table is not None:
        params["table"] = table
    if timestamp is not None:
        params["timestamp"] = timestamp

    code = _classify_by_sqlstate(sqlstate) or _classify_by_text(text)
    return DatabaseDirectError(
        render_bank_facing(code, **params),
        internal_detail=f"{type(exc).__name__}[sqlstate={sqlstate or '?'}]: {exc}",
    )


def _sqlstate_of(exc: BaseException) -> str | None:
    """Extract an ANSI SQLSTATE from a DBAPI exception, if it exposes one.

    ODBC/JDBC drivers commonly carry ``exc.args[0]`` as the 5-char SQLSTATE;
    some expose a ``sqlstate`` attribute. Oracle's ``oracledb`` does not use
    SQLSTATE, so this returns ``None`` there and text classification applies.
    """
    state = getattr(exc, "sqlstate", None)
    if isinstance(state, str) and len(state) == 5:  # noqa: PLR2004 - ANSI SQLSTATE width
        return state
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], str) and len(args[0]) == 5:  # noqa: PLR2004
        candidate = args[0]
        if candidate[:2].isalnum():
            return candidate
    return None


# ANSI/ODBC SQLSTATE class prefixes shared across compliant drivers.
_SQLSTATE_CLASSES: dict[str, DbDirectErrorCode] = {
    "28": DbDirectErrorCode.CREDENTIAL_INVALID,  # invalid authorization specification
    "42": DbDirectErrorCode.INSUFFICIENT_PRIVILEGE,  # syntax error or access rule violation
    "08": DbDirectErrorCode.CORE_UNAVAILABLE,  # connection exception
    "HYT": DbDirectErrorCode.QUERY_TIMEOUT,  # timeout expired (HYT00/HYT01)
    "57": DbDirectErrorCode.CORE_UNAVAILABLE,  # operator intervention / db unavailable
}


def _classify_by_sqlstate(sqlstate: str | None) -> DbDirectErrorCode | None:
    if sqlstate is None:
        return None
    for prefix, code in _SQLSTATE_CLASSES.items():
        if sqlstate.startswith(prefix):
            # 42S02 (base table not found) is more specific than access rule.
            if sqlstate in ("42S02", "42P01"):
                return DbDirectErrorCode.TABLE_NOT_FOUND
            return code
    return None


def _classify_by_text(text: str) -> DbDirectErrorCode:  # noqa: PLR0911 - one branch per signal class
    if any(s in text for s in ("password", "login failed", "invalid credential", "authentication")):
        return DbDirectErrorCode.CREDENTIAL_INVALID
    if any(s in text for s in ("permission", "privilege", "not authorized", "access denied")):
        return DbDirectErrorCode.INSUFFICIENT_PRIVILEGE
    # Snowflake compute-warehouse problems, checked before the generic
    # "does not exist" table rule so a suspended/missing warehouse is not
    # mistaken for a missing table.
    if "warehouse" in text and any(
        s in text
        for s in ("no active warehouse", "cannot be resumed", "suspended", "does not exist")
    ):
        return DbDirectErrorCode.WAREHOUSE_UNAVAILABLE
    if any(s in text for s in ("does not exist", "not found", "unknown table", "invalid object")):
        return DbDirectErrorCode.TABLE_NOT_FOUND
    if "timeout" in text or "timed out" in text:
        return DbDirectErrorCode.QUERY_TIMEOUT
    mtls_signals = (
        "wallet",
        "mutual tls",
        "mtls",
        "client certificate",
        "certificate required",
        "peer did not return a certificate",
        "handshake failure",
        "alert certificate",
        "sslv3 alert",
    )
    if any(s in text for s in mtls_signals):
        return DbDirectErrorCode.MUTUAL_TLS_REQUIRED
    if any(s in text for s in ("ssl", "tls", "encryption")):
        return DbDirectErrorCode.TLS_REQUIRED
    network_signals = ("could not connect", "unreachable", "refused", "no route", "network")
    if any(s in text for s in network_signals):
        return DbDirectErrorCode.NETWORK_ERROR
    return DbDirectErrorCode.CORE_UNAVAILABLE
