"""Config-driven connection and extraction models for the database-direct adapter.

There is NO per-bank code in this adapter: a bank on Oracle, SQL Server, a
JDBC-only core, or a plain ODBC DSN is onboarded entirely through these typed
models plus a :class:`~app.domain.ingestion.contracts.MappingConfig`. The
connection model is stored per bank (secrets excluded — they live in the vault);
the extraction spec names which physical tables/views map to which canonical
entity, and how each is pulled incrementally.

Two layers, deliberately separate:

- :class:`ConnectionConfig` — *where* and *how* to connect (backend, endpoints,
  TLS, read-replica preference). Consumed by the live drivers during a pull.
- :class:`ExtractionSpec` — *what* to read (table -> canonical entity, columns,
  incremental cursor, equality filters). Consumed by the query builder.

Both are parsed from ``AdapterConfig.options`` so the adapter's offline
``extract`` and the live ``pull`` share one validated shape.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.ingestion.constants import ReferenceDatasetKind
from app.domain.ingestion.contracts import ENTITY_TYPES, ExtractionMode, RecordKind

# The four backends this adapter's driver abstraction supports. ``jdbc`` and
# ``odbc`` are generic bridges (any JDBC/ODBC-reachable core), while ``oracle``
# and ``sqlserver`` are native drivers with backend-specific dialect handling.
Backend = Literal["oracle", "sqlserver", "jdbc", "odbc"]
BACKENDS: tuple[Backend, ...] = ("oracle", "sqlserver", "jdbc", "odbc")


class TlsConfig(BaseModel):
    """Transport-security policy for a core-database connection.

    TLS is *required by default*: a direct pipe into a bank's core carries the
    whole book in the clear otherwise. ``enabled=False`` is only honored for a
    driver/endpoint that terminates TLS out of band (e.g. a stunnel/mTLS
    sidecar), and drivers still record that the encryption guarantee was waived.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    verify_server_certificate: bool = True
    ca_cert_path: str | None = None
    # Some drivers accept a distinguished name / SNI host to match; optional.
    server_dn_match: str | None = None


class JdbcConfig(BaseModel):
    """JDBC-bridge specifics (``jaydebeapi`` + ``JPype1`` over a vendor JAR)."""

    model_config = ConfigDict(frozen=True)

    # Fully-qualified JDBC driver class, e.g.
    # "com.microsoft.sqlserver.jdbc.SQLServerDriver" or
    # "oracle.jdbc.OracleDriver". Never guessed — supplied at onboarding.
    driver_class: str
    # jdbc: URL template. ``{host}``/``{port}``/``{database}`` are substituted
    # from ConnectionConfig; anything else is passed through verbatim.
    url_template: str
    # Absolute path(s) to the vendor JDBC JAR(s) on the AequorOS worker host.
    jar_paths: tuple[str, ...] = ()
    # Extra JDBC connection properties (e.g. {"encrypt": "true"}), passed to the
    # driver as-is. TLS-related keys are also set from TlsConfig by the driver.
    properties: dict[str, str] = Field(default_factory=dict)


class OdbcConfig(BaseModel):
    """Generic-ODBC specifics (``pyodbc`` over a system ODBC driver)."""

    model_config = ConfigDict(frozen=True)

    # The registered ODBC driver name, e.g. "ODBC Driver 18 for SQL Server" or
    # "Oracle 21 ODBC driver". Supplied at onboarding; never fabricated.
    driver_name: str
    # Optional pre-registered DSN; when set, host/port/database are ignored and
    # the DSN supplies them. Extra keyword pairs go into ``extra_keywords``.
    dsn: str | None = None
    extra_keywords: dict[str, str] = Field(default_factory=dict)


class ConnectionConfig(BaseModel):
    """Where and how to reach a bank's core database (secrets excluded).

    A ``primary`` endpoint plus zero or more ``read_replicas``; the pull ALWAYS
    prefers a replica and treats the primary as read-only-of-last-resort, and
    NEVER issues a write. ``credential_ref`` is a logical vault locator, not a
    secret; the driver resolves it through the credential vault per pull cycle.
    """

    model_config = ConfigDict(frozen=True)

    backend: Backend
    host: str = ""
    port: int | None = None
    # Oracle service name / SQL Server & generic database catalog name.
    database: str = ""
    service_name: str | None = None
    # Schemas to introspect / qualify tables with. Empty = driver default.
    schemas: tuple[str, ...] = ()
    # Replica endpoints as "host:port" (or bare host). Preferred over primary.
    read_replicas: tuple[str, ...] = ()
    # A friendly, bank-safe label for error messages ("your T24 database").
    display_label: str = "your core banking system"
    # Logical vault path; resolved to real credentials only during a pull.
    credential_ref: str = ""
    tls: TlsConfig = Field(default_factory=TlsConfig)
    # Per-statement server-side timeout (seconds); a guard against runaway pulls.
    query_timeout_seconds: int = 300
    jdbc: JdbcConfig | None = None
    odbc: OdbcConfig | None = None

    @model_validator(mode="after")
    def _require_backend_specifics(self) -> ConnectionConfig:
        if self.backend == "jdbc" and self.jdbc is None:
            msg = "backend 'jdbc' requires a 'jdbc' configuration block."
            raise ValueError(msg)
        if self.backend == "odbc" and self.odbc is None:
            msg = "backend 'odbc' requires an 'odbc' configuration block."
            raise ValueError(msg)
        return self

    def endpoints_in_preference_order(self) -> tuple[str, ...]:
        """Replicas first (never write to source), then the primary host.

        Each entry is a ``host`` or ``host:port`` string; an empty primary host
        (JDBC url-template / ODBC DSN cases) contributes no bare entry.
        """
        primary = ()
        if self.host:
            primary = (f"{self.host}:{self.port}" if self.port else self.host,)
        return (*self.read_replicas, *primary)


class JoinExtraction(BaseModel):
    """A detail/child table LEFT- or INNER-JOINed onto a base extraction.

    Real cores are normalized header/detail: a loan's balance, currency and
    maturity live in an account master while its *pricing* (interest rate, index,
    spread, repricing date) lives in a separate contract table keyed by account.
    A single canonical position needs both, so a join pulls the detail columns
    into the same row rather than losing them — without inventing a value the
    source lacks.

    ``on`` is a ``{base_column: detail_column}`` map of equality keys (ANDed).
    ``columns`` is the explicit, non-empty set of detail columns to project; each
    is exposed to the mapping under its own bare name, so the join columns must
    not collide with a base column or with another join's columns (the query
    builder rejects a detectable collision). ``kind`` defaults to ``left`` so a
    missing detail row degrades the enrichment to NULL instead of dropping the
    base position.
    """

    model_config = ConfigDict(frozen=True)

    table: str
    on: dict[str, str] = Field(default_factory=dict)
    columns: tuple[str, ...] = ()
    kind: Literal["left", "inner"] = "left"

    @model_validator(mode="after")
    def _require_keys_and_columns(self) -> JoinExtraction:
        if not self.on:
            msg = f"join on {self.table!r} requires at least one 'on' key pair."
            raise ValueError(msg)
        if not self.columns:
            msg = f"join on {self.table!r} requires a non-empty 'columns' projection."
            raise ValueError(msg)
        return self


class TableExtraction(BaseModel):
    """One physical table/view mapped to one canonical record kind.

    ``table`` is schema-qualified as the source expects (``DBO.GL_ACCOUNTS``,
    ``COREBANK.POSITIONS``); the query builder quotes it per backend dialect.
    ``columns`` empty means "select every introspected column" so onboarding can
    start from discovery. ``incremental_column`` names a timestamp/rowversion
    cursor for incremental pulls; absent means the table is full-refresh only.
    """

    model_config = ConfigDict(frozen=True)

    table: str
    record_kind: RecordKind
    dataset_kind: ReferenceDatasetKind | None = None
    columns: tuple[str, ...] = ()
    # Detail tables LEFT/INNER-JOINed onto this base table to enrich each row
    # with columns from a normalized child (e.g. loan pricing keyed by account).
    joins: tuple[JoinExtraction, ...] = ()
    incremental_column: str | None = None
    # Equality predicates applied as parameterized ``WHERE col = :p`` clauses —
    # e.g. pinning a company/branch code. Values are bound, never interpolated.
    filters: dict[str, Any] = Field(default_factory=dict)
    # Soft-delete marker column (§5.3: incremental must handle deletes); when
    # set, rows are pulled regardless of the flag and the flag is carried into
    # attributes for downstream supersession/soft-delete handling.
    soft_delete_column: str | None = None
    # Constant values injected into every row of this table at staging, as synthetic
    # columns the mapping reads like any other. For cores that imply a value by table
    # rather than carrying it as a column — e.g. a deposit table whose rows are all
    # position_type DEPOSIT — set ``{"POSITION_TYPE": "DEPOSIT"}`` here.
    constant_fields: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reference_needs_dataset_kind(self) -> TableExtraction:
        if self.record_kind == "reference" and self.dataset_kind is None:
            msg = f"reference extraction for {self.table!r} requires a 'dataset_kind'."
            raise ValueError(msg)
        if self.record_kind != "reference" and self.record_kind not in ENTITY_TYPES:
            msg = f"unknown record_kind {self.record_kind!r} for table {self.table!r}."
            raise ValueError(msg)
        return self


class ExtractionSpec(BaseModel):
    """The per-bank list of tables to pull and how to pull them."""

    model_config = ConfigDict(frozen=True)

    tables: tuple[TableExtraction, ...] = ()
    default_mode: ExtractionMode = "full"
    # Name of the source column carrying the snapshot's authoritative reporting
    # date (e.g. FLEXCUBE ``AS_OF_DATE``). When set, the sync reads the actual
    # as-of from the pulled data and reconciles it against the requested date, so
    # a point-in-time regulatory book can never be silently valued at the wrong
    # date. Tables lacking the column simply do not contribute a date. Empty ->
    # the requested as-of is used verbatim (no reconciliation).
    as_of_column: str | None = None

    def for_entity_types(self, entity_types: list[str]) -> tuple[TableExtraction, ...]:
        """Extractions producing one of ``entity_types`` plus all reference tables.

        Reference tables are always included (they are consumed as-is by the
        engines regardless of the entity filter), mirroring the file adapters.
        """
        wanted = set(entity_types)
        return tuple(
            t for t in self.tables if t.record_kind == "reference" or t.record_kind in wanted
        )


def parse_connection_config(options: dict[str, Any]) -> ConnectionConfig:
    """Parse the ``connection`` block out of ``AdapterConfig.options``."""
    raw = options.get("connection")
    if not isinstance(raw, dict):
        msg = "AdapterConfig.options must carry a 'connection' object for database-direct."
        raise ValueError(msg)
    return ConnectionConfig.model_validate(raw)


def parse_extraction_spec(options: dict[str, Any]) -> ExtractionSpec:
    """Parse the ``extraction`` block out of ``AdapterConfig.options``."""
    raw = options.get("extraction")
    if raw is None:
        return ExtractionSpec()
    if not isinstance(raw, dict):
        msg = "AdapterConfig.options 'extraction' must be an object when present."
        raise ValueError(msg)
    return ExtractionSpec.model_validate(raw)
