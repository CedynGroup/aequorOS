"""The backend-driver abstraction: the ONLY place a live DB connection exists.

Everything above this seam (extraction, bundle staging, translation) is pure and
offline. A :class:`DatabaseDriver` knows how to open an authenticated, TLS-
enforced, read-only session to one backend (Oracle / SQL Server / JDBC / ODBC),
introspect its catalog, and run a parameterized :class:`BuiltQuery`. The live
network I/O is confined to the concrete drivers so the rest of the adapter — and
its whole contract test suite — runs against an offline fixture driver that
implements the same Protocol.

Design rules every driver obeys:

- **Read replica preference, never write to source.** Drivers connect through
  :meth:`ConnectionConfig.endpoints_in_preference_order` (replicas first) and
  execute only the ``SELECT`` statements the query builder produces.
- **TLS enforced.** A driver refuses to open an unencrypted session when
  :attr:`TlsConfig.enabled` is set, raising a classified ``TLS_REQUIRED``.
- **Credentials per cycle.** Credentials arrive as a transient
  :class:`DbCredentials`, are used for one session, and are never persisted or
  logged (only their fingerprint may appear in logs).
- **Lazy, classified driver imports.** The heavy client library is imported
  inside a method; its absence raises a classified ``DRIVER_UNAVAILABLE`` rather
  than breaking app import.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.adapters.database_direct.config import Backend, ConnectionConfig
    from app.adapters.database_direct.query_builder import BuiltQuery


@dataclass(frozen=True)
class DbCredentials:
    """Transient credentials for one pull cycle. Never persist or log the values.

    ``extra`` carries backend-specific secret material (e.g. a wallet password,
    a Kerberos ticket cache path) the driver may need. The credential fingerprint
    — not these values — is the only representation that may reach a log.
    """

    username: str
    password: str = ""
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DriverCapabilities:
    """What extraction modes a backend/driver actually supports.

    ``supports_change_data_capture`` reflects a native CDC facility (SQL Server
    CDC, Oracle GoldenGate/LogMiner). When false, incremental extraction falls
    back to a timestamp/rowversion cursor if the table declares one.
    """

    supports_change_data_capture: bool = False
    supports_incremental_timestamp: bool = True
    supports_schema_introspection: bool = True


@dataclass(frozen=True)
class ColumnSchema:
    """One introspected column: physical name, declared type, nullability."""

    name: str
    data_type: str
    nullable: bool = True


@dataclass(frozen=True)
class TableSchema:
    """One introspected table/view and its columns."""

    name: str
    columns: tuple[ColumnSchema, ...]
    schema: str | None = None
    approximate_row_count: int | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


@dataclass(frozen=True)
class QueryResult:
    """A parameterized read's result: column names + row tuples, values as-read.

    Values are whatever the DBAPI returned (``Decimal``, ``datetime``, ``str``,
    ``bytes`` for non-UTF charsets, ...). Normalization to canonical-friendly
    forms happens in the extraction layer, not the driver, so the driver stays a
    thin, faithful transport of exactly what the source held.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    def as_dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row, strict=False)) for row in self.rows]


@runtime_checkable
class DriverSession(Protocol):
    """An open, authenticated, read-only session against one backend.

    Used as a context manager so the connection is always closed, even on error.
    """

    def introspect(self, schemas: tuple[str, ...]) -> list[TableSchema]:
        """List tables/views (and their columns) in the given schemas.

        Empty ``schemas`` means the driver's default/current schema.
        """
        ...

    def fetch(self, query: BuiltQuery) -> QueryResult:
        """Execute one parameterized read query and return all rows."""
        ...

    def __enter__(self) -> DriverSession: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None: ...


class DatabaseDriver(ABC):
    """Opens sessions to one backend. Concrete drivers confine all network I/O."""

    @abstractmethod
    def backend(self) -> Backend:
        """The backend identifier this driver serves."""

    @abstractmethod
    def capabilities(self) -> DriverCapabilities:
        """The extraction facilities this backend/driver supports."""

    @abstractmethod
    def connect(self, connection: ConnectionConfig, credentials: DbCredentials) -> DriverSession:
        """Open a TLS-enforced, read-only session, preferring a read replica.

        Raises a classified :class:`~app.adapters.database_direct.errors.
        DatabaseDirectError` on any failure — never a raw DBAPI exception, and
        never one carrying core-internal text onto a bank-facing surface.
        """
